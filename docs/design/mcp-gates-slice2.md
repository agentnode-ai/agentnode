# MCP Auto-Publish — Slice 2: hard server gates (scoping + plan, not built)

> Status: **SCOPING / PLAN**. No code, no migration, no auto-publish activation.
> Slice 1 (status convergence) is merged (PR #67, main c169f5c): a clean MCP
> submission now lands in `quarantined_review` and admin approve → publish is
> still required. This document scopes the hard server-side gates that would let
> a submission become auto-publish-eligible, and recommends the next buildable
> slice. Two gates hit the founder's stop-conditions (new infra / architecture
> decision) and are broken out as their own decision-gated slices.

## 1. Gates that ALREADY exist (server-authoritative)

Encoded today in `_verify_publish_gate` (`backend/app/mcp/router.py:773-847`),
`verify_registry` (`registry_verify.py:270-360`), and `mcp_policy`:

| Gate | Where | Kind |
|---|---|---|
| Package exists on npm/PyPI | verify_registry `package_exists` | hard |
| Version resolves | verify_registry `resolved_version` / `version_exists` | hard |
| Registry reachable (not `unavailable`) | verify_registry `server_status` | hard |
| Registry/metadata not contradicting manifest (`mismatch`) | verify_registry `server_status` | hard |
| `repo_consistency` != mismatch (source_repo vs registry repo) | verify_registry | hard (mismatch blocks; indeterminate allowed) |
| Credentialed-publish policy (egress allowlist + install descriptor binding) | `mcp_policy.check_credentialed_publish` | hard |
| No high-severity actions in the report | publish gate | hard |
| Ownership claim verified/non-expired/non-revoked | publish gate + `PublisherPackageClaim` | hard **but only fulfillable manually** (see §3) |
| Manifest runtime=mcp + mcp_server present | publish gate | hard |

**Key insight:** the hard-gate *logic* for auto-publish is largely already
written — it is the `blocks` list in `_verify_publish_gate`. What is missing is
(a) an automatable ownership fulfillment, (b) a server-authoritative functional
check, (c) a couple of easy signals (typosquat, pin-as-hard), and (d) a
structured result instead of the admin-only status gate.

## 2. Gates that are MISSING or only advisory

| Gap | Today | For auto-publish |
|---|---|---|
| **Version pinned** | verify_registry records `command_pinning` and only WARNS on unpinned | promote to a hard gate |
| **Typosquat** | `find_similar_slugs_db` exists in the package publish path (`packages/service.py:219`) but is **NOT run for MCP** | add to the MCP gate (importable, no new infra) |
| **Abuse / denylist / policy** | only the credentialed gate exists; no general abuse/denylist signal | add a denylist/abuse signal gate |
| **Client `TESTED` report** | a **hard** block in the publish gate (`report.status == "TESTED"`) | reclassify to **advisory** — it is self-attestation; the functional claim must come from server-side smoke (§3) |

## 3. The two gates that hit the stop-conditions

These are NOT buildable in a small slice — each is a separate decision-gated arc.

### 3.1 Automatable ownership — STOP: "ownership not objectively automatable" + architecture decision
- Today `PublisherPackageClaim` is only creatable via `method="manual_admin"`
  (`router.py:1029-1054` — an admin explicitly attests). The `challenge_token_hash`
  / `registry_challenge` fields are **prepared but unused** (`models.py:40,55,61`).
- So ownership IS gated, but its **only fulfillment path is an admin** — this is
  the review bottleneck the arc exists to remove.
- Automatable, unforgeable ownership needs one of: **npm provenance** (Sigstore),
  **PyPI Trusted Publishing** (OIDC), or a **verified maintainer identity**
  (GitHub OAuth / npm-maintainer / DNS challenge). Each is an external
  integration and a real build. `method` is `VARCHAR` and `evidence` is JSONB,
  so **new method values likely need no migration** — but the verification logic
  is substantial and is an architecture decision (which proof(s) to trust).

### 3.2 Server-authoritative sandbox smoke — STOP: "server smoke needs new infrastructure"
- `verify_registry` "never executes MCP code". The only "it works" signal today
  is the client `TESTED` attestation.
- A server-authoritative functional gate = start the MCP in the **0.21.0
  container sandbox** (network-restricted, resource-limited) and complete the
  JSON-RPC `initialize` + `tools/list` handshake. This reuses shipped infra but
  is a real build (a sandboxed MCP-runner + result capture). Until it exists,
  the functional claim stays advisory and human review covers it.

## 4. Recommended gate result structure

