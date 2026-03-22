# Security Changelog

## 2026-03-21 — Hardening Sprint 4 (Consolidation)

### Sandbox Fallback Hardened

#### 21. No Silent Degradation in Container Mode
- **File**: `app/verification/sandbox.py` — `run_python_code_enforced()`
- **Issue**: When `VERIFICATION_SANDBOX_MODE=container` was set but no container runtime (docker/podman) was available, the system silently fell back to best-effort subprocess. Trust communication drifted from reality.
- **Fix**: If mode is `container` and no runtime is found, verification now **hard fails** with a clear error message. Silent fallback only applies in default `subprocess` mode.
- **Behavior change**: Production environments with `VERIFICATION_SANDBOX_MODE=container` will fail verification (not silently degrade) if docker/podman is unavailable.

### Install Resolution Regression Tests

#### 22. Business-Critical Priority Logic Tests
- **File**: `tests/test_install_resolution.py` (19 tests)
- **Purpose**: Cover all install resolution priority scenarios to prevent silent regressions.
- **Scenarios**: verified > partial > recent pending > stale pending/fallback, pinned version, central mapping consistency.

### Transaction Boundary Fix

#### 23. `track_download()` No Longer Commits Mid-Transaction
- **File**: `app/install/service.py`, `app/install/router.py`
- **Issue**: `track_download()` called `session.commit()` internally, committing the download count increment before the install endpoint finished assembling its response. If anything failed after, state was inconsistent.
- **Fix**: Removed `session.commit()` from `track_download()`. Both callers (`install`, `download` endpoints) now commit explicitly after all writes complete.

### Priority Logic Deduplication

#### 24. Central `TIER_PRIORITY` Mapping
- **File**: `app/packages/version_queries.py`
- **Issue**: The SQL `CASE` expression in `get_latest_installable_version()` and the Python `_derive_install_reason()` function encoded the same priority rules independently. Changing one without the other would cause silent divergence.
- **Fix**: Extracted `_tier_priority_for_version()` as single source of truth for bucket assignment. `_derive_install_reason()` now looks up the result in the central `TIER_PRIORITY` mapping dict.

### Dockerfile Cleanup

#### 25. Removed No-Op apt-get Layer
- **File**: `Dockerfile.verifier`
- **Fix**: Removed empty `apt-get update && apt-get install` line that installed no packages but created a wasted layer with the apt cache.

---

### New Tests (19 added, 250 total)

| Test File | Count | Coverage |
|-----------|-------|----------|
| `tests/test_install_resolution.py` | 19 | Install priority (6 scenarios), mapping consistency, ordering |

---

### Known Open Risks

| Risk | Status | Mitigation |
|------|--------|------------|
| Enforced sandbox not yet deployed | Container code ready, needs image build + config on Hetzner | Deploy verifier image, set `VERIFICATION_SANDBOX_MODE=container` |
| Network isolation not on all steps | Import step enforced, smoke step still best-effort | Extend container path to smoke step (needs network-needed tool detection) |
| `check-updates` N+1 queries | 2 queries per package in batch | Acceptable at current scale; batch-optimize when needed |
| Refresh token storage | Not audited | Document current model, evaluate security |
| Builder YAML/manifest parsing | No size/depth limits on YAML input | Add safe parser config |

---

## 2026-03-21 — Hardening Sprint 3 (Enforced Sandbox)

### Container-Based Isolation for Verification

#### 16. Container Runtime Detection
- **File**: `app/config.py`
- **Purpose**: At startup, detects available container runtime (podman preferred, docker fallback). Stored as `CONTAINER_RUNTIME` constant.
- **Behavior**: If no runtime available, all verification continues in best-effort subprocess mode. No silent degradation.

#### 17. Enforced Execution Path
- **File**: `app/verification/sandbox.py` — `run_python_code_enforced()`
- **Purpose**: Runs verification code inside an ephemeral container with hard isolation.
- **Security flags**: `--network=none`, `--read-only`, `--cap-drop=ALL`, `--security-opt=no-new-privileges`, `--pids-limit=128`, `--memory=512m`, `--cpus=1.0`, `--user 1000:1000`, `--tmpfs /tmp:rw,noexec,nosuid,size=64m`
- **Fallback**: If no container runtime, falls back to best-effort subprocess with structured warning.

#### 18. Import Step Enforced by Default
- **File**: `app/verification/steps.py`
- **Change**: When `VERIFICATION_SANDBOX_MODE=container`, the import step uses `run_python_code_enforced()` instead of `run_python_code()`.
- **Behavior change**: Import step runs in a container with no network access when container mode is active. No change to verification results — same code, stronger isolation.

#### 19. Dynamic Isolation Level Detection
- **File**: `app/verification/pipeline.py`
- **Change**: Isolation levels are now determined dynamically per step based on actual sandbox capabilities (not hardcoded). Stored in `environment_info.isolation`.
- **Values**: `none` (install — needs network), `best_effort` (subprocess with env hint), `enforced` (container with --network=none).

