# Backend Signing Implementation Plan

Date: 2026-05-23
Status: Review pending
Prerequisite: SIGNING_DISCOVERY.md (Phase B0)

## Blocker: Endpoint Mismatch

**This must be resolved before or together with signing activation.**

The SDK (v0.9.0+) calls `GET /v1/publishers/{slug}/keys/{key_id}` for
online key verification (`lock verify --online`). This endpoint does
not exist in the backend. The backend has only
`GET /v1/publishers/{slug}/signing-key`.

**Impact without fix:**
- `lock verify --online` returns 404 for every key → UNKNOWN status
- `is_trust_critical()` in the SDK matches `/keys/{key_id}` but not
  `/signing-key` — registry signing on `/signing-key` would not be
  verified by the SDK
- TG-4 signing is deployed but TG-2 online verification remains broken

**Resolution:** Add `GET /v1/publishers/{slug}/keys/{key_id}` to the
backend (Sprint 1, Step 2). The existing `/signing-key` endpoint stays
unchanged for backward compatibility.

---

## Sprint 1 — Signing Infrastructure + Endpoint Fix

### Step 1: Config — Signing Key Settings

**File:** `app/config.py`

**Add to Settings class** (after line 135, `MAX_ARTIFACT_SIZE_BYTES`):

```python
REGISTRY_SIGNING_KEY: str = ""        # base64-encoded Ed25519 private key (PEM)
REGISTRY_SIGNING_KEY_ID: str = ""     # e.g. "registry-2026"
```

**Add to `_check_production_secrets()`** (after existing checks,
line ~160):

```python
if not self.REGISTRY_SIGNING_KEY:
    print("WARNING: REGISTRY_SIGNING_KEY not set — responses will not be signed",
          file=sys.stderr)
```

Phase 1 = warning only. Phase 2 (after SDK pins key) = add to
`insecure` list → `sys.exit(1)`.

**Rationale for warning-not-fatal:** During Phase 1, the backend can
run without signing — old SDKs ignore the header. Once the SDK ships
with pinned keys (v0.11.0), missing signatures break installs, and
the config must be mandatory.

### Step 2: Additive Endpoint — `/keys/{key_id}`

**File:** `app/publishers/router.py`

**New endpoint** (after line 73, after `get_signing_key`):

```
GET /v1/publishers/{slug}/keys/{key_id}
```

**Behavior:**
- Look up publisher by slug (existing `get_publisher_by_slug()`)
- If publisher has no signing key → 404 `SIGNING_KEY_NOT_FOUND`
- If publisher has a signing key but `key_id` doesn't match → 404
  `KEY_NOT_FOUND`
- If match → return key details

**Key ID matching:** The backend currently stores one key per publisher
with no `key_id` field. Derive `key_id` from the key using the same
algorithm as the SDK's `compute_key_id()` in `signing_key.py`:

```python
import hashlib, base64

raw_bytes = base64.b64decode(publisher.signing_public_key)
fingerprint = hashlib.sha256(raw_bytes).hexdigest()[:16]
key_id = f"ed25519:{fingerprint}"
```

Format: `ed25519:{sha256_hex_first_16_chars}`.

This MUST match the SDK exactly — `compute_key_id()` returns
`f"ed25519:{hashlib.sha256(public_key_bytes).hexdigest()[:16]}"`.
A mismatch means the SDK constructs a URL with a `key_id` that the
backend doesn't recognize → 404.

**Response model** — new `KeyDetailResponse`:

```python
class KeyDetailResponse(BaseModel):
    key_id: str
    public_key: str
    algorithm: str          # always "ed25519"
    registered_at: datetime
    status: str             # "active"
```

The `status` field exists for future key lifecycle (revoked, rotated).
Currently always `"active"`.

**The existing `/signing-key` endpoint stays unchanged.** No
modifications to `get_signing_key()`, `SigningKeyResponse`, or the
PUT endpoint. This is purely additive.

### Step 3: RegistrySigningMiddleware

