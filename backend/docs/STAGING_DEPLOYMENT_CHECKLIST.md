# Staging Deployment Checklist — Registry Signing

Date: 2026-05-25
Prerequisite: Sprint 1 commits deployed (b4ac558, db43089, dcf26e4)

## 1. Generate Staging Signing Key

On a local secure machine (NOT in CI, NOT on the server):

```bash
python3 -c "
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding, PrivateFormat, PublicFormat, NoEncryption,
)
import base64

private = Ed25519PrivateKey.generate()

# Private key → base64(PEM) for REGISTRY_SIGNING_KEY env var
pem = private.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
pem_b64 = base64.b64encode(pem).decode()
print(f'REGISTRY_SIGNING_KEY={pem_b64}')
print(f'REGISTRY_SIGNING_KEY_ID=staging-2026')
print()

# Public key → base64(raw 32 bytes) for verification
pub_bytes = private.public_key().public_bytes_raw()
pub_b64 = base64.b64encode(pub_bytes).decode()
print(f'Public key (for verification): {pub_b64}')
print(f'Public key length: {len(pub_bytes)} bytes')
"
```

**Save the output.** You need:
- `REGISTRY_SIGNING_KEY` → staging env var
- `REGISTRY_SIGNING_KEY_ID` → staging env var
- Public key base64 → for step 5 verification script

**This is a staging-only key.** Production gets its own key later.

## 2. Set Environment Variables

Set on the staging server / deployment config:

```
REGISTRY_SIGNING_KEY=<base64-pem from step 1>
REGISTRY_SIGNING_KEY_ID=staging-2026
```

## 3. Deploy

Deploy the backend with Sprint 1 commits to staging. Restart the
service to pick up the new env vars.

Verify startup log contains:
```
INFO agentnode.signing: Registry signing key loaded (key_id=staging-2026)
```

If instead you see:
```
CRITICAL agentnode.signing: Failed to load REGISTRY_SIGNING_KEY
```
→ The env var is malformed. Re-check the base64 encoding.

## 4. Health Check

```bash
curl -s https://staging.api.agentnode.net/v1/health/signing | python3 -m json.tool
```

Expected:
```json
{
    "signing_active": true,
    "key_id": "staging-2026",
    "algorithm": "ed25519"
}
```

If `signing_active` is `false` → env vars not set or not picked up.

## 5. Signature Presence Check

```bash
curl -sI https://staging.api.agentnode.net/v1/packages/<existing-slug> | \
  grep -i x-agentnode-signature
```

Expected: header present with format `ed25519:staging-2026:<base64>`.

If missing → middleware not running or endpoint not trust-critical.

Also check a non-trust-critical endpoint:
```bash
curl -sI https://staging.api.agentnode.net/health | \
  grep -i x-agentnode-signature
```

Expected: NO header (health is not trust-critical).

## 6. Body-Byte Invariant Test

**This is the critical test.** It proves that the bytes the backend
signed are identical to the bytes received over the network.

```bash
# Fetch a trust-critical response — save raw bytes + headers
curl -s https://staging.api.agentnode.net/v1/packages/<existing-slug> \
  --compressed -o body.bin -D headers.txt

# Verify with Python
python3 -c "
import base64, sys
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# The public key from step 1
PUB_B64 = '<paste-public-key-base64-here>'
pub_bytes = base64.b64decode(PUB_B64)
pub_key = Ed25519PublicKey.from_public_bytes(pub_bytes)

body = open('body.bin', 'rb').read()
print(f'Body: {len(body)} bytes')

# Parse signature header from headers.txt
with open('headers.txt') as f:
    headers = f.read()
sig_line = [l for l in headers.splitlines() if 'x-agentnode-signature' in l.lower()]
if not sig_line:
    print('FAIL: No X-AgentNode-Signature header found')
    sys.exit(1)

value = sig_line[0].split(': ', 1)[1].strip()
alg, kid, sig_b64 = value.split(':', 2)
sig_bytes = base64.b64decode(sig_b64)

print(f'Algorithm: {alg}')
print(f'Key ID: {kid}')
print(f'Signature: {len(sig_bytes)} bytes')

try:
    pub_key.verify(sig_bytes, body)
    print('PASS: Signature verified — body-byte invariant holds')
except Exception as e:
    print(f'FAIL: Signature verification failed — {e}')
    sys.exit(1)
"
```

**Run this for all three trust-critical endpoints:**
- `GET /v1/packages/<slug>`
- `GET /v1/packages/<slug>/install-info`
- `GET /v1/publishers/<slug>/keys/<key_id>`

**CRITICAL:** `--compressed` tells curl to decompress, matching what
`httpx.Response.content` returns. Do NOT parse and re-serialize the
JSON — compare raw bytes.

## 7. Reverse Proxy / CDN Check

If staging has nginx, Caddy, Cloudflare, or any reverse proxy:

**Check for body-modifying directives:**
```bash
# On the staging server
grep -rn 'sub_filter\|subs_filter\|gzip\|brotli\|charset' /etc/nginx/
grep -rn 'encode\|compress' /etc/caddy/
```

**Check for trailing newlines:**
```bash
# Compare last byte of body.bin
xxd body.bin | tail -1
# Should end with JSON close brace/bracket, no trailing 0a (newline)
```

**Check Content-Encoding:**
```bash
grep -i content-encoding headers.txt
# gzip/br is fine — curl --compressed handles decompression
# But verify the invariant test above still passes
```

## 8. SDK Cross-Verification (Optional but Recommended)

Run the SDK's `verify_registry_response()` against the staging
response from a local machine:

```python
import httpx, base64
from types import MappingProxyType

# Fetch from staging
resp = httpx.get("https://staging.api.agentnode.net/v1/packages/<slug>")
sig_header = resp.headers.get("X-AgentNode-Signature")

# Import SDK verification
from agentnode_sdk.registry_trust import (
    RegistryKey, RegistrySignatureStatus,
    verify_registry_response,
)
import agentnode_sdk.registry_trust as rt

# Temporarily inject staging key
PUB_B64 = "<paste-public-key-base64-here>"
staging_keys = MappingProxyType({
    "staging-2026": RegistryKey(
        key_id="staging-2026",
        algorithm="ed25519",
        public_key=PUB_B64,
        not_after="2027-12-31",
    ),
})

# Monkeypatch for test
original = rt.REGISTRY_KEYS
rt.REGISTRY_KEYS = staging_keys
result = verify_registry_response(resp.content, sig_header)
rt.REGISTRY_KEYS = original

print(f"Status: {result.status.value}")
print(f"Key ID: {result.key_id}")
assert result.status == RegistrySignatureStatus.VALID
print("PASS: SDK accepts staging backend signature over real network")
```

## 9. Document Results

Create a results section below after running the checklist.

---

## Results

| Check | Status | Notes |
|-------|--------|-------|
| Key generated | | |
| Env vars set | | |
| Deployed | | |
| Health check | | |
| Signature present on trust-critical | | |
| Signature absent on non-critical | | |
| Body-byte invariant: /packages/{slug} | | |
| Body-byte invariant: /packages/{slug}/install-info | | |
| Body-byte invariant: /publishers/{slug}/keys/{key_id} | | |
| Reverse proxy check | | |
| Trailing newline check | | |
| SDK cross-verification | | |

**Decision:** [ ] Proceed to production / [ ] Fix issues first

---

## After Staging Is Green

Next step: repeat steps 1-8 for production with a **production key**
(different from staging). Then monitor for 7+ days per
`REGISTRY_SIGNING_ACTIVATION.md` before SDK key pinning.
