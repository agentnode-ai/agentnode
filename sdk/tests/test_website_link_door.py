"""#36: the door a device uses to redeem a website link grant, end to end over HTTP.

#8 made the grant: the website signs `{v, iss, aud, sub, iat, exp, nonce}` for one signed-in user and
one gateway; `website_link.redeem_grant` checks it and maps the website user to exactly one account.
This is the product path that makes it usable: `POST /v1/link {"grant": ...}` on the gateway answers
with an invitation into that user's account, and the device then pairs with that invitation through
the ordinary `POST /v1/pair`. Nothing here decides who the user is or which account they get -- the
signed grant and the gateway's own map do.

Two properties of the invitation, both found while building this door:

* it is a JOINING invitation (`gateway/joining.py`), not the operator's single pairing code. The
  operator's code is one-at-a-time by design; issuing link invitations there meant every website
  link silently replaced whatever code the operator had just handed somebody, and the reverse;
* the answer names no account. A stranger at the door learns a code to use once, the gateway it
  belongs to, and whether this was the user's first link -- not the account id.
"""
from __future__ import annotations

import base64
import http.client
import json
import time
import urllib.parse

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agentnode_sdk.access import dispatch
from agentnode_sdk.gateway import website_link as wl
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.server import GatewayService, make_server
from tests import serving
from tests.test_em3c_gateway import StandInBackend, _store_measurement


@pytest.fixture()
def key():
    return Ed25519PrivateKey.generate()


@pytest.fixture()
def door(tmp_path, key):
    """A real gateway on a real port that trusts this website key."""
    state = GatewayState(str(tmp_path / "state"), version="test")
    service = GatewayService(state, backend=StandInBackend())
    _store_measurement(service)
    wl.trust_website_key(state, key.public_key(), key_id="web-1")
    server = make_server(service, port=0, host="127.0.0.1")
    serving.owned(server, state)
    return service, "http://127.0.0.1:%d" % server.server_address[1]


def _post(base, path, body):
    host, port = urllib.parse.urlparse(base).netloc.split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=15)
    conn.request("POST", path, json.dumps(body), {"Content-Type": "application/json"})
    answer = conn.getresponse()
    raw = answer.read().decode("utf-8") or "{}"
    conn.close()
    return answer.status, json.loads(raw)


def _grant(service, key, user="u-alice", **kw):
    return wl.issue_grant(key, key_id="web-1", gateway_id=service.state.identity.gateway_id,
                          website_user=user, **kw)


def _link_and_pair(service, base, grant, name="laptop"):
    status, linked = _post(base, "/v1/link", {"grant": grant})
    assert status == 200, linked
    status, paired = _post(base, "/v1/pair", {"code": linked["code"], "client_name": name})
    assert status == 200, paired
    return linked, dispatch.identify(service, paired["token"])


# ---- the way through ------------------------------------------------------------------------- #


def test_a_grant_becomes_a_device_in_the_users_own_account(door, key):
    service, base = door
    linked, device = _link_and_pair(service, base, _grant(service, key))
    assert linked["new_account"] is True
    assert device.account_id and device.account_id.startswith("acct-")


def test_the_same_website_user_always_lands_in_the_same_account(door, key):
    service, base = door
    first, laptop = _link_and_pair(service, base, _grant(service, key), "laptop")
    second, server = _link_and_pair(service, base, _grant(service, key), "server")
    assert second["new_account"] is False
    assert server.account_id == laptop.account_id


def test_two_website_users_never_share_an_account(door, key):
    service, base = door
    _a, alice = _link_and_pair(service, base, _grant(service, key, user="u-alice"))
    _b, bob = _link_and_pair(service, base, _grant(service, key, user="u-bob"))
    assert alice.account_id != bob.account_id


def test_the_answer_names_no_account(door, key):
    service, base = door
    status, linked = _post(base, "/v1/link", {"grant": _grant(service, key)})
    assert status == 200
    assert set(linked) == {"code", "expires_at", "new_account", "gateway", "fingerprint"}
    assert "acct-" not in json.dumps(linked)


# ---- once, and only once ---------------------------------------------------------------------- #


def test_a_grant_cannot_be_used_twice(door, key):
    service, base = door
    grant = _grant(service, key)
    assert _post(base, "/v1/link", {"grant": grant})[0] == 200
    status, refused = _post(base, "/v1/link", {"grant": grant})
    assert status == 403 and "already used" in json.dumps(refused), refused


