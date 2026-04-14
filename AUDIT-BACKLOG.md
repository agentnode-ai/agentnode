# AgentNode Full Audit Backlog (2026-04-02)

171+ findings from 8 parallel audit agents. **171 resolved, 5 deferred.**

---

## DONE (fixed in this session)

### Round 1 — Critical Security
- [x] Ban check in `get_current_user()` (Security C2, API F22)
- [x] Blog XSS — DOMPurify sanitization (Security C1)
- [x] Scanner path traversal — extractall outside loop (Business Logic 11.1)
- [x] readyz info leak — removed `str(e)` (API F14)

### Round 2 — Critical Code Quality
- [x] `select` import missing in `auth/router.py:158` — token refresh crashes (CodeQuality #1)
- [x] Security tokens logged plaintext in `email.py:245,278` (CodeQuality #2)
- [x] HTML escaping in email templates — `_esc()` on all user inputs (CodeQuality #3)
- [x] DB connection pool config — pool_size=20, max_overflow=10, pool_pre_ping (Performance 7.1, 10.1)
- [x] Mutable JSONB defaults `default=[]` → `default=list` on 8 columns (DB C2)
- [x] Missing `email-validator` in backend pyproject.toml (Deps C2)

### Round 2 — High
- [x] UUID type on webhook path params — prevent 500s (API F3)
- [x] LIKE pattern escape in admin search `%` and `_` — 5 locations (API F1)
- [x] Webhook URL SSRF — `is_safe_url(block_private=True)` (Security H1)
- [x] `is_safe_url()` enhanced with private IP blocking (Security H2, API F18)
- [x] httpx search client closed on shutdown (CodeQuality #8)
- [x] Dead query in service.py:253-255 — removed (CodeQuality #6)
- [x] `packages_published_count` incremented on publish (DB M6, BizLogic 1.3)
- [x] Revalidate endpoint — auth secret via `X-Revalidate-Secret` header (Security H3)
- [x] Provenance URL validation — block private IPs (Security H2)

### Round 2 — Medium
- [x] Import/convert endpoint now requires auth (Security M6, API F16)
- [x] `_notified_milestones` memory leak — added size cap (CodeQuality #10)
- [x] Dead code removed: `_dev_only_default`, unused `secrets`, unused `Callable` (CodeQuality #16, #17)
- [x] `asyncio.get_event_loop()` → `get_running_loop()` in 4 locations (CodeQuality #23)
- [x] PII in email logs — removed token logging (CodeQuality #9)
- [x] Stronger password policy — uppercase + lowercase + digit required (Security M1)
- [x] Blog image upload extension whitelist (Security M5)
- [x] Admin edit user — email/username validation (API F19)
- [x] Signature verification failure now blocks publish (BizLogic 3.3, CodeQuality #5)
- [x] `downloads_24h` now uses Installation count (CodeQuality #25)
- [x] `BACKEND_URL` extracted to `@/lib/constants` — 11 files cleaned (CodeQuality #11)
- [x] `timeAgo` + `timeAgoShort` extracted to `@/lib/time` — 3 duplicates removed (CodeQuality #13)
- [x] Undeclared `pydantic` added to adapter pyproject.toml (Deps M2)
- [x] `except Exception: pass` → logging in cron cleanup (CodeQuality #19)

### Round 3 — Quick Fixes
- [x] Rate limits added to ~40 unprotected endpoints across 6 routers (API F9, Security M2)
- [x] SQL injection in admin scripts → parameterized bind queries (Security H4)
- [x] Unused `ora` package removed from CLI (Deps M1)
- [x] Content-type map deduplicated — `packages/router.py` now imports from `shared/storage.py` (CodeQuality #12)
- [x] Docker-compose credentials use env vars with defaults (Deps M4)
- [x] Content-type validation on artifact upload (BizLogic 12.3)
- [x] Duplicate publisher creation — IntegrityError catch (API F24)
- [x] Webhook fire_event already fires after commit — verified correct (API F21)

---

## QUICK FIXES (remaining — verified/deferred)

### Verified OK (no action needed)
- [x] Error response format — verified all routers use AppError consistently (API F17)
- [x] Rate limiter — verified correct, no off-by-one (BizLogic 12.2)
- [x] Purpose token secret reuse — mitigated by token_type checks (Security L3)
- [x] `is_admin` cookie not httpOnly — by design, UI hint only (Security L1)
- [x] Email enumeration via registration — known UX trade-off (Security M3)

### Closed (done enough or resolved)
- [x] `require_2fa` dead code — removed unused dependency (CodeQuality #15, BizLogic 3.2)
- [x] S3 client thread-safety — all calls wrapped in `asyncio.to_thread()`, singleton safe in async context (CodeQuality #24)
- [x] Install idempotency — Redis SET NX dedup with 1h TTL covers the gap (API F23)

### Ignore for now (cosmetic / no runtime impact)
- [ ] CLI uses `any` pervasively — TypeScript refactor, no runtime effect (CodeQuality #14)
- [ ] Inconsistent DELETE status codes — cosmetic, 1/9 uses 204, rest use 200 (API F20)

---

## MEDIUM EFFORT — DONE

### N+1 Query Fixes
- [x] `check_updates` — batch load with window function (API F4, Perf 1.1)
- [x] `resolve_upgrade` — batch load scored packages with `Package.slug.in_()` (API F5, Perf 1.2)
- [x] `/recommend` — single batch resolve + batch capability load instead of per-cap loops (API F6, Perf 1.3)
- [x] Billing reviews — `_batch_review_context()` + `_batch_publisher_context()` batch helpers (API F8)
- [x] Admin candidates listing — ROW_NUMBER() window + batch click counts (API F7)
- [x] Deprecate email loop — batch query + background task (API F15, Perf 1.4)
- [x] Weekly publisher digests — 2 aggregate queries instead of 3N+1 (Perf 1.6)

### Caching
- [x] Redis cache for `/v1/capabilities` — 5-min TTL, `package_count` added to response (Perf 6.1)
- [x] Redis cache for package detail — 2-min TTL, invalidated on publish/yank/update (Perf 6.2)
- [x] Meilisearch search result caching — 60s TTL, SHA-256 key from params (Perf 6.4)

### FK Constraints (migration 021)
- [x] `PackageReport.package_id` → `ON DELETE CASCADE` (DB C3)
- [x] `PackageReport.reporter_user_id` → `ON DELETE SET NULL` (DB C3)
- [x] `AdminAuditLog.admin_user_id` → `ON DELETE SET NULL` (DB C4)
- [x] `Capability.capability_id` → `ON DELETE CASCADE` (DB C5)

### Missing Indices (migration 020)
- [x] `Installation.status` + `package_version_id` + `installed_at` (DB H1, H2)
- [x] `PackageReport.status` + `reporter_user_id` (DB H3)
- [x] `Review.package_id` (DB H4)
- [x] `SecurityFinding.package_version_id` (DB H5)
- [x] `Dependency.package_version_id` (DB H6)
- [x] `PackageVersion.verification_status` (Perf 2.2)
- [x] Composite index `(package_id, quarantine_status, is_yanked, channel, published_at)` (Perf 2.3)
- [x] `PackageVersion.quarantine_status` — already existed as `idx_versions_quarantine` (Perf 2.1)

### Pagination
- [x] `GET /packages/{slug}/reviews` (API F10)
- [x] `GET /webhooks` (API F11)
- [x] `GET /admin/quarantined` (API F11)
- [x] `GET /admin/reports` (API F11)
- [x] `GET /packages/{slug}/versions` (Perf 3.1, 3.2)

### Race Conditions
- [x] Concurrent publish — catch IntegrityError (API F12, BizLogic 3.1)
- [x] Double review submission — catch IntegrityError (API F13)

### Test Fixes
- [x] Redis mock now stores refresh JTIs (test_refresh_token)
- [x] Fixed test_clear_non_quarantined_fails (auto-quarantine behavior)
- [x] Fixed test_quarantined_not_installable (wrong endpoint URL)
- [x] Fixed test_2fa_setup_and_verify (field name mismatch)
- [x] Fixed test_delete_webhook (pagination response shape)

---

## MEDIUM EFFORT (remaining)

(All medium effort items complete!)

---

## LARGE EFFORT (half-day+, needs planning)

### Architecture
- [x] Meili httpx singleton client — init/close in app lifespan (Perf 7.2, CodeQuality #7)
- [x] Webhook httpx shared client — init/close in app lifespan (Perf 7.3)
- [x] All boto3 S3 calls wrapped in `asyncio.to_thread()` — 6 functions + all callers (Perf 7.4, 8.1-8.3, 10.2)
- [x] Background tasks for email + webhooks — 20+ endpoints moved to BackgroundTasks (Perf 8.4)
- [x] Replace `get_all_package_slugs` with pg_trgm (DB C6, Perf 3.4, 4.2)
- [x] `BaseHTTPMiddleware` → pure ASGI middleware (Perf 10.3)

### Missing Migrations
- [x] Create migration 022 for 5 missing tables + 3 enums + email_preferences column (DB C1)
- [x] Fix alembic/env.py model imports — all model modules now imported (DB H7)
- [x] Add UniqueConstraint to import_candidates(source, source_url) (DB H8)

### Business Logic
- [x] Quarantine auto-clear bypass — only clears verification/new_publisher quarantine, preserves admin/security (BizLogic 4.2)
- [x] Resolution engine stale versions — joins on Package.latest_version_id (BizLogic 2.1)
- [x] Download count deduplication — Redis SET NX with 1h TTL, user ID or IP (BizLogic 8.1, API F23)
- [x] CLI publish — include artifact tar.gz (BizLogic 10.1)
- [x] Signing key registration — PUT/GET /{slug}/signing-key endpoints + migration 024 (BizLogic 1.1)

### Frontend
- [x] Dynamic import TipTap editor + loading skeleton (Perf 5.1)
- [x] Dynamic import react-markdown + qrcode (Perf 5.2, 5.3)
- [x] Blog image optimization — Pillow resize to 1200px + WebP conversion (Perf 9.1)

### Testing (ongoing)
- [x] Frontend test framework setup — Vitest + React Testing Library, 74 tests across 5 suites (Testing P0.3)
- [x] JWT security negative tests — 17 tests covering expired/tampered/alg-confusion/wrong-type (Testing P0.1)
- [x] Ed25519 signature tests — 22 tests covering valid/invalid/tampered/malformed (Testing P0.2)
- [x] Cross-publisher authorization tests — 17 tests covering ownership isolation + admin override (Testing P0.4)
- [x] Fix rate limit tests — stateful sorted-set mock, 7 tests verifying 429 behavior (Testing P1.5)
- [x] Verification pipeline integration test — 10 tests covering pass/fail/quarantine/S3-error (Testing P1.6)
- [x] Shared test fixtures — 5 reusable helpers + TEST_MANIFEST in conftest.py (Testing P1.9)

### Dependencies
- [x] Generate Python lock file — 275 pinned deps in requirements.lock (Deps C1)
- [x] Add upper bounds on security packages — bcrypt, PyJWT, pyotp, PyNaCl (Deps H1)
- [x] Update Meilisearch Docker v1.6 → v1.12 (Deps M5)

---

## DESIGN DECISIONS — IMPLEMENTED

- [x] Capability expansion gaming — diminishing returns after 10 caps, log2 decay (BizLogic 2.2)
- [x] SMTP settings cache — loaded at app start, immediate reload on admin update (Perf 6.3)
- [x] Deploy CI/CD — GitHub Actions: test → build → SCP → migrate → restart (Security M7)

## DEFERRED (later, when needed)

- [ ] CDN for blog images — images already optimized, act when measured as bottleneck (Perf 9.2)
- [ ] `next/image` wrapper component — `<OptImage>`, blog images first (Perf 9.3)
- [ ] Separate download-count from install-count — event-based aggregation (BizLogic 8.1)

---

## 2026-04-10 — 12-Agent pre-launch audit (new items)

Full raw findings in `docs/internal/audit-2026-04/`, consolidated in `MERGED.md`.
~303 raw → 118 unique after dedup + backlog filter. Items already resolved in the
2026-04-02 round are NOT re-listed here.

### P0 — launch blockers (17)

- [x] **P0-01** Password reset & `change_password` do not invalidate refresh tokens — `backend/app/auth/router.py`, `backend/app/auth/service.py` (sources: 03, 01, 11) — Sprint A (`revoke_all_refresh_tokens` helper, called from both password flows)
- [x] **P0-02** Security scan findings bypass auto-clear for `new_publisher_review` — `backend/app/trust/scanner.py:269-276` + `verification/pipeline.py` (sources: 05, 03, 11) — Sprint A (quarantine gate blocks auto-clear when findings present)
- [x] **P0-03** Scanner refuses to re-quarantine an already auto-cleared version — `backend/app/trust/scanner.py` (source: 05) — Sprint A (scanner can now re-quarantine cleared versions)
- [x] **P0-04** SDK `AsyncAgentNode` strips `/v1` from every request — `sdk/agentnode_sdk/async_client.py:13-18` (source: 08) — Sprint B (URL base fixed)
- [x] **P0-05** SDK `AgentNodeClient.install` never POSTs `/packages/{slug}/install` — `sdk/agentnode_sdk/client.py` (source: 08) — Sprint B (install() now POSTs)
- [x] **P0-06** `run_tool(mode=auto)` runs trusted packages in-process while docs claim isolation — `sdk/agentnode_sdk/runner.py`, `python_runner.py:168-185`; docs in `README.md`, `sdk/README.md`, `docs/sdk-reference.md`, `docs/getting-started.md`, `web/src/app/docs/page.tsx` (sources: 08, 12) — Sprint B (`_DIRECT_TRUST_LEVELS = set()`, auto always subprocess)
- [x] **P0-07** CLI `publish` broken on Windows — single-quoted `--exclude` flags — `cli/src/commands/publish.ts:92` (source: 09) — Sprint C (`--exclude-from=file` instead of shell-quoted args)
- [x] **P0-08** CLI `install` silently skips hash verification when `meta.artifact_hash` empty — `cli/src/commands/install.ts:62`, `installer.ts:86` (source: 09) — Sprint C (fail-closed with `--allow-unhashed` opt-in)
- [x] **P0-09** `deploy.sh` does not run alembic migrations and does not sync `alembic/` — `deploy.sh:147-177` (source: 10, 2× merged) — Sprint F (deploy_api now uploads alembic/, alembic.ini, pyproject.toml, requirements.lock; installs pinned deps; runs alembic upgrade head)
- [x] **P0-10** Auth forms (login/register/forgot/reset) lack `autoComplete` & `name` attrs — breaks password managers (source: 06) — Sprint D
- [x] **P0-11** No visible `:focus-visible` outline site-wide — `web/src/app/globals.css:1-157` (source: 06) — Sprint D
- [x] **P0-12** Navbar overflows at `md` breakpoint — `web/src/components/Navbar.tsx:8-20` (source: 06) — Sprint D (raised to lg)
- [x] **P0-13** Mobile menu button missing `aria-expanded`/`aria-controls` — `web/src/components/Navbar.tsx:151-159` (source: 06) — Sprint D
- [x] **P0-14** `docs/anp-format.md` documents v0.1 only; `docs/publishing.md` internally inconsistent v0.1/v0.2 (source: 12) — Sprint E (rewrote anp-format.md for v0.2, publishing.md now v0.2)
- [x] **P0-15** Homepage pins `pdf-reader-pack@1.2.0` but pack is `1.0.0` (source: 12) — Sprint E
- [x] **P0-16** README claims "89+ verified packages" but repo has 77 (source: 12) — Sprint E (→ "75+")
- [x] **P0-17** Critical auth flows untested (password reset, `change_password`, `is_banned`, API-key revocation, `PUBLISH_SIGNATURE_INVALID`) — (source: 11) — Sprint A (`test_sprint_a_auth.py` covers password reset, change_password, banned-user, signature flows)

### P1 — real bugs (58)

Security (01): refresh-token TOCTOU; `is_safe_url` DNS-rebinding bypass (new evidence on Security H1/H2); rate limiter fails open when Redis down; 2FA enrol without password reauth; email-change without reauth.

Backend logic (03): signature `except Exception` swallow; `signature_verified` not persisted; reverify lock released early; `AUTO_CLEARABLE_REASONS` enum mismatch; auto-clear `MissingGreenlet`; `fire_event` holds session across HTTP; orphan Stripe checkout sessions; `mark_published_callback` 500 on yank race; `cleanup_stale_verification_dirs` deletes in-use dirs.

DB/perf (04): Meili drift on deprecate/yank/undeprecate; `PackageReport.package_id` no index; uninstall doesn't decrement count; resolution full-scans taxonomy; sitemap unbounded; `check_download_milestones` loads all; `reconcile_install_counts` race; admin `/stats` 13 sequential queries; `support_tickets.category` no index.

Verification (05): auto-clear doesn't check `triggered_by`; no `admin_reverify` audit trail; auto-clear counts open trust gate; retroactive tier changes on re-verify (downgraded from P0); AI scan skipped for trusted publishers.

Frontend UX (06): `CapabilityDropdown` keyboard inaccessible; `CollapsiblePanel` no aria; `forgot-password` silent to SR; dropzone not keyboard accessible; `verify-email` StrictMode double-fire; registration auto-login silent fail; `/import` uses `window.alert`; no skip-to-main; no loading skeletons on discover/search.

Frontend data/SEO (07): root layout no `metadataBase`; package detail no canonical with `?v=`; 8 files `force-dynamic` disabling ISR; `/docs` is 2798-line `"use client"` with zero SSR.

SDK (08): `_user_config` dead code; `detect_and_install` silent pin override; `_handle` crashes on non-dict JSON; `_request` crashes on non-JSON; `run_tool` kwargs collisions; installer no size ceiling; `_run_direct` thread-unsafe env; `_SUBPROCESS_WRAPPER` `str.format` fragility; `trust_level` inconsistency install vs can_install; `run_tool` mode=auto no logging/opt-out.

CLI (09): cryptic SyntaxError on non-JSON; plaintext API key echo; unquoted Windows Python path; `import` clobbers without prompt; `install --version` shadow (additive fix only); Windows config permission spam; `--limit` not validated; `api-keys list` misaligned; `validate` no timeout; `resolve-upgrade` missing `--no-network`; `doctor` serialized API calls; `publish` no signing-key reconfirm.

Config/deploy (10): broken backend Dockerfile; `deploy.sh` ignores deps; CI ignores `requirements.lock`; lifespan swallows config-load errors; committed `.env` contains `change-me-in-production`; cron Redis leak; cron holds DB sessions across SMTP; `stop_cron_tasks` not awaited; docker-compose 0.0.0.0 bindings; Meili "non-critical" in readyz; no logging config; `rsync --exclude` ordering fragility.

Sprint F progress (2026-04-10):
- [x] **P1-CD1** broken `backend/Dockerfile` deleted — not referenced by compose/CI, was shadowed by `Dockerfile.verifier` which stays.
- [x] **P1-CD2** `deploy.sh` deps — subsumed by P0-09 fix.
- [x] **P1-CD3** CI installs from `requirements.lock` — `backend.yml` + `deploy.yml` now pin via `pip install -r requirements.lock` then `pip install -e . --no-deps` and only add dev-only test deps (pytest/httpx) on top. Deploy-job tarball now ships `requirements.lock` and the remote install uses it.
- [x] **P1-CD4** lifespan narrow `except` + `logger.exception` — `backend/app/main.py` only swallows `SQLAlchemyError` for the `api_keys`/SMTP DB loads; everything else surfaces with `logger.exception`.
- [ ] **P1-CD5** deferred — `backend/.env` is NOT tracked in git but exists on disk with `change-me-in-production`; deleting it breaks local dev. Needs user confirmation before removal.
- [x] **P1-CD6** cron milestone Redis connection leak — `check_download_milestones` now wraps the Redis client in try/finally so `aclose()` always runs.
- [x] **P1-CD7** cron digest holds DB session across SMTP — `send_weekly_publisher_digests` collects a plain-data payload inside the session, exits the context, then sends. Daily digest already exited before SMTP.
- [x] **P1-CD8** `stop_cron_tasks` not awaited — now `async def`, uses `asyncio.gather(..., return_exceptions=True)`, awaited from `main.py` lifespan.
- [x] **P1-CD9** docker-compose `0.0.0.0` bindings — postgres/redis/meili/minio now bound to `127.0.0.1`.
- [x] **P1-CD10** Meili "non-critical" in `/readyz` — production (`settings.ENVIRONMENT == "production"`) now treats Meili down as unhealthy; dev keeps the legacy "non-critical" label.
- [x] **P1-CD11** logging dictConfig — `backend/app/main.py` installs a `logging.config.dictConfig` at module-load time with `agentnode`/`uvicorn`/`uvicorn.access` loggers routed through a consistent formatter.
- [x] **P1-CD12** `RELEASING.md` rsync fragility — doc rewritten to recommend `./deploy.sh` or `.github/workflows/deploy.yml` (both tar-based) and explicitly warns against `rsync --delete` for prod.

API contract (02): `/invites` admin surface no rate-limits; `installation/activate,uninstall` no rate-limit; report endpoint 400 vs 422 inconsistent.

Sprint G progress (2026-04-10):
- [x] **P1-D1** Meili drift on deprecate/yank/undeprecate — new shared helper `app/shared/meili.py::sync_package_to_meili(session, package_id)` resolves current DB state and either upserts or deletes in Meili. Called from publisher-facing `deprecate_package` + `yank_version` (`app/packages/router.py`) and admin-facing `deprecate/undeprecate/yank/unyank` (`app/admin/router.py`) after `session.commit()`.
- [x] **P1-D2** `PackageReport.package_id` no index — `PackageReport.package_id` now `index=True` and Alembic `028_add_perf_indexes.py` creates `ix_package_reports_package_id`.
- [x] **P1-D3** Uninstall doesn't decrement count — `app/install/router.py::uninstall_installation` now updates `Package.install_count = greatest(install_count - 1, 0)` when transitioning from an active/installed row (guarded against double-decrement).
- [x] **P1-D4** Resolution engine full-scans taxonomy — `app/resolution/engine.py` adds a 5-minute process-local taxonomy id cache (`_TAXONOMY_CACHE_TTL`); `invalidate_taxonomy_cache()` exposed for admin taxonomy-edit flows.
- [x] **P1-D5** Sitemap unbounded — `app/sitemap/router.py` paginates posts/packages/publishers at 5000/page and wraps in a 10-minute Redis JSON cache (`_cached()`), falling back to direct build if Redis unavailable.
- [x] **P1-D6** `check_download_milestones` loads all packages — rewritten to iterate highest-to-lowest milestone and query only packages past that threshold per pass; skip when the result set is empty.
- [x] **P1-D7** `reconcile_install_counts` race — now wraps the work in a `pg_try_advisory_lock(0x52494331)` / `pg_advisory_unlock` pair so concurrent workers skip rather than double-write.
- [x] **P1-D8** Admin `/stats` sequential queries — `get_platform_stats` now uses conditional aggregates (`COUNT(*) FILTER (WHERE ...)`) collapsing 13 round-trips to ~6 while preserving single-transaction consistency (avoided `asyncio.gather` because `AsyncSession` is not concurrency-safe).
- [x] **P1-D9** `support_tickets.category` no index — column now `index=True`; Alembic `028_add_perf_indexes.py` creates `ix_support_tickets_category`.
- [x] **P1-API1** `/invites` admin surface no rate-limits — `app/invites/router.py` adds `rate_limit(...)` to all admin mutating endpoints: candidates create/update (30/min), generate invite (20/min), email-sent (30/min), delete invite (20/min), bulk-send (5/min), followup (20/min), auto-followup (5/min).
- [x] **P1-API2** installation `/activate`, `/uninstall` no rate-limit — `app/install/router.py` now applies `rate_limit_authenticated(30, 60)` to both.
- [x] **P1-API3** report endpoint — already rate-limited via `rate_limit(10, 3600)` on `POST /v1/packages/{slug}/report` + application-level per-user/per-package caps (max 3 active, max 10/hour). No change needed; verified in place.

Sprint H progress (2026-04-10):
- [x] **P1-S2** `is_safe_url` DNS rebinding — `backend/app/shared/validators.py` now resolves hostnames via `socket.getaddrinfo` and checks every returned IP against `ipaddress.IPv*.is_private/loopback/link_local/multicast/reserved`. New helper `resolve_public_ip()` returns the first public IP or `None`. Applied a delivery-time re-resolve in `app/webhooks/service.py::fire_event` — webhook deliveries whose hostname resolves to a non-public IP at send-time are refused and logged as `status_code="blocked"`. Reduces the TOCTOU window between registration and delivery from unbounded to sub-second.
- [x] **P1-S3** Rate limiter fail-open in production — `backend/app/shared/rate_limit.py` catches Redis exceptions and, when `ENVIRONMENT=production`, raises `AppError("RATE_LIMIT_UNAVAILABLE", ..., 503)` with `retry_after=5`. Non-production environments keep the pre-existing fail-open behavior so dev without Redis still works.
- [x] **P1-S4** 2FA enrol requires reauth — `backend/app/auth/schemas.py` adds `Setup2FARequest{current_password}`; `backend/app/auth/service.py::setup_2fa` verifies the current password before issuing a new TOTP secret. `backend/app/auth/router.py` route updated to accept and forward the new body.
- [x] **P1-S5** Email change requires reauth — `UpdateProfileRequest` grows an optional `current_password` field that becomes mandatory when the payload actually changes `email`. `update_profile` raises `AUTH_REAUTH_REQUIRED` (403) if missing/wrong. Username-only updates still work without a password.
- [x] **P1-L1** Signature verify catches all exceptions — `backend/app/trust/signatures.py::verify_signature` now catches only `ValueError`, `TypeError`, `binascii.Error` for base64 decoding and `BadSignatureError`/`ValueError` for the PyNaCl verify call. Any other exception now propagates so real bugs aren't silently masked as "signature invalid".
- [x] **P1-L2** `signature_verified` never persisted — `PackageVersion` gains a non-null `signature_verified` Boolean column (Alembic `029_add_signature_verified.py`); `backend/app/packages/service.py` now passes the computed value from `verify_signature` into the new row instead of discarding it.
- [x] **P1-L3** Reverify lock span — verified the `FOR UPDATE` on `PackageVersion` is released by the same transaction commit that writes the `VerificationResult(status='running')` row. Serialization downstream is enforced by the running-VR check with `stale_cutoff`, so holding FOR UPDATE across the slow verify step would only pin a write row for minutes without adding protection. Added an in-code clarifying comment; no code change.
- [x] **P1-L6** `fire_event` holds session over HTTP — `backend/app/webhooks/service.py::fire_event` restructured into three phases: (1) load webhook targets and exit the session, (2) deliver HTTP posts without any DB connection held, (3) reopen a short session to persist `WebhookDelivery` rows. Same helper also carries the P1-S2 delivery-time DNS check.
- [x] **P1-L7** Orphan Stripe checkout — `backend/app/billing/service.py::create_review_request` now commits the `ReviewRequest` row BEFORE calling Stripe. If Stripe fails, a follow-up transaction deletes the local row; if that also fails, the row remains as a harmless `pending_payment` entry surfaced in the UI instead of an invisible Stripe-side orphan.
- [x] **P1-L8** `mark_published_callback` yank race — `backend/app/invites/router.py` now resolves the candidate's published package via `SELECT id ... WHERE publisher_id=? AND is_deprecated=false ORDER BY created_at DESC LIMIT 1` rather than a plain "most recent version" lookup; yank/deprecate of the just-published package no longer ghost-publishes a stale invite.
- [x] **P1-L9** `cleanup_stale_verification_dirs` pid-aware — `backend/app/verification/sandbox.py::VerificationSandbox.__init__` writes `<work_dir>/.pid` on construction. `backend/app/tasks/cron.py::cleanup_stale_verification_dirs` now reads the pidfile and uses `os.kill(pid, 0)` (ProcessLookupError/PermissionError/OSError handled) to skip directories whose owning process is still alive, even if the mtime crosses the 30-minute cutoff.
- [x] **P1-V2** `admin_reverify` audit trail — `run_verification` accepts an `admin_user_id: UUID | None = None` kwarg. `backend/app/admin/router.py::reverify_version` and the batch reverify endpoint now pass `user.id`. When the verification completes and `triggered_by='admin_reverify'` with a user id, the pipeline writes an `AdminAuditLog(action='reverify_version_completed')` row with outcome/score/tier/per-step status, pairing with the `reverify_version` trigger row already written by the admin endpoint.
- [x] **P1-V3** `packages_cleared_count` manual-only — removed the `+=1` increment from `backend/app/verification/pipeline.py` auto-clear path. Counter now only advances when an admin clicks "clear quarantine" in `backend/app/admin/router.py:130`, so publishers can't farm the reputation metric via trivial re-verifies.
- [x] **P1-V4** Retroactive tier downgrade-only — `backend/app/verification/pipeline.py` now uses `TIER_ORDER` (from scoring) to compare old vs. new tier on every run. Only `triggered_by='publish'` (or first-time verification) may write an arbitrary tier; `admin_reverify`/`runner_upgrade`/`scheduled`/`owner_request` can only move the tier DOWN. Logs the blocked case so operators see when a re-verify wanted to promote.
- [x] **P1-V5** AI scan always runs — `backend/app/trust/scanner.py` removed the `trust_level in ("unverified","verified")` gate. AI semantic scan now runs for all trust levels, but findings from `trusted`/`curated` publishers are downgraded to `severity=medium` and their descriptions are prefixed with `[advisory]`, so the auto-quarantine gate below doesn't flip a curated package on a probabilistic AI signal. History and audit trail still capture the underlying finding.

Sprint I progress (2026-04-10):
- [x] **P1-SEO3** Removed `force-dynamic` from blog/tutorial/case-study/changelog pages — all 9 files now use `export const revalidate = 3600;` for ISR instead of per-request rendering. Files updated: `web/src/app/blog/page.tsx`, `blog/[slug]/page.tsx`, `blog/category/[slug]/page.tsx`, `case-studies/page.tsx`, `case-studies/[slug]/page.tsx`, `changelog/page.tsx`, `changelog/[slug]/page.tsx`, `tutorials/page.tsx`, `tutorials/[slug]/page.tsx`.
- [x] **P1-SEO4** `/docs` SSR split — `web/src/app/docs/page.tsx` is now a server component. Only the interactive scroll-spy sidebar was extracted into a tiny client component `web/src/app/docs/DocsSidebar.tsx`. The long-form documentation content is now sent as fully-rendered HTML so robots see real content on first response.
- [x] **P1-SDK1** Removed dead `_user_config` load in `sdk/agentnode_sdk/client.py`.
- [x] **P1-SDK2** `AgentNodeClient.install()` now fails loud on pin mismatch — if `install(slug, version=X)` is called and the server returns `version=Y` where `X != Y`, the install is refused with an explanatory `InstallResult(installed=False, message=...)` instead of silently installing a different version.
- [x] **P1-SDK7** Thread-safe env mutation in `_run_direct` — `sdk/agentnode_sdk/runtimes/python_runner.py` now holds a `threading.Lock` across the `os.environ["AGENTNODE_LOCKFILE"]` save/restore dance and the `load_tool()` lookup. The user code itself runs OUTSIDE the lock so concurrent direct-mode `run_tool` calls aren't serialized, but the env-var race is closed.
- [x] **P1-SDK8** Static subprocess wrapper — `_SUBPROCESS_WRAPPER` no longer uses `.format()` at all. `slug`, `tool_name`, and `kwargs` travel on stdin as JSON. Eliminates the format-injection surface where a crafted slug containing escape sequences, braces, or non-ASCII characters could either break `.format()` or inject Python via `repr()`.
- [x] **P1-SDK9** `trust_level` consistency — normalized all SDK defaults from `"unknown"` to `"unverified"` across `sdk/agentnode_sdk/client.py` (CanInstallResult defaults, ResolvedPackage) and `sdk/agentnode_sdk/runtime.py` (2 call sites). Matches the backend vocabulary so downstream consumers see a single canonical value.
- [x] **P1-C4** `agentnode import` clobber guard — new `--force` flag; by default the command refuses to overwrite any existing files in the target output directory and prints the exact files that would be clobbered.
- [x] **P1-C5** `agentnode install --pkg-version` alias — additive alias for `-v/--version` so scripts can disambiguate from the global `agentnode --version`. Both forms work.
- [x] **P1-C8** `agentnode search` column-aware output — summary lines are now truncated to `process.stdout.columns - 4` (fallback 80) so narrow terminals no longer produce unreadable wrapping blocks.
- [x] **P1-C10** `agentnode validate --no-network` — new flag performs only local manifest parse + minimal shape check (package_id / version / package_type). Lets users validate in air-gapped environments without the server round-trip.
- [x] **P1-C11** `agentnode publish` pre-publish preview — the command now prints `package:`, `publisher:`, and a redacted `auth:` token (first-4…last-4) before any network call so users can Ctrl-C out if they're publishing under the wrong identity.
- [x] **P1-T** Consolidated negative-test suite — new `backend/tests/test_sprint_i_negative.py` adds webhook HMAC signing/tamper detection tests, manifest validation negative tests (missing fields, non-dict manifest), and a rate-limit smoke check. Webhook HMAC and tamper tests pass; validate-endpoint tests run against the existing fixture.

### Launch Gate 1 — lock preflight (2026-04-10)

- [x] **Gate 1 remediation** — minimal lock repair applied before launch.
    - Fixed bcrypt policy drift: `bcrypt==5.0.0` → `bcrypt==4.3.0` (latest 4.x; satisfies the `bcrypt>=4.0,<5` upper bound in `backend/pyproject.toml`). Code paths in `backend/app/auth/security.py` use only `bcrypt.hashpw` / `gensalt` / `checkpw` which are API-compatible across 4.x and 5.x, so the runtime effect is zero.
    - Removed four invalid local `file://` dev artifacts from `requirements.lock` that pointed at long-gone Windows temp directories (`pdf-reader-pack`, `web-search-pack`, `webpage-extractor-pack`, `word-counter-pack`). These were SDK-side tool packs that got pulled into a `pip freeze`-generated lock back in commit `b51aa7f` and would have broken `pip install -r requirements.lock` on any fresh machine — the Sprint F deploy path had never been live-tested against the lock, so this blocker went unnoticed.
    - Diff: `1 insertion, 5 deletions`. All 20 direct pyproject constraints verified via structural parse + substring match. `pip install --dry-run -r requirements.lock` completes without errors on a clean Python 3.13 venv.
    - **Full lock decontamination deferred post-launch.** The lock still contains ~200 transitive packages that are not real backend dependencies (chromadb, crewai, browser-use, celery, google-api-*, beautifulsoup4 and friends — leftovers from the same `pip freeze` contamination). They do not break install and do not affect runtime, only disk footprint and install time. A proper `pip-compile`-based regen would bring Redis 6→7, Stripe 14→15, FastAPI 0.128→0.135, Anthropic 0.76→0.93 and other bumps that are too risky to land on HN day; this goes into the post-launch Sprint J decontamination item below.

Post-launch follow-up (Sprint J addition):
- [ ] **Lock contamination cleanup** — regenerate `backend/requirements.lock` cleanly from `backend/pyproject.toml` via `pip-compile --extra=security --extra=dev`. Own PR, own smoke-test window, own rollback plan. Review the resulting bumps explicitly: Redis 6→7, Stripe 14→15, FastAPI bump, Anthropic 0.76→0.93, pydantic-settings / uvicorn / python-multipart / pyjwt / sqlalchemy / boto3 / pillow / alembic. Expected clean-lock size is ~68 pinned packages (vs. current 271).

### P2 — polish (43)

See `docs/internal/audit-2026-04/MERGED.md` for the full themed list. High-value clusters:

- Security polish: ~~lockout keyed only by email~~; `COOKIE_SECURE=False` default; ~~banned users receive tokens on first refresh after ban~~; ~~API-key timing sidechannel~~.
- API polish: untyped `dict` bodies; manual `UUID(str)`; 14 missing `response_model`s; webhook `DELETE` shape drift.
- SEO polish: sitemap no `lastmod`; silent empty-sitemap 200; `robots.txt` missing `/i/` `/invite/`; OG images missing on 12 routes; `next/image` not used anywhere.
- SDK polish: missing type hints on 11 public functions; shallow exception hierarchy; stale compatibility matrix.
- CLI polish: help text drift (`install --pin` ghost flag); Windows PowerShell 5.1 color leak; `rollback` double prompt; ~~`doctor` serialized API calls~~.
- Deploy polish: no cron jitter (all tasks start together); ~~CORS missing `PATCH`~~; no Redis client limits/timeouts; `[dev]` extras in production install; unpinned CI `ruff`.
- Docs polish: CLI README command order drift; error-codes drift (docs 12, code 17); `docker-compose` vs `docker compose`; hardcoded `0.3.0` in `cli/src/index.ts:28`.

Sprint K progress (2026-04-14):
- [x] **P2-S1** Lockout keyed per email+IP — `backend/app/auth/security.py` lockout/record/clear functions now key on `{email}:{client_ip}` so an attacker cannot lock out a victim by spraying bad passwords from a different IP. `login_user` and `login` router pass `client_ip` through the full chain.
- [x] **P2-S2** Banned user refresh check — `backend/app/auth/router.py` `/refresh` now queries `User.is_admin, User.is_banned` before issuing new tokens. Banned users get cookies cleared + 403.
- [x] **P2-S3** API-key constant-time comparison — `backend/app/auth/dependencies.py` `_authenticate_api_key` uses `hmac.compare_digest()` instead of `==` for hash comparison, closing the timing sidechannel.
- [x] **P2-D1** CORS missing PATCH — `backend/app/main.py` `allow_methods` now includes `PATCH`.
- [x] **P2-C1** Doctor serialized API calls — `cli/src/commands/doctor.ts` uses `Promise.allSettled` for parallel package fetches instead of sequential loop.

### Adapter follow-up (P2)

- [ ] Refresh `adapter-langchain` (pinned to `langchain==0.1.20`, 6 months stale), `adapter-crewai`, `adapter-mcp`; re-run smoke tests; publish patch releases. Not a launch blocker. (source: 09 out-of-scope)
