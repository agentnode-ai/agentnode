"""Production OAuth login bound to the REAL gateway accounts/devices/sessions/capabilities
/revocations (profile R3/R4, /goal #2), driven in-process through httpx ASGITransport.

A real :class:`GatewayState` + :class:`Sessions` are built in a tmp dir with one account and one
paired device holding RUN; a browser session cookie is opened. The suite then drives the full
browser consent flow end to end — GET /authorize (consent page) → POST /authorize (allow, CSRF)
→ code → /token → access token → tools/call — and the refusals: no session, suspended account,
device without RUN, withdrawn device, ended session, declined consent, bad CSRF, and the
``X-AgentNode-Login`` shim being structurally dead in production.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from urllib.parse import parse_qs, urlsplit

import httpx

from agentnode_sdk.access.sessions import Sessions
from agentnode_sdk.gateway import accounts as A
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.remote_mcp.accounts_login import GatewayLogin
from agentnode_sdk.remote_mcp.http_transport import RemoteMCPApp
from agentnode_sdk.remote_mcp.limits import Limits
from agentnode_sdk.remote_mcp.oauth_as import AuthorizationServer, pkce_s256_challenge
from agentnode_sdk.remote_mcp.prm import ResourceConfig
from agentnode_sdk.remote_mcp.tokens import TokenStore

HOST = "mcp.agentnode.net"
RESOURCE = "https://mcp.agentnode.net/mcp"
ISSUER = "https://mcp.agentnode.net"
ORIGIN_OK = "https://claude.ai"
COOKIE = "__Host-agentnode"
REDIRECT = "https://client.example/cb"


class FakeAdapter:
    """Minimal stand-in for UsabilityMCPAdapter.handle; approved so tools/call returns a result."""

    def __init__(self):
        self.approved = True

    def handle(self, msg):
        mid = msg.get("id")
        if msg.get("method") == "tools/list":
            return {"jsonrpc": "2.0", "id": mid, "result": {"tools": [
                {"name": "sequentialthinking", "description": "", "inputSchema": {"type": "object"}}]}}
        if msg.get("method") == "tools/call":
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "isError": not self.approved,
                "content": [{"type": "text", "text": "ok"}],
                "structuredContent": {"invocation_hash": "h", "audit_seq": 1}}}
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "nf"}}


def build_gateway(root, *, caps=("run", "read"), suspended=False):
    state = GatewayState(root, "test")
    acct = A.new_account_id()
    state.accounts.create(account_id=acct, name="Customer")
    if suspended:
        state.accounts.suspend(acct, because="testing")
    rec = {"client_id": "dev-1", "account_id": acct, "client_name": "Laptop",
           "issued_at": 1.0, "capabilities": list(caps)}
    with open(os.path.join(root, "tokens.json"), "w", encoding="utf-8") as fh:
        json.dump({"hash-dev-1": rec}, fh)
    sid, csrf = Sessions(root).open("dev-1", label="browser")
    return acct, sid, csrf


def make_login_app(root, **gw):
    acct, sid, csrf = build_gateway(root, **gw)
    store = TokenStore(b"k" * 32)
    resource = ResourceConfig(resource=RESOURCE, authorization_server=ISSUER)
    authz = AuthorizationServer(issuer=ISSUER, resource=RESOURCE, token_store=store,
                                preregistered={"test-client": (REDIRECT,)})
    adapter = FakeAdapter()
    adapter.approved = True
    gl = GatewayLogin(root)
    app = RemoteMCPApp(adapter=adapter, token_store=store, authz=authz, resource=resource,
                       allowed_hosts=(HOST,), allowed_origins=(ORIGIN_OK,),
                       login_provider=gl, live_authority=gl,
                       console_url="https://mcp.agentnode.net/console", limits=Limits())
    return app, store, acct, sid, csrf


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://%s" % HOST, follow_redirects=False)


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


AUTHZ_PARAMS = {
    "client_id": "test-client", "redirect_uri": REDIRECT, "response_type": "code",
    "code_challenge_method": "S256", "resource": RESOURCE, "state": "xyz",
}


# ---- full positive browser flow ----------------------------------------------------------- #

def test_browser_consent_code_pkce_token_and_call(tmp_path):
    app, store, acct, sid, csrf = make_login_app(str(tmp_path))
    verifier = "v" * 64
    params = dict(AUTHZ_PARAMS, code_challenge=pkce_s256_challenge(verifier))
    cookie = {"cookie": "%s=%s" % (COOKIE, sid)}

    async def go():
        async with client(app) as c:
            # GET /authorize -> consent page naming the account, no code yet
            r = await c.get("/authorize", params=params, headers=cookie)
            assert r.status_code == 200 and "text/html" in r.headers["content-type"]
            assert acct in r.text
            confirm = re.search(r'name="confirm" value="([^"]+)"', r.text).group(1)
            # POST /authorize allow -> 302 with the code
            form = dict(params, confirm=confirm, decision="allow")
            r2 = await c.post("/authorize", data=form, headers=cookie)
            assert r2.status_code == 302
            loc = r2.headers["location"]
            assert loc.startswith(REDIRECT)
            q = parse_qs(urlsplit(loc).query)
            assert q["state"] == ["xyz"]
            code = q["code"][0]
            # /token -> access token
            r3 = await c.post("/token", data={
                "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
                "code_verifier": verifier, "client_id": "test-client", "resource": RESOURCE})
            assert r3.status_code == 200
            token = r3.json()["access_token"]
            assert r3.json()["scope"] == "mcp:use"
            # the token drives a real tools/call
            r4 = await c.post("/mcp", headers={
                "content-type": "application/json", "mcp-protocol-version": "2026-07-28",
                "authorization": "Bearer %s" % token},
                content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                    "params": {"name": "sequentialthinking", "arguments": {}}}))
            assert r4.status_code == 200 and r4.json()["result"]["isError"] is False
            # the principal on the token is the real gateway account
            principal = store.verify(token, expected_audience=RESOURCE, required_scopes=("mcp:use",))
            assert principal.principal_id == acct

    run(go())


# ---- refusals / revocations --------------------------------------------------------------- #

def fclient(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://%s" % HOST, follow_redirects=True)


async def _bounces_to_signin(app, headers=None):
    """GET /authorize with no valid session must bounce (via the SameSite retry) to the
    same-origin /login sign-in page, and NEVER reach a consent page or issue a code."""
    async with fclient(app) as c:
        r = await c.get("/authorize", params=dict(AUTHZ_PARAMS, code_challenge="x"),
                        headers=headers or {})
    assert str(r.url).endswith("/login") or "/login?" in str(r.url), str(r.url)
    assert "Sign in to AgentNode" in r.text
    assert "Authorize access" not in r.text and "code=" not in str(r.url)
    return r


def test_no_session_bounces_to_signin(tmp_path):
    app, *_ = make_login_app(str(tmp_path))
    run(_bounces_to_signin(app))


def test_samesite_retry_then_login(tmp_path):
    # first hop (no _ss) is a same-origin redirect to pick up a SameSite=Strict cookie;
    # still no cookie -> second hop goes to /login carrying return_to=/authorize.
    app, *_ = make_login_app(str(tmp_path))

    async def go():
        async with client(app) as c:  # no auto-follow
            r1 = await c.get("/authorize", params=dict(AUTHZ_PARAMS, code_challenge="x"))
            assert r1.status_code == 302 and "_ss=1" in r1.headers["location"]
            r2 = await c.get(r1.headers["location"])
            assert r2.status_code == 302
            loc = r2.headers["location"]
            assert loc.startswith("/login?return_to=") and "%2Fauthorize" in loc

    run(go())


def test_login_page_sanitizes_return_to(tmp_path):
    app, *_ = make_login_app(str(tmp_path))

    async def go():
        async with client(app) as c:
            r = await c.get("/login", params={"return_to": "https://evil.example/x"})
            assert r.status_code == 200
            # open-redirect target rejected -> falls back to the same-origin /authorize
            assert '"/authorize"' in r.text or "'/authorize'" in r.text
            assert "evil.example" not in r.text

    run(go())


def test_x_agentnode_login_header_is_dead_in_production(tmp_path):
    # The stand shim header must do nothing: it still bounces to sign-in, never authenticates.
    app, *_ = make_login_app(str(tmp_path))
    run(_bounces_to_signin(app, headers={"x-agentnode-login": "stand-login-secret"}))


def test_suspended_account_cannot_authorize(tmp_path):
    app, store, acct, sid, csrf = make_login_app(str(tmp_path), suspended=True)
    run(_bounces_to_signin(app, headers={"cookie": "%s=%s" % (COOKIE, sid)}))


def test_device_without_run_is_not_permitted(tmp_path):
    app, store, acct, sid, csrf = make_login_app(str(tmp_path), caps=("read",))

    async def go():
        async with client(app) as c:
            r = await c.get("/authorize", params=dict(AUTHZ_PARAMS, code_challenge="x"),
                            headers={"cookie": "%s=%s" % (COOKIE, sid)})
            assert r.status_code == 403 and "RUN" in r.text

    run(go())


def test_ended_session_stops_authorizing(tmp_path):
    app, store, acct, sid, csrf = make_login_app(str(tmp_path))
    Sessions(str(tmp_path)).end(sid)   # the person logged out
    run(_bounces_to_signin(app, headers={"cookie": "%s=%s" % (COOKIE, sid)}))


def test_withdrawn_device_stops_authorizing(tmp_path):
    app, store, acct, sid, csrf = make_login_app(str(tmp_path))
    os.remove(os.path.join(str(tmp_path), "tokens.json"))   # device record gone
    run(_bounces_to_signin(app, headers={"cookie": "%s=%s" % (COOKIE, sid)}))


def test_declined_consent_issues_no_code(tmp_path):
    app, store, acct, sid, csrf = make_login_app(str(tmp_path))
    cookie = {"cookie": "%s=%s" % (COOKIE, sid)}
    params = dict(AUTHZ_PARAMS, code_challenge=pkce_s256_challenge("v" * 64))

    async def go():
        async with client(app) as c:
            r = await c.get("/authorize", params=params, headers=cookie)
            confirm = re.search(r'name="confirm" value="([^"]+)"', r.text).group(1)
            r2 = await c.post("/authorize", data=dict(params, confirm=confirm, decision="deny"),
                              headers=cookie)
            assert r2.status_code == 200 and "declined" in r2.text.lower()
            assert "location" not in r2.headers

    run(go())


def test_bad_csrf_cannot_confirm(tmp_path):
    app, store, acct, sid, csrf = make_login_app(str(tmp_path))
    cookie = {"cookie": "%s=%s" % (COOKIE, sid)}
    params = dict(AUTHZ_PARAMS, code_challenge=pkce_s256_challenge("v" * 64))

    async def go():
        async with client(app) as c:
            await c.get("/authorize", params=params, headers=cookie)
            r2 = await c.post("/authorize", data=dict(params, confirm="not-the-token",
                                                      decision="allow"), headers=cookie)
            assert r2.status_code == 403

    run(go())