#### 20. Verifier Container Image
- **File**: `Dockerfile.verifier`
- **Purpose**: Minimal Python 3.13-slim image with non-root user. No shell entrypoint, no unnecessary packages.
- **Build**: `docker build -f Dockerfile.verifier -t agentnode-verifier:latest .`

### New Configuration

| Setting | Default | Purpose |
|---------|---------|---------|
| `VERIFICATION_SANDBOX_MODE` | `subprocess` | Set to `container` to enable enforced isolation |
| `VERIFICATION_CONTAINER_IMAGE` | `agentnode-verifier:latest` | Container image for enforced verification |

---

## 2026-03-21 — Hardening Sprint 2

### Auth Hardening

#### 9. Rate Limits on All Sensitive Auth Endpoints
- **File**: `app/auth/router.py`
- **Issue**: Several auth endpoints had no rate limiting: refresh, password reset (request + confirm), change password, 2FA setup/verify, email verification.
- **Fix**: Added rate limits to all sensitive endpoints: register (5/min), refresh (20/min), password reset request (3/min), reset confirm (5/min), change password (5/min), 2FA setup (5/min), 2FA verify (10/min), email verify request (3/min), email verify (5/min).
- **Behavior change**: Excessive requests to auth endpoints now return 429.

#### 10. Admin Role Leakage via Cookie Removed
- **Files**: `app/auth/router.py`, `web/src/components/Navbar.tsx`
- **Issue**: Login response set a non-httpOnly `is_admin` cookie readable by any JS on the page. This unnecessarily exposed admin status as a client-side signal.
- **Fix**: Removed `is_admin` cookie from login. Navbar now derives admin status from `/auth/me` API call (server-authoritative).
- **Behavior change**: `is_admin` cookie no longer set. Existing cookies cleared on logout. Frontend admin link now loads via API.

#### 11. CSRF Decision Documented
- **File**: `app/auth/router.py` (module docstring)
- **Decision**: CSRF protection NOT required under current auth model. Auth uses httpOnly cookies with `SameSite=lax`, which blocks cross-origin POST/PUT/DELETE. If auth model changes to `SameSite=none` or cookie-based GET mutations, this must be revisited.

### Input Validation Systematization

#### 12. Triple-Quote String Escape in Code Generation (Critical)
- **File**: `app/verification/steps.py` (lines 448-531, 630-661)
- **Issue**: JSON data was embedded in generated Python code using triple-quoted strings (`'''`). Only backslashes were escaped. A crafted input_schema with `'''` in enum values could break out of the string literal and inject code.
- **Fix**: Replaced all triple-quote embedding with `json.dumps()` double-serialization. JSON strings are now proper Python string literals with all special chars escaped.
- **Behavior change**: None externally. Verification produces identical results.

#### 13. Tool Name Injection in Auto-Generated Tests
- **File**: `app/verification/sandbox.py` (line 277)
- **Issue**: Tool names were sanitized with only `.replace("-", "_").replace(" ", "_")` before being used as Python function names in generated test code. A tool name with quotes or newlines could inject code.
- **Fix**: `re.sub(r"[^a-zA-Z0-9_]", "_", name)` — strip all non-identifier chars.
- **Behavior change**: None for normal tool names. Exotic names produce safe identifiers.

#### 14. Shared Validation Helpers
- **File**: `app/shared/validators.py` (new)
- **Purpose**: Central validators for URL, slug, filter, sort, tag, and identifier validation. Used by search schemas, package validator, and verification sandbox.
- **Functions**: `is_safe_url()`, `is_safe_filter_value()`, `is_valid_slug()`, `is_allowed_sort()`, `normalize_tag()`, `is_safe_identifier()`, `sanitize_to_identifier()`

### Sandbox Isolation Groundwork

#### 15. Isolation Levels Defined and Exposed
- **Files**: `app/verification/sandbox.py`, `app/verification/pipeline.py`, `web/src/app/packages/[slug]/VerificationMainPanel.tsx`
- **Issue**: Verification sandbox used env-var hints for network restriction but presented itself as if isolation were enforced. No way for users or API consumers to know the actual isolation level.
- **Fix**: Defined `IsolationLevel` enum (`none`, `best_effort`, `enforced`). Pipeline outputs per-step isolation levels in `environment_info.isolation`. Frontend shows isolation status badge: green (isolated), yellow (best effort), or gray (not isolated).
- **Behavior change**: New `isolation` field in verification environment info. UI now honestly labels current isolation as "best effort".

---

### New Tests (24 added, 231 total)

| Test File | Count | Coverage |
|-----------|-------|----------|
| `tests/test_shared_validators.py` | 24 | URL, slug, filter, sort, tag, identifier validators |

---

## 2026-03-21 — Hardening Sprint 1

### P0 Fixes (Exploitable vulnerabilities)

#### 1. Code Injection via tool_name in Verification (Critical)
- **File**: `app/verification/steps.py`
- **Issue**: Freeform `tool_name` values were interpolated directly into generated Python code via f-strings. A malicious tool name containing double quotes could break out of the string literal and execute arbitrary code in the verification sandbox.
- **Fix**: Tool names are now serialized via `json.dumps()` and assigned to a `_tn` variable in generated code. No user-supplied values are interpolated into code strings.
- **Behavior change**: None externally. Verification produces identical results for valid tool names.

