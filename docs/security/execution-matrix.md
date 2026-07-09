# Execution Matrix

Where each kind of package runs — host, sandbox, or refused — as a function of
its trust tier and the two user-facing controls. This is the single reference
for "what happens when I run this?"; the per-mechanism details live in
[agent-execution-boundary.md](agent-execution-boundary.md) and
[host-trust-policy.md](host-trust-policy.md).

## The two controls

- **`sandbox.host_trust_policy`** — how much the *host-eligible* tiers
  (`trusted`, `curated`) are trusted to run directly on the host:
  - `default` — `curated` and `trusted` run on the host.
  - `curated_only` — only `curated` on the host; `trusted` is sandboxed.
  - `none` — nothing on the host; `curated` and `trusted` are sandboxed too.
- **`agent_sandbox.enabled`** (default **ON** since 0.21.0) — whether community
  agents may run at all. ON → community agents run sandbox-or-refuse. OFF →
  community agents are refused outright (the pre-0.21 behavior).

"Sandbox" always means **sandbox-or-refuse**: the container when a runtime and
the pinned image are present, otherwise a clean refusal. There is **never a host
fallback** for a tier that requires the sandbox.

## Matrix (under the `default` host-trust policy)

| Package kind | `curated` | `trusted` | `verified` (community) | `unverified` / unknown (community) |
|---|---|---|---|---|
| **Skill** (prompt-only) | host¹ | host¹ | host¹ | host¹ |
| **Tool pack** (code) | host | host | sandbox-or-refuse | sandbox-or-refuse |
| **MCP server** (code) | host² | host² | sandbox-or-refuse | sandbox-or-refuse |
| **Agent** (code) | host | host | sandbox-or-refuse³ | sandbox-or-refuse³ |

¹ **Skills execute no code** — they are prompt-only (`runtime: none`,
`install_mode: prompt_only`, no entrypoint), enforced by the publish validator.
There is nothing to sandbox, so a skill never needs the sandbox regardless of
tier. This is why skills show "No sandbox needed" everywhere.

² Curated/trusted MCP servers run on the host today; community MCP servers are
sandbox-or-refuse. See the MCP routing notes for credential brokering.

³ Community agents are gated by `agent_sandbox.enabled` (default ON). With it
OFF, the community-agent cells become **refused** (no sandbox attempt).

## How the controls shift the host-eligible tiers

`host_trust_policy` only moves `curated`/`trusted` between *host* and *sandbox*;
it never lets a community tier onto the host.

| Tier | `default` | `curated_only` | `none` |
|---|---|---|---|
| `curated` | host | host | sandbox |
| `trusted` | host | sandbox | sandbox |
| community (`verified`/`unverified`/unknown) | sandbox-or-refuse | sandbox-or-refuse | sandbox-or-refuse |

## Invariants

- **No host fallback for community code.** A community tool pack, MCP server, or
  agent either runs inside the container or is refused — it is never run on the
  host because the sandbox was unavailable.
- **Fail-closed.** A missing runtime, missing image, missing/stale sandbox
  volume, or an unknown network mode results in a refusal, never a silent
  downgrade to host execution.
- **Host secrets stay on the host.** Sandboxed code receives a minimal
  environment (`PYTHONPATH` only for agents); LLM keys are used by a host-side
  broker and never enter the container; tool calls are brokered host-side where
  the allowlist and policy are enforced.

## Known limitation (tracked)

`trust_level` is stored in the lockfile as a mutable field (so the periodic
trust-refresh can update it) and is **not** covered by the unkeyed integrity
hash. A local attacker with write access to the lockfile could forge a higher
tier; under the `default` policy that would grant host execution. Mitigations
today: tighten `sandbox.host_trust_policy`, and the periodic refresh corrects
the value from the registry. The robust fix (a registry-signed trust
attestation bound to the sealed `artifact_hash`) is scoped as a separate,
future change.
