"""Task #8: one identity across the website and the gateway, handed over by a signed grant.

The website is the authority on who a website user is; the gateway stays the authority on its own
accounts. They meet in one short-lived grant:

  website (holds an Ed25519 key)          gateway (holds the website's PUBLIC key)
  issue_grant(user, gateway_id)  ------>  redeem_grant(token)
                                            verify signature, issuer, audience, time, nonce
                                            user -> exactly one account (made on first use)
                                            -> a pairing invitation FOR that account

What it must never do, each with a test: accept a forged, foreign-keyed, tampered, expired,
not-yet-valid, over-long, replayed or wrong-gateway grant; let the grant name an account (the
redeemer never chooses one); put two website users into one account; link a suspended account.
Local fixtures only -- keys are generated in the test, nothing real is used.
"""
from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agentnode_sdk.access import dispatch
from agentnode_sdk.gateway import website_link as wl
from tests.test_what_is_kept import gateway  # noqa: F401 - the fixture

T = 2_000_000_000.0


@pytest.fixture
def key():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def linked(gateway, key):
    """A gateway that trusts this website key."""
    wl.trust_website_key(gateway.state, key.public_key(), key_id="web-1")
    return gateway


def _gid(gateway):
    return gateway.state.identity.gateway_id


def _grant(gateway, key, user="u-alice", **kw):
    kw.setdefault("now", T)
    return wl.issue_grant(key, key_id="web-1", gateway_id=_gid(gateway), website_user=user, **kw)


def _device(gateway, link, name="laptop"):
    token = gateway.state.redeem_pairing(link.pairing_code, client_name=name)
    return dispatch.identify(gateway, token)


# ---- the handover, end to end ------------------------------------------------------------------ #


def test_a_grant_becomes_an_invitation_into_the_users_own_account(linked, key):
    link = wl.redeem_grant(linked.state, _grant(linked, key), now=T + 5)
    assert link.created and link.pairing_code
    device = _device(linked, link)
    assert device.account_id == link.account_id


def test_the_same_user_always_lands_in_the_same_account(linked, key):
    first = wl.redeem_grant(linked.state, _grant(linked, key, nonce="a" * 32), now=T + 5)
    _device(linked, first, "laptop")
    second = wl.redeem_grant(linked.state, _grant(linked, key, nonce="b" * 32), now=T + 6)
    assert second.account_id == first.account_id and not second.created
    assert _device(linked, second, "server").account_id == first.account_id


def test_two_website_users_never_share_an_account(linked, key):
    a = wl.redeem_grant(linked.state, _grant(linked, key, user="u-alice"), now=T + 5)
    b = wl.redeem_grant(linked.state, _grant(linked, key, user="u-bob"), now=T + 5)
    assert a.account_id != b.account_id


# ---- what must be refused ---------------------------------------------------------------------- #


def _refused(code, call):
    with pytest.raises(wl.LinkRefused) as caught:
        call()
    assert caught.value.code == code, (caught.value.code, str(caught.value))


def test_no_trusted_website_key_refuses(gateway, key):
    _refused(wl.NOT_CONFIGURED, lambda: wl.redeem_grant(gateway.state, _grant(gateway, key), now=T + 5))


def test_a_grant_signed_by_another_key_is_refused(linked):
    other = Ed25519PrivateKey.generate()
    _refused(wl.BAD_SIGNATURE, lambda: wl.redeem_grant(linked.state, _grant(linked, other), now=T + 5))


def test_an_unknown_key_id_is_refused(linked, key):
    token = wl.issue_grant(key, key_id="web-9", gateway_id=_gid(linked), website_user="u-alice", now=T)
    _refused(wl.UNKNOWN_KEY, lambda: wl.redeem_grant(linked.state, token, now=T + 5))


def test_a_changed_body_is_refused(linked, key):
    body, sig = _grant(linked, key).split(".")
    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    claims["sub"] = "u-mallory"
    forged = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    _refused(wl.BAD_SIGNATURE, lambda: wl.redeem_grant(linked.state, forged + "." + sig, now=T + 5))


@pytest.mark.parametrize("token", ["", "x", "a.b.c", "not-base64!.sig", None])
def test_a_malformed_grant_is_refused(linked, token):
    _refused(wl.MALFORMED, lambda: wl.redeem_grant(linked.state, token, now=T + 5))


