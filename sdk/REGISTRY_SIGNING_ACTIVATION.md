# Registry Signing Activation Plan

Last updated: 2026-05-23

## Context

The SDK (v0.10.0+) ships with full registry response verification
infrastructure but **empty `REGISTRY_KEYS`** — bootstrap mode. All
trust-critical endpoints pass through without signature checks.

Activating enforcement requires two coordinated steps:
1. The **registry backend** starts signing trust-critical responses
2. A **new SDK release** pins the registry's public key

The order is critical: backend first, SDK second. Reversing the
order breaks all installs for users on the new SDK.

## 1. Registry Signing Key Generation

**Algorithm:** Ed25519 (only algorithm the SDK accepts).

**Key naming:** `registry-{year}` (e.g. `registry-2026`). Year indicates
generation cohort, not expiry.

**Expiry (`not_after`):** 3 years from generation date. The SDK checks
`date.today() > date.fromisoformat(not_after)` — date-granular, no
timezone. Set generously to avoid premature enforcement failures.

**Generation procedure:**

```bash
# On a secure machine, not in CI
python3 -c "
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding, PrivateFormat, PublicFormat, NoEncryption,
)
import base64

private = Ed25519PrivateKey.generate()
pub_bytes = private.public_key().public_bytes_raw()

# Private key — PEM for storage
pem = private.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
print('=== PRIVATE KEY (store securely) ===')
print(pem.decode())

# Public key — base64 for SDK pinning
pub_b64 = base64.b64encode(pub_bytes).decode()
print(f'=== PUBLIC KEY (pin in SDK) ===')
print(f'key_id:     registry-2026')
print(f'algorithm:  ed25519')
print(f'public_key: {pub_b64}')
print(f'not_after:  2029-05-23')
print(f'length:     {len(pub_bytes)} bytes')
"
```

**Output artifacts:**
- Private key PEM → goes to secure storage (step 2)
- Public key base64 (32 bytes) → goes to SDK `REGISTRY_KEYS` (step 5)

**Do not generate multiple keys upfront.** Generate one key, activate
it, prove the pipeline works end-to-end. Generate the rotation key
when rotation is actually needed.

## 2. Key Storage

The private key must never touch disk unencrypted on production servers.

**Recommended: application-level signing with encrypted secret.**

| Option | Pros | Cons |
|--------|------|------|
| Environment variable (encrypted at rest) | Simple, works on Hetzner | Key in process memory |
| HashiCorp Vault Transit | Key never leaves Vault, audit trail | Additional infrastructure |
| GCP Cloud KMS | HSM-backed, Ed25519 supported | Cloud dependency |

For the current infrastructure (Hetzner):

```
Private key → encrypted env var or secrets file
  → loaded at application startup
  → held in memory for signing
  → never logged, never serialized to disk at runtime
```

**Access control:**
- Only the registry API process reads the signing key
- Separate from database credentials and API secrets
- Key material excluded from backups (re-generate on loss)
- Deployment pipeline injects key, does not store it in repo

## 3. Signing Trust-Critical Responses

**What gets signed:** The final serialized JSON response body bytes.
No canonicalization — exact bytes as returned by the JSON serializer.

**Trust-critical endpoints:**
- `GET /v1/packages/{slug}`
- `GET /v1/packages/{slug}/install-info`
- `GET /v1/publishers/{slug}/keys/{key_id}`

**NOT signed (low-value or non-GET):**
- `POST /v1/search`
- `POST /v1/resolve`
- `POST /v1/packages/{slug}/install`
- `POST /v1/packages/{slug}/download`

### Implementation

Signing middleware/decorator on the response path:

```python
# Pseudocode — adapt to your web framework
def sign_response_middleware(response, request):
    if request.method != "GET":
        return response
    if not is_trust_critical(request.path):
        return response

    # Sign the FINAL body bytes — after JSON serialization,
    # before any compression or transfer encoding
    body_bytes = response.body  # bytes, not str
    signature = signing_key.sign(body_bytes)
    sig_b64 = base64.b64encode(signature).decode()

    response.headers["X-AgentNode-Signature"] = (
        f"ed25519:{KEY_ID}:{sig_b64}"
    )
    return response
```

### Critical constraint: sign-last architecture

