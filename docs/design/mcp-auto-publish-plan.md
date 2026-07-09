# MCP Auto-Publish — Feature Plan (design only, not built)

> Status: **PLAN**. No backend is built from this document. Building the
> auto-publish gate, any DB migration, and the final copy switch each need a
> separate explicit go. Founder direction (2026-07-10): AgentNode is
> self-service-first — admin review is a *fallback exception*, not the standard
> path. This plan makes MCP publishing self-service when objective automatic
> gates pass, and converges MCP onto the same publish/quarantine model the other
> package types already use.

## 1. Where we are today

- Skill / toolpack / agent / upgrade **auto-publish** via `POST /v1/packages/publish`:
  the service computes quarantine signals (typosquatting, new-publisher) and,
  if none fire, the version goes live and is synced to search; otherwise it is
  quarantined (invisible in search) pending clearance
  (`backend/app/packages/service.py:220-252, 364-375, 625-628`).
- MCP is the outlier: `POST /v1/mcp/submit` runs server-side `verify_registry`
  and `derive_status` → best case `pending`; going live requires an **admin**
  (`PUT /v1/admin/mcp/submissions/{id}/review` → `approved`, then
  `POST /v1/admin/mcp/submissions/{id}/publish`, both `require_admin`;
  `_verify_publish_gate` requires `status == "approved"`,
  `backend/app/mcp/router.py:712-937`). So **every** MCP needs a human today.

**Goal:** an MCP submission that clears every *objective server-side* gate goes
live self-service; review is reserved for the cases where the gates are
inconclusive or a risk signal fires — mirroring the auto-publish + quarantine
model of the other types.

## 2. Design principles (the five refinements)

1. **Server verification is the hard gate; the client `TESTED` report is
   advisory only.** A client report is publisher self-attestation and is
   worthless as a security gate. Auto-publish may only key on what the *server*
   can objectively establish. The "does it actually run" claim is only
   server-authoritative if the server runs the MCP itself (sandbox smoke — see
   §5, uses the container sandbox shipped in 0.21.0); until then the functional
   claim is attested and backed by retroactive moderation.
2. **Ownership must be unforgeable.** Repo-URL match ≠ ownership. Auto-publish
   only where ownership is provable: npm provenance, PyPI Trusted Publishing, or
   a verified maintainer/publisher identity. Otherwise → review fallback.
3. **Converge onto the existing package model.** Reuse the publish + structured-
   quarantine machinery instead of a bespoke MCP state machine. One status
   model, one audit model, one "quarantined vs live" concept for all four types.
4. **Post-publish safety net.** Auto-publish is only acceptable with a fast
   retroactive path: quarantine-after-publish, yank/unpublish/disable, abuse
   report, admin override, periodic re-checks. Fail-safe retroactively, not only
   fail-closed pre-publish.
5. **Copy follows reality.** The UI must not claim self-service publishing until
   this ships. Phase-1 copy already describes automatic *verification* honestly
   and keeps "review still required today"; the self-service wording flips on
   only when the gate below is live.

## 3. State machine (converged)

Reuse the package quarantine vocabulary. An MCP submission resolves to exactly
one outcome on submit / re-verify:

```
submit / resubmit
   │
   ├─ server verification runs (authoritative)
   │
   ├─ ALL hard gates pass  ─────────────►  published (live, self-service)
   │                                        = the package-publish "not quarantined" path
   │
   ├─ risk/uncertain signal ───────────►  quarantined_review
   │                                        (not live, in the review queue; the
   │                                         publisher sees exactly which gate blocked)
   │
   └─ hard failure (schema, package missing,
      ownership contradiction) ─────────►  action_required
                                            (publisher fixes + resubmits; never queued
                                             for a human until it is at least coherent)
```

- `published`: converges with a normal package version going live (synced to
  search, installable).
- `quarantined_review`: converges with today's `quarantined` state + a review
  queue. Admin review is now the *exception path into here*, not the default.
- `action_required` / `needs_changes`: unchanged; publisher self-serves the fix.

Admin actions become **overrides on the exception set** (approve a
`quarantined_review`, or force-quarantine a `published`), never the required
step for the happy path.

## 4. Auto-publish gate (hard, server-authoritative)

Auto-publish iff **every** condition holds — each independently verifiable by
the server, no client trust:

1. **Schema valid** — runtime=mcp, `mcp_server` with a non-empty pinned command,
   npm_package XOR pypi_package (already enforced by `verify_registry`).
2. **Package exists** on npm/PyPI and resolves to the exact pinned version.
3. **Version pinned** — command pins the exact version (no floating tag).
4. **Ownership provable** — see §4.1. Not "repo string matches".
5. **Registry/metadata check green** — repo consistency is `match` (not
   `mismatch`); required metadata complete.
6. **No typosquat signal** against existing catalog names (reuse the package
   typosquatting detector).
7. **No abuse/policy signal** — denylist, known-bad maintainer, disallowed
   category, credential/permission red flags.
8. **No high-risk finding** and **no `action_required` / `needs_changes`** from
   server verification.
9. **Permissions declared honestly** — declared vs detected permissions do not
   under-declare (network/filesystem/code-execution).
10. **(future) Sandbox smoke green** — the server starts the MCP in the 0.21.0
    container sandbox and completes the JSON-RPC `initialize` + `tools/list`
    handshake. Until this exists, the functional claim is the client `TESTED`
    report (advisory) plus retroactive moderation; the objective gates 1–9 still
    hold and are what auto-publish keys on.

Anything short of all-pass → `quarantined_review` (soft signal) or
`action_required` (hard failure). The client `TESTED` report is recorded for the
reviewer and for UX, but is **never** one of the hard gates.

### 4.1 Ownership model (the crux)