Replace the admin-only `list[str]` block check with a pure, reusable evaluator
returning a structured result (computed from `server_verification` + `report` +
`ownership_claim` + typosquat lookup):

```jsonc
{
  "auto_publish_eligible": false,          // true only when every hard gate passes
  "checked_at": "2026-07-10T…Z",
  "gates": [
    { "id": "package_exists",   "passed": true,  "blocking": true,  "evidence": {…} },
    { "id": "version_pinned",   "passed": false, "blocking": true,  "evidence": {…} },
    { "id": "ownership_proven", "passed": false, "blocking": true,  "evidence": {"method": null} },
    { "id": "sandbox_smoke",    "passed": null,  "blocking": true,  "evidence": {"ran": false} },
    { "id": "client_tested",    "passed": true,  "blocking": false, "evidence": {…} }  // advisory
  ],
  "blockers": ["version_pinned", "ownership_proven", "sandbox_smoke"],
  "review_fallback_reason": "ownership not automatically proven; functional smoke not run",
  "advisory": ["client report TESTED"]
}
```

- `auto_publish_eligible` is `all(g.passed for g in gates if g.blocking)`.
- Reuses today's blockers as the `blocking` gates; adds typosquat/pin; moves
  `client_tested` to `blocking:false` (advisory).
- `is_live()`/routing (Slice 1) consumes this: eligible → (future) published;
  else → `quarantined_review` with `review_fallback_reason`.

## 5. Migration need

- **Gate evaluator (pure function):** no migration.
- **Persisting the gate result:** avoid a migration by storing it inside the
  existing `server_verification` JSONB (or a sibling JSONB), OR add a nullable
  `gate_result` JSONB column (additive migration — gated, not now).
- **Automated ownership:** `method`/`status` are VARCHAR, `evidence` is JSONB —
  new method values likely need **no** migration; confirm at build time.
- **Sandbox smoke:** no migration (JSONB evidence); it is an infra build.

**Verdict: the recommended next slice needs NO migration.**

## 6. Tests needed (per slice)

- Gate evaluator: table-driven — each hard gate pass/fail flips
  `auto_publish_eligible`; advisory `client_tested` never flips it; unknown
  inputs → not eligible (fail-closed).
- Safety line: no input combination yields `auto_publish_eligible:true` while a
  blocking gate is unmet; nothing auto-lives (Slice 1 invariant preserved).
- Typosquat gate: a near-duplicate name → not eligible.
- Parity: the evaluator's blockers match `_verify_publish_gate`'s current blocks
  for the overlapping gates (no behavioral regression for admin publish).

## 7. Where auto-publish would hook in (but is NOT activated)

The evaluator is **read-only/advisory** in the next slice: `submit`/`re-verify`
compute it and store `review_fallback_reason`, but routing still sends everything
to `quarantined_review` and **admin approve → publish stays required**. Auto-
publish flips on only in the FINAL slice, when ownership automation + sandbox
smoke + post-publish quarantine/yank/audit + copy are all in place.

## 8. Risk

- Low for the evaluator slice (pure function, no migration, no infra, no
  activation). Main risk is scope creep into ownership/smoke — explicitly
  deferred.
- The two deferred gates carry real risk/decisions (which ownership proofs to
  trust; sandboxing arbitrary MCP servers) and each gets its own arc + go.

## 9. Recommended build order (each its own gated slice)

- **Slice 2a (recommended next, buildable now):** the pure **gate evaluator +
  GateResult** — encode the existing hard gates + typosquat + pin-as-hard,
  reclassify `client_tested` to advisory, compute `auto_publish_eligible`
  (advisory-only, stored in JSONB, routing unchanged, admin still publishes).
  No migration, no infra, no activation. Tests per §6.
- **Slice 2b:** automatable ownership (§3.1) — its own arc; founder picks which
  proof(s) to trust (npm provenance / PyPI Trusted Publishing / verified
  maintainer). Architecture decision required.
- **Slice 2c:** server-authoritative sandbox smoke (§3.2) — its own arc; reuses
  the 0.21.0 container.
- **Slice 3 (final):** flip auto-publish on when 2a+2b+2c + post-publish
  quarantine/yank/audit (from the main plan) + copy are all ready.

## 10. Recommendation

Build **Slice 2a** next: the gate evaluator is the honest, low-risk foundation —
it turns the review decision into an explicit, evidenced, testable computation
without activating anything or touching the schema. It also makes the two hard
problems (ownership, smoke) visible as concrete `blocking:true, passed:false`
gates, so the founder can decide those arcs on evidence. Auto-publish stays off
until every blocking gate can pass automatically.
