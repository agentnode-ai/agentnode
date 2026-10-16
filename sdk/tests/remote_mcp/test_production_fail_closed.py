"""Fail-closed properties of the PRODUCTION path (/goal corrections #1–#5).

  * production can never select a local worker; it has no such parameter and missing
    remote/mTLS/keyring/CRL/floor/tombstones artifacts fail the start;
  * already-issued tokens obey the LIVE revocation state on the NEXT call — account suspended,
    device withdrawn, RUN taken away, or token explicitly revoked all refuse immediately;
  * a production token must be device-bound (an unbound one is refused);
  * the production entrypoint starts only build_production_app, with no local fallback;
  * principal_resolver and login_provider are mutually exclusive, so the X-AgentNode-Login
    stand shim can never coexist with production login.

Driven in-process; the live authority check runs BEFORE the adapter, so a FakeAdapter is fine.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os

import pytest

from agentnode_sdk.access.sessions import Sessions
from agentnode_sdk.gateway import accounts as A
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.remote_mcp import production as production_entry
from agentnode_sdk.remote_mcp.accounts_login import GatewayLogin
from agentnode_sdk.remote_mcp.http_transport import RemoteMCPApp
from agentnode_sdk.remote_mcp.limits import Limits
from agentnode_sdk.remote_mcp.oauth_as import AuthorizationServer
from agentnode_sdk.remote_mcp.prm import ResourceConfig
from agentnode_sdk.remote_mcp.server import build_production_app, _build_remote_worker
from agentnode_sdk.remote_mcp.tokens import TokenStore

HOST = "mcp.agentnode.net"
RESOURCE = "https://mcp.agentnode.net/mcp"
ISSUER = "https://mcp.agentnode.net"


class FakeAdapter:
    def handle(self, msg):
        mid = msg.get("id")
        if msg.get("method") == "tools/call":
            return {"jsonrpc": "2.0", "id": mid, "result": {"isError": False,
                    "content": [{"type": "text", "text": "ok"}], "structuredContent": {}}}
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": []}}


def tokens_json(root, caps=("run", "read"), account_id="acct-x", client_id="dev-1"):
    rec = {"client_id": client_id, "account_id": account_id, "client_name": "Laptop",
           "issued_at": 1.0, "capabilities": list(caps)}
    with open(os.path.join(root, "tokens.json"), "w", encoding="utf-8") as fh:
        json.dump({"hash-dev-1": rec}, fh)


def prod_style_app(root, *, caps=("run", "read")):
    """A production-STYLE app: real gateway + GatewayLogin as the live authority, FakeAdapter."""
    state = GatewayState(root, "test")
    acct = A.new_account_id()
    state.accounts.create(account_id=acct, name="Cust")
    tokens_json(root, caps=caps, account_id=acct)
    store = TokenStore(b"k" * 32)
    gl = GatewayLogin(root)
    app = RemoteMCPApp(adapter=FakeAdapter(), token_store=store,
                       authz=AuthorizationServer(issuer=ISSUER, resource=RESOURCE, token_store=store),
                       resource=ResourceConfig(resource=RESOURCE, authorization_server=ISSUER),
                       allowed_hosts=(HOST,), login_provider=gl, live_authority=gl, limits=Limits())
    token = store.mint(principal_id=acct, audience=RESOURCE, scopes=("mcp:use",),
                       issuance_kind="oauth", ttl_seconds=600,
                       device_or_connection_id="dev-1").plaintext
    return app, store, acct, token


def call(app, token, *, mid=1):
    async def go():
        import httpx
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://%s" % HOST) as c:
            return await c.post("/mcp", headers={
                "content-type": "application/json",
                "accept": "application/json, text/event-stream",
                "mcp-protocol-version": "2026-07-28", "mcp-method": "tools/call",
                "mcp-name": "sequentialthinking", "authorization": "Bearer %s" % token},
                content=json.dumps({"jsonrpc": "2.0", "id": mid, "method": "tools/call",
                                    "params": {"name": "sequentialthinking", "arguments": {},
                                               "_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                                                         "io.modelcontextprotocol/clientCapabilities": {}}}}))
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(go())
    finally:
        loop.close()


# ---- #1 / #5: production can never select a local worker --------------------------------- #

def test_build_production_app_has_no_local_worker_param():
    assert "local_worker" not in inspect.signature(build_production_app).parameters


def test_server_module_resolves_remote_adapter_symbols():
    # _build_remote_adapter references these at runtime; a missing import only shows up when a
    # real stand is built, so pin the names here (regression: NameError UsabilityMCPAdapter).
    from agentnode_sdk.remote_mcp import server as S
    assert hasattr(S, "UsabilityMCPAdapter") and hasattr(S, "build_from_stand")


def test_production_entrypoint_has_no_local_fallback():
    # Check the CODE, not the module docstring (which names these words deliberately in prose).
    body = inspect.getsource(production_entry).split('"""', 2)[2]
    assert "local-worker" not in body and "local_worker" not in body
    assert "LocalWorker" not in body and "build_app" not in body and "build_from_stand" not in body
    assert "build_production_app" in body
    # and the entrypoint imported only the production builder, never the stand/local one
    assert not hasattr(production_entry, "build_app")
    assert not hasattr(production_entry, "LocalWorker")


