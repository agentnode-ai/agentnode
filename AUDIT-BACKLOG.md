# AgentNode Full Audit Backlog (2026-04-02)

171+ findings from 8 parallel audit agents. Organized by effort, not severity.

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

### Deferred (needs design decision or larger effort)
- [ ] `require_2fa` defined but never used — would break existing publishers (CodeQuality #15, BizLogic 3.2)
- [ ] CLI uses `any` pervasively — TypeScript refactor (CodeQuality #14)
- [ ] Inconsistent DELETE status codes — cosmetic (API F20)
- [ ] Install idempotency gaps (API F23)
- [ ] S3 client not thread-safe (CodeQuality #24)

---

## MEDIUM EFFORT — DONE

### N+1 Query Fixes
- [x] `check_updates` — batch load with window function (API F4, Perf 1.1)

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

### N+1 Query Fixes
- [ ] `resolve_upgrade` — batch load scored packages (API F5, Perf 1.2)
- [ ] `/recommend` — batch resolution (API F6, Perf 1.3)
- [ ] Admin candidates listing — join instead of loop (API F7)
- [ ] Billing reviews — selectinload (API F8)
- [ ] Deprecate email loop — background task (API F15, Perf 1.4)
- [ ] Weekly publisher digests — aggregate query (Perf 1.6)

### FK Constraints
- [ ] `PackageReport` — add ondelete CASCADE/SET NULL (DB C3)
- [ ] `AdminAuditLog` — add ondelete SET NULL (DB C4)
- [ ] `Capability` → `capability_taxonomy` — explicit ondelete (DB C5)

### Caching
- [ ] Redis cache for `/v1/capabilities` (Perf 6.1)
- [ ] Redis cache for package detail (Perf 6.2)
- [ ] Meilisearch result caching (Perf 6.4)

---

## LARGE EFFORT (half-day+, needs planning)

### Architecture
- [ ] Wrap all boto3 S3 calls in `asyncio.to_thread()` or switch to aiobotocore (Perf 7.4, 8.1-8.3, 10.2)
- [ ] Meili httpx singleton client (Perf 7.2, CodeQuality #7)
- [ ] Webhook httpx shared client (Perf 7.3)
- [ ] Background tasks for email + webhooks (Perf 8.4)
- [ ] Replace `get_all_package_slugs` with pg_trgm (DB C6, Perf 3.4, 4.2)
- [ ] `BaseHTTPMiddleware` → pure ASGI middleware (Perf 10.3)

### Missing Migrations
- [ ] Create migration for 5 missing tables + 4 enums + email_preferences column (DB C1)
- [ ] Fix alembic/env.py model imports (DB H7)
- [ ] Add UniqueConstraint to import_candidates model (DB H8)

### Business Logic
- [ ] Quarantine auto-clear bypass — only clear verification-related quarantine (BizLogic 4.2)
- [ ] Resolution engine stale versions — join with latest version only (BizLogic 2.1)
- [ ] Download count auth + deduplication (BizLogic 8.1, API F23)
- [ ] CLI publish — include artifact tar.gz (BizLogic 10.1)
- [ ] Signing key registration endpoint (BizLogic 1.1)

### Frontend
- [ ] Dynamic import TipTap editor (Perf 5.1)
- [ ] Dynamic import react-markdown/qrcode (Perf 5.2, 5.3)
- [ ] Blog image optimization on upload (Perf 9.1)

### Testing (ongoing)
- [ ] Frontend test framework setup (Testing P0.3)
- [ ] JWT security negative tests (Testing P0.1)
- [ ] Ed25519 signature tests (Testing P0.2)
- [ ] Cross-publisher authorization tests (Testing P0.4)
- [ ] Fix rate limit tests — real Redis (Testing P1.5)
- [ ] Verification pipeline integration test (Testing P1.6)
- [ ] Shared test fixtures (Testing P1.9)

### Dependencies
- [ ] Generate Python lock files (Deps C1)
- [ ] Add upper bounds on security packages (Deps H1)
- [ ] Update Meilisearch Docker v1.6 → v1.12 (Deps M5)

---

## DEFERRED (needs design decision)

- [ ] Capability expansion gaming prevention (BizLogic 2.2)
- [ ] SMTP settings cache → Redis (Perf 6.3)
- [ ] CDN for blog images (Perf 9.2)
- [ ] `next/image` adoption (Perf 9.3)
- [ ] Separate download-count from install-count (BizLogic 8.1)
- [ ] Deploy script SSH key in repo (Security M7)
