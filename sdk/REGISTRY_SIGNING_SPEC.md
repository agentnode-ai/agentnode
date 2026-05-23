# Registry Signing — Backend Implementation Spec

Last updated: 2026-05-23

Companion to `REGISTRY_SIGNING_ACTIVATION.md` (rollout strategy).
This document specifies what the backend must implement.

## 1. Registry Signing Key Format

**Algorithm:** Ed25519 only. The SDK rejects all other algorithms.

**Private key format:** PEM-encoded PKCS8.

```
-----BEGIN PRIVATE KEY-----
MC4CAQAwBQYDK2VwBCIEI...
-----END PRIVATE KEY-----
```

**Loading at startup:**

```python
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

def load_signing_key(pem_bytes: bytes) -> Ed25519PrivateKey:
    key = load_pem_private_key(pem_bytes, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError(f"Expected Ed25519 key, got {type(key).__name__}")
    return key
```

**Startup validation (mandatory):**

```python
# Self-test: sign + verify a test payload at startup
test_payload = b'{"_self_test": true}'
sig = signing_key.sign(test_payload)
signing_key.public_key().verify(sig, test_payload)
# If this raises, the key is corrupt → refuse to start
```

If the key is missing or corrupt, the application **must log an error**
but **may start without signing** (see section 8 on degraded mode).

## 2. Key Storage

**Environment variable:** `REGISTRY_SIGNING_KEY`

**Format:** Base64-encoded PEM. The PEM itself contains newlines, so
base64-wrapping avoids env-var escaping issues.

```bash
# Encoding (one-time, on the secure machine where the key was generated)
base64 -w0 < registry-2026.pem > registry-2026.pem.b64

# In deployment config
REGISTRY_SIGNING_KEY=<contents of registry-2026.pem.b64>
REGISTRY_SIGNING_KEY_ID=registry-2026
```

**Loading:**

```python
import base64, os

def load_signing_key_from_env() -> tuple[Ed25519PrivateKey, str] | None:
    key_b64 = os.environ.get("REGISTRY_SIGNING_KEY")
    key_id = os.environ.get("REGISTRY_SIGNING_KEY_ID")
    if not key_b64 or not key_id:
        return None
    pem_bytes = base64.b64decode(key_b64)
    key = load_signing_key(pem_bytes)
    return key, key_id
```

**Security constraints:**

- `REGISTRY_SIGNING_KEY` must not appear in logs, error messages,
  health endpoints, or API responses.
- The variable must not be passed to child processes (tool execution,
  workers) unless they also need to sign.
- The decoded PEM must not be written to disk at runtime.
- The environment variable must be excluded from debug dumps and
  crash reports.

## 3. Signing Middleware

### Core signing function

```python
import base64
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SIGNATURE_HEADER = "X-AgentNode-Signature"

def make_signature_header(
    body: bytes,
    private_key: Ed25519PrivateKey,
    key_id: str,
) -> str:
    """Sign response body bytes. Returns the full header value."""
    sig_bytes = private_key.sign(body)
    sig_b64 = base64.b64encode(sig_bytes).decode("ascii")
    return f"ed25519:{key_id}:{sig_b64}"
```

Ed25519 signing is deterministic: same body + same key = same
signature. This is useful for testing but irrelevant for correctness.

### Middleware placement

The signing middleware **must** run:
- **After** JSON serialization (it signs the serialized bytes)
- **After** any response body modification (content negotiation, etc.)
- **Before** compression middleware (gzip, brotli)
- **Before** the response is handed to the reverse proxy

```
Route handler
  → JSON serializer (produces bytes)
  → Signing middleware ← HERE
  → Compression middleware (gzip)
  → Reverse proxy (nginx)
  → Client
```

### Framework integration patterns

**Django:**

```python
class RegistrySigningMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.signing_key, self.key_id = load_signing_key_from_env() or (None, None)

    def __call__(self, request):
        response = self.get_response(request)
        if self._should_sign(request, response):
            self._sign_response(response)
        return response

    def _should_sign(self, request, response):
        if self.signing_key is None:
            return False
        if request.method != "GET":
            return False
        if response.status_code >= 400:
            return False
        return is_trust_critical(request.path)

    def _sign_response(self, response):
        body = response.content  # materialized bytes
        header_value = make_signature_header(body, self.signing_key, self.key_id)
        response[SIGNATURE_HEADER] = header_value
```

**FastAPI / Starlette:**

