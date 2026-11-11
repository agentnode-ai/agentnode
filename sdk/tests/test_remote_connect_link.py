"""#37, the device half of "connect a gateway": `agentnode remote connect <address> --link <grant>`.

The website page (web/src/app/dashboard/gateway/page.tsx) signs a grant for the signed-in user and
shows exactly this command. The browser never talks to the gateway itself: the gateway sends no
CORS headers and is often on another machine than the browser, so the device that will use the
gateway is the one that redeems -- through the same transport checks as every other connect.

What the command must do: ask the gateway who it is, redeem the grant at `/v1/link` for a joining
invitation, check the invitation came from the gateway it asked, then pair with it as with any
code. A grant that is refused is said in the gateway's words and nothing is saved.
"""
from __future__ import annotations

import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agentnode_sdk.access import dispatch
from agentnode_sdk.cli import remote_commands
from agentnode_sdk.gateway import website_link as wl
from agentnode_sdk.gateway.connections import ConnectionStore
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.server import GatewayService, make_server
from tests import serving
from tests.test_em3c_gateway import StandInBackend, _store_measurement


@pytest.fixture()
def key():
    return Ed25519PrivateKey.generate()


@pytest.fixture()
def gateway(tmp_path, key):
    state = GatewayState(str(tmp_path / "state"), version="test")
    service = GatewayService(state, backend=StandInBackend())
    _store_measurement(service)
    wl.trust_website_key(state, key.public_key(), key_id="web-1")
    server = make_server(service, port=0, host="127.0.0.1")
    serving.owned(server, state)
    return service, "http://127.0.0.1:%d" % server.server_address[1]


def _grant(service, key, **kw):
    return wl.issue_grant(key, key_id="web-1", gateway_id=service.state.identity.gateway_id,
                          website_user="u-alice", **kw)


def _args(tmp_path, url, link="", code="", as_name="home"):
    class Args:
        pass
    a = Args()
    a.url, a.link, a.code, a.as_name, a.name = url, link, code, as_name, ""
    a.store = str(tmp_path / "client-store")
    return a


def test_a_link_connects_this_device_into_the_website_users_account(gateway, key, tmp_path, capsys):
    service, base = gateway
    args = _args(tmp_path, base, link=_grant(service, key))
    assert remote_commands.cmd_connect(args) == 0, capsys.readouterr().out
    saved = ConnectionStore(args.store).get("home")
    assert saved is not None and saved.gateway_id == service.state.identity.gateway_id
    device = dispatch.identify(service, saved.token)
    assert device.account_id.startswith("acct-")
    assert "Connected" in capsys.readouterr().out


def test_a_second_device_with_its_own_link_lands_in_the_same_account(gateway, key, tmp_path):
    service, base = gateway
    first = _args(tmp_path, base, link=_grant(service, key), as_name="laptop")
    second = _args(tmp_path, base, link=_grant(service, key), as_name="server")
    second.store = str(tmp_path / "other-client-store")
    assert remote_commands.cmd_connect(first) == 0
    assert remote_commands.cmd_connect(second) == 0
    a = dispatch.identify(service, ConnectionStore(first.store).get("laptop").token)
    b = dispatch.identify(service, ConnectionStore(second.store).get("server").token)
    assert a.account_id == b.account_id


def test_a_used_link_is_refused_and_nothing_is_saved(gateway, key, tmp_path, capsys):
    service, base = gateway
    grant = _grant(service, key)
    assert remote_commands.cmd_connect(_args(tmp_path, base, link=grant)) == 0
    again = _args(tmp_path, base, link=grant, as_name="again")
    again.store = str(tmp_path / "second-store")
    capsys.readouterr()
    assert remote_commands.cmd_connect(again) == 1
    said = capsys.readouterr().out
    assert "already used" in said, said
    assert ConnectionStore(again.store).get("again") is None


def test_an_expired_link_is_refused_and_nothing_is_saved(gateway, key, tmp_path, capsys):
    service, base = gateway
    args = _args(tmp_path, base, link=_grant(service, key, now=time.time() - 600))
    assert remote_commands.cmd_connect(args) == 1
    assert "expired" in capsys.readouterr().out
    assert ConnectionStore(args.store).get("home") is None


def test_a_link_and_a_code_together_are_refused_before_anything_is_contacted(gateway, key, tmp_path,
                                                                            monkeypatch, capsys):
    service, base = gateway
    from agentnode_sdk.gateway import client as gateway_client

    monkeypatch.setattr(gateway_client, "hello",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("contacted")))
    args = _args(tmp_path, base, link=_grant(service, key), code="ABCD-EFGH-IJKL")
    assert remote_commands.cmd_connect(args) == 2
    assert "either" in capsys.readouterr().out.lower()


def test_the_gateway_status_shows_the_whole_id_the_website_asks_for(tmp_path, capsys):
    """The website page asks for the gateway's id; `gateway status` is where the page sends people
    to find it, so it has to show all of it, not the twelve characters it abbreviates to."""
    from agentnode_sdk.cli import gateway_commands

    state = GatewayState(str(tmp_path / "status-state"), version="test")
    service = GatewayService(state, backend=StandInBackend())
    _store_measurement(service)
    full = state.identity.gateway_id
    state.close()

    class Args:
        dir = str(tmp_path / "status-state")

    gateway_commands.cmd_status(Args())
    said = capsys.readouterr().out
    assert "Link id (for the website): %s" % full in said, said
