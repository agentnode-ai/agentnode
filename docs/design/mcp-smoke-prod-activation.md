# MCP sandbox smoke — production activation (scoping + phased plan)

> Status: **SCOPING / PLAN**. Nothing is activated here. The whole 2c arc is built
> and both executors (npm/PyPI) are host-verified, but the smoke is INERT by
> default (`MCP_SMOKE_MODE=disabled`). This documents how to turn it on in
> production **safely**, as separate gated steps — NOT the final auto-publish flip.

## 0. Ist-Analyse (prod, read-only verified 2026-07)

- **Runtime:** Docker 29.1.3 on the host; `agentnode-api` runs as **root** (empty
  `User=`) → has docker access (a future de-root needs the docker group).
- **Pinned image:** `ghcr.io/agentnode-ai/sandbox@sha256:6c77…c80f` is **present**.
- **Code:** prod `/opt/agentnode` = `b0ce927`; **`smoke_executor.py` is ABSENT** —
  the 2c code is not in prod yet. **Activation requires a deploy first.**
- **Config:** `MCP_SMOKE_*` is **not** in `/opt/agentnode/backend/.env` → default
  `MCP_SMOKE_MODE=disabled` (no `EnvironmentFile=`; pydantic reads `.env` from the
  WorkingDirectory `/opt/agentnode/backend`).
- **Clean:** 0 `mcp-smoke-*` volumes/containers.
- **Host:** **2 CPU cores**, shared with API + PostgreSQL + Meilisearch → resource
  headroom is the main risk.
- **Drift live→main:** 6 `backend/app` files (5 mcp + config) + 1 web (admin
  SmokeBadge) + docs. **NO migration, NO dependency change.**

## 1. Activation is THREE separate gated steps (not one)

Mixing code rollout and runtime activation into one gate would be hard to roll
back. Keep them apart:

1. **Phase 1 — deploy the 2c code, INERT.** Deploy `main` → `/opt/agentnode`.
   `MCP_SMOKE_MODE` stays unset → disabled. Nothing smokes; no container runs.
   Modest backend+web deploy, no migration, no deps. Fully reversible (ff-back).
2. **Phase 2 — one controlled isolated host smoke.** After Phase 1, run the REAL
   production runner (`run_smoke`, same image / limits / `network=none`) against 1
   npm + 1 PyPI submission via an isolated process (`MCP_SMOKE_MODE=container` set
   only in that process), WITHOUT flipping the global config or touching the
   scheduling logic. Validate result + resources + cleanup. Do NOT trigger it via
   a normal submit/reverify.
3. **Phase 3 — global activation.** Only after Phase 2 is green: set
   `MCP_SMOKE_MODE=container` in `backend/.env` + restart `agentnode-api`. Now
   submit/reverify schedule real smokes (freshness-gated). With monitoring, abort
   criteria, and instant rollback.

The final **auto-publish flip is a SEPARATE later gate** — not part of activation.

## 2. Inert safety of Phase 1 (why the deploy runs nothing)

`smoke_availability()` short-circuits: `if MCP_SMOKE_MODE != "container": return
(False, "disabled")` **before** any `docker` call. `maybe_schedule_smoke` only
runs on submit/reverify and returns early when unavailable. So with mode=disabled
no smoke is ever scheduled and no `docker image inspect`/`run` happens from the
smoke path. (config.py already runs `docker info` at import — pre-existing, not new.)

## 3. Per-area notes (Phases 2–3)

| Area | Note |
|---|---|
| `MCP_SMOKE_MODE` origin | pydantic default `disabled`; set in `backend/.env` at Phase 3. Rollback = remove/flip + restart. |
| Runtime | docker present, root service → works. |
| Image/digest preflight | re-`docker image inspect <digest>` before Phase 3; `sandbox pull` (anon, public) if missing — fail-closed otherwise. |
| systemd/env change | exactly one line `MCP_SMOKE_MODE=container` (+ optional `MCP_SMOKE_MAX_CONCURRENT`/timeouts) + api restart. |
| API resources / timeouts / concurrency | smoke = BackgroundTask → `run_in_executor` + Semaphore(1); never blocks the submit response. 2 `docker run` per smoke (install ≤120s, run ≤30s), container capped 1cpu/512m → up to ~50% of the 2-core host for ≤2min. Keep `max_concurrent=1`, watch API p95. |
| First controlled smoke | isolated runner on 1 real public+pinned+non-credentialed npm + PyPI submission; if prod has none, use a controlled test submission (e.g. `@modelcontextprotocol/server-everything@2026.7.4` / `mcp-server-time==2026.7.10`). |
| Logging | executor logs no container stdout/stderr verbatim — only reason codes + the SmokeResult (public package/version/hashes, no tokens; smoke uses no credentials). |
| Volume/container cleanup | `run_smoke` removes its volume in `finally`; containers `--rm` (0 leftovers in host tests). Residual: a hard-killed task could leak a volume → optional safety-net timer pruning stale `mcp-smoke-*` (not built; low risk at concurrency 1). |
| Rate limits / anti-spam | freshness-gated (skip-if-fresh) + running marker + Semaphore(1) + submit rate_limit + no cron. Submit=once, reverify=admin → bounded. |
| Rollback | flip/remove `MCP_SMOKE_MODE` + api restart → instant inert. Stored SmokeResults stay in JSONB (harmless; nothing auto-publishes). |
| Monitoring / abort | watch API p95, host load/CPU, smoke failure-rate + durations, leftover volume/container count, journal errors, admin SmokeBadge. Abort (→ disabled) on latency degradation, CPU saturation, leaks, unexpected behavior, or any secret in a log. |
| Existing submissions | NOT retroactively smoked (no smoke result → `not_run` → future → not eligible, unchanged). Only new submits + explicit admin reverifies smoke → no retroactive load spike. |
| Auto-publish | activation makes real SmokeResults and can make `auto_publish_eligible` computably-True, but `publish_submission` is admin-only and nothing auto-publishes. Publish behavior is unchanged. |

## 4. Migration / deploy / config

- **Migration: none.** **Deploy: yes** (6 backend/app + 1 web, no migration/deps) —
  required because the code is absent in prod. **Config: only** `MCP_SMOKE_MODE=
  container` at Phase 3.

## 5. Recommendation

Do **Phase 1 (inert deploy)** as its own gate; then Phase 2 (isolated controlled
smoke); then Phase 3 (global activation) with monitoring + rollback. Real
production SmokeResults must be produced and evaluated before the separate,
later auto-publish flip.
