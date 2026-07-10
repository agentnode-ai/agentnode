# MCP Auto-Publish — Slice 2b: ownership automation (scoping + plan, not built)

> Status: **SCOPING / PLAN**. No code, no migration, no deploy, no activation.
> Founder product line: **auto-publish only on STRONG ownership evidence; a
> forged repo link never suffices; everything else stays review-fallback.**
> This scopes how AgentNode can prove MCP package ownership server-side, and
> which proofs are strong enough to (later) satisfy the `ownership_automatically_proven`
> gate. Slice 1 + 2a are live; that gate is currently a permanent `passed:false`
> future-blocker, so `auto_publish_eligible` stays False for everything.

## 1. Ist-Analyse (current ownership code)

- **`PublisherPackageClaim`** (`backend/app/mcp/models.py:36-69`) — the ONLY
  ownership axis (kept strictly separate from `repo_consistency`). Fields:
  `registry` (npm|pypi), `package_name` + `package_name_normalized`,
  `method` VARCHAR(30) (`"manual_admin"` only today), `strength` VARCHAR(20)
  (`"manual"` only), `status` VARCHAR(20) (verified|rejected|revoked|expired),
  `evidence` JSONB, `challenge_token_hash` VARCHAR(128) (**prepared, unused**),
  `verified_at`, `verified_by_id`, `expires_at`.
- **Lookup** (`router.py:~37-46`): newest verified claim by
  `publisher_id + registry + package_name_normalized`.
- **Creation**: an admin-only endpoint sets `method="manual_admin",
  strength="manual", status="verified"` (`router.py:~1044-1054`). **The only
  fulfillment path today is a human admin** — the review bottleneck.
- **Publish gate** (`router.py:836-845`): requires a verified, non-expired,
  non-revoked claim.
- **Gate evaluator (2a)**: `ownership_automatically_proven` passes only if
  `method ∈ AUTOMATED_OWNERSHIP_METHODS` (empty) → always False.
- **Registry metadata already captured** by `verify_registry` (NOT used as
  ownership): `registry_repo_url`, `maintainer_list`, `shasum`/`integrity`.

**Reusable infrastructure found:**
- **GitHub OAuth already exists** (`app/credentials/oauth.py`, provider
  `github`, Redis state) — the "submitter controls the repo" step is feasible
  without net-new OAuth infra.
- **`app/trust/provenance.py`** verifies a GitHub/GitLab repo EXISTS (httpx) —
  a base for repo checks (existence only, not ownership).

## 2. What is objectively, server-side, UNFORGEABLY verifiable

### npm
| Mechanism | Proves | Forgeable? | Verdict |
|---|---|---|---|
| **npm provenance** (Sigstore attestation, `registry.npmjs.org/-/npm/v1/attestations/<pkg>@<ver>`) | this tarball was built from repo X by workflow Y | No (cryptographic, Rekor log) | proves ORIGIN, not submitter ownership → strong ONLY combined with repo-control |
| `repository` field / metadata | a URL the publisher typed | **Yes** | Medium signal at best |
| `maintainers` list | npm usernames with publish rights | reading it is fine; proving "I am maintainer X" needs npm auth | hard to automate alone |
| dist-tags / integrity / shasum | release mgmt / tarball content | n/a | not ownership |

### PyPI
| Mechanism | Proves | Forgeable? | Verdict |
|---|---|---|---|
| **PyPI Trusted Publishing + PEP 740 attestations** | artifact published via an OIDC identity from repo X / workflow Y | No (OIDC + Sigstore) | proves ORIGIN, not submitter ownership → strong ONLY with repo-control |
| project URLs / `Source` / `Home-page` | a URL the publisher typed | **Yes** | Medium signal |
| owner/maintainer role holders | PyPI usernames | proving "I am user X" needs PyPI auth | hard to automate alone |
| version artifacts | content | n/a | not ownership |

**The crux (both registries):** an attestation proves *repo → package*. It does
NOT prove *this AgentNode submitter owns that repo*. Strong ownership needs BOTH:
(a) an attestation binding the exact `package@version` to a source repo, AND
(b) the submitter proving control of that repo (GitHub OAuth → admin/maintain
permission; the OAuth infra exists).

**A second, provenance-independent strong path — the publish-challenge:** issue a
one-time token (the prepared `challenge_token_hash`); the submitter publishes a
new version whose metadata/file carries the token; the server fetches the new
version and confirms it. Publishing to the package == controlling it → ownership,
for ANY package, no provenance or OAuth needed. Point-in-time (needs recheck).

## 3. Ownership evidence matrix

| Tier | Evidence | Server-verifiable? | Auto-publish? |
|---|---|---|---|
| **A — Strong** | (i) npm provenance / PyPI PEP-740 binding `package@version`→repo **AND** submitter proves repo-control (GitHub OAuth); **or** (ii) publish-challenge token confirmed in a published version; **or** (iii) verified maintainer identity via registry auth | Yes, reproducibly | **Yes** (satisfies the gate) |
| **B — Medium** | `source_repo` matches registry `repository`/URLs, metadata plausible, maintainer names plausible — but all forgeable | Partially | **No** — used only to prioritise review / lower reviewer effort |
| **C — Weak / none** | user claim, client report, README/repo link only, or mismatch/missing | No | **No** — review-fallback |

