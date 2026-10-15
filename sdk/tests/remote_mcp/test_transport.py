"""Loopback positive + negative suite for the remote MCP transport (profile R1-R13, R16).

Driven in-process through httpx ASGITransport so there is no socket, no pytest-asyncio and
no network. A FakeAdapter stands in for the frozen UsabilityMCPAdapter so transport behavior
is deterministic; the REAL adapter + real worker are exercised on the host stand (R17/R9/R14)
and by the counter-check harness. Every refusal here is a guard the counter-check harness
reverts to prove it goes red.
"""
from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qs, urlsplit

import httpx

from agentnode_sdk.remote_mcp.http_transport import RemoteMCPApp
from agentnode_sdk.remote_mcp.limits import Limits
from agentnode_sdk.remote_mcp.oauth_as import AuthorizationServer, pkce_s256_challenge
from agentnode_sdk.remote_mcp.prm import ResourceConfig
from agentnode_sdk.remote_mcp.tokens import TokenStore

HOST = "mcp.agentnode.net"
RESOURCE = "https://mcp.agentnode.net/mcp"
ISSUER = "https://mcp.agentnode.net"
ORIGIN_OK = "https://claude.ai"


class FakeAdapter:
    """Mimics UsabilityMCPAdapter.handle: full JSON-RPC envelopes; approval_required unless
    an approval was deposited out of band. It has NO approve method -- the transport cannot
    make it self-approve."""

    def __init__(self):
        self.approved = False
        self.calls = []

    def handle(self, msg):
        mid = msg.get("id")
        m = msg.get("method")
        if m == "tools/list":
            return {"jsonrpc": "2.0", "id": mid, "result": {"tools": [
                {"name": "sequentialthinking", "description": "", "inputSchema": {"type": "object"}}]}}
        if m == "tools/call":
            self.calls.append(msg)
            if not self.approved:
                return {"jsonrpc": "2.0", "id": mid, "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": "approval required. Next step:\n  python -m ... approve"}],
                    "structuredContent": {"status": "approval_required", "invocation_hash": "h",
                                          "next_step": "python -m ... approve"}}}
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "isError": False, "content": [{"type": "text", "text": "ok"}],
                "structuredContent": {"call": {"content": [{"type": "text", "text": "ok"}]},
                                      "invocation_hash": "h", "audit_seq": 1}}}
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "nf"}}


def make_app(*, allow_x_access_token=False, server_key=b"k" * 32, cimd_fetcher=None, enable_dcr=False):
    store = TokenStore(server_key)
    resource = ResourceConfig(resource=RESOURCE, authorization_server=ISSUER)
    kw = {}
    if cimd_fetcher is not None:
        kw["cimd_fetcher"] = cimd_fetcher
    authz = AuthorizationServer(issuer=ISSUER, resource=RESOURCE, token_store=store,
                                preregistered={"test-client": ("https://client.example/cb",)},
                                enable_dcr=enable_dcr, **kw)
    adapter = FakeAdapter()
    app = RemoteMCPApp(adapter=adapter, token_store=store, authz=authz, resource=resource,
                       allowed_hosts=(HOST,), allowed_origins=(ORIGIN_OK,),
                       principal_resolver=lambda scope, h: h.get("x-test-principal"),
                       limits=Limits(), allow_x_access_token=allow_x_access_token)
    return app, store, adapter, authz


def mint(store, *, aud=RESOURCE, scopes=("mcp:use",), kind="oauth", ttl=600, principal="u1"):
    return store.mint(principal_id=principal, audience=aud, scopes=scopes,
                      issuance_kind=kind, ttl_seconds=ttl).plaintext


def drive(app, method, path, *, headers=None, content=None):
    async def _go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://%s" % HOST) as c:
            return await c.request(method, path, headers=headers or {}, content=content)
    return asyncio.run(_go())


