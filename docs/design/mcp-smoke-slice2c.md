# MCP Auto-Publish — Slice 2c: server-authoritative sandbox smoke (scoping + plan)

> Status: **SCOPING / PLAN**. This document is a plan only. Slice 2c-1 (the smoke
> evaluator + result model, advisory-only) is built alongside it; the real
> executors (2c-2/2c-3) and the gate activation (2c-5) are NOT built here.
> Founder line unchanged: **auto-publish only when ALL hard gates pass; nothing
> auto-lives until strong ownership AND a real sandbox smoke both pass.**

`sandbox_smoke` is the **last future-blocker** before auto-publish. Today
`gates.py` hardcodes it to `passed=False, future=True`, so `auto_publish_eligible`
is structurally False for everything, MCP stays review-gated. This slice defines
what a server-authoritative smoke must prove and wires the gate to read a real
`SmokeResult` — without running anything yet.

## 1. Ist-Analyse (code-verified)

**Reusable infrastructure already present:**
- **`ContainerBackend`** (`sdk/agentnode_sdk/sandbox/container_backend.py`):
  docker/podman auto-detect, **digest-pinned** GHCR image, **fail-closed / no
  host fallback**. Primitives: `run_process(spec) -> (rc, stdout, stderr)` and
  `open_agent_session(spec)` = bidirectional JSON-RPC over stdio.
- **Hardened flags** (`_HARDENED_FLAGS`): `--network none · --read-only ·
  --cap-drop=ALL · --security-opt=no-new-privileges · --user 1000 · --pids-limit
  256 · --memory 512m · --cpus 1`, tmpfs `noexec,nosuid`, clean HOME, env by name.
- **MCP JSON-RPC handshake EXISTS**: `sdk/agentnode_sdk/runtimes/mcp_runner.py`
  `MCPServerProcess` (start → `initialize` protocolVersion `2024-11-05` →
  `notifications/initialized` → `tools/list`/`tools/call` → clean shutdown). The
  same handshake is in `sdk/agentnode_sdk/cli/mcp_verify.py` `check_protocol()`
  (used by `agentnode mcp verify --test`).
- **The pinned sandbox image already contains BOTH MCP runtimes** — verified on
  the server: node v20.18.1 + `npx`/`npm` AND python 3.11 + `uvx`/`uv`/`pip`
  (image `ghcr.io/agentnode-ai/sandbox@sha256:6c77…c80f`). **No new image needed.**
- **`server_verification`** is a free-form JSONB column on `McpSubmission`
  (`backend/app/mcp/models.py`); `gate_result` already lives inside it
  (`router.py` `_attach_gate_result`). A smoke result fits alongside — **no
  migration**.
- **The `sandbox_smoke` gate slot exists** (`backend/app/mcp/gates.py`), hardcoded
  `passed=False, future=True`.
- **Today the backend NEVER executes an MCP** (`registry_verify.py` does registry
  metadata only) — so the real work in 2c-2/2c-3 is a background executor.

## 2. Machbarkeits-Verdikt

**Feasible, and cheaper than expected.** ~80% is already built (sandbox backend,
hardened flags, fail-closed, JSON-RPC handshake, gate wiring, JSONB storage). The
biggest feared blocker — a multi-runtime image — **does not exist**: the pinned
image already runs npm and PyPI MCPs.

- **No new image needed.**
- **No migration needed** for the MVP (smoke result lives in `server_verification`
  JSONB; a historical `mcp_smoke_run` audit table is optional, in 2c-4).
- **No secret / OAuth / infra arc needed** (unlike 2b-3b repo-control).

Remaining real work: a **background executor** (install pinned MCP in the
container → start → speak the existing handshake → collect evidence) plus a
`smoke=` input on `evaluate_gates` and one extra sv-write point. That is 2c-2+.

## 3. Smoke definition — what a PASS must prove

1. **package/version pinned** — already enforced before the smoke by the
   `version_pinned` gate + npx re-pin in `registry_verify`.
2. **Container starts** the pinned MCP (process launches, no immediate crash).
3. **MCP `initialize` ok** (JSON-RPC handshake, protocolVersion negotiated).
4. **`tools/list` ok** — returns a tools array; **`tools_count` captured**.
5. **Clean shutdown** (terminate → kill; a hang → **timeout = fail**).
6. **No host secrets** in the container (env by-name allowlist; no real creds).
7. **No host-FS leak** (read-only rootfs, RO mount, tmpfs `/tmp`).
8. **Runtime `network=none`** as the target (the handshake needs no network).
9. **Timeout → fail-closed.**

## 4. GateResult design (all in JSONB, no migration)

The future executor produces a `SmokeResult`; `evaluate_gates` reads it via a new
`smoke=` param and derives the gate:

```jsonc
{
  "id": "sandbox_smoke",
  "passed": true,                 // only when a real smoke passed
  "blocking": true,
  "future": false,                // false ONLY once a real smoke result exists;
                                  // true while not-run / unavailable / skipped
  "evidence": {
    "runtime": "npm|pypi", "package": "…", "version": "1.2.3",
    "command_hash": "…", "initialized": true, "tools_count": 7,
    "duration_ms": 2140, "sandbox_backend": "docker|podman",
    "image_digest": "sha256:6c77…", "failure_reason": null
  },
  "reason": "",
  "checked_at": "…", "expires_at": "…", "recheck_at": "…",
  "review_fallback_reason": null  // set when smoke unavailable / skipped
}
```