def test_missing_worker_json_fails_the_start(tmp_path):
    with pytest.raises(RuntimeError, match="worker.json"):
        _build_remote_worker(str(tmp_path))


def test_incomplete_worker_artifacts_fail_the_start(tmp_path):
    # present file but missing mandatory keys
    with open(os.path.join(str(tmp_path), "worker.json"), "w", encoding="utf-8") as fh:
        json.dump({"worker_address": "tcps://10.0.0.9:8443"}, fh)
    with pytest.raises(RuntimeError, match="mandatory artifacts"):
        _build_remote_worker(str(tmp_path))


def test_worker_artifact_files_must_exist(tmp_path):
    cfg = {"worker_address": "tcps://10.0.0.9:8443", "deployment": "dep", "accept": ["w1"],
           "keyring": str(tmp_path / "keyring"),
           "tls": {"certificate": str(tmp_path / "c"), "key": str(tmp_path / "k"),
                   "anchor": str(tmp_path / "a"), "revocation_list": str(tmp_path / "crl"),
                   "floor": str(tmp_path / "floor"), "identity_tombstones": str(tmp_path / "tomb")}}
    with open(os.path.join(str(tmp_path), "worker.json"), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh)
    with pytest.raises(RuntimeError, match="do not exist"):
        _build_remote_worker(str(tmp_path))


# ---- #2 / #3: already-issued tokens obey the live revocation state ------------------------ #

def test_token_works_then_account_suspended_is_refused(tmp_path):
    app, store, acct, token = prod_style_app(str(tmp_path))
    assert call(app, token).json()["result"]["isError"] is False
    GatewayState(str(tmp_path), "test").accounts.suspend(acct, because="stopped")
    assert call(app, token, mid=2).status_code == 403


def test_token_works_then_device_withdrawn_is_refused(tmp_path):
    app, store, acct, token = prod_style_app(str(tmp_path))
    assert call(app, token).json()["result"]["isError"] is False
    os.remove(os.path.join(str(tmp_path), "tokens.json"))   # device record gone
    assert call(app, token, mid=2).status_code == 403


def test_token_works_then_run_withdrawn_is_refused(tmp_path):
    app, store, acct, token = prod_style_app(str(tmp_path))
    assert call(app, token).json()["result"]["isError"] is False
    tokens_json(str(tmp_path), caps=("read",), account_id=acct)   # RUN taken away
    assert call(app, token, mid=2).status_code == 403


def test_explicit_token_revocation_is_refused(tmp_path):
    app, store, acct, token = prod_style_app(str(tmp_path))
    assert call(app, token).json()["result"]["isError"] is False
    store.revoke(token.split("_")[1])
    assert call(app, token, mid=2).status_code == 401   # revoked_token at the verifier


def test_unbound_token_is_refused_in_production(tmp_path):
    app, store, acct, token = prod_style_app(str(tmp_path))
    unbound = store.mint(principal_id=acct, audience=RESOURCE, scopes=("mcp:use",),
                         issuance_kind="oauth", ttl_seconds=600).plaintext  # no device binding
    assert call(app, unbound).status_code == 403


# ---- #4: shim / mutual exclusion ---------------------------------------------------------- #

def test_resolver_and_login_provider_are_mutually_exclusive(tmp_path):
    state = GatewayState(str(tmp_path), "test")
    store = TokenStore(b"k" * 32)
    with pytest.raises(ValueError, match="mutually exclusive"):
        RemoteMCPApp(adapter=FakeAdapter(), token_store=store,
                     authz=AuthorizationServer(issuer=ISSUER, resource=RESOURCE, token_store=store),
                     resource=ResourceConfig(resource=RESOURCE, authorization_server=ISSUER),
                     allowed_hosts=(HOST,), principal_resolver=lambda s, h: "x",
                     login_provider=GatewayLogin(str(tmp_path)))
