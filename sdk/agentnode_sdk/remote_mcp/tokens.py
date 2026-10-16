"""One opaque bearer token, one hashed record, one verifier -- two issuance paths.

Codex decision Q-RMCP-0002 item 2 / profile R5-R8: OAuth issuance (interactive) and
human-minted personal/service tokens create the SAME ``TokenRecord`` and are checked by the
SAME :meth:`TokenVerifier.verify`. There is no OAuth-only validation branch and no PAT-only
authorization branch after lookup.

Hardening the module guarantees (R6):
  * The plaintext token is generated once and returned once; it is NEVER stored. At rest we
    keep only a keyed HMAC-SHA256 digest of the secret, compared in constant time.
  * A non-secret ``token_id`` prefixes the token so lookup is a single O(1) record fetch,
    never a scan, and the ``token_id`` alone (never the secret) is what may enter audit
    telemetry.
  * Tokens are least-privilege scoped, carry a mandatory audience and expiry, and support
    rotation + revocation (+ an account/revocation ``generation`` so a bulk revoke is one
    bump).
  * ``__repr__`` and logging never reveal the secret; :func:`redact` scrubs a presented
    credential for safe error text.

The persistent store binds to real AgentNode accounts in deployment; here a file-backed
store writes ONLY hashed records (no plaintext) so the loopback/test stand is faithful.
"""
from __future__ import annotations

import hmac
import io
import json
import os
import secrets
import time
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from typing import Any, Optional

TOKEN_PREFIX = "ant"          # non-secret, marks an AgentNode remote-MCP token
_SECRET_BYTES = 32            # 256-bit secret


def _now() -> int:
    return int(time.time())


def redact(credential: Optional[str]) -> str:
    """A safe-for-logs stand-in for a presented credential. Never returns the secret."""
    if not credential:
        return "<none>"
    return "<redacted:%d chars>" % len(credential)


@dataclass(frozen=True)
class IssuedToken:
    """Returned to the issuer ONCE. ``plaintext`` is never persisted or logged."""
    plaintext: str
    record: "TokenRecord"

    def __repr__(self) -> str:  # never leak the secret through a repr
        return "IssuedToken(token_id=%r, plaintext=%s)" % (self.record.token_id, redact(self.plaintext))


@dataclass
class TokenRecord:
    """The canonical record both issuance paths insert (Q-RMCP-0002 item 2)."""
    token_id: str
    token_hash: str                       # keyed HMAC-SHA256 of the secret; never plaintext
    principal_id: str                     # AgentNode account/service identity
    audience: str                         # exact MCP resource URI
    scopes: tuple[str, ...]               # normalized least-privilege scope set
    issued_at: int
    expires_at: int                       # mandatory; 0 is not allowed (enforced at mint)
    issuance_kind: str                    # "oauth" | "personal" | "service" (audit metadata)
    account_generation: int = 0           # bump to mass-revoke a principal's tokens
    device_or_connection_id: Optional[str] = None
    revoked_at: Optional[int] = None
    revocation_reason: Optional[str] = None
    last_used_at: Optional[int] = None
    label: Optional[str] = None           # operator-readable purpose

    def public_view(self) -> dict[str, Any]:
        """Everything EXCEPT the hash -- safe to show an operator or write to audit telemetry."""
        d = asdict(self)
        d.pop("token_hash", None)
        d["scopes"] = list(self.scopes)
        return d


class TokenVerifyError(Exception):
    """Raised with a stable ``reason`` code; the HTTP layer maps it to 401/403. The message
    never contains the presented secret."""

    def __init__(self, reason: str, http_status: int):
        super().__init__(reason)
        self.reason = reason
        self.http_status = http_status


@dataclass
class Principal:
    """The one normalized security context the verifier hands to MCP routing (R8).

    ``device_or_connection_id`` is the gateway device/client this token was bound to at mint.
    The resource server re-checks that binding against the live gateway on EVERY call (account
    still active, device still paired, still holds RUN), so a token outlives neither a device
    withdrawal nor a permission change -- a check at ``/authorize`` alone would not do that.
    """
    principal_id: str
    scopes: tuple[str, ...]
    audience: str
    token_id: str
    issuance_kind: str
    device_or_connection_id: Optional[str] = None

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