**SmokeResult status → gate mapping (2c-1 contract):**

| SmokeResult status | gate `passed` | gate `future` | meaning |
|---|---|---|---|
| `passed` | true | **false** | a real smoke passed → gate can pass |
| `failed` (hard: initialize/tools-list/startup/protocol) | false | **false** | genuine, objective failure of the submission → objective blocker |
| `failed` (transient: install/registry/timeout) | false | true | retryable / review-fallback, not the submission's objective fault |
| `unavailable` (backend/image/runtime missing) | false | true | infra gap → review-fallback, not an objective blocker |
| `skipped` (credentialed / private / high-risk) | false | true | policy → human review-fallback |
| `not_run` / absent (today) | false | true | no result yet → identical to today's hardcoded gate |

The `future` flag drives the existing split: **future blockers are excluded from
`objective_blockers`**, so a submission blocked ONLY by not-run/unavailable/skipped
smoke reads "objectively clean, pending infrastructure"; a genuine `failed` smoke
is an objective blocker (the submission is broken).

## 5. Review-fallback cases (smoke never auto-publishes → human/retry)

`smoke unavailable` · `registry unavailable` · `install fails` (retryable → then
action_required) · `initialize fails` (hard block) · `tools/list fails` (hard
block) · `high-risk permissions` · `credentialed MCP` · `private package` ·
`sandbox backend unavailable`. In every case `auto_publish_eligible` stays False
and the submission is handled by review; the objective-vs-future distinction only
changes *reporting* (submission's fault vs infra/policy). (`unpinned version` and
`ownership missing` are handled by their own gates, not the smoke.)

## 6. Security risks → handling

| Risk | Handling |
|---|---|
| Malicious MCP code / postinstall / install scripts | runs ONLY in the container: cap-drop=ALL, read-only rootfs, non-root, no-new-privileges; host never touched |
| Network egress / data exfiltration | two-phase: install with registry network, **runtime `network=none`** (handshake needs none) |
| Fork bomb / long-running / hang | `--pids-limit 256`, `--memory`, `--cpus`, **timeout → kill → fail** |
| Huge output | output truncation (backend sandbox has `_truncate_log` as the pattern) |
| Dependency confusion / version drift / unpinned | version pinned/resolved BEFORE the smoke; unpinned already blocked by `version_pinned` |
| Private / credentialed packages | **review-fallback** (a fair smoke can't run without private-auth / real secrets) |

## 7. Slice order (each its own gated go)

- **2c-1 — smoke evaluator + result model, advisory-only** *(built with this doc;
  no execution, no migration, no infra)*: define the `SmokeResult` contract +
  `derive_smoke_evidence(...)` + a `smoke=` param on `evaluate_gates`; the gate
  reads the result. With no result the gate stays `future=true` — identical to
  today. Pure, table-testable. **The 2a / 2b-1 pattern.**
- **2c-2 — npm sandbox-smoke executor** *(reuses image + backend + handshake)*:
  two-phase install-with-net → run `network=none`, `initialize` + `tools/list`,
  collect evidence; background task; a 4th sv-write point.
- **2c-3 — PyPI sandbox-smoke executor** (uvx/pip), analogous.
- **2c-4 — recheck / expiry / audit** (per exact version; TTL; optional
  `mcp_smoke_run` history table → the only migration, if wanted).
- **2c-5 — gate activation** (flip `future=false` when a real result is present) —
  **still no auto-publish**.
- **final — auto-publish activation** (separate, later; also needs strong ownership).

## 8. Decision points (founder) — needed before 2c-2, not for 2c-1

1. **Install network**: two-phase (install with net → run `network=none`)
   *[recommended]* vs. one container with restricted egress?
2. **Runtime network**: `network=none` *[recommended]* vs. restricted?
3. **Credentialed MCPs**: always review-fallback *[recommended]* vs. smoke with
   placeholder env + "auth-required" as a soft pass?
4. **Private packages**: review-fallback *[recommended]*?
5. **Smoke TTL** + **per-version recheck** *[recommended: yes, per exact
   `package@version`]*?
6. **Failure classes** hard-block vs retryable (esp. timeout, install-fail)?

## 9. Migration need — **No** (for the MVP)

`server_verification` JSONB carries the smoke result and gate; `evidence` is
free-form. The only place a migration could appear is an optional `mcp_smoke_run`
history table in 2c-4 — a separate mini-plan, only if a run audit trail is wanted.

## 10. Recommendation

Build **2c-1** next: the smoke evaluator + result model + gate wiring is the
honest, zero-risk foundation — it defines the smoke contract and makes the gate
read it, without executing anything, without a migration, and without letting
anything auto-publish (no result → the gate stays a future-blocker). It turns the
smoke into an explicit, testable `SmokeResult` the executor (2c-2) can later
populate. Auto-publish stays off until a real smoke AND strong ownership can both
pass.