```python
@app.middleware("http")
async def sign_registry_responses(request: Request, call_next):
    response = await call_next(request)
    if (
        signing_key is not None
        and request.method == "GET"
        and response.status_code < 400
        and is_trust_critical(request.url.path)
    ):
        # StreamingResponse must be consumed to get body bytes
        body = b""
        async for chunk in response.body_iterator:
            body += chunk if isinstance(chunk, bytes) else chunk.encode()
        header_value = make_signature_header(body, signing_key, key_id)
        return Response(
            content=body,
            status_code=response.status_code,
            headers={**dict(response.headers), SIGNATURE_HEADER: header_value},
            media_type=response.media_type,
        )
    return response
```

**Flask:**

```python
@app.after_request
def sign_registry_response(response):
    if (
        signing_key is not None
        and request.method == "GET"
        and response.status_code < 400
        and is_trust_critical(request.path)
    ):
        body = response.get_data()
        header_value = make_signature_header(body, signing_key, key_id)
        response.headers[SIGNATURE_HEADER] = header_value
    return response
```

### Error handling in signing path

If `private_key.sign()` raises an exception:

1. **Log the error at CRITICAL level** — this should never happen
   with a valid Ed25519 key and arbitrary input.
2. **Increment `registry.signing.errors` counter.**
3. **Serve the response without signature.** Do not return 500.
   Reason: a signing infrastructure failure should not cause a
   total outage. The SDK will report MISSING (enforcement) or
   silently pass (bootstrap) — both preferable to a 500.
4. **Alert on-call** via the monitoring counter.

```python
try:
    header_value = make_signature_header(body, signing_key, key_id)
    response.headers[SIGNATURE_HEADER] = header_value
    metrics.increment("registry.signing.operations", tags={"status": "success"})
except Exception:
    logger.critical("Registry signing failed", exc_info=True)
    metrics.increment("registry.signing.errors")
    # Response served without signature — degraded mode
```

### What NOT to sign

- Error responses (status >= 400). The SDK checks signatures before
  parsing the response. Signing a 404 is meaningless — the SDK will
  raise on the status code first.
- Non-GET responses. The SDK only verifies GET requests. POST
  endpoints are not trust-critical.
- Non-trust-critical endpoints (search, resolve, download).

## 4. Trust-Critical Endpoint List

These patterns **must match the SDK exactly.**

SDK source of truth: `agentnode_sdk/registry_trust.py`:

```python
_TRUST_CRITICAL_PATTERNS = (
    re.compile(r"^(/v1)?/packages/[^/]+$"),
    re.compile(r"^(/v1)?/packages/[^/]+/install-info$"),
    re.compile(r"^(/v1)?/publishers/[^/]+/keys/[^/]+$"),
)
```

**Concrete endpoints:**

| Endpoint | Example | Trust data |
|----------|---------|------------|
| `GET /v1/packages/{slug}` | `/v1/packages/pdf-reader` | trust_level, publisher.slug, publisher.key_status |
| `GET /v1/packages/{slug}/install-info` | `/v1/packages/pdf-reader/install-info` | public_key, _signatures, publisher_slug |
| `GET /v1/publishers/{slug}/keys/{key_id}` | `/v1/publishers/acme/keys/ed25519-k1` | key status (active/revoked) |

**NOT signed:**

| Endpoint | Reason |
|----------|--------|
| `POST /v1/search` | Metadata only, no trust decisions |
| `POST /v1/resolve` | Same |
| `POST /v1/packages/{slug}/install` | Event tracking only |
| `POST /v1/packages/{slug}/download` | URL only, artifact has its own hash |
| `GET /v1/packages/{slug}/stats` | Non-trust metadata |
| `GET /v1/packages/{slug}/download` | Same |

### Backend endpoint matching

The backend can use simpler matching than the SDK's regex — it knows
its own routes. A decorator or route-level flag is cleaner:

```python
# Option A: decorator
@app.get("/v1/packages/{slug}")
@trust_critical
async def get_package(slug: str): ...

# Option B: route list
TRUST_CRITICAL_ROUTES = {
    "get_package",
    "get_install_info",
    "get_publisher_key",
}
```

The backend does not need to replicate the SDK's regex. It only needs
to ensure that every endpoint the SDK considers trust-critical actually
gets signed. The contract test (section 9) verifies this.

## 5. Header Format

```
X-AgentNode-Signature: ed25519:registry-2026:nY4s8B7kL2...base64...==
                       ^^^^^^^ ^^^^^^^^^^^^^ ^^^^^^^^^^^^^^^^^^^^^^^^^^^
                       alg     key_id        base64(64 bytes signature)
```

**Field separator:** colon (`:`). Safe because base64 uses `A-Za-z0-9+/=`
and colon never appears in base64 output.