**New file:** `app/middleware/signing.py`

**Middleware type:** Starlette `BaseHTTPMiddleware` (matches existing
`RequestLoggingMiddleware` pattern).

**Trust-critical path matching** (same patterns as SDK):

```python
_TRUST_CRITICAL_PATTERNS = (
    re.compile(r"^/v1/packages/[^/]+$"),
    re.compile(r"^/v1/packages/[^/]+/install-info$"),
    re.compile(r"^/v1/publishers/[^/]+/keys/[^/]+$"),
)
```

Note: `/v1/publishers/{slug}/signing-key` is NOT in this list. It is
a legacy endpoint. The SDK's `is_trust_critical()` matches
`/keys/{key_id}`, not `/signing-key`.

**Signing logic:**

```
1. call_next(request) → response
2. if request.method != "GET" → return response unchanged
3. if path not trust-critical → return response unchanged
4. if no signing key configured → log CRITICAL once, return response
   (degraded mode — MUST NOT return 500)
5. Read response body bytes
6. Sign body bytes with Ed25519 private key
7. Add X-AgentNode-Signature header: "ed25519:{key_id}:{base64_sig}"
8. Return new Response with same body, status, original headers + signature
```

**Degraded mode (step 4):** If `REGISTRY_SIGNING_KEY` is empty or
invalid, the middleware MUST NOT fail the request. It logs a CRITICAL
warning and serves the response without the signature header. This
ensures deployment without the key doesn't break the API.

**Body-byte invariant — the single contract:**

```
signed_bytes == httpx.Response.content as received by the SDK
```

This must hold **independent of Content-Encoding or Transfer-Encoding
transformations.** The middleware signs the raw JSON body bytes. The
SDK reads `response.content` which is the decompressed, de-chunked,
fully reconstructed body. These must be identical.

Reverse proxies can silently break this invariant through:
- **Chunked encoding:** does not affect `response.content` (httpx
  reassembles), but verify in staging
- **gzip/brotli wrapping:** `response.content` is auto-decompressed
  by httpx, so compression is transparent IF the middleware signs
  BEFORE compression is applied (sign-last architecture)
- **Newline normalization:** Some proxies append `\n` or `\r\n` to
  response bodies — even a single trailing byte breaks verification
- **Charset injection:** `Content-Type: application/json; charset=utf-8`
  header is fine, but re-encoding the body (e.g. UTF-16) breaks it

The staging body-byte invariant test (Sprint 3) must compare raw
bytes, not parsed JSON. A test that parses and re-serializes would
mask exactly the kind of transformation that breaks signing.

**Body reading:** `BaseHTTPMiddleware` provides `response.body` as
an async iterator. The middleware must consume the body, sign it,
then return a new `Response` with the same bytes. This is the standard
pattern for body-reading middleware in Starlette.

**Key loading:** Load and parse the private key once at middleware
`__init__`, not per-request. Store the `Ed25519PrivateKey` object
and `key_id` as instance attributes.

```python
class RegistrySigningMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, signing_key_b64: str = "", key_id: str = ""):
        super().__init__(app)
        self._private_key = None
        self._key_id = key_id
        if signing_key_b64:
            # Parse base64-encoded PEM private key
            pem_bytes = base64.b64decode(signing_key_b64)
            self._private_key = load_pem_private_key(pem_bytes, password=None)
```

**Registration in `main.py`** (after line 167):

```python
app.add_middleware(
    RegistrySigningMiddleware,
    signing_key_b64=settings.REGISTRY_SIGNING_KEY,
    key_id=settings.REGISTRY_SIGNING_KEY_ID,
)
```

**Registration order matters.** After `RequestLoggingMiddleware`:

```
app.add_middleware(RequestLoggingMiddleware)      # line 167, existing
app.add_middleware(RegistrySigningMiddleware)     # NEW — outermost
```

Starlette processes responses inner→outer. Signing middleware is
outermost, so it sees the response AFTER `RequestLoggingMiddleware`
has added `X-Trace-ID`. Since neither middleware modifies the body,
order only matters conceptually — but outermost-signing is correct
by convention (sign the final state).