#### 2. Meilisearch Filter Injection (Critical)
- **File**: `app/search/schemas.py`, `app/search/router.py`
- **Issue**: All filter fields (`package_type`, `framework`, `runtime`, `trust_level`, `verification_tier`, `publisher_slug`, `capability_id`) were unvalidated strings interpolated into Meilisearch filter DSL. An attacker could manipulate filter logic via crafted values like `evil" OR 1=1 OR "`.
- **Fix**: Pydantic `field_validator` on all filter fields: only `^[a-zA-Z0-9_-]+$` values accepted. `sort_by` restricted to explicit whitelist (`download_count`, `published_at`, `name` — each `:asc`/`:desc`).
- **Behavior change**: Requests with special characters in filter fields now return 422. All legitimate filter values (slugs, enum strings) are unaffected.

#### 3. javascript: URL XSS (Critical)
- **Files**: `app/packages/validator.py`, `web/src/app/packages/[slug]/page.tsx`
- **Issue**: URL fields (`homepage_url`, `docs_url`, `source_url`) accepted any string at publish time and were rendered as `<a href>` without protocol validation. A publisher could set `javascript:alert(document.cookie)` as a URL.
- **Fix**: Backend rejects URLs not starting with `https://` or `http://` (case-insensitive). Frontend independently filters URLs with `/^https?:\/\//i` before rendering.
- **Behavior change**: Publishing with non-HTTP URLs now fails validation. Existing packages with non-HTTP URLs will have their links hidden on the frontend.

---

### P1 Fixes (Logic bugs, defense-in-depth)

#### 4. Temporary Password in Admin Response
- **File**: `app/admin/router.py`
- **Issue**: `POST /admin/users/{id}/reset-password` returned the temporary password in the JSON response body. If admin sessions were logged or intercepted, the password was exposed.
- **Fix**: Response now only confirms the reset. Password is sent via email only.
- **Behavior change**: Admin API response no longer contains `temp_password` field.

#### 5. TAR Extraction Path Traversal Guard (No-op Fix)
- **File**: `app/verification/sandbox.py`
- **Issue**: The loop checking for `..` or absolute paths in tar members used `continue` (skip), which had no effect — all members were still extracted. Protection relied solely on `filter="data"` (Python 3.12+).
- **Fix**: Changed to `return False` — archives containing dangerous paths are now rejected entirely before extraction.
- **Behavior change**: Artifacts with path traversal entries are now rejected instead of silently extracted.

#### 6. Installation State Bypass
- **File**: `app/install/router.py`
- **Issue**: `POST /installations/{id}/activate` set status to `active` regardless of current state. An already-uninstalled or active installation could be re-activated.
- **Fix**: Added state guard — only installations in `installed` or `inactive` state can be activated. Others get 409.
- **Behavior change**: Activating an already-active or uninstalled installation now returns 409.

---

### Verification Trust Hardening

#### 7. Verification Race Condition Guard
- **File**: `app/verification/pipeline.py`
- **Issue**: Multiple verification triggers (publish, admin reverify, cron, owner request) could fire concurrently on the same version, creating parallel `VerificationResult` rows and race conditions on `latest_verification_result_id`.
- **Fix**: `SELECT ... FOR UPDATE` lock on `PackageVersion` row before starting a run. If a non-stale "running" result exists, the new run is skipped. Stale runs (older than `VERIFICATION_TIMEOUT + 60s`) are auto-marked as `error`.
- **Behavior change**: Concurrent verification requests for the same version are now serialized. Duplicate triggers are silently skipped.

#### 8. Artifact Integrity Verification
- **Files**: `app/verification/pipeline.py`, `app/install/schemas.py`, `app/install/router.py`
- **Issue**: The verification pipeline downloaded artifacts from S3 without verifying their SHA-256 hash. If storage was tampered with, verification would run against a different artifact than what was published.
- **Fix**: Pipeline now computes SHA-256 of downloaded bytes and compares to stored `artifact_hash_sha256`. Mismatch aborts verification with `error` status. Download API response now includes `artifact_hash_sha256` and `artifact_size_bytes` for client-side verification.
- **Behavior change**: Tampered artifacts are caught before verification runs. Download response has two new optional fields.

---

### New Response Fields

| Endpoint | New Fields | Type |
|----------|-----------|------|
| `POST /{slug}/download` | `artifact_hash_sha256` | `str \| null` |
| `POST /{slug}/download` | `artifact_size_bytes` | `int \| null` |

All new fields are optional with `null` defaults — existing clients unaffected.

---

### New Tests (25)

| Test File | Count | Coverage |
|-----------|-------|----------|
| `tests/test_p0_security.py` | 25 | URL XSS (7), Filter injection (11), Code injection (7) |

All tests are negative/adversarial — they verify that malicious inputs are rejected, not just that valid inputs work.
