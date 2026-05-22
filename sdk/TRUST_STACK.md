# AgentNode SDK — Trust Stack Architecture

Last updated: 2026-05-22

## Layer Overview

```
┌─────────────────────────────────────────────────┐
│  Trust Surface (TG-3)                           │
│  Install messages, dashboard, inspect, wording  │
├─────────────────────────────────────────────────┤
│  Freshness + Identity (v0.9.0)                  │
│  Online key verification, publisher_slug        │
├─────────────────────────────────────────────────┤
│  Authenticity (v0.8.0)                          │
│  Publisher Ed25519 signatures                   │
├─────────────────────────────────────────────────┤
│  Integrity (v0.7.0)                             │
│  Per-entry SHA-256 hash, canonical fields       │
└─────────────────────────────────────────────────┘
```

Each layer is independently useful. Upper layers compose lower layers
but do not replace them.

## Guarantees per Layer

### Layer 1 — Integrity (`lock_integrity.py`)

**What it guarantees:**
- Every lockfile entry has a SHA-256 hash over its canonical fields.
- Post-install modification of any canonical field is detectable.
- Canonical field set evolves with the entry: v1 (base), v2 (+signatures),
  v3 (+publisher_slug). Version is auto-detected, not declared.

**What it does NOT guarantee:**
- No global lockfile hash. Adding a new malicious entry is undetected.
- `trust_level` and `last_trust_check` are mutable fields — excluded
  from integrity by design (TTL refresh needs to write them).
- Integrity is per-entry, not cross-entry. Swapping two entries' positions
  is not a meaningful attack since entries are keyed by slug.

**Modules:** `lock_integrity.py`
**CLI:** `agentnode lock seal`, `agentnode lock verify`, `agentnode inspect`
**Canonical fields (v1):** version, package_type, runtime, entrypoint,
artifact_hash, tools, permissions, mcp_command, remote_endpoint,
connector, agent, prompts, resources, assets
**v2 adds:** `_signatures` — signature swap invalidates integrity
**v3 adds:** `publisher_slug` — publisher identity is integrity-protected

### Layer 2 — Authenticity (`signature.py`)

**What it guarantees:**
- Publisher signs a canonical payload (slug + canonical fields) with
  Ed25519 at publish time.
- Invalid signature at install → install blocked (no override).
- Verification uses the public key embedded in the lockfile entry
  (`_signatures.publisher.public_key`).
- `signature.py` is offline-only. No network calls, no registry dependency.

**What it does NOT guarantee:**
- Missing signature → warn, not block (gradual adoption period).
- Public key trust is bootstrapped from the registry install response.
  A compromised registry can serve a different key pair.
- Signature payload is v1 forever — never changes.
- `publisher_slug` is NOT in the signature payload.
  Publisher identity is protected by integrity (v3), not by signature.

**Modules:** `signature.py`, `signing_key.py`
**Statuses:** VALID, MISSING, INVALID, REVOKED, UNKNOWN_KEY
**Payload fields:** slug + all CANONICAL_FIELDS (not v2/v3 extensions)
**Design constraint:** `build_sign_payload()` uses CANONICAL_FIELDS (v1),
not CANONICAL_FIELDS_V2/V3. The signature payload is frozen.

### Layer 3 — Freshness + Identity (`key_status.py`)

**What it guarantees:**
- `lock verify --online` checks each publisher key against the registry.
- Revoked, unknown, or mismatched keys → exit 1 (fail-closed).
- Registry unreachable → exit 1 (fail-closed for CI).
- Install-time revocation: if the registry install response reports a
  key as revoked, install is blocked. No additional network call.
- `publisher_slug` is a stable identity field, write-once at install,
  never overwritten by trust refresh.

**What it does NOT guarantee:**
- Install is offline-default. No mandatory online check at install time.
  Revocation only triggers when the registry actively reports it in the
  install response.
- Runtime does not check revocation. Execution uses offline signature
  only. A revoked key does not prevent running already-installed tools.
- No TTL cache for key status. Each `--online` run is a fresh check.

**Modules:** `key_status.py`
**Statuses:** ACTIVE, REVOKED, UNKNOWN, MISMATCH, ERROR
**Severity map:** mismatch=critical, revoked=high, unknown=medium,
error=availability, active=none
**Design constraint:** `key_status.py` uses httpx directly,
not AgentNodeClient. No coupling to SDK client lifecycle.

### Layer 4 — Trust Surface (TG-3)

**What it guarantees:**
- Every install shows publisher verification status (verified/unverified).
- Dashboard shows aggregate trust counts (signed, unsigned, sealed).
- `inspect` and `lock verify` use consistent "publisher" terminology.
- JSON output keys are unchanged (`"signature"`, not `"publisher"`).
  Only human-readable labels were harmonized.

**What it does NOT guarantee:**
- No new trust semantics. Pure presentation layer.
- No new enforcement. Unverified packages are shown, not blocked.

## Failure Modes

