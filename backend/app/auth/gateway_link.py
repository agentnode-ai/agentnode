"""The website side of one identity across website and gateway (task #8).

A signed-in website user asks for a link grant addressed to one gateway; the gateway checks it and
hands back an invitation into that user's one account (sdk/agentnode_sdk/gateway/website_link.py).
This module only ISSUES. It holds the website's Ed25519 link key, from configuration:

    GATEWAY_LINK_SIGNING_KEY   base64url of the 32-byte Ed25519 seed (empty = not configured)
    GATEWAY_LINK_KEY_ID        the id the gateway trusts that key under

The grant format is a contract with the gateway, held by shared/website-link-grant-contract.json:
from the same inputs both sides produce exactly the same grant. This backend does not depend on the
SDK, so the format is written out here and the contract test is what keeps the two in step.
"""

from __future__ import annotations

import base64
import json
import re
import secrets
import time

ISSUER = "agentnode-website"
VERSION = 1
LIFETIME_SECONDS = 120
_GATEWAY_ID = re.compile(r"^[0-9a-f]{32}$")


class LinkNotConfigured(Exception):
    """The website has no link key, so it cannot hand an identity to any gateway."""


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def gateway_id_ok(gateway_id: str) -> bool:
    return isinstance(gateway_id, str) and bool(_GATEWAY_ID.fullmatch(gateway_id))


def website_user_of(user_id) -> str:
    """The website user as a grant names it: ``u-`` and the account's UUID in hex."""
    return "u-" + getattr(user_id, "hex", str(user_id).replace("-", ""))


def issue_grant(
    seed: bytes,
    *,
    key_id: str,
    gateway_id: str,
    website_user: str,
    now: int | None = None,
    lifetime: int = LIFETIME_SECONDS,
    nonce: str | None = None,
) -> str:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if not website_user:
        raise ValueError("a grant names the website user it is for")
    key = Ed25519PrivateKey.from_private_bytes(seed)
    at = int(time.time() if now is None else now)
    claims = {
        "v": VERSION,
        "iss": ISSUER,
        "aud": str(gateway_id),
        "sub": str(website_user),
        "iat": at,
        "exp": at + int(lifetime),
        "nonce": nonce or secrets.token_hex(16),
    }
    body = _b64e(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode())
    signed = ("%s|%s" % (key_id, body)).encode()
    return "%s.%s" % (body, _b64e(key_id.encode() + b":" + key.sign(signed)))


def configured_key(settings) -> tuple[bytes, str]:
    """(seed, key_id) from configuration, or LinkNotConfigured."""
    raw = str(getattr(settings, "GATEWAY_LINK_SIGNING_KEY", "") or "")
    key_id = str(getattr(settings, "GATEWAY_LINK_KEY_ID", "") or "")
    if not raw or not key_id:
        raise LinkNotConfigured("no gateway link key is configured")
    try:
        seed = _b64d(raw)
    except ValueError as exc:
        raise LinkNotConfigured("the gateway link key is not base64url") from exc
    if len(seed) != 32:
        raise LinkNotConfigured("the gateway link key is not a 32-byte Ed25519 seed")
    return seed, key_id