```
Request → Route handler → JSON serialize → SIGN → Compress → Send
                                            ↑
                                    Signing happens HERE
```

The signing **must** happen after JSON serialization and before any
response modification. If a reverse proxy (nginx, Cloudflare) modifies
whitespace, encoding, or adds headers that affect the body, verification
will fail.

**Verification:** The SDK calls `httpx.Response.content` which returns
the decompressed, fully reconstructed body. The backend signs these
same bytes — the pre-compression JSON output.

**Common pitfalls:**
- Django/Flask response middleware that re-encodes JSON
- nginx `sub_filter` or `subs_filter` directives
- CDN edge transformation (minification, charset normalization)
- gzip middleware that runs before signing middleware (order matters)

### Testing the signing path

```python
# End-to-end test — run against staging
import httpx, base64
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

resp = httpx.get("https://staging.api.agentnode.net/v1/packages/test-pack")
header = resp.headers.get("X-AgentNode-Signature")
assert header is not None, "No signature header"

alg, key_id, sig_b64 = header.split(":", 2)
sig_bytes = base64.b64decode(sig_b64)
pub_key = Ed25519PublicKey.from_public_bytes(PUBLIC_KEY_BYTES)
pub_key.verify(sig_bytes, resp.content)  # raises on failure
print("Signature valid")
```

## 4. Header Rollout

**Header format:** `X-AgentNode-Signature: ed25519:{key_id}:{base64_sig}`

**Rollout sequence:**

1. **Deploy signing to staging.** Run the end-to-end test above.

2. **Deploy signing to production.** All trust-critical responses now
   carry the header. Old SDKs (≤0.10.1) ignore it — they are in
   BOOTSTRAP mode. Zero user impact.

3. **Monitor for 7+ days:**
   - 100% of trust-critical responses have the header
   - Signature verification succeeds for all sampled responses
   - No increased error rates on trust-critical endpoints
   - Signing latency is negligible (Ed25519 is ~60μs per signature)

4. **Only after monitoring confirms stability:** proceed to step 5.

**Health check endpoint (recommended):**

```
GET /v1/health/signing → 200
{
  "signing_active": true,
  "key_id": "registry-2026",
  "algorithm": "ed25519"
}
```

This endpoint is NOT trust-critical and NOT signed. It exists for
monitoring and on-call diagnostics only.

## 5. SDK Key Pinning

Once the backend has been signing stably for 7+ days, pin the key
in the SDK:

```python
# agentnode_sdk/registry_trust.py

REGISTRY_KEYS: MappingProxyType = MappingProxyType({
    "registry-2026": RegistryKey(
        key_id="registry-2026",
        algorithm="ed25519",
        public_key="<base64-encoded-32-byte-public-key>",
        not_after="2029-05-23",
    ),
})
```

**This is the activation point.** Once this ships, all three behaviors
change simultaneously for users who upgrade:

| Before (BOOTSTRAP) | After (ENFORCEMENT) |
|---------------------|---------------------|
| Missing header → allow | Missing header → **deny** |
| Invalid signature → allow | Invalid signature → **deny** |
| Unknown key → allow | Unknown key → **deny** |

**There is no gradual activation.** The user either has the old SDK
(bootstrap) or the new SDK (enforcement). This is intentional — a
"warn but allow" mode would let attackers through during the window.

## 6. Activation Release

**Version:** v0.11.0 (minor bump — new security enforcement).

**Release checklist:**

- [ ] Backend signing stable for 7+ days in production
- [ ] End-to-end test passes against production (not just staging)
- [ ] Monitoring confirms 100% header coverage on trust-critical endpoints
- [ ] Public key pinned in `REGISTRY_KEYS`
- [ ] `enforcement_active()` returns `True` in unit test
- [ ] All existing tests still pass (BOOTSTRAP tests need key patching)
- [ ] New test: real pinned key verifies against test-signed body
- [ ] CHANGELOG documents activation
- [ ] TRUST_STACK.md updated: bootstrap → enforcement
- [ ] THREAT_MODEL.md: "Registry response freshness" is the only remaining gap
- [ ] Version bumped to 0.11.0

**Release notes should include:**