### Step 4: Health Check

**New endpoint** (in a health router or inline in `main.py`):

```
GET /v1/health/signing → 200
{
  "signing_active": true,
  "key_id": "registry-2026",
  "algorithm": "ed25519"
}
```

This endpoint is NOT trust-critical and NOT signed. It exists for
monitoring and on-call diagnostics. Returns `signing_active: false`
when `REGISTRY_SIGNING_KEY` is empty.

---

## Sprint 2 — Tests

### Unit Tests

**File:** `tests/test_signing_middleware.py` (NEW)

| Test | Asserts |
|------|---------|
| `test_trust_critical_get_has_signature` | `X-AgentNode-Signature` header present on `GET /v1/packages/test-pack` |
| `test_non_critical_get_no_signature` | No header on `GET /v1/search` |
| `test_post_no_signature` | No header on `POST /v1/packages/test-pack/install` |
| `test_signature_format` | Header matches `ed25519:{key_id}:{base64}` |
| `test_signature_verifies` | `Ed25519PublicKey.verify(sig, body)` succeeds |
| `test_different_body_fails_verification` | Verify with wrong body raises `InvalidSignature` |
| `test_degraded_mode_no_key` | No key configured → no header, response 200 (not 500) |
| `test_degraded_mode_invalid_key` | Bad key → no header, response 200, CRITICAL logged |
| `test_install_info_has_signature` | `GET /v1/packages/test-pack/install-info` signed |
| `test_keys_endpoint_has_signature` | `GET /v1/publishers/test-pub/keys/{key_id}` signed |
| `test_signing_key_endpoint_not_signed` | `GET /v1/publishers/test-pub/signing-key` NOT signed |
| `test_redis_cache_hit_still_signed` | Cached `get_package` response still gets header |

**Test setup:** Generate an Ed25519 key pair in the test fixture.
Configure the middleware with the test private key. Verify signatures
with the test public key.

```python
@pytest.fixture
def signing_keys():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    private = Ed25519PrivateKey.generate()
    return private, private.public_key()
```

### Endpoint Tests

**File:** `tests/test_publishers.py` (EXTEND)

| Test | Asserts |
|------|---------|
| `test_get_key_by_id` | `GET /v1/publishers/{slug}/keys/{key_id}` → 200, correct fields |
| `test_get_key_by_id_wrong_id` | Wrong `key_id` → 404 |
| `test_get_key_by_id_no_key_registered` | No key set → 404 |
| `test_get_key_by_id_publisher_not_found` | Unknown slug → 404 |
| `test_signing_key_endpoint_unchanged` | Existing `/signing-key` still returns same response |

### SDK Cross-Verification Test

**File:** `tests/test_sdk_cross_verify.py` (NEW)

End-to-end test that proves the body-byte invariant:

```python
async def test_sdk_can_verify_backend_signature(client, signing_keys):
    """Prove: bytes_signed_by_backend == bytes_verified_by_sdk."""
    private_key, public_key = signing_keys

    # 1. Hit a trust-critical endpoint through the test client
    #    (middleware signs with test key)
    resp = await client.get("/v1/packages/test-pack")

    # 2. Extract signature header
    header = resp.headers["X-AgentNode-Signature"]

    # 3. Verify using SDK's verify_registry_response()
    from agentnode_sdk.registry_trust import (
        verify_registry_response, RegistrySignatureStatus,
        RegistryKey, REGISTRY_KEYS,
    )

    # Temporarily inject test key (monkeypatch REGISTRY_KEYS)
    test_key = RegistryKey(
        key_id="test-key",
        algorithm="ed25519",
        public_key=base64.b64encode(
            public_key.public_bytes_raw()
        ).decode(),
        not_after="2099-12-31",
    )

    result = verify_registry_response(
        response_body=resp.content,
        signature_header=header,
        # Would need to patch REGISTRY_KEYS for this to work
    )

    assert result.status == RegistrySignatureStatus.VALID
```