**Recommendation (matches the founder line):** only Tier A satisfies
`ownership_automatically_proven`. Tier B/C → review-fallback. Attestation ALONE
(without repo-control) is **not** Tier A — it is Tier B, because it does not tie
the submitter to the repo.

## 4. GateResult integration (later)

`ownership_automatically_proven` becomes, per submission `package@version`:
```jsonc
{
  "id": "ownership_automatically_proven",
  "passed": true,                      // strong, verified, non-expired, matches this exact version
  "blocking": true,
  "confidence": "strong",              // strong | medium | weak
  "method": "npm_provenance+repo_control",  // or pypi_trusted_publishing+repo_control | publish_challenge | verified_maintainer
  "evidence": { "repo": "owner/repo", "workflow": "...", "attestation_id": "...",
                "version": "1.2.3", "repo_control": "github_oauth_admin" },
  "verified_at": "…", "expires_at": "…", "recheck_at": "…",
  "reason": "",
  "review_fallback_reason": null       // set when confidence < strong
}
```
- `passed` requires the claim to match the EXACT `registry+package+version`
  being published (attestations are per-artifact) — an old version's proof does
  NOT carry to a new version.
- Feeds the existing `objective_blockers` / `auto_publish_eligible` logic
  unchanged; still gated behind `sandbox_smoke` (Slice 2c) — so nothing
  auto-lives from this slice alone.

## 5. Migration need — **No.**

`method`, `strength`, `status` are VARCHAR (new values fit); `evidence` is JSONB
(any proof payload); `expires_at` exists; `challenge_token_hash` is already
present for the publish-challenge; a `recheck_at` can live in `evidence` JSONB or
reuse `expires_at`. **No schema migration is required** for the ownership arc.

## 6. Security / abuse risks and how the design handles them

| Risk | Handling |
|---|---|
| Typosquat + false ownership claim | 2a typosquat gate blocks; strong ownership is of the REAL package name, a typosquat can't earn it |
| Package takeover / maintainer change | ownership evidence **expires** + periodic **recheck**; a changed maintainer/attestation fails recheck → claim `expired`/`revoked` → not eligible |
| Repo transfer | recheck repo-control + attestation on schedule and on each new version |
| Provenance spoofing | Sigstore/Rekor (npm) and OIDC+PEP-740 (PyPI) are cryptographic; unverifiable attestation → not Tier A |
| Missing provenance | fall back to publish-challenge, else review — never auto on metadata alone |
| Package exists but repo not owned by submitter | this is exactly why attestation-alone is Tier B; the repo-control step (or challenge) closes it |
| Old version has different provenance than current | ownership is checked **per exact version**; never inherited |
| Revocation | admin `status=revoked`; abuse signal can revoke + re-quarantine (post-publish safety, main plan §6) |

## 7. Proposed build slices (not now — each its own gated go)

- **2b-1 (recommended first, buildable, no migration, no external calls):**
  ownership **evidence model + strength taxonomy + evaluator wiring** — define
  `method`/`strength`/`confidence` constants, populate `AUTOMATED_OWNERSHIP_METHODS`
  with the future method ids, and make the gate read `strength=="strong" &&
  verified && !expired && version-match`. No proof mechanism yet → no claim earns
  strong → still nothing auto-eligible. Pure framework + tests.
- **2b-2: publish-challenge verifier** — reuse `challenge_token_hash`; issue token,
  verify a published version carries it. Self-contained (no OAuth/Sigstore),
  works for npm AND PyPI. A good first *real* strong mechanism.
- **2b-3: npm provenance + repo-control** — npm attestations API + GitHub OAuth
  (exists) to prove repo-control; optional Sigstore verification vs trusting the
  npm attestation endpoint (decision).
- **2b-4: PyPI Trusted Publishing / PEP-740 + repo-control** — analogous.
- **2b-5: recheck / expiry / revocation + per-version + audit.**
- **2b-6: UI/Admin visibility** — ownership evidence + strength badges (honest).

## 8. Decision points (founder)

1. **Which proofs are "strong" enough for auto-publish?** Recommendation: Tier A
   only — (attestation + repo-control) and/or publish-challenge.
2. **Does npm provenance ALONE suffice?** Recommendation: **No** (Tier B without
   repo-control).
3. **Does PyPI Trusted Publishing ALONE suffice?** Recommendation: **No** (same).
4. **Packages without strong evidence** → permanent review-fallback? Recommendation: **Yes.**
5. **Ownership evidence validity** (TTL) and **per-version recheck**?
   Recommendation: per-version proof + a TTL for repo-control (e.g. 90 days) with
   recheck.
6. **First mechanism to build after 2b-1:** publish-challenge (universal, no
   OAuth) **or** npm-provenance+repo-control (elegant, reuses existing GitHub
   OAuth)? — your call.

## 9. Recommendation

Build **2b-1** next: the evidence-model + evaluator wiring is the honest, no-risk
foundation — it defines what "strong" means and wires the gate to read it,
without adding any external integration, without a migration, and without
letting anything auto-publish (no claim can be strong until 2b-2+ ship). It turns
the ownership question into an explicit, testable `confidence` on the claim, so
you can then decide (Decision 6) which real mechanism to build first. Auto-publish
stays off until strong ownership AND sandbox-smoke can both pass.
