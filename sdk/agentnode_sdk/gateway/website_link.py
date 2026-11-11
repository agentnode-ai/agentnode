"""One identity across the website and a gateway (task #8): a signed, single-use link grant.

The website is the authority on who a website user is. The gateway stays the authority on its own
accounts. They meet in one short-lived grant the website signs and the gateway checks:

    website: issue_grant(private_key, key_id, gateway_id, website_user)  -> "<body>.<signature>"
    gateway: redeem_grant(state, token)  -> LinkResult(account_id, pairing_code, created)

What the gateway does with a grant, and nothing else:

* checks it: the signature under a website key this gateway was told to trust (by key id), the
  issuer, that it is addressed to THIS gateway, its time window (at most MAX_LIFETIME_SECONDS long,
  small clock skew), a well-formed website user, and that its nonce was never used before;
* maps the website user to exactly ONE account -- made on first use, the same one every time after
  -- and refuses when that account is suspended;
* answers with an ordinary pairing invitation FOR that account. The device that redeems it joins
  that account through the existing pairing path, so a website user's devices and AI connections
  sit in one account with one set of ceilings and one suspension switch.

The grant can name a website user and nothing about accounts: the claim set is exact, so a grant
that tries to choose an account is malformed. That is the property `start_pairing` already has --
whoever redeems never decides which customer they join.

Nothing secret is stored here: the gateway keeps the website's PUBLIC key, a hashed user -> account
map and used nonces until they expire.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass

from agentnode_sdk.gateway import accounts as _accounts
from agentnode_sdk.gateway.filelock import ProcessLock, atomically

ISSUER = "agentnode-website"
VERSION = 1
MAX_LIFETIME_SECONDS = 300
CLOCK_SKEW_SECONDS = 60

TRUST_NAME = "website-link-keys.json"
LINKS_NAME = "website-links.json"
NONCES_NAME = "website-link-nonces.json"

NOT_CONFIGURED = "not_configured"
MALFORMED = "malformed"
UNKNOWN_KEY = "unknown_key"
BAD_SIGNATURE = "bad_signature"
WRONG_ISSUER = "wrong_issuer"
WRONG_AUDIENCE = "wrong_audience"
EXPIRED = "expired"
NOT_YET_VALID = "not_yet_valid"
TOO_LONG = "too_long"
REPLAYED = "replayed"
BAD_SUBJECT = "bad_subject"
SUSPENDED = "suspended"
TOO_MANY = "too_many_open"

_CLAIMS = {"v", "iss", "aud", "sub", "iat", "exp", "nonce"}
_SUBJECT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_NONCE = re.compile(r"^[0-9a-f]{32,64}$")
_KEY_ID = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


class LinkRefused(Exception):
    """The grant does not hand over an identity. `code` says which check said no."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__("%s: %s" % (code, detail))
        self.code = code


