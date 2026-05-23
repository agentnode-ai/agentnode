# Phase B0 — Backend Signing Discovery

Date: 2026-05-23

## 1. Framework

**FastAPI** + uvicorn + SQLAlchemy async (asyncpg) + Pydantic v2.

`cryptography>=42.0` is already a dependency — no new package needed
for Ed25519 signing.

## 2. Trust-Critical Route Handlers

### GET /v1/packages/{slug}

**File:** `app/packages/router.py:182`
**Router prefix:** `/v1/packages` (in `app/packages/router.py:39`)
**Returns:** `PackageDetailResponse` (Pydantic model) via `response_model`

**Two return paths:**

| Path | What happens | Signing implication |
|------|-------------|---------------------|
| Redis cache hit | `return json.loads(cached)` — returns plain dict | FastAPI re-serializes through `JSONResponse` |
| Cache miss | `return response` (Pydantic model) | FastAPI serializes through `response_model` |

Both paths go through FastAPI's `jsonable_encoder()` → `json.dumps()`
inside `JSONResponse`. The signing middleware sees the **final bytes**
from `JSONResponse` regardless of which path was taken.

No body-byte invariant risk here.

### GET /v1/packages/{slug}/install-info

**File:** `app/install/router.py:75`
**Router prefix:** `/v1/packages` (in `app/install/router.py:30`)
**Returns:** `InstallMetadataResponse` (Pydantic model) directly

Clean single path — Pydantic model → FastAPI serializes. No Redis cache.

### GET /v1/publishers/{slug}/signing-key

**File:** `app/publishers/router.py:64`
**Router prefix:** `/v1/publishers` (in `app/publishers/router.py:20`)
**Returns:** `SigningKeyResponse` (Pydantic model) directly

Clean single path.

## 3. CRITICAL: Endpoint Path Mismatch

**The SDK expects an endpoint that does not exist in the backend.**

| Component | Path | Status |
|-----------|------|--------|
| SDK `key_status.py` | `GET /v1/publishers/{slug}/keys/{key_id}` | Called by `check_key_status()` |
| SDK `registry_trust.py` | `^(/v1)?/publishers/[^/]+/keys/[^/]+$` | Matched by `is_trust_critical()` |
| Backend | `GET /v1/publishers/{slug}/signing-key` | Actual endpoint |

**Impact:**
- `agentnode lock verify --online` calls `/publishers/{slug}/keys/{key_id}`
  → backend returns 404 → SDK reports `UNKNOWN` for every key
- `is_trust_critical()` would not match the actual backend path
  `/publishers/{slug}/signing-key`

**This is a pre-existing issue from v0.9.0**, not introduced by TG-4.
The online key verification feature was designed against a planned
endpoint that was never built.

**Resolution options:**

| Option | Change | Risk |
|--------|--------|------|
| A: Add `/keys/{key_id}` to backend | New endpoint, backend-only change | None — additive |
| B: Change SDK to `/signing-key` | SDK change, drops `key_id` from path | Breaks `is_trust_critical()` pattern, loses multi-key model |
| C: Both | Backend adds `/keys/{key_id}`, SDK stays as-is | Clean forward path |

**Recommendation: Option A.** The SDK's multi-key model (`key_id` in
the path) is architecturally correct for future key rotation. The
backend currently stores one key per publisher but should serve it
under the keyed path. The existing `/signing-key` endpoint stays
for backward compatibility.

## 4. JSON Serialization Point

All trust-critical endpoints return Pydantic models (or dicts on cache
hit). FastAPI converts them through:

```
Pydantic model / dict
  → jsonable_encoder() (Python objects → JSON-safe types)
  → json.dumps() (Python dict → JSON string)
  → JSONResponse (bytes via .encode("utf-8"))
```

**The final bytes are produced by `starlette.responses.JSONResponse`.**
This is the signing point — the signing middleware must intercept
these bytes.

FastAPI does NOT add trailing newlines.

## 5. Middleware That Could Modify Response Bodies

| Middleware | Registered in | Modifies body? |
|-----------|---------------|----------------|
| `CORSMiddleware` | `main.py:155` | No — headers only |
| `RequestLoggingMiddleware` | `main.py:167` | No — injects `X-Trace-ID` header, body passes through untouched |

**No body-modifying middleware exists.** The signing middleware can
safely run after these two without body-byte invariant risk.

## 6. Compression / CDN / nginx

**No HTTP compression in the application.** No `GZipMiddleware` or
equivalent registered. Grep for `gzip`, `brotli`, `compress`,
`GZipMiddleware` across all `.py` files returns zero HTTP compression
hits.