This test is the **single most important test** in the suite. It
proves the body-byte invariant holds across the backend/SDK boundary.

---

## Sprint 3 — Rollout

### Phase 1: Backend Signs (Zero User Impact)

**Deployment order:**

```
1. Deploy config changes (REGISTRY_SIGNING_KEY empty → warning logged)
2. Deploy middleware + /keys/{key_id} endpoint
3. Generate signing key on secure machine
4. Set REGISTRY_SIGNING_KEY + REGISTRY_SIGNING_KEY_ID in production env
5. Restart registry service
6. Verify: all trust-critical GETs have X-AgentNode-Signature header
```

**Old SDKs (≤ v0.10.1):** REGISTRY_KEYS empty → BOOTSTRAP mode →
signature header ignored. Zero user impact.

### Staging Validation

Before production deployment:

1. **Deploy to staging** with test signing key
2. **Body-byte invariant test** from external network:
   ```bash
   # Fetch raw bytes — do NOT parse JSON, compare bytes
   curl -s https://staging.api.agentnode.net/v1/packages/test-pack \
     --compressed -o body.bin -D headers.txt

   # Extract signature from headers, verify against raw body.bin
   python3 -c "
   import base64, sys
   from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

   body = open('body.bin', 'rb').read()
   headers = open('headers.txt').read()
   sig_line = [l for l in headers.splitlines() if 'X-AgentNode-Signature' in l][0]
   _, _, sig_b64 = sig_line.split(':', 2)  # after header name
   # Parse algorithm:key_id:base64sig from header value
   value = sig_line.split(': ', 1)[1].strip()
   alg, kid, sig_b64 = value.split(':', 2)
   sig = base64.b64decode(sig_b64)

   pub = Ed25519PublicKey.from_public_bytes(PUBLIC_KEY_BYTES)
   pub.verify(sig, body)
   print(f'PASS: {len(body)} bytes verified, key={kid}')
   "
   ```
   **Critical:** `--compressed` tells curl to decompress, matching
   what `httpx.Response.content` returns. The test verifies raw bytes,
   not parsed JSON — a JSON-level comparison would mask trailing
   newlines, whitespace changes, or charset re-encoding.

3. **Reverse proxy check:** Compare `body.bin` bytes to what the
   middleware signed. If staging uses nginx/Caddy, check for:
   - `sub_filter` / `subs_filter` directives
   - `gzip` on JSON responses (body must be signed BEFORE compression)
   - Charset normalization (re-encoding UTF-8 → UTF-16 breaks invariant)
   - Trailing newline injection (`\n` or `\r\n` appended to body)
   - Chunked transfer encoding (transparent to httpx, but verify)
4. **CDN check:** If Cloudflare or similar is in the path, verify
   the body bytes are identical after CDN processing

### Monitoring Checklist (7+ Days)

| Metric | Target | Alert threshold |
|--------|--------|----------------|
| `X-AgentNode-Signature` presence on trust-critical GETs | 100% | < 100% |
| Signature verification success (sampled) | 100% | Any failure |
| Signing latency p99 | < 1ms | > 5ms |
| `/v1/health/signing` → `signing_active: true` | Continuous | Any `false` |
| Error rate on trust-critical endpoints | Baseline | > baseline + 0.1% |
| `/keys/{key_id}` 200 rate (after key registered) | > 0 | 0 for 24h |

### Phase 2: SDK Pins Key (Enforcement Active)

**Only after 7+ days clean monitoring.**

SDK change (separate release, v0.11.0):
```python
REGISTRY_KEYS: MappingProxyType = MappingProxyType({
    "registry-2026": RegistryKey(
        key_id="registry-2026",
        algorithm="ed25519",
        public_key="<base64-from-keygen>",
        not_after="2029-05-23",
    ),
})
```

After this ships, the backend MUST sign. Missing signatures → deny.

---

## File Change Summary

