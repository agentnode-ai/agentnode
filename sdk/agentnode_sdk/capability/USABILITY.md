# AgentNode Capability Layer — Closed-Beta Usability Guide (Slice 2)

Import a **real, existing** MCP server, unchanged, check where it came from and what it is
allowed to do, approve one call by hand, and run it inside AgentNode's sandbox — then drive
it from any MCP client (a generic client, Claude Code, or Codex). No AgentNode-internal
knowledge is needed: every step below is a command you can run.

## What you get

A new user imports the real upstream server
`@modelcontextprotocol/server-sequential-thinking` (npm, MIT, published by the Model Context
Protocol project). It is **not** rewritten — its published bytes are packaged and pinned by a
content digest. Its one tool, `sequentialthinking`, runs inside the AgentNode sandbox on a
worker; nothing runs unsandboxed, and no model can approve its own call.

## Prerequisites

- **Python 3.10+** and the `agentnode_sdk` package on your `PYTHONPATH` (the SDK you already
  have). The capability CLI is `python -m agentnode_sdk.capability.usability_cli`.
- A **container runtime** (Podman or Docker) and the pinned AgentNode sandbox image. The
  image is acquired explicitly (`agentnode sandbox pull`) and is never auto-pulled; if it is
  absent, execution stays fail-closed (it refuses, it never runs on the host). Importing an
  npm server also uses the runtime (it installs the package inside that image at build time;
  the *run* path has no network).
- A working directory for your **stand** (holds the local index, your operator key, the
  audit key, approvals). Below it is `./stand`.

No account, API key, or network credential is required: this capability is credential-free.

## 1. Import the real server (unchanged)

```
python -m agentnode_sdk.capability.usability_cli import-npm \
  --stand ./stand \
  --package @modelcontextprotocol/server-sequential-thinking --version 1.0.0 \
  --publisher-namespace modelcontextprotocol --name server-sequential-thinking \
  --publisher "Model Context Protocol" --license MIT \
  --source-url https://github.com/modelcontextprotocol/servers/tree/v1.0.0/src/sequentialthinking
```

This vendors the exact `@...@1.0.0` plus its dependency closure inside the pinned image,
packs the tree deterministically, pins it by a content `tree_digest`, asks the server over a
real stdio handshake what tools it exposes, and registers a **signed** capability manifest.
The server's published files are delivered byte-for-byte; the manifest is signed by *you* as
the importing publisher, never presented as upstream-signed.

## 2. Inspect provenance, permissions and guarantees

```
python -m agentnode_sdk.capability.usability_cli list   --stand ./stand
python -m agentnode_sdk.capability.usability_cli search --stand ./stand thinking
python -m agentnode_sdk.capability.usability_cli describe --stand ./stand \
  modelcontextprotocol/server-sequential-thinking --with-worker
```

`describe` shows the source and version, the signature (role, key-id) and the artifact +
schema digests, the execution model, the **requested** permissions (no network, no env
secrets), and the **achievable guarantees computed from your worker right now** — not a
vendor claim. If the worker cannot isolate, it says so, and a run would be refused rather
than executed unsandboxed.

## 3. Generate a client configuration

Generic MCP client (works with the broad MCP ecosystem):

```
python -m agentnode_sdk.capability.usability_cli config --stand ./stand --client generic
```

It prints a ready-to-use `mcpServers` object that launches the capability adapter over
stdio:

```json
{
  "mcpServers": {
    "agentnode-capability": {
      "command": "python",
      "args": ["-m", "agentnode_sdk.capability.usability_adapter", "./stand"],
      "env": {}
    }
  }
}
```

### Claude Code

Save the generic object (above) as `.mcp.json` in your project, or run
`config --client claude-code`. Claude Code reads `.mcp.json` with exactly this `mcpServers`
shape. Nothing vendor-specific lives in AgentNode's core — this is the same generic spec.

### Codex

Run `config --client codex` and add the printed block to `~/.codex/config.toml`:

```toml
[mcp_servers.agentnode-capability]
command = "python"
args = ["-m", "agentnode_sdk.capability.usability_adapter", "./stand"]
env = {}
```

> These two examples are **structurally validated** by the SDK (well-formed, point at the
> generic adapter, declare no secrets). Whether a specific Claude Code or Codex build was
> actually driven end-to-end is stated separately in the release evidence and is only claimed
> when it was really measured.

## 4. Call the tool — it is refused until a human approves

From a generic MCP client (or `usability_cli prepare`), calling `sequentialthinking` the
first time returns `approval_required` with the **exact next step**:

```json
{
  "status": "approval_required",
  "invocation_hash": "<hash>",
  "next_step": "python -m agentnode_sdk.capability.usability_cli approve --stand ./stand --invocation-hash <hash> --actor <your-name>",
  "note": "No model can approve this. A human runs the command above ..."
}
```

No model or client can approve itself — granting needs the operator key, which the adapter
never holds and exposes no tool for.

## 5. A human approves exactly this call

```
python -m agentnode_sdk.capability.usability_cli approve \
  --stand ./stand --invocation-hash <hash> --actor you@example.com
```

This deposits a single-use approval bound to that one `invocation_hash`.

## 6. Run it — in the sandbox, over the worker

```
python -m agentnode_sdk.capability.usability_cli run --stand ./stand \
  modelcontextprotocol/server-sequential-thinking sequentialthinking \
  --args '{"thought":"2 + 2 = 4","thoughtNumber":1,"totalThoughts":1,"nextThoughtNeeded":false}'
```

The same call now runs the real server inside the sandbox on the worker (no local fallback)
and returns the **result, the manifest, the job record and a signed, chain-bound audit
entry, bound together**. A generic MCP client gets the same worker-executed result through
the adapter.

## 7. The approval is spent

Run the exact same call again. It is **refused** — the approval was single-use and is already
consumed (durably, surviving a restart). Approve again (step 5) to run again.

## What this does and does not promise

- Runs only inside the sandbox on an accepted worker; **no unsandboxed local fallback**.
- The imported upstream server stays **byte-unchanged**.
- **No model can approve itself** or mint a grant.
- Isolation is whatever the worker **measures for the run**; a local/single-host worker
  shares a kernel with the control plane (not a multi-tenant boundary) and says so.
- Out of scope here: remote/SSE/HTTP MCP, a public registry/marketplace, credentialed
  servers or a credential broker, and cloud routing.