**Algorithm:** Always `ed25519`. Literal string, lowercase.

**Key ID:** `^[a-z0-9._-]{1,64}$`. The SDK validates this regex and
rejects anything else. Use `registry-{year}` convention.

**Signature:** Standard base64 encoding of 64 raw signature bytes.
Always 88 characters (`ceil(64 * 4/3)` + padding). Use
`base64.b64encode()`, not `base64.urlsafe_b64encode()`.

**Total header length:** ~120 bytes typical. SDK rejects > 8192 bytes.

**Character encoding:** ASCII only. The header value must not contain
non-ASCII characters.

## 6. Body-Byte Invariant

**The invariant that makes TG-4 work:**

```
bytes_signed_by_backend == httpx.Response.content_on_sdk_side
```

If this invariant breaks, all verifications fail. This section
documents every layer that can break it.

### What `httpx.Response.content` returns

httpx performs these transformations transparently:
- **Content-Encoding decompression** (gzip, br, deflate → raw bytes)
- **Transfer-Encoding reassembly** (chunked → contiguous bytes)
- **No other transformation** (no charset conversion, no JSON parsing)

Therefore: `response.content` == the bytes the backend sent before
compression. The backend must sign these same bytes.

### JSON serializer requirements

- **Encoding:** UTF-8 without BOM. Python's `json.dumps()` returns
  `str`, `.encode("utf-8")` produces the correct bytes.
- **No trailing newline.** Some frameworks append `\n` after the JSON
  body. If the framework does this, the `\n` must be included in the
  signed bytes (it will be in `response.content`).
- **Determinism across responses is NOT required.** Each response is
  signed individually. Key order, whitespace, and formatting may vary
  between responses — as long as the bytes signed match the bytes sent.

### What can break the invariant

| Threat | How it breaks | Prevention |
|--------|---------------|------------|
| Reverse proxy body rewrite | nginx `sub_filter`, charset conversion | Do not use body-modifying directives on API routes |
| CDN response transformation | Cloudflare Auto Minify, Rocket Loader | Disable transformations for API routes |
| Framework middleware after signing | Django middleware that modifies response.content | Ensure signing middleware runs last before compression |
| Double compression | gzip middleware + nginx gzip | Disable nginx gzip for already-compressed responses, or disable framework gzip and let nginx handle it |
| Trailing newline inconsistency | Framework adds `\n`, signing doesn't include it | Sign `response.content` (which includes the newline) |
| BOM in JSON | Some serializers prepend UTF-8 BOM | Use `json.dumps().encode("utf-8")`, not `codecs` with BOM |

### Verification procedure

Run this against every trust-critical endpoint after deployment:

```python
import httpx, base64
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# Use the SAME public key that will be pinned in the SDK
PUB_KEY_B64 = "<public-key-base64>"
pub_bytes = base64.b64decode(PUB_KEY_B64)
pub_key = Ed25519PublicKey.from_public_bytes(pub_bytes)

for path in [
    "/v1/packages/test-pack",
    "/v1/packages/test-pack/install-info",
    "/v1/publishers/test-pub/keys/ed25519-k1",
]:
    resp = httpx.get(f"https://staging.api.agentnode.net{path}")
    header = resp.headers.get("X-AgentNode-Signature")
    assert header is not None, f"Missing signature on {path}"

    alg, kid, sig_b64 = header.split(":", 2)
    sig = base64.b64decode(sig_b64)

    pub_key.verify(sig, resp.content)
    print(f"  {path}: valid ({len(resp.content)} bytes)")
```

**This test must pass from an external network**, not just localhost.
It proves the full chain: backend → reverse proxy → network → client.

### nginx configuration

If nginx sits in front of the application:

```nginx
location /v1/ {
    proxy_pass http://backend;

    # Do NOT modify response bodies
    # sub_filter is OFF by default — keep it that way
    # Do NOT enable charset conversion
    # proxy_set_body is for requests, not responses — safe

    # If nginx handles gzip, the backend must NOT also gzip.
    # Choose one compression layer, not two.
    gzip on;
    gzip_types application/json;
}
```

## 7. Monitoring Counters

### Metrics

| Metric | Type | Tags | Description |
|--------|------|------|-------------|
| `registry.signing.operations` | counter | `endpoint`, `status` | Signing attempts (success/error) |
| `registry.signing.latency_ms` | histogram | `endpoint` | Time to sign (should be < 1ms) |
| `registry.signing.errors` | counter | `error_type` | Signing failures |
| `registry.signing.key_loaded` | gauge | — | 1 = key loaded at startup, 0 = not |
| `registry.signing.skipped` | counter | `reason` | Requests not signed (non-GET, non-critical, error response) |