| File | Change | Sprint |
|------|--------|--------|
| `app/config.py` | Add `REGISTRY_SIGNING_KEY`, `REGISTRY_SIGNING_KEY_ID`, warning in `_check_production_secrets()` | 1 |
| `app/publishers/router.py` | Add `GET /{slug}/keys/{key_id}` endpoint | 1 |
| `app/publishers/schemas.py` | Add `KeyDetailResponse` model | 1 |
| `app/middleware/signing.py` | NEW — `RegistrySigningMiddleware` | 1 |
| `app/main.py` | Register `RegistrySigningMiddleware` (line ~168) | 1 |
| `tests/test_signing_middleware.py` | NEW — 12 middleware tests | 2 |
| `tests/test_publishers.py` | Add 5 `/keys/{key_id}` tests | 2 |
| `tests/test_sdk_cross_verify.py` | NEW — body-byte invariant test | 2 |

## Files NOT Changed

| File | Why |
|------|-----|
| `app/publishers/router.py` `get_signing_key()` | Legacy endpoint stays unchanged |
| `app/publishers/schemas.py` `SigningKeyResponse` | No schema change |
| `app/publishers/models.py` | No DB migration — `key_id` is derived, not stored |
| Any SDK file | Backend-only change |

## Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Reverse proxy modifies body after signing | Unknown | Body-byte invariant test on staging |
| CDN modifies response body | Unknown | Same test from external network |
| `BaseHTTPMiddleware` body streaming breaks for large responses | Low (JSON responses are small) | Test with largest known package response |
| Redis cached response has different byte ordering | None — `json.loads()` → dict → `JSONResponse` → `json.dumps()` always re-serializes | Verified in discovery |
| Key ID derivation mismatch between SDK and backend | Medium | Cross-verify test (Sprint 2) |

## Key ID Derivation — Critical Detail

The SDK's `signing_key.py:compute_key_id()` computes `key_id` as:

```python
fingerprint = hashlib.sha256(raw_public_key_bytes).hexdigest()[:16]
key_id = f"ed25519:{fingerprint}"
```

Full format: `ed25519:{16_hex_chars}` (e.g. `ed25519:a1b2c3d4e5f67890`).

The backend's `/keys/{key_id}` endpoint must compute the same
prefixed hash from `publisher.signing_public_key` (base64-encoded
in DB). If these diverge, `lock verify --online` gets 404 even with
the new endpoint.

**Note:** The `key_id` contains a colon. The URL path is
`/publishers/{slug}/keys/ed25519:a1b2c3d4e5f67890`. Colons are
legal in URL path segments (RFC 3986 §3.3) and FastAPI/Starlette
handle them without issue.

**Verification:** Sprint 2 endpoint tests must use the SDK's
`compute_key_id()` function to derive the expected `key_id` and
confirm the endpoint returns 200 with the correct key.

## Dependencies

```
Sprint 1 (Config + Endpoint + Middleware) — no external dependencies
  ↓
Sprint 2 (Tests) — depends on Sprint 1 code
  ↓
Sprint 3 (Rollout) — depends on:
  - Secure machine access for key generation
  - Production env var injection capability
  - Staging environment availability
  - 7+ day monitoring window
```

## Decision Log

| Decision | Rationale |
|----------|-----------|
| Derive `key_id` from public key hash, don't store | No DB migration, matches SDK behavior, single key per publisher for now |
| Warning-not-fatal for missing signing key | Phase 1 must not break API when key isn't set yet |
| `/signing-key` not in trust-critical list | SDK matches `/keys/{key_id}`, not `/signing-key`. Legacy endpoint stays but isn't signed. |
| `BaseHTTPMiddleware` not pure ASGI | Matches existing `RequestLoggingMiddleware` pattern. If perf issues arise, migrate both to pure ASGI. |
| No DB migration | `key_id` is derived, `status` is always "active". Future key lifecycle may need migration. |
| Health check not signed | Monitoring endpoint, not trust-critical. Signing it would create circular dependency in verification. |
