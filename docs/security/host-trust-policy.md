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
| `default` | Today's behavior: `curated` and `trusted` run on the host; everything else is sandboxed or fail-closed. |
| `curated_only` | Only `curated` runs on the host. **`trusted` is sandboxed.** |
| `none` | **Nothing** runs on the host. `curated`, `trusted` and community are all sandboxed. |

The default is `default`, so upgrading changes nothing until you opt in. The
setting is honored uniformly by all three foreign-code paths — **toolpacks, MCP
servers, and agents** — through one shared decision (`requires_sandbox_for_policy`).

- **Toolpacks / MCPs:** a tier the policy sandboxes runs in the container instead
  of on the host (network per its declaration for toolpacks; `none` or a sealed
  egress allowlist for MCPs).
- **Agents:** a tier the policy sandboxes runs its orchestration code inside the
  container agent session instead of on the host thread/process.

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
> by the opt-in `agent_sandbox.enabled` flag (refused by default, sandboxed when
> enabled). `host_trust_policy` only adds routing for the `trusted`/`curated`
> tiers. See [agent-execution-boundary.md](agent-execution-boundary.md).

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
