# MCP Auto-Publish — Slice 2c-4: smoke recheck / expiry / audit (scoping + plan)

> Status: **SCOPING / PLAN**. 2c-4a (the freshness/expiry model) is built alongside
> this doc; the recheck triggers (2c-4b), admin/API visibility (2c-4c), and the
> optional audit table (2c-4d) are NOT built here. No migration, no deploy, no
> prod activation, no auto-publish. Smoke executors stay INERT by default.

Once smoke results count toward the `sandbox_smoke` gate (2c-5), a passed result
must not be valid forever. This slice defines WHEN a smoke stays valid, when it
becomes stale, and how a stale result maps to the gate — without running anything.

## 1. Ist-Analyse (code-verified)

- **SmokeResult today** (`smoke_executor._result`): `status, runtime, package,
  version, command_hash, initialized, tools_count, duration_ms, sandbox_backend,
  image_digest, run_model, failure_reason, review_reason, checked_at`. Present:
  `checked_at` + all binding keys. **Missing:** `expires_at`, `recheck_at`,
  `schema_version`.
- **`derive_smoke_evidence` checks NO freshness** — it only forwards
  checked_at/expires_at/recheck_at if present. A `passed` smoke therefore counts
  forever, regardless of age or of version/command/image changes. This is the gap.
- **`maybe_schedule_smoke` runs only at submit** (`router.py`), NOT at reverify.
- **Smoke is not surfaced** anywhere (SubmissionSummary / admin / web show no
  smoke status; the admin badge only names "pending ownership+smoke").
- **Reusable:** the ownership claim already has an `expires_at <= now` TTL pattern;
  `run_and_store_smoke` (background, semaphore, running-marker) exists; the reverify
  cron in `app/tasks/cron.py` is a pattern for a later scheduled recheck.

## 2. Freshness rules

A stored `passed` smoke is **fresh** only when ALL binding keys match the current
submission AND it is within TTL:

- **Binding keys:** `runtime (registry) · package · version · command_hash ·
  image_digest · run_model · schema_version`.
- **TTL:** 30 days default (`MCP_SMOKE_TTL_DAYS`). The reason for a TTL despite an
  immutable pinned version is transitive-dependency drift.
- **Invalid (recheck-needed)** on: TTL expiry · any binding-key mismatch ·
  image-digest rotation · command change · version change · `schema_version` bump.

Implementation: a pure `evaluate_smoke_freshness(smoke, current_keys, now) ->
fresh | expired | key_mismatch | running | unavailable | not_passed | none`. No
I/O — the current keys come from the manifest/sv/settings; the stored keys are
already in the smoke evidence.

## 3. Recheck strategy

- **2c-4a is the MODEL only** — no trigger, no scheduling, no cron.
- **2c-4b (later):** trigger a recheck at submit (already) + reverify (new); skip
  when a fresh result already exists (anti-spam); reuse `run_and_store_smoke`
  (BackgroundTasks + semaphore + running-marker). No new queue/worker.
- **Cron (later, optional):** a scheduled recheck of expiring smokes, reusing the
  `cron.py` pattern.
- **Transient recheck:** an old FRESH passed smoke stays valid while a recheck runs
  or transiently fails (registry/sandbox unavailable) — we never overwrite a good
  result with "unavailable". Only a definitive new result (passed/failed) or
  TTL-expiry / key-mismatch invalidates. An already-EXPIRED smoke whose recheck is
  unavailable stays not-passed / recheck-needed (freshness unconfirmable). A
  sandbox outage never flips a verified server to blocked.

## 4. Expiry behavior

- `expired` or `key_mismatch` is **not** an objective package failure (the server
  isn't broken; the proof is stale). It **blocks auto-publish** and routes to
  **review / recheck-needed** — never a hard reject.
- Wiring: `evaluate_gates` computes freshness and downgrades a stale `passed` smoke
  to `expired`/`key_mismatch` before `derive_smoke_evidence`, so the gate reads
  `passed` only for a fresh passed smoke.

## 5. Audit — recommendation: Variant A (MVP, no migration)

- **A (MVP):** only the latest SmokeResult in `server_verification["smoke"]`, with
  `checked_at/expires_at/recheck_at/schema_version` in the evidence. Old runs
  overwritten. No history. No migration.
- **B (`mcp_smoke_runs` table):** full history — needs a migration + more surface.
  Deferred as **2c-4d**, added only when a real forensic/abuse-history need arises;
  append-only, writes in `run_and_store_smoke`, does not change the gate logic.

## 6. GateResult mapping

| smoke state | passed | future | class | auto_eligible |
|---|---|---|---|---|
| passed + fresh | yes | – | gate passes | possible (if all else clean) |
| passed + expired | no | yes | future / review / recheck-needed | **false** |
| passed + key_mismatch | no | yes | future / review / recheck-needed | **false** |
| running | no | yes | future (recheck running) | false |
| unavailable | no | yes | future / review | false |
| failed hard (startup/protocol/tools-list) | no | no | **objective_blocker** | false |
| failed transient (install/registry/timeout/…) | no | yes | review / retryable | false |
| skipped (credentialed/private/unsupported) | no | yes | review-fallback | false |
| no result (not_run) | no | yes | future | false |

`auto_publish_eligible` stays false for expired/key_mismatch/running/unavailable/
no-result because each yields `passed=false` → a non-passed blocking gate ⇒ not
eligible (guaranteed by the existing gate logic).

## 7. Security / invalidation rules

- **Image-digest rotation** invalidates old smokes (`image_digest` is a binding
  key → key_mismatch → recheck).
- **`schema_version` bump** invalidates old smokes — bump it on any
  security-relevant executor change so a pre-fix passed smoke stops counting.
- No old passed smoke survives a version / command / image / schema change or the
  TTL. (`executor_version` could split from `schema_version` later; one
  `schema_version` int is enough for the MVP.)

## 8. Slice order

- **2c-4a — freshness/expiry model** *(built with this doc; no migration, no
  execution, no trigger, no cron, no UI)*: add `expires_at/recheck_at/
  schema_version` to the SmokeResult; a pure `evaluate_smoke_freshness`;
  `evaluate_gates` downgrades stale-passed → expired. The 2a/2b-1/2c-1 pattern.
- **2c-4b — recheck triggers** (submit + reverify; freshness-gated; reuse
  background infra).
- **2c-4c — admin/API visibility** (smoke_status / checked_at / expires_at /
  recheck_reason in SubmissionSummary + admin page).
- **2c-4d — audit table** (optional, only if history is needed; the only migration).
- **2c-5 — gate activation** (`future=false` for real fresh passed smokes) — still
  no auto-publish.
- **final — auto-publish activation** (separate).

## 9. Migration need — **No** for 2c-4a/b/c

All in `server_verification["smoke"]` JSONB. Only 2c-4d (audit table) would need a
migration — deferred/optional.

## 10. Recommendation

Build **2c-4a** next: the freshness/expiry model is the honest, no-risk foundation
— it defines when a smoke is valid and makes the gate read it, without executing
anything, without a migration, and without letting anything auto-publish (a stale
passed smoke stops counting; nothing new becomes eligible). Then 2c-4b recheck
triggers (make the logic act), then 2c-4c visibility. Audit table (B) only when a
real history need arises.
