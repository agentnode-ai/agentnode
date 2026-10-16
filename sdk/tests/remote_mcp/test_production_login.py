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
from urllib.parse import parse_qs, parse_qsl, urlsplit

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


def make_login_app(root, *, max_continuations=1024, **gw):
    acct, sid, csrf = build_gateway(root, **gw)
    store = TokenStore(b"k" * 32)
    resource = ResourceConfig(resource=RESOURCE, authorization_server=ISSUER)
    authz = AuthorizationServer(issuer=ISSUER, resource=RESOURCE, token_store=store,
                                preregistered={"test-client": (REDIRECT,),
                                               "q-client": ("https://client.example/cb?foo=bar",)})
    adapter = FakeAdapter()
    adapter.approved = True
    gl = GatewayLogin(root)
    app = RemoteMCPApp(adapter=adapter, token_store=store, authz=authz, resource=resource,
                       allowed_hosts=(HOST,), allowed_origins=(ORIGIN_OK,),
                       login_provider=gl, live_authority=gl, max_continuations=max_continuations,
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
                "content-type": "application/json",
                "accept": "application/json, text/event-stream",
                "mcp-protocol-version": "2026-07-28", "mcp-method": "tools/call",
                "mcp-name": "sequentialthinking",
                "authorization": "Bearer %s" % token},
                content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                    "params": {"name": "sequentialthinking", "arguments": {},
                                               "_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                                                         "io.modelcontextprotocol/clientCapabilities": {}}}}))
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


def _mint_handle(app):
    """Drive a valid-but-signed-out GET /authorize and return the continuation handle the server
    stashed (the 302 target is /authorize?resume=<handle>, server-generated — never a free URL)."""
    async def go():
        async with client(app) as c:
            r = await c.get("/authorize", params=dict(AUTHZ_PARAMS,
                                                      code_challenge=pkce_s256_challenge("v" * 64)))
            assert r.status_code == 302
            loc = r.headers["location"]
            assert loc.startswith("/authorize?resume=")
            return loc.split("resume=", 1)[1]
    return run(go())


def test_valid_signed_out_mints_handle_then_login(tmp_path):
    app, *_ = make_login_app(str(tmp_path))
    handle = _mint_handle(app)
    assert re.fullmatch(r"[A-Za-z0-9_-]{16,128}", handle)

    async def go():
        async with client(app) as c:
            r = await c.get("/authorize", params={"resume": handle})   # not signed in
            assert r.status_code == 302 and r.headers["location"] == "/login?k=" + handle
            r2 = await c.get("/login", params={"k": handle})
            assert r2.status_code == 200 and "Sign in to AgentNode" in r2.text
            # the page carries ONLY the handle and resumes via a server path, no free URL
            assert ("/authorize?resume='+encodeURIComponent(k)" in r2.text
                    or "resume='+encodeURIComponent(k)" in r2.text)

    run(go())


def test_login_requires_a_handle_and_ignores_return_to(tmp_path):
    app, *_ = make_login_app(str(tmp_path))

    async def go():
        async with client(app) as c:
            # no handle -> rejected, never a sign-in prompt
            r = await c.get("/login")
            assert r.status_code == 400 and "Sign in to AgentNode" not in r.text
            # a free return_to is ignored entirely (not reflected, no open redirect)
            r2 = await c.get("/login", params={"return_to": "https://evil.example/x", "k": "bad"})
            assert r2.status_code == 400 and "evil.example" not in r2.text

    run(go())


def test_invalid_oauth_request_rejected_before_login(tmp_path):
    app, *_ = make_login_app(str(tmp_path))

    async def go():
        async with client(app) as c:
            for bad in (dict(AUTHZ_PARAMS, client_id="not-registered", code_challenge="x"),
                        dict(AUTHZ_PARAMS, redirect_uri="https://evil.example/cb", code_challenge="x"),
                        dict(AUTHZ_PARAMS, response_type="token", code_challenge="x"),
                        dict(AUTHZ_PARAMS, code_challenge_method="plain", code_challenge="x"),
                        dict(AUTHZ_PARAMS, resource="https://other.example/mcp", code_challenge="x"),
                        {k: v for k, v in dict(AUTHZ_PARAMS, code_challenge="x").items()
                         if k != "code_challenge"}):
                r = await c.get("/authorize", params=bad)
                # a 4xx error, NOT a 302 bounce to login/pairing
                assert 400 <= r.status_code < 500, (bad, r.status_code)
                assert r.status_code != 302 and "/login" not in r.headers.get("location", "")

    run(go())


def test_unknown_and_reused_and_expired_handle(tmp_path):
    app, store, acct, sid, csrf = make_login_app(str(tmp_path))
    cookie = {"cookie": "%s=%s" % (COOKIE, sid)}
    handle = _mint_handle(app)                                    # minted outside the loop
    handle2 = _mint_handle(app)
    app._continuations[handle2] = (app._continuations[handle2][0], 0.0)  # force-expire

    async def go():
        async with client(app) as c:
            # unknown handle
            r = await c.get("/authorize", params={"resume": "Z" * 40})
            assert r.status_code == 400 and "expired" in r.text.lower()
            r = await c.get("/login", params={"k": "Z" * 40})
            assert r.status_code == 400
            # signed in -> consent consumes the single-use handle
            r2 = await c.get("/authorize", params={"resume": handle}, headers=cookie)
            assert r2.status_code == 200 and "Authorize access" in r2.text
            # reused -> gone (single use)
            r3 = await c.get("/authorize", params={"resume": handle}, headers=cookie)
            assert r3.status_code == 400 and "expired" in r3.text.lower()
            # expired -> rejected
            r4 = await c.get("/authorize", params={"resume": handle2}, headers=cookie)
            assert r4.status_code == 400

    run(go())


def test_handle_rejects_injection_charset(tmp_path):
    app, *_ = make_login_app(str(tmp_path))
    evil = "</script><script>alert(1)</script>"

    async def go():
        async with client(app) as c:
            r = await c.get("/login", params={"k": evil})
            assert r.status_code == 400
            assert "<script>alert" not in r.text and evil not in r.text
            r2 = await c.get("/authorize", params={"resume": evil})
            assert r2.status_code == 400

    run(go())


def test_consent_escapes_injection_in_state(tmp_path):
    # a signed-in request whose state carries HTML/script + a unicode line separator must be
    # escaped into the consent page (attribute context), never reflected raw into any script.
    app, store, acct, sid, csrf = make_login_app(str(tmp_path))
    cookie = {"cookie": "%s=%s" % (COOKIE, sid)}
    evil_state = "\"><script>alert(1)</script> x"
    params = dict(AUTHZ_PARAMS, code_challenge=pkce_s256_challenge("v" * 64), state=evil_state)

    async def go():
        async with client(app) as c:
            r = await c.get("/authorize", params=params, headers=cookie)
            assert r.status_code == 200 and "Authorize access" in r.text
            assert "<script>alert(1)" not in r.text        # escaped, not executable
            assert " " not in r.text or "&" in r.text  # no raw line separator into a script

    run(go())


def test_security_headers_on_login_and_consent(tmp_path):
    app, store, acct, sid, csrf = make_login_app(str(tmp_path))
    cookie = {"cookie": "%s=%s" % (COOKIE, sid)}
    handle = _mint_handle(app)

    def check(h):
        csp = h.get("content-security-policy", "")
        assert "frame-ancestors 'none'" in csp and "base-uri 'none'" in csp
        assert "form-action 'self'" in csp
        assert h.get("x-frame-options") == "DENY"
        assert h.get("referrer-policy") == "no-referrer"
        assert h.get("x-content-type-options") == "nosniff"
        assert h.get("cache-control") == "no-store"

    async def go():
        async with client(app) as c:
            check((await c.get("/login", params={"k": handle})).headers)        # login page
            check((await c.get("/authorize", params={"resume": handle},
                               headers=cookie)).headers)                         # consent page

    run(go())


def test_oversize_authorize_query_rejected(tmp_path):
    app, *_ = make_login_app(str(tmp_path))

    async def go():
        async with client(app) as c:
            r = await c.get("/authorize", params=dict(AUTHZ_PARAMS, code_challenge="x",
                                                      state="A" * 5000))
            assert r.status_code == 414

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


# ---- OAuth redirect encoding + duplicate/fragment rejection + store cap ------------------- #

def test_authorize_encodes_state_and_preserves_query():
    store = TokenStore(b"k" * 32)
    authz = AuthorizationServer(issuer=ISSUER, resource=RESOURCE, token_store=store,
                                preregistered={"q": ("https://client.example/cb?foo=bar",)})
    tricky = "a&code=INJECTED=+ %#ü z"   # &, =, +, space, %, #, unicode, line-sep
    status, headers, _ = authz.authorize({
        "client_id": "q", "redirect_uri": "https://client.example/cb?foo=bar",
        "response_type": "code", "code_challenge": pkce_s256_challenge("v" * 64),
        "code_challenge_method": "S256", "resource": RESOURCE, "state": tricky},
        principal_id="acct-x")
    assert status == 302
    loc = dict(headers)["location"]
    assert "#" not in loc                                   # no fragment introduced
    q = parse_qsl(urlsplit(loc).query, keep_blank_values=True)
    assert [k for k, _ in q].count("code") == 1             # exactly one server code
    assert ("foo", "bar") in q                              # existing query preserved
    assert [v for k, v in q if k == "state"] == [tricky]    # state round-trips byte-exactly
    code = [v for k, v in q if k == "code"][0]
    assert code != "INJECTED" and len(code) >= 20           # server-generated, not injected


def test_redirect_query_preserved_byte_faithful():
    # the registered redirect_uri's query must survive BYTE-FOR-BYTE in Location (no parse_qsl+
    # urlencode canonicalization of %20/+/%2B/%2F, no reorder, no dropped empties, no folded dups).
    store = TokenStore(b"k" * 32)
    raw = "a=%20&b=+&c=%2B&d=%2F&e=&f=1&f=2&z=keep"
    redirect = "https://client.example/cb?" + raw
    authz = AuthorizationServer(issuer=ISSUER, resource=RESOURCE, token_store=store,
                                preregistered={"c": (redirect,)})
    status, headers, _ = authz.authorize({
        "client_id": "c", "redirect_uri": redirect, "response_type": "code",
        "code_challenge": pkce_s256_challenge("v" * 64), "code_challenge_method": "S256",
        "resource": RESOURCE, "state": "s pace+%2F"}, principal_id="a")
    assert status == 302
    q = urlsplit(dict(headers)["location"]).query
    assert q.startswith(raw + "&")                  # original bytes preserved as an exact prefix
    assert q.count("code=") == 1                     # exactly one server-generated code appended
    pairs = parse_qsl(q, keep_blank_values=True)
    assert [v for k, v in pairs if k == "state"] == ["s pace+%2F"]   # state round-trips exactly
    assert [v for k, v in pairs if k == "f"] == ["1", "2"]           # duplicate ordinary kept


def test_empty_state_round_trips_and_absent_state_omitted():
    store = TokenStore(b"k" * 32)
    authz = AuthorizationServer(issuer=ISSUER, resource=RESOURCE, token_store=store,
                                preregistered={"c": ("https://client.example/cb",)})
    base = {"client_id": "c", "redirect_uri": "https://client.example/cb", "response_type": "code",
            "code_challenge": pkce_s256_challenge("v" * 64), "code_challenge_method": "S256",
            "resource": RESOURCE}
    # present-but-empty state echoes state=
    _, h1, _ = authz.authorize(dict(base, state=""), principal_id="a")
    q1 = parse_qsl(urlsplit(dict(h1)["location"]).query, keep_blank_values=True)
    assert ("state", "") in q1
    # absent state is NOT fabricated
    _, h2, _ = authz.authorize(dict(base), principal_id="a")
    q2 = parse_qsl(urlsplit(dict(h2)["location"]).query, keep_blank_values=True)
    assert "state" not in [k for k, _ in q2]


def test_duplicate_security_param_rejected_before_login(tmp_path):
    app, *_ = make_login_app(str(tmp_path))
    base = dict(AUTHZ_PARAMS, code_challenge="x")

    async def go():
        async with client(app) as c:
            for dupkey in ("client_id", "redirect_uri", "response_type", "resource", "state"):
                pairs = list(base.items()) + [(dupkey, "second")]   # a repeated security param
                r = await c.get("/authorize", params=pairs)
                assert r.status_code == 400 and "duplicate" in r.text.lower(), dupkey
                assert "/login" not in r.headers.get("location", "")

    run(go())


def test_fragment_redirect_uri_rejected_before_login(tmp_path):
    app, *_ = make_login_app(str(tmp_path))

    async def go():
        async with client(app) as c:
            r = await c.get("/authorize", params=dict(AUTHZ_PARAMS, code_challenge="x",
                                                      redirect_uri=REDIRECT + "#frag"))
            assert r.status_code == 400 and "fragment" in r.text.lower()
            assert r.status_code != 302

    run(go())


def test_continuation_store_is_capped_and_sweeps(tmp_path):
    app, *_ = make_login_app(str(tmp_path), max_continuations=3)

    async def go():
        async with client(app) as c:
            locs = []
            for _ in range(3):
                r = await c.get("/authorize", params=dict(AUTHZ_PARAMS,
                                                          code_challenge=pkce_s256_challenge("v" * 64)))
                assert r.status_code == 302 and "resume=" in r.headers["location"]
                locs.append(r.headers["location"])
            assert len(app._continuations) == 3
            # store full -> structured refusal with a retry hint, no eviction of valid handles
            r4 = await c.get("/authorize", params=dict(AUTHZ_PARAMS,
                                                       code_challenge=pkce_s256_challenge("v" * 64)))
            assert r4.status_code == 503 and r4.headers.get("retry-after")
            assert len(app._continuations) == 3                       # unchanged, none displaced
            for loc in locs:                                          # earlier handles still valid
                h = loc.split("resume=", 1)[1]
                assert app._peek_continuation(h) is not None

    run(go())


def test_expired_continuations_swept_on_insert(tmp_path):
    app, *_ = make_login_app(str(tmp_path), max_continuations=1000)

    async def go():
        async with client(app) as c:
            for _ in range(5):
                await c.get("/authorize", params=dict(AUTHZ_PARAMS,
                                                      code_challenge=pkce_s256_challenge("v" * 64)))
            # force all existing to be expired, then one more insert must sweep them away
            for hnd in list(app._continuations):
                app._continuations[hnd] = (app._continuations[hnd][0], 0.0)
            await c.get("/authorize", params=dict(AUTHZ_PARAMS,
                                                  code_challenge=pkce_s256_challenge("v" * 64)))
            assert len(app._continuations) == 1   # expired ones swept, not accumulated

    run(go())