**No nginx/reverse proxy config in the backend repo.** No
`docker-compose.yml`, `nginx.conf`, or `Caddyfile` found in the
backend directory or the monorepo root (depth 3).

**Risk:** If the production deployment uses nginx or a CDN that
applies compression or body transformation, the body-byte invariant
could break. This must be verified during Phase 1 deployment with the
external body-byte invariant test from `REGISTRY_SIGNING_SPEC.md §9`.

## 7. Secret/Env Handling

**Pattern:** `pydantic_settings.BaseSettings` with `env_file=".env"`.
All secrets are env vars with defaults for development.

**Production safety:** `_check_production_secrets()` aborts on startup
if critical secrets are still at default values in production.

**Signing key integration point:**

```python
# In app/config.py Settings class:
REGISTRY_SIGNING_KEY: str = ""        # base64-encoded PEM
REGISTRY_SIGNING_KEY_ID: str = ""     # e.g. "registry-2026"
```

Add to `_check_production_secrets()`:

```python
if self.ENVIRONMENT == "production" and not self.REGISTRY_SIGNING_KEY:
    # Phase 1: warning only (signing is new)
    # Phase 2: add to insecure list → sys.exit(1)
    print("WARNING: REGISTRY_SIGNING_KEY not set", file=sys.stderr)
```

## 8. Existing Tests

**Trust-critical endpoint tests:**
- `test_packages.py:117` — `test_get_package_not_found` (404 only)
- `test_publish_v02.py:245` — `test_install_info_v02_has_entrypoints`
- `test_publish_v02.py:268` — `test_install_info_v01_null_entrypoints`
- `test_publishers.py:127` — `test_get_signing_key` (200 + fields)
- `test_publishers.py:143` — `test_get_signing_key_not_registered` (404)

**Test pattern:** Uses `httpx.AsyncClient` as `client` fixture with
`app=app`. Tests can read `response.headers` and `response.content`
for signing verification.

## 9. Risks for bytes_signed == bytes_received

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Reverse proxy body modification | Unknown (no config in repo) | Body-byte invariant test from external network |
| Redis cache → double JSON serialization | None — both paths go through FastAPI's JSONResponse | N/A |
| Pydantic model field ordering differences | None — same model, same serializer per request | N/A |
| FastAPI trailing newline | None — verified: FastAPI does not add newlines | N/A |
| `X-Trace-ID` header injection | None — header only, body untouched | N/A |
| CDN transformation (Cloudflare, etc.) | Unknown | Verify in Phase 1 with external test |

**Only two unknowns remain:** reverse proxy and CDN. Both are
verified by the body-byte invariant test.

---

## Summary: Implementation Position

**Clean implementation path.** The backend has:
- No body-modifying middleware
- No compression
- Clean JSON serialization through FastAPI's standard pipeline
- Existing secret management pattern via pydantic-settings
- `cryptography` already installed
- Test infrastructure that supports response header/body assertions

**One blocker to resolve:** The SDK/backend endpoint mismatch on
`/publishers/{slug}/keys/{key_id}` vs `/publishers/{slug}/signing-key`.
Must be resolved before TG-4 signing is meaningful for online key
verification.

**Signing middleware position:**

```
Request
  → CORSMiddleware (headers only)
  → RequestLoggingMiddleware (X-Trace-ID header only)
  → RegistrySigningMiddleware ← NEW (signs response body, adds header)
  → Route handler → Pydantic model → JSONResponse
```

FastAPI middleware executes in **reverse registration order** for
responses (last registered = innermost = runs first on response).
`RegistrySigningMiddleware` must be registered **before**
`RequestLoggingMiddleware` in `main.py` so it runs **after** the
route handler serializes the response but **before** the logging
middleware (which doesn't modify the body anyway).

Actually: in Starlette/FastAPI, `app.add_middleware(A)` then
`app.add_middleware(B)` means B wraps A — B's response processing
runs AFTER A's. Since we want signing to run AFTER the route handler
produces the response body, the signing middleware should be
registered LAST (outermost wrapper, runs last on request, first on
response... no — Starlette ASGI middleware order is:

```
B (outer) → A (inner) → route handler
Response: route handler → A → B
```

So registering signing AFTER logging means signing sees the response
AFTER logging has added X-Trace-ID. This is correct — signing sees
the final body bytes.

**Correct registration order in main.py:**

```python
app.add_middleware(RequestLoggingMiddleware)      # existing
app.add_middleware(RegistrySigningMiddleware)      # NEW — outermost
```

Signing middleware is outermost, runs last on request, first on
response. But since neither middleware modifies the body, the order
only matters conceptually.