```
## 0.11.0 — Registry Trust Enforcement Active

Registry response authenticity is now enforced. Trust-critical API
responses are verified against a pinned Ed25519 registry key.

If you see `REGISTRY_SIGNATURE_MISSING` or `REGISTRY_KEY_UNKNOWN`
errors, upgrade to the latest SDK version.
```

## 7. Rollout and Rollback Strategy

### Deployment order (irreversible asymmetry)

```
Phase 1: Backend signs → zero impact (old SDKs ignore header)
Phase 2: SDK pins key → enforcement active (backend MUST sign)
```

**Phase 1 is safe and reversible.** If signing breaks, remove the
middleware. Old SDKs never noticed it.

**Phase 2 is the commitment point.** Once users upgrade to the
enforcement SDK, the backend cannot stop signing without breaking
those users' installs.

### Rollback scenarios

| Scenario | Impact | Response |
|----------|--------|----------|
| Backend signing breaks | Users on enforcement SDK cannot install | **P0:** Fix signing or deploy emergency rollback of signing middleware. Users on old SDK unaffected. |
| Backend deploys without signing middleware | Same as above — MISSING → deny | Same fix. Add deployment check: "signing middleware present?" |
| Key compromise | Attacker can sign arbitrary responses | **Emergency key rotation** (see below). |
| SDK has wrong public key pinned | All verifications fail → INVALID → deny | Emergency SDK patch with correct key. |

### Emergency key rotation

1. Generate new key pair
2. Backend switches to new key immediately
3. Emergency SDK release with both old and new keys in `REGISTRY_KEYS`
4. After overlap period (30+ days), remove compromised key from SDK
5. If the compromised key was the only key, the emergency release
   must add the new key — users on the compromised SDK are in
   UNKNOWN_KEY state until they upgrade

```python
# During rotation: both keys active
REGISTRY_KEYS: MappingProxyType = MappingProxyType({
    "registry-2026": RegistryKey(
        key_id="registry-2026",
        algorithm="ed25519",
        public_key="<old-key>",
        not_after="2026-12-31",  # shortened expiry
    ),
    "registry-2026b": RegistryKey(
        key_id="registry-2026b",
        algorithm="ed25519",
        public_key="<new-key>",
        not_after="2029-05-23",
    ),
})
```

### Planned key rotation (non-emergency)

1. Generate new key pair (step 1 of this plan)
2. SDK release adds new key alongside old key
3. Wait for adoption (60+ days)
4. Backend switches to new key
5. SDK release removes old key (after its `not_after`)

**Key insight:** The SDK selects the verification key by `key_id` from
the signature header. Multiple keys in `REGISTRY_KEYS` is the rotation
mechanism — no protocol change needed.

### Deployment safeguard

Add a pre-deployment check to the registry backend CI:

```bash
# Fail deployment if signing middleware is missing
curl -s https://api.agentnode.net/v1/packages/test-pack \
  -o /dev/null \
  -w "%{http_code}" \
  -H "Accept: application/json" | grep -q 200 && \
curl -s -I https://api.agentnode.net/v1/packages/test-pack | \
  grep -qi "X-AgentNode-Signature" || \
  { echo "FATAL: Signing middleware missing"; exit 1; }
```

## Monitoring Checklist

Before activation (Phase 1):
- [ ] All trust-critical responses carry `X-AgentNode-Signature`
- [ ] Signature verification success rate = 100%
- [ ] Signing latency p99 < 1ms
- [ ] No response body mutations by reverse proxy / CDN
- [ ] Health check endpoint returns `signing_active: true`

After activation (Phase 2):
- [ ] SDK error rate for `REGISTRY_SIGNATURE_*` codes = 0%
- [ ] Alert on any `REGISTRY_SIGNATURE_MISSING` errors from SDK telemetry
- [ ] Alert on signing key approaching `not_after` (90 days before)

## Timeline

| Step | Duration | Prerequisite |
|------|----------|-------------|
| Key generation | 1 hour | Secure machine access |
| Key storage setup | 1-2 days | Infrastructure decision |
| Backend signing implementation | 2-3 days | Key available to app |
| Staging validation | 1-2 days | Signing deployed to staging |
| Production deployment (Phase 1) | 1 day | Staging validated |
| Monitoring period | 7+ days | Signing live in production |
| SDK key pinning + release (Phase 2) | 1 day | Monitoring clean |
| **Total** | **~2-3 weeks** | |