### Alerts

| Condition | Severity | Action |
|-----------|----------|--------|
| `key_loaded == 0` at startup | P0 (Phase 2), P2 (Phase 1) | Key missing or corrupt. Check env var. |
| `errors > 0` in 5 min window | P1 | Signing infrastructure broken. Investigate immediately. |
| `latency_ms p99 > 10` | P3 | Unusual — Ed25519 is ~60μs. Check CPU / key loading. |
| `operations == 0` for 10 min | P2 | No trust-critical traffic? Or middleware not attached? |

### Structured log events

```json
{"event": "signing_key_loaded", "key_id": "registry-2026", "level": "info"}
{"event": "signing_key_missing", "level": "warning"}
{"event": "signing_key_invalid", "error": "Expected Ed25519", "level": "critical"}
{"event": "signing_self_test_passed", "key_id": "registry-2026", "level": "info"}
{"event": "signing_failed", "endpoint": "/v1/packages/foo", "error": "...", "level": "critical"}
{"event": "response_signed", "endpoint": "/v1/packages/foo", "key_id": "registry-2026", "body_size": 1423, "level": "debug"}
```

`signing_failed` at CRITICAL because it should never happen with a
valid Ed25519 key — any occurrence indicates infrastructure corruption.

## 8. Rollback Procedure

### Phase 1 (backend signs, SDK in bootstrap)

**Rollback: disable signing.** No user impact.

```bash
# Option A: unset the env var and restart
unset REGISTRY_SIGNING_KEY
unset REGISTRY_SIGNING_KEY_ID
systemctl restart registry

# Option B: feature flag (if implemented)
REGISTRY_SIGNING_ENABLED=false
```

The middleware checks `signing_key is None` and skips signing.
Old SDKs never noticed the header. Rolled back cleanly.

### Phase 2 (SDK pins key, enforcement active)

**Rollback is constrained.** Users on the enforcement SDK expect
signatures. Disabling signing causes MISSING → deny for those users.

| Scenario | Rollback action |
|----------|-----------------|
| Signing middleware bug | Fix and redeploy. Do not disable signing. |
| Key compromise | Emergency key rotation (see activation plan). |
| Persistent signing failures | Investigate root cause. If unrecoverable: emergency SDK release with empty REGISTRY_KEYS (revert to bootstrap). Last resort. |

**There is no "disable signing for Phase 2" option that doesn't
break enforcement SDK users.** This is by design — it's what makes
TG-4 a real security guarantee rather than an advisory.

### Degraded mode (signing key absent)

If the application starts without `REGISTRY_SIGNING_KEY`:

1. Log `signing_key_missing` at WARNING.
2. Set `registry.signing.key_loaded` gauge to 0.
3. **Continue serving responses without signatures.**
4. All trust-critical responses go out unsigned.

In Phase 1 this is harmless. In Phase 2 this triggers MISSING errors
on enforcement SDKs — the P0 alert on `key_loaded == 0` catches it.

## 9. Tests

### Unit: signing function

```python
def test_make_signature_header():
    key = Ed25519PrivateKey.generate()
    body = b'{"slug": "test", "version": "1.0.0"}'
    header = make_signature_header(body, key, "test-key-1")

    assert header.startswith("ed25519:test-key-1:")
    parts = header.split(":", 2)
    sig = base64.b64decode(parts[2])
    assert len(sig) == 64
    key.public_key().verify(sig, body)

def test_signature_is_deterministic():
    key = Ed25519PrivateKey.generate()
    body = b'{"data": true}'
    h1 = make_signature_header(body, key, "k")
    h2 = make_signature_header(body, key, "k")
    assert h1 == h2

def test_different_body_different_signature():
    key = Ed25519PrivateKey.generate()
    h1 = make_signature_header(b'{"a": 1}', key, "k")
    h2 = make_signature_header(b'{"a": 2}', key, "k")
    assert h1 != h2
```

### Unit: middleware routing

```python
def test_signs_trust_critical_get():
    # GET /v1/packages/foo with 200 → signature header present
    ...

def test_skips_post_request():
    # POST /v1/search → no signature header
    ...

def test_skips_non_trust_critical():
    # GET /v1/packages/foo/stats → no signature header
    ...

def test_skips_error_response():
    # GET /v1/packages/nonexistent → 404 → no signature header
    ...

def test_skips_when_key_absent():
    # signing_key is None → no signature header, no error
    ...

def test_signing_failure_serves_without_header():
    # Mock signing to raise → response served, counter incremented
    ...
```