Ranked by strength; auto-publish requires at least one "strong" proof:

- **Strong (auto-publish eligible):**
  - **npm provenance** (Sigstore attestation linking the published package to a
    source repo + CI identity).
  - **PyPI Trusted Publishing** (OIDC-attested publish from a known repo).
  - **Verified maintainer identity** — the submitting AgentNode publisher is
    linked (e.g. via a one-time GitHub OAuth / DNS / npm-maintainer proof) to
    the package's maintainer set.
- **Weak (review fallback, never auto):** repo-URL string match, homepage match,
  self-declared `source_repo` with no attestation.

If no strong proof is available, the submission is `quarantined_review` with a
clear "prove ownership to publish automatically" affordance. This is the honest
answer to "ownership not unambiguously automatable" — those cases fall to review
by design, and the review path is the fallback, not the norm, for well-attested
ecosystems.

## 5. Server verification vs attestation

| Claim | Who can prove it | Gate role |
|---|---|---|
| Package exists / version pinned | Server (registry API) | Hard |
| Ownership | Server (provenance/OIDC/maintainer proof) | Hard |
| Repo consistency / metadata | Server | Hard |
| Typosquat / abuse / policy | Server (detectors + lists) | Hard |
| Permissions honest | Server (declared vs detected) | Hard |
| "It runs / tools work" | Server **only if** it runs the MCP in the sandbox | Hard *when sandbox smoke exists*; until then advisory (client TESTED) + retroactive moderation |

The sandbox smoke (10) is the piece that turns the functional claim from
attested to server-authoritative. It reuses the container sandbox from 0.21.0
(network-restricted, resource-limited) to run `initialize` + `tools/list`. This
is the recommended follow-on that makes MCP auto-publish as strong as it can be.

## 6. Post-publish safety (makes auto-publish acceptable)

- **Quarantine-after-publish:** an admin or an automated abuse signal can pull a
  live MCP back to `quarantined_review` immediately (reuse the package
  quarantine machinery — de-sync from search, mark version).
- **Yank / unpublish / disable:** a publisher can yank their own listing; an
  admin can disable a malicious one. Installs of a yanked version warn/refuse.
- **Abuse report:** a public "report this listing" path that raises a signal and
  can auto-quarantine on threshold.
- **Periodic re-checks:** re-run ownership + package-exists + typosquat on a
  schedule; a newly-failing check quarantines and notifies.
- **Admin override + audit:** every override (approve, force-quarantine, yank,
  disable) writes an audit record.

## 7. Audit log

One append-only record per state transition and per admin/automated action:
`{submission_id, actor (system|publisher|admin id), from_status, to_status,
gate_results (which gates passed/failed), reason_code, at}`. Reuse the existing
audit sink used by package publish / guard where possible; no secrets, fixed
reason codes.

## 8. Status-model convergence with existing package flows

- Map MCP `published` → a real `Package` + `PackageVersion` (package_type
  toolpack, runtime mcp — as today's admin-publish already does) so a live MCP is
  a normal catalog entry, searchable and installable through the same paths.
- Map MCP `quarantined_review` → the package `quarantined` concept + a review
  queue, so the dashboard "under review" surface is shared.
- Result: one mental model across skill/toolpack/agent/MCP — submit → automatic
  checks → live, or quarantined on a signal.

## 9. Migration needed?

**Likely yes, small.** Assessment to confirm during build:

- New submission statuses (`published`, `quarantined_review`) — if the
  `McpSubmission.status` column is a free-form string, **no enum migration**;
  if it is a DB enum, an additive enum migration is needed (same additive class
  as the skill enum fix — cover it with the new enum-consistency guard).
- Ownership-proof fields (provenance/OIDC/maintainer-proof result) — a few
  nullable columns on the submission or a small side table.
- Post-publish quarantine reuses existing package columns (no new migration if
  MCP-published maps onto `Package`/`PackageVersion`).

Any migration is additive and gated behind a separate go. **No migration is run
from this plan.**

## 10. Tests

- Gate unit tests: each hard gate pass/fail flips the outcome
  (published / quarantined_review / action_required), table-driven.
- Ownership matrix: strong proofs → auto-publish; weak/absent → review fallback;
  contradiction → action_required.
- Client `TESTED` is advisory: a TESTED report with a failing server gate does
  NOT auto-publish; a missing report with all server gates green (and, later,
  sandbox smoke green) DOES.
- Fail-closed: any server-verification error → not-live (quarantined_review),
  never live.
- Convergence: a `published` MCP appears as a normal searchable/installable
  package; a `quarantined_review` MCP is absent from search.
- Post-publish: quarantine-after-publish de-syncs from search; yank refuses
  install; abuse threshold auto-quarantines; audit record written per transition.
- Regression: existing `mcp submit` / resubmit / report-nachreichen flow still
  works; admin override still works.

## 11. Copy switch (only when live)

When the gate ships, flip the honest Phase-1 copy to the self-service wording:
- /publish MCP card + /mcp/submit: "Clean submissions publish automatically once
  all automated gates pass; unclear or risky ones go to review."
Not before — until then the current "review still required today" copy stays.

## 12. Recommended build order (each its own gated slice)

1. Status-model convergence + `quarantined_review` (map MCP onto the package
   quarantine model; admin review becomes the exception path).
2. Hard auto-publish gates 1–3, 5–9 (everything except ownership + sandbox).
3. Ownership model 4.1 (start with npm provenance / PyPI Trusted Publishing).
4. Post-publish safety net (§6) + audit (§7).
5. Sandbox smoke (10) — server-authoritative functional gate.
6. Copy switch (§11).

Each slice is fail-closed on its own: until a gate is trustworthy, its cases
fall to `quarantined_review` (review), never to a silent live publish.