def test_the_invitation_it_gives_works_once(door, key):
    service, base = door
    status, linked = _post(base, "/v1/link", {"grant": _grant(service, key)})
    assert _post(base, "/v1/pair", {"code": linked["code"], "client_name": "a"})[0] == 200
    assert _post(base, "/v1/pair", {"code": linked["code"], "client_name": "b"})[0] == 403


# ---- time --------------------------------------------------------------------------------------- #


def test_an_expired_grant_is_refused(door, key):
    service, base = door
    stale = _grant(service, key, now=time.time() - 600, lifetime=120)
    status, refused = _post(base, "/v1/link", {"grant": stale})
    assert status == 403 and "expired" in json.dumps(refused), refused


def test_a_grant_from_the_future_is_refused(door, key):
    service, base = door
    status, refused = _post(base, "/v1/link", {"grant": _grant(service, key, now=time.time() + 3600)})
    assert status == 403 and "future" in json.dumps(refused), refused


def test_a_grant_living_too_long_is_refused(door, key):
    service, base = door
    status, refused = _post(base, "/v1/link", {"grant": _grant(service, key, lifetime=3600)})
    assert status == 403 and "at most" in json.dumps(refused), refused


# ---- tampering ---------------------------------------------------------------------------------- #


def _tamper(grant, **changes):
    body, signature = grant.split(".")
    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    claims.update(changes)
    raw = json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=") + "." + signature


def test_a_changed_user_is_refused(door, key):
    service, base = door
    status, refused = _post(base, "/v1/link", {"grant": _tamper(_grant(service, key), sub="u-mallory")})
    assert status == 403 and "signature" in json.dumps(refused), refused


def test_a_grant_signed_by_another_key_is_refused(door):
    service, base = door
    status, refused = _post(base, "/v1/link", {"grant": _grant(service, Ed25519PrivateKey.generate())})
    assert status == 403, refused


def test_a_grant_for_another_gateway_is_refused(door, key):
    service, base = door
    other = wl.issue_grant(key, key_id="web-1", gateway_id="0" * 32, website_user="u-alice")
    status, refused = _post(base, "/v1/link", {"grant": other})
    assert status == 403 and "another gateway" in json.dumps(refused), refused


def test_a_grant_that_tries_to_name_an_account_is_refused(door, key):
    service, base = door
    status, refused = _post(base, "/v1/link", {"grant": _grant(service, key, extra={"acct": "acct-x"})})
    assert status == 403, refused


@pytest.mark.parametrize("body", [{}, {"grant": ""}, {"grant": "not.a.grant"}, {"grant": 7}])
def test_rubbish_is_refused(door, body):
    _service, base = door
    assert _post(base, "/v1/link", body)[0] == 403


def test_a_suspended_users_account_is_not_joined_again(door, key):
    service, base = door
    _linked, device = _link_and_pair(service, base, _grant(service, key))
    service.state.accounts.suspend(device.account_id, because="test")
    status, refused = _post(base, "/v1/link", {"grant": _grant(service, key)})
    assert status == 403 and "suspended" in json.dumps(refused), refused


# ---- what it must leave alone ----------------------------------------------------------------- #


def test_the_operators_pairing_code_survives_a_link(door, key):
    service, base = door
    operator_code = service.state.start_pairing()
    assert _post(base, "/v1/link", {"grant": _grant(service, key)})[0] == 200
    status, paired = _post(base, "/v1/pair", {"code": operator_code, "client_name": "operator's"})
    assert status == 200, paired


def test_a_link_invitation_survives_the_operator_making_a_code(door, key):
    service, base = door
    status, linked = _post(base, "/v1/link", {"grant": _grant(service, key)})
    service.state.start_pairing()
    assert _post(base, "/v1/pair", {"code": linked["code"], "client_name": "laptop"})[0] == 200


def test_a_gateway_that_trusts_no_website_key_says_so(tmp_path, key):
    state = GatewayState(str(tmp_path / "plain"), version="test")
    service = GatewayService(state, backend=StandInBackend())
    _store_measurement(service)
    server = make_server(service, port=0, host="127.0.0.1")
    serving.owned(server, state)
    base = "http://127.0.0.1:%d" % server.server_address[1]
    status, refused = _post(base, "/v1/link", {"grant": _grant(service, key)})
    assert status == 403, refused


def test_every_refusal_is_recorded(door, key):
    service, base = door
    _post(base, "/v1/link", {"grant": "not.a.grant"})
    import io
    import os
    path = os.path.join(str(service.state.root), "audit.jsonl")
    with io.open(path, encoding="utf-8") as fh:
        lines = [json.loads(line) for line in fh if line.strip()]
    assert [line for line in lines if line.get("operation") == "link_website_user"
            and line.get("outcome") != "carried_out"], lines
