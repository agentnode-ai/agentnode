# Trust Activation Runbook

## Trust Matrix

| SDK State | Backend State | Response | SDK Behavior |
|-----------|---------------|----------|--------------|
| Bootstrap (REGISTRY_KEYS={}) | Signing off | No header | BOOTSTRAP — allow |
| Bootstrap | Signing on | Header present | BOOTSTRAP — allow (header ignored) |
| Enforcement (keys pinned) | Signing on, valid | Valid header | VALID — allow |
| Enforcement | Signing on, invalid sig | Bad header | INVALID — **hard deny** |
| Enforcement | Signing on, unknown key | Unrecognized key_id | UNKNOWN_KEY — **hard deny** |
| Enforcement | Signing on, expired key | Key past not_after | EXPIRED_KEY — **hard deny** |
| Enforcement | Signing off | No header | MISSING — **hard deny** (downgrade protection) |

## Bootstrap Lifecycle

- **Start:** SDK v0.10.0 ships with `REGISTRY_KEYS = {}`. No enforcement.
- **End:** Future SDK release ships with pinned key(s). Enforcement activates automatically.
- **Transition:** Binary flip per SDK version. No gradual rollout.
- **Safety invariant:** Backend MUST sign stably for 7+ days before the SDK release that ends bootstrap.

## Activation Sequence (Phase 2)

### Pre-activation checklist
- [ ] Health endpoint returns `signing_active: false` (not false-positive)
- [ ] `verify_signing.py --pre-activation` passes through nginx
- [ ] Rollback procedure understood by operator

### Activation steps
1. Generate Ed25519 keypair on secure machine (not on server, not in repo)
2. Set in `.env`:
   ```
   REGISTRY_SIGNING_KEY=<base64-encoded-PEM-private-key>
   REGISTRY_SIGNING_KEY_ID=registry-2026
   ```
3. `sudo systemctl restart agentnode-api`
4. Verify: `curl -s /v1/health/signing` → `signing_active: true, trust_mode: signing`
5. Verify: `python scripts/verify_signing.py --base-url http://localhost:3080 --slug mcp-filesystem --public-key-b64 <pubkey>`
6. Monitor 48h: no CRITICAL logs, no 5xx, responses still JSON-parseable
7. After 7 clean days → eligible for SDK key pinning (Phase 3)

### Post-activation monitoring
- `journalctl -u agentnode-api | grep -i signing` — no CRITICAL after activation
- `curl -s /v1/packages/mcp-filesystem/install-info -D-` — X-AgentNode-Signature header present
- SDK clients in bootstrap mode: unaffected (header ignored)

## Rollback Procedure

**When to roll back:**
- 5xx errors on trust-critical endpoints
- Response body corruption (JSON parse failures)
- X-AgentNode-Signature header contains garbage
- Health shows `signing_active: true` but no headers on responses

**Steps:**
1. Remove `REGISTRY_SIGNING_KEY` and `REGISTRY_SIGNING_KEY_ID` from `.env`
2. `sudo systemctl restart agentnode-api`
3. Verify: `curl -s /v1/health/signing` → `signing_active: false`

**Impact:**
- Bootstrap SDKs (current): zero impact
- Enforcement SDKs (future, post-pinning): trust-critical requests denied with MISSING
- Recovery time: immediate after restart
- No database changes, no cache invalidation needed

## Key Rotation Model (theoretical)

- **Planned rotation:** Ship new key alongside old in SDK → adoption window → backend switches → SDK removes old key after `not_after`
- **Emergency rotation:** Backend switches immediately → emergency SDK release with both keys → remove compromised key after overlap
- **Maximum 2 active keys simultaneously** (current + previous)
- **Mechanism:** `key_id` in signature header selects which key to verify against

## Degraded Modes

| Mode | Trigger | Behavior | Recovery |
|------|---------|----------|----------|
| Bootstrap | SDK has no pinned keys | All responses allowed | Automatic on SDK upgrade |
| Signing Inactive | Key not set or invalid | Responses served unsigned | Set valid key + restart |
| Enforcement Active | SDK has keys, backend signs | Full verification | Normal operation |
| **Enforcement Broken** | SDK has keys, backend stops signing | **All trust-critical requests denied** | **P0: fix backend signing** |

## What "verified" means (cryptographic definition)

Registry Ed25519 signature proves:
- **Authenticity:** Bytes were produced by the holder of the registry private key
- **Integrity:** Not modified in transit (nginx, CDN, proxy)

It does NOT prove:
- **Freshness:** A valid response can be replayed (TG-5+)
- **Publisher quality:** Registry signs everything it serves, including unverified packages
- **Not a TLS replacement:** TLS provides confidentiality; signing provides defense-in-depth

## Verification is body-byte based

The signature covers `response.content` — the decompressed, fully-reconstructed response body bytes. It is independent of:
- TLS termination point
- Content-Length header
- Transfer-Encoding (chunked vs content-length)
- Accept-Encoding negotiation (httpx decompresses transparently)

The only invariant: the exact bytes that the ASGI middleware signs must equal the exact bytes that `httpx.Response.content` returns to the SDK.