def test_a_grant_for_another_gateway_is_refused(linked, key):
    token = wl.issue_grant(key, key_id="web-1", gateway_id="0" * 32, website_user="u-alice", now=T)
    _refused(wl.WRONG_AUDIENCE, lambda: wl.redeem_grant(linked.state, token, now=T + 5))


def test_an_expired_grant_is_refused(linked, key):
    token = _grant(linked, key, lifetime=60)
    _refused(wl.EXPIRED, lambda: wl.redeem_grant(linked.state, token, now=T + 61))


def test_a_grant_from_the_future_is_refused(linked, key):
    token = _grant(linked, key, now=T + 3600)
    _refused(wl.NOT_YET_VALID, lambda: wl.redeem_grant(linked.state, token, now=T))


def test_a_grant_living_too_long_is_refused(linked, key):
    token = _grant(linked, key, lifetime=wl.MAX_LIFETIME_SECONDS + 1)
    _refused(wl.TOO_LONG, lambda: wl.redeem_grant(linked.state, token, now=T + 5))


def test_a_grant_cannot_be_used_twice(linked, key):
    token = _grant(linked, key)
    wl.redeem_grant(linked.state, token, now=T + 5)
    _refused(wl.REPLAYED, lambda: wl.redeem_grant(linked.state, token, now=T + 6))


@pytest.mark.parametrize("user", ["", "../acct", "x" * 200, "acct-0000000000000000"])
def test_a_bad_website_user_is_refused(linked, key, user):
    if user == "":
        with pytest.raises(ValueError):
            _grant(linked, key, user=user)
        return
    token = _grant(linked, key, user=user)
    _refused(wl.BAD_SUBJECT, lambda: wl.redeem_grant(linked.state, token, now=T + 5))


def test_a_grant_cannot_name_the_account(linked, key):
    victim = wl.redeem_grant(linked.state, _grant(linked, key, user="u-victim"), now=T + 5)
    token = _grant(linked, key, user="u-mallory", extra={"account": victim.account_id})
    _refused(wl.MALFORMED, lambda: wl.redeem_grant(linked.state, token, now=T + 6))


def test_a_suspended_account_is_not_linked_again(linked, key):
    first = wl.redeem_grant(linked.state, _grant(linked, key, nonce="c" * 32), now=T + 5)
    linked.state.accounts.suspend(first.account_id, because="test")
    _refused(wl.SUSPENDED,
             lambda: wl.redeem_grant(linked.state, _grant(linked, key, nonce="d" * 32), now=T + 6))


def test_a_refused_grant_leaves_no_invitation_and_no_account(linked, key):
    before = {a.account_id for a in linked.state.accounts.all()}
    other = Ed25519PrivateKey.generate()
    with pytest.raises(wl.LinkRefused):
        wl.redeem_grant(linked.state, _grant(linked, other), now=T + 5)
    assert {a.account_id for a in linked.state.accounts.all()} == before
    assert not linked.state.pairing_active(now=T + 5)


def test_nothing_secret_is_stored_by_the_gateway(linked, key):
    token = _grant(linked, key)
    wl.redeem_grant(linked.state, token, now=T + 5)
    root = linked.state.root
    for name in (wl.TRUST_NAME, wl.LINKS_NAME, wl.NONCES_NAME):
        text = (root / name).read_text(encoding="utf-8")
        assert token.split(".")[1] not in text and "PRIVATE" not in text


# ---- the contract with the website backend ------------------------------------------------------ #

CONTRACT = __import__("pathlib").Path(__file__).resolve().parents[2] / "shared" / "website-link-grant-contract.json"


def _contract():
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def _contract_key(doc):
    seed = base64.urlsafe_b64decode(doc["seed_b64url"] + "=" * (-len(doc["seed_b64url"]) % 4))
    return Ed25519PrivateKey.from_private_bytes(seed)


def test_the_gateway_produces_the_contract_grant_byte_for_byte():
    doc = _contract()
    grant = wl.issue_grant(_contract_key(doc), key_id=doc["key_id"], gateway_id=doc["gateway_id"],
                           website_user=doc["website_user"], now=doc["now"], lifetime=doc["lifetime"],
                           nonce=doc["nonce"])
    assert grant == doc["grant"]


def test_the_gateway_accepts_the_contract_grant_signature(gateway):
    doc = _contract()
    wl.trust_website_key(gateway.state, _contract_key(doc).public_key(), key_id=doc["key_id"])
    claims = wl._verify(gateway.state, doc["grant"])
    assert claims["sub"] == doc["website_user"] and claims["aud"] == doc["gateway_id"]