| Failure | Layer | Behavior | Exit code |
|---------|-------|----------|-----------|
| Integrity mismatch | L1 | Warn + audit (default), deny (strict mode) | `lock verify`: 1 |
| Missing integrity | L1 | Pass (default), fail (`--strict`) | `lock verify --strict`: 1 |
| Invalid signature | L2 | Install blocked, no override | install: RuntimeError |
| Missing signature | L2 | Warn, install proceeds | install: 0 |
| Revoked key (offline) | L2 | Status tracked, visible in verify | `lock verify`: 0 |
| Revoked key (online) | L3 | Flagged as failure | `lock verify --online`: 1 |
| Revoked key (install) | L3 | Install blocked when registry reports | install: RuntimeError |
| Key mismatch (online) | L3 | Highest severity (critical) | `lock verify --online`: 1 |
| Registry unreachable | L3 | Fail-closed | `lock verify --online`: 1 |
| Unknown key (online) | L3 | Medium severity | `lock verify --online`: 1 |

## What Runtime Never Does

Runtime (`runner.py`, `python_runner.py`, `mcp_runner.py`) operates on
local state only. It never:

1. **Makes network calls for trust decisions.** Integrity check and
   signature verification are pure computation on lockfile data.
2. **Checks key revocation.** A revoked key on an installed package
   does not prevent execution. The operator must run `lock verify --online`
   or reinstall to detect revocation.
3. **Modifies trust state.** Runtime reads the lockfile; it never writes
   to `_integrity`, `_signatures`, or `publisher_slug`.
4. **Enforces publisher identity.** `publisher_slug` is displayed, not
   gated. There is no "only run packages from publisher X" policy today.

This is by design. Runtime is latency-sensitive and must work offline.
Trust verification is a separate operation, not a runtime gate.

## Offline Verification

Everything verifiable without network access:

- `agentnode lock verify` — integrity hashes, signature validity
- `agentnode lock verify --strict` — also fails on missing integrity
- `agentnode inspect <slug>` — full trust profile per package
- `agentnode lock verify --json` — machine-readable for CI

These operations use only data already in `agentnode.lock`.
A compromised registry cannot retroactively invalidate offline
verification of correctly signed packages.

## Online Verification

Requires registry access:

- `lock verify --online` — checks key status against registry
- Install-time revocation — uses `publisher.key_status` from registry
  install response (no additional HTTP call)
- Trust level refresh — periodic TTL-based re-fetch (7 days)

Online operations are opt-in (install stays offline-default) or
background (trust refresh). Only `lock verify --online` is explicitly
fail-closed.

## Open Gaps

### 1. Registry Response Authenticity

**Current state:** The registry serves metadata (trust level, public key,
key_status) over HTTPS. TLS guarantees transport-level authenticity but
not application-level.

**Risk:** A compromised registry (or MitM with a valid cert) can:
- Serve a replacement public key → victim installs with attacker signature
- Report `key_status: "active"` for a revoked key
- Omit `publisher_slug` or substitute a different publisher

**Publisher signatures partially mitigate this:** If a user already has
a package installed with the real publisher's key, a replacement key
from a compromised registry would fail signature verification on upgrade.
But first-time installs have no prior trust anchor.

**Recommended next step:** TG-4 — sign registry API responses with a
registry-level key. The SDK verifies the registry signature before
trusting any metadata.

### 2. Transparency Log

**Current state:** No public log of publish events.

**Risk:** A compromised registry can serve different artifacts to
different users. Without a transparency log, there is no way to detect
equivocation (serving A to user 1 and B to user 2).

**Recommendation:** Not immediate priority. Registry response authenticity
(gap 1) must come first — a transparency log without registry signatures
is verifiable but not trustworthy.

### 3. Trust Policies

**Current state:** Policy gate uses `trust_level` for run decisions.
No publisher-based policies ("only run packages from publisher X").

**Risk:** Low. Trust level is a proxy for publisher quality (verified,
trusted, unknown). Publisher-based policy is additive, not a security gap.

**Recommendation:** Future feature, not a security priority. Current
trust levels + guard are sufficient for v1 threat model.

### 4. Publisher Reputation

**Current state:** `publisher_slug` identifies who published, but there
is no reputation score, publish history, or cross-package trust signal.

**Risk:** Low. A new publisher with a verified key is technically
indistinguishable from an established one.

**Recommendation:** Registry-side feature, not SDK-side. The SDK should
display reputation data when available but not compute it locally.

### 5. Global Lockfile Hash

**Current state:** Integrity is per-entry. Adding a new entry to the
lockfile is undetected.

**Risk:** Medium. An attacker with filesystem write access can inject a
new malicious entry. The entry itself will have valid integrity (the
attacker computes the hash).

**Recommendation:** Add a top-level hash over the sorted package list.
`lock seal` computes it, `lock verify` checks it. Detects addition
and removal of entries.

## Recommended Next Block

**TG-4 — Registry Response Authenticity**

Goal: Prove that registry API responses are authentic, not just
transport-encrypted. Close the first-install trust bootstrap gap.

Likely approach:
- Registry signs API responses with a long-lived Ed25519 key
- SDK ships a pinned registry public key (or fetches it via TLS + pins)
- `install_package()` verifies the registry signature before trusting
  the publisher public key in the install response
- `lock verify --online` verifies key_status responses the same way

This is the highest-leverage remaining gap: it upgrades the trust anchor
from "TLS to the registry" to "cryptographic proof from the registry".