def rpc(method, *, mid=1, params=None):
    msg = {"jsonrpc": "2.0", "id": mid, "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg).encode()


def mcp_headers(token=None, *, version="2026-07-28", extra=None):
    h = {"content-type": "application/json", "accept": "application/json"}
    if version:
        h["mcp-protocol-version"] = version
    if token:
        h["authorization"] = "Bearer %s" % token
    if extra:
        h.update(extra)
    return h


# ---- discovery ------------------------------------------------------------------------ #

def test_prm_discovery():
    app, *_ = make_app()
    r = drive(app, "GET", "/.well-known/oauth-protected-resource")
    assert r.status_code == 200
    doc = r.json()
    assert doc["resource"] == RESOURCE and doc["authorization_servers"] == [ISSUER]
    assert doc["bearer_methods_supported"] == ["header"]


def test_as_metadata_advertises_cimd_and_s256():
    app, *_ = make_app()
    r = drive(app, "GET", "/.well-known/oauth-authorization-server")
    doc = r.json()
    assert doc["client_id_metadata_document_supported"] is True
    assert doc["code_challenge_methods_supported"] == ["S256"]
    assert doc["grant_types_supported"] == ["authorization_code"]
    assert "registration_endpoint" not in doc  # DCR off by default


# ---- OAuth 2.1 auth-code + PKCE happy path -------------------------------------------- #

def test_oauth_authcode_pkce_then_use_token():
    app, store, adapter, authz = make_app()
    verifier = "verifier-abc123-verifier-abc123-xyz"
    chal = pkce_s256_challenge(verifier)
    q = ("client_id=test-client&redirect_uri=https://client.example/cb&response_type=code"
         "&code_challenge=%s&code_challenge_method=S256&resource=%s&state=S1" % (chal, RESOURCE))
    r = drive(app, "GET", "/authorize?" + q, headers={"x-test-principal": "u1"})
    assert r.status_code == 302, r.text
    code = parse_qs(urlsplit(r.headers["location"]).query)["code"][0]
    form = ("grant_type=authorization_code&code=%s&redirect_uri=https://client.example/cb"
            "&code_verifier=%s&client_id=test-client&resource=%s" % (code, verifier, RESOURCE))
    r2 = drive(app, "POST", "/token",
               headers={"content-type": "application/x-www-form-urlencoded"}, content=form.encode())
    assert r2.status_code == 200, r2.text
    token = r2.json()["access_token"]
    r3 = drive(app, "POST", "/mcp", headers=mcp_headers(token), content=rpc("tools/list"))
    assert r3.status_code == 200
    assert r3.json()["result"]["tools"][0]["name"] == "sequentialthinking"


def test_token_single_use_code():
    app, store, adapter, authz = make_app()
    verifier = "v" * 40
    chal = pkce_s256_challenge(verifier)
    q = ("client_id=test-client&redirect_uri=https://client.example/cb&response_type=code"
         "&code_challenge=%s&code_challenge_method=S256&resource=%s" % (chal, RESOURCE))
    code = parse_qs(urlsplit(drive(app, "GET", "/authorize?" + q,
                                   headers={"x-test-principal": "u1"}).headers["location"]).query)["code"][0]
    form = ("grant_type=authorization_code&code=%s&redirect_uri=https://client.example/cb"
            "&code_verifier=%s&client_id=test-client&resource=%s" % (code, verifier, RESOURCE))
    h = {"content-type": "application/x-www-form-urlencoded"}
    assert drive(app, "POST", "/token", headers=h, content=form.encode()).status_code == 200
    # replaying the same code must fail (single-use)
    assert drive(app, "POST", "/token", headers=h, content=form.encode()).json()["error"] == "invalid_grant"


def test_pkce_wrong_verifier_rejected():
    app, store, adapter, authz = make_app()
    chal = pkce_s256_challenge("right-verifier-right-verifier-right")
    q = ("client_id=test-client&redirect_uri=https://client.example/cb&response_type=code"
         "&code_challenge=%s&code_challenge_method=S256&resource=%s" % (chal, RESOURCE))
    code = parse_qs(urlsplit(drive(app, "GET", "/authorize?" + q,
                                   headers={"x-test-principal": "u1"}).headers["location"]).query)["code"][0]
    form = ("grant_type=authorization_code&code=%s&redirect_uri=https://client.example/cb"
            "&code_verifier=WRONG-verifier-WRONG-verifier-WRONG&client_id=test-client&resource=%s"
            % (code, RESOURCE))
    r = drive(app, "POST", "/token", headers={"content-type": "application/x-www-form-urlencoded"},
              content=form.encode())
    assert r.json()["error"] == "invalid_grant"


# ---- /mcp positive -------------------------------------------------------------------- #

def test_tools_list_authorized():
    app, store, *_ = make_app()
    r = drive(app, "POST", "/mcp", headers=mcp_headers(mint(store)), content=rpc("tools/list"))
    assert r.status_code == 200 and r.json()["result"]["tools"]


def test_unapproved_tools_call_returns_approval_required():
    app, store, adapter, _ = make_app()
    r = drive(app, "POST", "/mcp", headers=mcp_headers(mint(store)),
              content=rpc("tools/call", params={"name": "sequentialthinking", "arguments": {}}))
    assert r.status_code == 200
    res = r.json()["result"]
    assert res["isError"] is True
    assert res["structuredContent"]["status"] == "approval_required"
    assert adapter.calls, "transport must delegate to the frozen adapter"


def test_initialize_negotiates_primary_version():
    app, store, *_ = make_app()
    r = drive(app, "POST", "/mcp", headers=mcp_headers(mint(store)),
              content=rpc("initialize", params={"protocolVersion": "2026-07-28"}))
    assert r.json()["result"]["protocolVersion"] == "2026-07-28"


# ---- auth refusals (R7, R8) ----------------------------------------------------------- #

def test_no_auth_401_with_www_authenticate():
    app, *_ = make_app()
    r = drive(app, "POST", "/mcp", headers=mcp_headers(), content=rpc("tools/list"))
    assert r.status_code == 401
    assert "resource_metadata=" in r.headers.get("www-authenticate", "")


def test_bad_token_401():
    app, *_ = make_app()
    r = drive(app, "POST", "/mcp", headers=mcp_headers("ant_deadbeefdeadbeef_nope"),
              content=rpc("tools/list"))
    assert r.status_code == 401


def test_wrong_secret_with_real_id_401():
    app, store, *_ = make_app()
    tok = mint(store)
    forged = "ant_%s_wrongsecretwrongsecretwrong" % tok.split("_", 2)[1]
    r = drive(app, "POST", "/mcp", headers=mcp_headers(forged), content=rpc("tools/list"))
    assert r.status_code == 401


def test_expired_token_401():
    app, store, *_ = make_app()
    tok = mint(store, ttl=1)
    rec = store.get(tok.split("_", 2)[1])
    rec.expires_at = 1  # in the past
    r = drive(app, "POST", "/mcp", headers=mcp_headers(tok), content=rpc("tools/list"))
    assert r.status_code == 401


def test_revoked_token_401():
    app, store, *_ = make_app()
    tok = mint(store)
    store.revoke(tok.split("_", 2)[1])
    r = drive(app, "POST", "/mcp", headers=mcp_headers(tok), content=rpc("tools/list"))
    assert r.status_code == 401


def test_wrong_audience_401():
    app, store, *_ = make_app()
    tok = mint(store, aud="https://evil.example/mcp")
    r = drive(app, "POST", "/mcp", headers=mcp_headers(tok), content=rpc("tools/list"))
    assert r.status_code == 401


def test_insufficient_scope_403():
    app, store, *_ = make_app()
    tok = mint(store, scopes=("other",))
    r = drive(app, "POST", "/mcp", headers=mcp_headers(tok), content=rpc("tools/list"))
    assert r.status_code == 403


def test_both_credentials_rejected():
    app, store, *_ = make_app(allow_x_access_token=True)
    tok = mint(store)
    h = mcp_headers(tok, extra={"x-access-token": tok})
    r = drive(app, "POST", "/mcp", headers=h, content=rpc("tools/list"))
    assert r.status_code == 401


def test_x_access_token_disabled_by_default():
    app, store, *_ = make_app(allow_x_access_token=False)
    tok = mint(store)
    h = mcp_headers(extra={"x-access-token": tok})
    r = drive(app, "POST", "/mcp", headers=h, content=rpc("tools/list"))
    assert r.status_code == 401


def test_x_access_token_enabled_when_flagged():
    app, store, *_ = make_app(allow_x_access_token=True)
    tok = mint(store)
    h = mcp_headers(extra={"x-access-token": tok})
    r = drive(app, "POST", "/mcp", headers=h, content=rpc("tools/list"))
    assert r.status_code == 200


def test_token_in_query_not_accepted():
    app, store, *_ = make_app()
    tok = mint(store)
    r = drive(app, "POST", "/mcp?access_token=%s" % tok, headers=mcp_headers(), content=rpc("tools/list"))
    assert r.status_code == 401


def test_secret_never_in_error_response():
    app, store, *_ = make_app()
    tok = mint(store, aud="https://evil.example/mcp")
    secret = tok.split("_", 2)[2]
    r = drive(app, "POST", "/mcp", headers=mcp_headers(tok), content=rpc("tools/list"))
    assert secret not in r.text and secret not in str(r.headers)


# ---- transport hardening (R1, R11, R13) ----------------------------------------------- #

def test_bad_host_421():
    app, store, *_ = make_app()
    r = drive(app, "POST", "/mcp", headers=mcp_headers(mint(store), extra={"host": "evil.example"}),
              content=rpc("tools/list"))
    assert r.status_code == 421


def test_bad_origin_403():
    app, store, *_ = make_app()
    r = drive(app, "POST", "/mcp", headers=mcp_headers(mint(store), extra={"origin": "https://evil.example"}),
              content=rpc("tools/list"))
    assert r.status_code == 403


def test_get_and_delete_mcp_405():
    app, store, *_ = make_app()
    assert drive(app, "GET", "/mcp", headers={"authorization": "Bearer x"}).status_code == 405
    assert drive(app, "DELETE", "/mcp", headers={"authorization": "Bearer x"}).status_code == 405


def test_wrong_content_type_415():
    app, store, *_ = make_app()
    h = mcp_headers(mint(store)); h["content-type"] = "text/plain"
    r = drive(app, "POST", "/mcp", headers=h, content=rpc("tools/list"))
    assert r.status_code == 415


def test_oversize_body_413():
    app, store, *_ = make_app()
    big = b'{"jsonrpc":"2.0","id":1,"method":"tools/list","pad":"' + b"a" * (257 * 1024) + b'"}'
    r = drive(app, "POST", "/mcp", headers=mcp_headers(mint(store)), content=big)
    assert r.status_code == 413


def test_no_approve_route():
    app, store, *_ = make_app()
    assert drive(app, "POST", "/approve", content=b"{}").status_code == 404
    assert drive(app, "GET", "/approve").status_code == 404


# ---- version negotiation (R1, R2) ----------------------------------------------------- #

def test_missing_version_on_tools_call_not_inferred():
    app, store, *_ = make_app()
    h = mcp_headers(mint(store), version=None)
    r = drive(app, "POST", "/mcp", headers=h,
              content=rpc("tools/call", params={"name": "sequentialthinking", "arguments": {}}))
    assert r.status_code == 400 and r.json()["error"] == "protocol_version_required"


def test_unsupported_version_32022():
    app, store, *_ = make_app()
    h = mcp_headers(mint(store), version="2024-11-05")
    r = drive(app, "POST", "/mcp", headers=h, content=rpc("tools/list"))
    err = r.json()["error"]
    assert err["code"] == -32022 and "2026-07-28" in err["data"]["supported"]


def test_mcp_method_header_mismatch():
    app, store, *_ = make_app()
    h = mcp_headers(mint(store), extra={"mcp-method": "tools/call"})
    r = drive(app, "POST", "/mcp", headers=h, content=rpc("tools/list"))
    assert r.json()["error"]["code"] == -32600


def test_mcp_name_header_mismatch():
    app, store, *_ = make_app()
    h = mcp_headers(mint(store), extra={"mcp-method": "tools/call", "mcp-name": "other"})
    r = drive(app, "POST", "/mcp", headers=h,
              content=rpc("tools/call", params={"name": "sequentialthinking", "arguments": {}}))
    assert r.json()["error"]["code"] == -32600


def _authorize_with_client(app, client_id, verifier):
    chal = pkce_s256_challenge(verifier)
    q = ("client_id=%s&redirect_uri=https://client.example/cb&response_type=code"
         "&code_challenge=%s&code_challenge_method=S256&resource=%s" % (client_id, chal, RESOURCE))
    return drive(app, "GET", "/authorize?" + q, headers={"x-test-principal": "u1"})


def test_cimd_client_resolution_ok():
    cid = "https://client.example/meta"
    doc = {"client_id": cid, "client_name": "c", "redirect_uris": ["https://client.example/cb"]}
    app, *_ = make_app(cimd_fetcher=lambda u: doc)
    r = _authorize_with_client(app, cid, "verifier-cimd-verifier-cimd-verifier")
    assert r.status_code == 302, r.text


def test_cimd_client_id_must_equal_url():
    cid = "https://client.example/meta"
    doc = {"client_id": "https://attacker.example/meta", "client_name": "c",
           "redirect_uris": ["https://client.example/cb"]}
    app, *_ = make_app(cimd_fetcher=lambda u: doc)
    r = _authorize_with_client(app, cid, "verifier-cimd-verifier-cimd-verifier")
    assert r.status_code == 400 and r.json()["error"] == "invalid_client"


def test_dcr_absent_by_default():
    app, *_ = make_app()
    assert drive(app, "POST", "/register", content=b'{"redirect_uris":["https://x/cb"]}').status_code == 404


def test_rate_limit_429():
    app, store, *_ = make_app()
    tok = mint(store)
    adapter_approved = app.adapter
    adapter_approved.approved = True
    last = None
    for _ in range(11):
        last = drive(app, "POST", "/mcp", headers=mcp_headers(tok),
                     content=rpc("tools/call", params={"name": "sequentialthinking", "arguments": {}}))
    assert last.status_code == 429