### Integration: SDK cross-verification

The most important test. Proves the backend's signing is compatible
with the SDK's verification.

```python
from agentnode_sdk.registry_trust import (
    verify_registry_response,
    parse_signature_header,
    RegistrySignatureStatus,
    RegistryKey,
)
from types import MappingProxyType
from unittest.mock import patch

def test_sdk_verifies_backend_signature():
    """Backend-signed body is accepted by SDK verification."""
    key = Ed25519PrivateKey.generate()
    pub_b64 = base64.b64encode(key.public_key().public_bytes_raw()).decode()

    # Simulate backend response
    body = b'{"slug": "test-pack", "version": "1.0.0"}'
    header = make_signature_header(body, key, "registry-2026")

    # Verify with SDK function
    registry_key = RegistryKey(
        key_id="registry-2026",
        algorithm="ed25519",
        public_key=pub_b64,
        not_after="2099-12-31",
    )
    with patch(
        "agentnode_sdk.registry_trust.REGISTRY_KEYS",
        MappingProxyType({"registry-2026": registry_key}),
    ):
        result = verify_registry_response(body, header)

    assert result.status == RegistrySignatureStatus.VALID
    assert result.key_id == "registry-2026"

def test_sdk_rejects_tampered_body():
    """SDK rejects if body was modified after signing."""
    key = Ed25519PrivateKey.generate()
    pub_b64 = base64.b64encode(key.public_key().public_bytes_raw()).decode()

    body = b'{"slug": "test-pack"}'
    header = make_signature_header(body, key, "registry-2026")
    tampered_body = b'{"slug": "evil-pack"}'

    registry_key = RegistryKey(
        key_id="registry-2026",
        algorithm="ed25519",
        public_key=pub_b64,
        not_after="2099-12-31",
    )
    with patch(
        "agentnode_sdk.registry_trust.REGISTRY_KEYS",
        MappingProxyType({"registry-2026": registry_key}),
    ):
        result = verify_registry_response(tampered_body, header)

    assert result.status == RegistrySignatureStatus.INVALID
```

### Contract: body-byte invariant

Run against a live or staging environment. This test catches
reverse-proxy body modifications, compression mismatches, and
trailing-newline discrepancies.

```python
def test_body_byte_invariant_live():
    """Bytes received by httpx match what was signed."""
    import httpx

    endpoints = [
        "/v1/packages/test-pack",
        "/v1/packages/test-pack/install-info",
        "/v1/publishers/test-pub/keys/ed25519-k1",
    ]
    pub_key = Ed25519PublicKey.from_public_bytes(PUBLIC_KEY_BYTES)

    for path in endpoints:
        resp = httpx.get(f"https://staging.api.agentnode.net{path}")
        header = resp.headers.get("X-AgentNode-Signature")
        assert header, f"Missing signature on {path}"

        _, _, sig_b64 = header.split(":", 2)
        sig = base64.b64decode(sig_b64)

        # This verifies: bytes signed by backend == resp.content
        pub_key.verify(sig, resp.content)
```

### Startup self-test

```python
def test_startup_self_test_catches_corrupt_key():
    """Application refuses to sign if self-test fails."""
    # Load a valid key, corrupt it, verify self-test raises
    ...

def test_startup_without_key_logs_warning():
    """Missing REGISTRY_SIGNING_KEY → warning log, signing disabled."""
    ...
```

## Summary: Implementation Checklist

- [ ] Key generation (section 1)
- [ ] Key stored in `REGISTRY_SIGNING_KEY` env var (section 2)
- [ ] `load_signing_key_from_env()` with startup validation (sections 1-2)
- [ ] `make_signature_header()` function (section 3)
- [ ] Signing middleware on the response path (section 3)
- [ ] Middleware runs after serialization, before compression (section 6)
- [ ] Trust-critical routes identified and signed (section 4)
- [ ] Non-trust-critical routes not signed (section 4)
- [ ] Error responses (>= 400) not signed (section 3)
- [ ] Signing failure → serve without header + log CRITICAL (section 3)
- [ ] `X-AgentNode-Signature` header format correct (section 5)
- [ ] No reverse proxy body modification (section 6)
- [ ] Monitoring counters deployed (section 7)
- [ ] Alerts configured (section 7)
- [ ] Unit tests passing (section 9)
- [ ] SDK cross-verification test passing (section 9)
- [ ] Body-byte invariant test passing from external network (section 9)
- [ ] Startup self-test implemented (section 9)