@dataclass(frozen=True)
class LinkResult:
    account_id: str
    pairing_code: str
    created: bool
    #: when the invitation stops working, as the joining store set it
    expires_at: int = 0


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    if not isinstance(text, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", text):
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _subject_ok(sub) -> bool:
    return (isinstance(sub, str) and bool(_SUBJECT.fullmatch(sub))
            and not sub.startswith(("acct-", "solo")))


# ---------------------------------------------------------------------------------- the website


def issue_grant(private_key, *, key_id: str, gateway_id: str, website_user: str,
                now: float | None = None, lifetime: int = 120, nonce: str | None = None,
                extra: dict | None = None) -> str:
    """The website side: sign a grant for one signed-in user, addressed to one gateway.

    `extra` exists only so a test can show that a grant carrying anything more is refused."""
    if not website_user:
        raise ValueError("a grant names the website user it is for")
    at = int(time.time() if now is None else now)
    claims = {"v": VERSION, "iss": ISSUER, "aud": str(gateway_id), "sub": str(website_user),
              "iat": at, "exp": at + int(lifetime), "nonce": nonce or secrets.token_hex(16)}
    claims.update(extra or {})
    body = _b64e(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode())
    signed = ("%s|%s" % (key_id, body)).encode()
    return "%s.%s" % (body, _b64e(key_id.encode() + b":" + private_key.sign(signed)))


# ---------------------------------------------------------------------------------- the gateway


def trust_website_key(state, public_key, *, key_id: str) -> None:
    """Operator step: trust this website public key, under this id, for link grants."""
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    if not _KEY_ID.fullmatch(str(key_id)):
        raise ValueError("a key id is 1-32 letters, digits, '_' or '-'")
    path = state.root / TRUST_NAME
    with ProcessLock(path):
        keys = _read(path)
        keys[key_id] = {"ed25519": _b64e(public_key.public_bytes(Encoding.Raw, PublicFormat.Raw))}
        atomically(path, json.dumps(keys, indent=2, sort_keys=True) + "\n")


def _read(path) -> dict:
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(body, dict):
        raise LinkRefused(NOT_CONFIGURED, "%s is not an object" % path.name)
    return body


def _verify(state, token) -> dict:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    keys = _read(state.root / TRUST_NAME)
    if not keys:
        raise LinkRefused(NOT_CONFIGURED, "this gateway trusts no website key")
    if not isinstance(token, str) or token.count(".") != 1:
        raise LinkRefused(MALFORMED, "a grant is '<body>.<signature>'")
    body, sig = token.split(".")
    try:
        raw_sig = _b64d(sig)
        key_id, signature = raw_sig.split(b":", 1)
        key_id = key_id.decode("ascii")
        claims = json.loads(_b64d(body))
    except (ValueError, UnicodeDecodeError):
        raise LinkRefused(MALFORMED, "the grant does not decode") from None
    if key_id not in keys:
        raise LinkRefused(UNKNOWN_KEY, "no trusted website key has this id")
    try:
        public = Ed25519PublicKey.from_public_bytes(_b64d(keys[key_id]["ed25519"]))
        public.verify(signature, ("%s|%s" % (key_id, body)).encode())
    except (InvalidSignature, ValueError, KeyError, TypeError):
        raise LinkRefused(BAD_SIGNATURE, "the grant was not signed by that website key") from None
    if not isinstance(claims, dict) or set(claims) != _CLAIMS or claims.get("v") != VERSION:
        raise LinkRefused(MALFORMED, "the grant carries other claims than a link grant has")
    return claims


def _subject_key(sub: str) -> str:
    return hashlib.sha256(("%s|%s" % (ISSUER, sub)).encode()).hexdigest()


def redeem_grant(state, token, now: float | None = None, joining=None) -> LinkResult:
    """Check a grant and hand back an invitation into the website user's one account.

    The invitation is a JOINING invitation (`gateway/joining.py`), the store made for "an
    invitation into an account that already exists" -- several may be live and each belongs to one
    account. It used to be `state.start_pairing(for_account=...)`, the operator's ONE live code:
    every website link then silently replaced whatever code the operator had just handed
    somebody, and the operator's next `gateway pair` silently killed a website user's invitation
    (#36). `joining` is the gateway's own store; without one, the store in this gateway's
    directory is used.
    """
    claims = _verify(state, token)
    at = time.time() if now is None else float(now)
    if claims["iss"] != ISSUER:
        raise LinkRefused(WRONG_ISSUER, "the grant was not issued by the website")
    if claims["aud"] != state.identity.gateway_id:
        raise LinkRefused(WRONG_AUDIENCE, "the grant is addressed to another gateway")
    iat, exp = claims["iat"], claims["exp"]
    if not all(isinstance(x, int) and not isinstance(x, bool) for x in (iat, exp)):
        raise LinkRefused(MALFORMED, "the grant's times are not whole seconds")
    if exp - iat > MAX_LIFETIME_SECONDS:
        raise LinkRefused(TOO_LONG, "a grant lives at most %d seconds" % MAX_LIFETIME_SECONDS)
    if iat > at + CLOCK_SKEW_SECONDS:
        raise LinkRefused(NOT_YET_VALID, "the grant is dated in the future")
    if exp <= at:
        raise LinkRefused(EXPIRED, "the grant has expired")
    if not (isinstance(claims["nonce"], str) and _NONCE.fullmatch(claims["nonce"])):
        raise LinkRefused(MALFORMED, "the grant's nonce is not one")
    if not _subject_ok(claims["sub"]):
        raise LinkRefused(BAD_SUBJECT, "the grant does not name a website user this accepts")

    nonces_path, links_path = state.root / NONCES_NAME, state.root / LINKS_NAME
    with ProcessLock(nonces_path), ProcessLock(links_path):
        nonces = {n: e for n, e in _read(nonces_path).items() if isinstance(e, (int, float)) and e > at}
        if claims["nonce"] in nonces:
            raise LinkRefused(REPLAYED, "this grant was already used")
        links = _read(links_path)
        key = _subject_key(claims["sub"])
        account_id = links.get(key, "")
        created = not account_id
        if account_id:
            try:
                if not state.accounts.get(account_id).active:
                    raise LinkRefused(SUSPENDED, "this website user's account is suspended")
            except _accounts.NoSuchAccount:
                raise LinkRefused(SUSPENDED, "this website user's account no longer exists") from None
        else:
            account_id = state.accounts.create(name="website user", now=at).account_id
        # The nonce is spent before anything is handed out, so a crash after this point cannot
        # make the same grant redeemable twice.
        nonces[claims["nonce"]] = exp
        atomically(nonces_path, json.dumps(nonces, indent=2, sort_keys=True) + "\n")
        if created:
            links[key] = account_id
            atomically(links_path, json.dumps(links, indent=2, sort_keys=True) + "\n")
    if joining is None:
        from agentnode_sdk.gateway.joining import Joining
        joining = Joining(state.root)
    from agentnode_sdk.gateway.joining import TooManyOutstanding
    try:
        offered = joining.offer(account_id, label="website link")
    except TooManyOutstanding as too_many:
        raise LinkRefused(TOO_MANY, str(too_many)) from too_many
    return LinkResult(account_id=account_id, pairing_code=offered["code"], created=created,
                      expires_at=int(offered["expires_at"]))


__all__ = ["LinkRefused", "LinkResult", "issue_grant", "redeem_grant", "trust_website_key",
           "MAX_LIFETIME_SECONDS", "ISSUER"]
