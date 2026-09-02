# Host-Trust Policy

> `sandbox.host_trust_policy` lets **you** decide which trust tiers are allowed to
> run directly on your host. AgentNode trusting a package's code is not the same
> as *you* trusting it with your machine — this setting closes that gap.

## What it is

```bash
agentnode config set sandbox.host_trust_policy curated_only
```

| Value | Meaning |
|---|---|
| `curated_only` | **Shipped default.** Only `curated` (AgentNode-owned) runs on the host. **Trusted third-party code is sandboxed.** |
| `default` | More permissive than the shipped default: `curated` **and** `trusted` run on the host; everything else is sandboxed or fail-closed. |
| `none` | **Nothing** runs on the host. `curated`, `trusted` and community are all sandboxed. |

**The shipped default is `curated_only`.** A fresh install sandboxes trusted
third-party tool packs and MCP servers; only AgentNode's own curated packages run
directly on your host. Because the sandbox path is fail-closed, a trusted package
on a machine **without** a container runtime will now refuse to run rather than
falling back to the host — install Docker or Podman, or set the policy to
`default` if you accept host execution for trusted third-party code.

**An existing configuration is not rewritten.** If your config already records a
value, it keeps it; this changes what new installations get, never what you chose.
To adopt the new default explicitly:

```bash
agentnode config set sandbox.host_trust_policy curated_only
```

## What this setting does and does not decide

The setting is **not** applied uniformly to all three foreign-code paths. It
decides host-versus-sandbox routing for tool packs and MCP servers, and it can
add sandbox routing for `trusted`/`curated` agents — but it never decides whether
an agent's own entrypoint may run on the host, and it does not govern community
agents at all.

- **Tool packs and MCP servers** — this is the routing decision. A tier the policy
  sandboxes runs in the container instead of on the host (network per its
  declaration for tool packs; `none` or a sealed egress allowlist for MCPs).
- **Community agents** (`verified`, `unverified`, unknown) — **not governed by
  this setting**. They are governed by the separate opt-in flag
  `agent_sandbox.enabled`, which routes them sandbox-or-refuse. Tightening the
  host-trust policy neither enables nor disables them.
- **`trusted`/`curated` agents** — when the policy sandboxes their tier, the agent
  runs inside the container agent session. When the policy leaves their tier
  host-eligible, that does **not** grant host execution: an agent's own entrypoint
  is **independently and structurally refused** on the host at every tier and
  under every policy value. See
  [agent-execution-boundary.md](agent-execution-boundary.md).

  The one host-side agent path that still runs is a **declarative sequential
  orchestration** (`agent.orchestration.mode == "sequential"`). It loads no
  foreign entrypoint: it dispatches declared tool steps, and every step is
  re-gated through `run_tool` exactly like a direct tool call.

## ⚠️ Sandboxing is stronger isolation — it can break packages

This setting **isolates more strongly**. The container has no host filesystem, a
clean `HOME`, no host environment/secrets, and a restricted network. A package
that AgentNode marks `trusted`/`curated` but that **expects host access will
break** when the policy sandboxes it. Concretely, a sandboxed package loses:

- **Host filesystem** access (read-only `/pack` only; no host paths).
- **Host environment** and secrets (clean environment).
- **Broad network** (toolpacks get their declared network; MCPs get `none` or a
  sealed allowlist; **agents get `network=none`**).

This is the honest cost of the setting: you are choosing isolation over
transparency for code you don't want on your host.

### Agents are the strictest case

A sandboxed agent runs under the **same strict profile as a community agent** —
there are **no special rights for `trusted`/`curated` agents** in the first
version of this feature. That profile is:

- **Tools:** only the packages the agent declared in `tool_access.allowed_packages`.
  An agent with no declared allowlist gets **no tool access**.
- **LLM:** host-brokered and **default-deny** — the agent reaches an LLM only if
  it declared `llm_access.enabled`, and the host config ceiling
  (`agent_sandbox.llm`) always wins. The provider API key **never enters the
  container**.
- **Network:** `network=none`. **Read-only** `/pack`, clean `HOME`.

So a `trusted` agent that relied on host filesystem, broad tools, direct network,
or ambient host LLM keys will **stop working** under `curated_only`/`none`. A more
generous "sandboxed-but-trusted" agent profile is a deliberate **future** design
block, not part of this release.

> Community agents are unaffected by this setting — they are governed separately
> by `agent_sandbox.enabled`, which is **ON by default** (SDK 0.21.0): on a fresh
> configuration a community agent runs **sandbox-or-refuse**, never on the host.
> Setting it to `false` (or `AGENTNODE_AGENT_SANDBOX=0`) restores the pre-0.21
> behaviour, in which community agents are refused outright.
> `host_trust_policy` only adds routing for the `trusted`/`curated` tiers.
> See [agent-execution-boundary.md](agent-execution-boundary.md).

## After changing the policy: reinstall

Making the policy **stricter** means packages that were built for the host now
need a sealed sandbox volume. That volume is built **at install time**, so an
already-installed package must be **reinstalled** to run under the stricter
policy:

```bash
agentnode install <slug>          # rebuilds it into the sealed sandbox volume
```

Until you do, the package is **fail-closed** (refused with a reinstall hint) — it
is **never** silently run on the host. Use the doctor to see exactly what a
package needs:

```bash
agentnode sandbox doctor <slug>
```

The doctor distinguishes the cases for you:

- **built for the host under an older policy** → reinstall to rebuild in the sandbox;
- **sandbox volume missing** → reinstall;
- **MCP not pinnable** (ships no `mcp_install`) → **the publisher** must pin it;
  reinstalling cannot fix this;
- under **`none`**, a warning that `curated`/system packages needing host access
  may break.

## Fail-closed, always

If a sandboxed tier cannot actually be isolated — no container runtime, no built
volume, or a community MCP that isn't pinnable — the package is **refused**, never
run on the host as a fallback. Isolation-or-nothing is the whole point of the
setting.

## See also

- [agent-execution-boundary.md](agent-execution-boundary.md) — how agent code is executed and gated.
- `agentnode sandbox doctor` / `agentnode sandbox status` — per-package and environment diagnosis.