class TokenStore:
    """Hashed-only token records, keyed by non-secret ``token_id``.

    ``server_key`` is the HMAC key kept server-side (root-readable only in deployment); it is
    what makes a stolen at-rest hash useless without it. The store never sees or keeps a
    plaintext token after :meth:`mint` returns it.
    """

    def __init__(self, server_key: bytes, *, account_generation: Optional[dict[str, int]] = None):
        if not server_key or len(server_key) < 32:
            raise ValueError("server_key must be >= 32 random bytes")
        self._server_key = server_key
        self._by_id: dict[str, TokenRecord] = {}
        # live per-principal generation; a token is valid only if its account_generation matches
        self._account_generation: dict[str, int] = dict(account_generation or {})

    # issuance ------------------------------------------------------------- #
    def _hash_secret(self, secret: str) -> str:
        return hmac.new(self._server_key, secret.encode("utf-8"), sha256).hexdigest()

    def mint(self, *, principal_id: str, audience: str, scopes: tuple[str, ...],
             issuance_kind: str, ttl_seconds: int, device_or_connection_id: Optional[str] = None,
             label: Optional[str] = None) -> IssuedToken:
        """Create one token. Both OAuth and personal/service paths call THIS -- one record shape.

        The secret is generated here, hashed, and returned exactly once inside ``IssuedToken``;
        the store keeps only the hash.
        """
        if issuance_kind not in ("oauth", "personal", "service"):
            raise ValueError("issuance_kind must be oauth|personal|service")
        if ttl_seconds <= 0:
            raise ValueError("expiry is mandatory; ttl_seconds must be > 0")
        if not audience:
            raise ValueError("audience (exact MCP resource URI) is mandatory")
        token_id = secrets.token_hex(8)
        secret = secrets.token_urlsafe(_SECRET_BYTES)
        plaintext = "%s_%s_%s" % (TOKEN_PREFIX, token_id, secret)
        issued = _now()
        rec = TokenRecord(
            token_id=token_id,
            token_hash=self._hash_secret(secret),
            principal_id=principal_id,
            audience=audience,
            scopes=tuple(sorted(set(scopes))),
            issued_at=issued,
            expires_at=issued + int(ttl_seconds),
            issuance_kind=issuance_kind,
            account_generation=self._account_generation.get(principal_id, 0),
            device_or_connection_id=device_or_connection_id,
            label=label,
        )
        self._by_id[token_id] = rec
        return IssuedToken(plaintext=plaintext, record=rec)

    # lifecycle ------------------------------------------------------------ #
    def revoke(self, token_id: str, reason: str = "revoked") -> bool:
        rec = self._by_id.get(token_id)
        if rec is None or rec.revoked_at is not None:
            return False
        rec.revoked_at = _now()
        rec.revocation_reason = reason
        return True

    def revoke_all_for(self, principal_id: str) -> int:
        """Bump the principal's generation: every token minted at the old generation is dead."""
        self._account_generation[principal_id] = self._account_generation.get(principal_id, 0) + 1
        return self._account_generation[principal_id]

    def get(self, token_id: str) -> Optional[TokenRecord]:
        return self._by_id.get(token_id)

    # verification --------------------------------------------------------- #
    def verify(self, presented: str, *, expected_audience: str,
               required_scopes: tuple[str, ...] = ()) -> Principal:
        """The ONE verifier. Both issuance paths' tokens pass through here identically.

        Raises :class:`TokenVerifyError` with an HTTP status: 401 for anything that makes the
        token invalid (unknown/expired/revoked/wrong-audience/stale-generation/malformed), 403
        for a valid token that merely lacks a required scope. The message is reason-only and
        never contains the presented secret.
        """
        parts = (presented or "").split("_", 2)
        if len(parts) != 3 or parts[0] != TOKEN_PREFIX or not parts[1] or not parts[2]:
            raise TokenVerifyError("malformed_token", 401)
        token_id, secret = parts[1], parts[2]
        rec = self._by_id.get(token_id)
        if rec is None:
            raise TokenVerifyError("unknown_token", 401)
        # constant-time compare of the keyed digest; a wrong secret with a real id fails here
        if not hmac.compare_digest(rec.token_hash, self._hash_secret(secret)):
            raise TokenVerifyError("bad_token", 401)
        if rec.revoked_at is not None:
            raise TokenVerifyError("revoked_token", 401)
        if _now() >= rec.expires_at:
            raise TokenVerifyError("expired_token", 401)
        if rec.account_generation != self._account_generation.get(rec.principal_id, 0):
            raise TokenVerifyError("revoked_token", 401)
        if rec.audience != expected_audience:
            raise TokenVerifyError("wrong_audience", 401)
        for scope in required_scopes:
            if scope not in rec.scopes:
                raise TokenVerifyError("insufficient_scope", 403)
        rec.last_used_at = _now()
        return Principal(principal_id=rec.principal_id, scopes=rec.scopes, audience=rec.audience,
                         token_id=rec.token_id, issuance_kind=rec.issuance_kind,
                         device_or_connection_id=rec.device_or_connection_id)

    # persistence (hashed-only) -------------------------------------------- #
    def dump_hashed(self, path: str) -> None:
        """Persist ONLY hashed records + generations. No plaintext is ever written."""
        payload = {"records": [asdict(r) for r in self._by_id.values()],
                   "account_generation": self._account_generation}
        tmp = path + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1, sort_keys=True)
        os.replace(tmp, path)

    @classmethod
    def load_hashed(cls, path: str, server_key: bytes) -> "TokenStore":
        store = cls(server_key)
        with io.open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        store._account_generation = dict(payload.get("account_generation", {}))
        for d in payload.get("records", []):
            d["scopes"] = tuple(d.get("scopes", []))
            store._by_id[d["token_id"]] = TokenRecord(**d)
        return store
