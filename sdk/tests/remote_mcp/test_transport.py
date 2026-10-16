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


PV_META = "io.modelcontextprotocol/protocolVersion"
CAPS_META = "io.modelcontextprotocol/clientCapabilities"
#: mcp_types.jsonrpc.HEADER_MISMATCH in the official SDK (2.3.0) — the literal, so a wrong
#: server constant cannot make these assertions agree with themselves.
HEADER_MISMATCH = -32020


def rpc(method, *, mid=1, params=None, modern="2026-07-28", meta=True):
    # A modern (2026-07-28) request carries the per-request _meta envelope the spec requires.
    p = dict(params or {})
    if meta and modern == "2026-07-28":
        p["_meta"] = {PV_META: modern, CAPS_META: {}}
    msg = {"jsonrpc": "2.0", "id": mid, "method": method}
    if p:
        msg["params"] = p
    return json.dumps(msg).encode()


def mcp_headers(token=None, *, version="2026-07-28", method="tools/list",
                name="sequentialthinking", extra=None):
    # MCP Streamable HTTP: the client Accepts both content types; the modern era carries the
    # SEP-2243 metadata headers (auto-added here unless a test overrides via `extra`).
    h = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
    if version:
        h["mcp-protocol-version"] = version
    if token:
        h["authorization"] = "Bearer %s" % token
    if version == "2026-07-28" and method in ("tools/list", "tools/call", "server/discover"):
        h["mcp-method"] = method
        if method == "tools/call":
            h["mcp-name"] = name
    if extra:
        h.update(extra)
    return h


def discover_body(mid=1, pv="2026-07-28"):
    return json.dumps({"jsonrpc": "2.0", "id": mid, "method": "server/discover",
                       "params": {"_meta": {PV_META: pv, CAPS_META: {}}}}).encode()


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
    r = drive(app, "POST", "/mcp", headers=mcp_headers(mint(store), method="tools/call"),
              content=rpc("tools/call", params={"name": "sequentialthinking", "arguments": {}}))
    assert r.status_code == 200
    res = r.json()["result"]
    assert res["isError"] is True
    assert res["structuredContent"]["status"] == "approval_required"
    assert adapter.calls, "transport must delegate to the frozen adapter"


def test_server_discover_modern():
    app, store, *_ = make_app()
    r = drive(app, "POST", "/mcp", headers=mcp_headers(mint(store), method="server/discover"),
              content=discover_body())
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    assert res["supportedVersions"] == ["2026-07-28"]
    assert res["resultType"] == "complete"
    assert "tools" in res["capabilities"]
    assert res["_meta"]["io.modelcontextprotocol/serverInfo"]["name"]


def test_modern_initialize_is_rejected_not_a_handshake():
    app, store, *_ = make_app()
    h = mcp_headers(mint(store), extra={"mcp-method": "initialize"})
    r = drive(app, "POST", "/mcp", headers=h,
              content=rpc("initialize", params={"protocolVersion": "2026-07-28"}))
    # a modern initialize must NOT be a successful handshake; it is refused as unsupported
    assert "result" not in r.json() or r.json().get("result", {}).get("protocolVersion") is None
    assert r.json()["error"]["code"] == -32022


def test_legacy_initialize_handshake():
    app, store, *_ = make_app()
    h = mcp_headers(mint(store), version="2025-11-25")
    r = drive(app, "POST", "/mcp", headers=h,
              content=rpc("initialize", modern="2025-11-25", params={
                  "protocolVersion": "2025-11-25", "capabilities": {},
                  "clientInfo": {"name": "probe", "version": "0"}}))
    res = r.json()["result"]
    assert res["protocolVersion"] == "2025-11-25" and res["serverInfo"]["name"]


def test_legacy_tools_list_no_meta_required():
    app, store, *_ = make_app()
    h = mcp_headers(mint(store), version="2025-11-25")
    r = drive(app, "POST", "/mcp", headers=h, content=rpc("tools/list", modern="2025-11-25"))
    assert r.status_code == 200 and r.json()["result"]["tools"]


def test_modern_tools_list_has_result_type():
    app, store, *_ = make_app()
    r = drive(app, "POST", "/mcp", headers=mcp_headers(mint(store)), content=rpc("tools/list"))
    assert r.json()["result"]["resultType"] == "complete"


def test_modern_request_without_meta_envelope_rejected():
    app, store, *_ = make_app()
    r = drive(app, "POST", "/mcp", headers=mcp_headers(mint(store)),
              content=rpc("tools/list", meta=False))   # no _meta envelope
    assert r.status_code == 400 and r.json()["error"]["code"] == -32602


def test_legacy_request_with_modern_meta_rejected():
    app, store, *_ = make_app()
    h = mcp_headers(mint(store), version="2025-11-25")
    r = drive(app, "POST", "/mcp", headers=h, content=rpc("tools/list"))  # modern _meta on legacy
    assert r.json()["error"]["code"] == -32600


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
    assert r.status_code == 400 and r.json()["error"]["code"] == HEADER_MISMATCH


def test_mcp_name_header_mismatch():
    app, store, *_ = make_app()
    h = mcp_headers(mint(store), extra={"mcp-method": "tools/call", "mcp-name": "other"})
    r = drive(app, "POST", "/mcp", headers=h,
              content=rpc("tools/call", params={"name": "sequentialthinking", "arguments": {}}))
    assert r.status_code == 400 and r.json()["error"]["code"] == HEADER_MISMATCH


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
        last = drive(app, "POST", "/mcp", headers=mcp_headers(tok, method="tools/call"),
                     content=rpc("tools/call", params={"name": "sequentialthinking", "arguments": {}}))
    assert last.status_code == 429


# ---- R1 strictness: Accept both, required SEP-2243 headers, version header/body agree ----- #

def test_accept_must_list_both_media_types():
    app, store, *_ = make_app()
    h = mcp_headers(mint(store))
    h["accept"] = "application/json"               # missing text/event-stream
    r = drive(app, "POST", "/mcp", headers=h, content=rpc("tools/list"))
    assert r.status_code == 406


def test_modern_tools_list_requires_mcp_method_header():
    app, store, *_ = make_app()
    h = mcp_headers(mint(store))
    del h["mcp-method"]                            # omit the required modern header
    r = drive(app, "POST", "/mcp", headers=h, content=rpc("tools/list"))
    assert r.json()["error"]["code"] == HEADER_MISMATCH


def test_modern_tools_call_requires_mcp_name_header():
    app, store, *_ = make_app()
    h = mcp_headers(mint(store), method="tools/call")
    del h["mcp-name"]                              # omit the required name header
    r = drive(app, "POST", "/mcp", headers=h,
              content=rpc("tools/call", params={"name": "sequentialthinking", "arguments": {}}))
    assert r.json()["error"]["code"] == HEADER_MISMATCH


def test_protocol_version_header_meta_disagreement_rejected():
    app, store, *_ = make_app()
    h = mcp_headers(mint(store))                        # header says 2026-07-28, mcp-method tools/list
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list",
                       "params": {"_meta": {PV_META: "2025-11-25", CAPS_META: {}}}}).encode()
    r = drive(app, "POST", "/mcp", headers=h, content=body)
    assert r.status_code == 400 and r.json()["error"]["code"] == HEADER_MISMATCH and "disagree" in r.text


# ---- legacy wire flow: the one headerless request is initialize ----------------------- #

def init_body(pv="2025-11-25", mid=1, **override):
    params = {"protocolVersion": pv, "capabilities": {},
              "clientInfo": {"name": "probe", "version": "0"}}
    params.update(override)
    return json.dumps({"jsonrpc": "2.0", "id": mid, "method": "initialize", "params": params}).encode()


def test_legacy_wire_flow_headerless_initialize_then_header():
    # exactly what the official SDK sends: initialize WITHOUT MCP-Protocol-Version, then every
    # later request carrying the negotiated header.
    app, store, adapter, _ = make_app()
    tok = mint(store)
    r = drive(app, "POST", "/mcp", headers=mcp_headers(tok, version=None), content=init_body())
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    assert res["protocolVersion"] == "2025-11-25" and res["serverInfo"]["name"]
    assert "mcp-session-id" not in r.headers          # stateless legacy: no session minted
    n = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode()
    h = mcp_headers(tok, version="2025-11-25")
    assert drive(app, "POST", "/mcp", headers=h, content=n).status_code == 202
    r2 = drive(app, "POST", "/mcp", headers=h, content=rpc("tools/list", modern="2025-11-25"))
    assert r2.status_code == 200 and r2.json()["result"]["tools"]
    assert "_meta" not in r2.json()["result"] and "resultType" not in r2.json()["result"]
    r3 = drive(app, "POST", "/mcp", headers=h, content=rpc(
        "tools/call", modern="2025-11-25", params={"name": "sequentialthinking", "arguments": {}}))
    assert r3.json()["result"]["structuredContent"]["status"] == "approval_required"


def test_headerless_follow_up_legacy_calls_refused():
    app, store, *_ = make_app()
    tok = mint(store)
    h = mcp_headers(tok, version=None)
    for body in (rpc("tools/list", modern="2025-11-25"),
                 json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode(),
                 rpc("tools/call", modern="2025-11-25",
                     params={"name": "sequentialthinking", "arguments": {}})):
        r = drive(app, "POST", "/mcp", headers=h, content=body)
        assert r.status_code == 400 and r.json()["error"] == "protocol_version_required"


def test_headerless_initialize_offering_modern_selects_legacy_only():
    # an initialize can only ever negotiate a LEGACY version; it never yields a modern handshake
    app, store, *_ = make_app()
    r = drive(app, "POST", "/mcp", headers=mcp_headers(mint(store), version=None),
              content=init_body(pv="2026-07-28"))
    assert r.json()["result"]["protocolVersion"] == "2025-11-25"


def test_legacy_initialize_params_are_validated():
    app, store, *_ = make_app()
    tok = mint(store)
    for bad in ({"protocolVersion": 20251125}, {"capabilities": []},
                {"clientInfo": {"name": "x"}}, {"clientInfo": "probe"}):
        r = drive(app, "POST", "/mcp", headers=mcp_headers(tok, version=None), content=init_body(**bad))
        assert r.status_code == 400 and r.json()["error"]["code"] == -32602, bad


def test_legacy_initialize_header_body_disagreement_rejected():
    app, store, *_ = make_app()
    h = mcp_headers(mint(store), version="2025-11-25")
    r = drive(app, "POST", "/mcp", headers=h, content=init_body(pv="2025-06-18"))
    assert r.json()["error"]["code"] == HEADER_MISMATCH


def test_session_ids_never_mix_into_either_era():
    app, store, *_ = make_app()
    tok = mint(store)
    legacy = mcp_headers(tok, version="2025-11-25", extra={"mcp-session-id": "s-1"})
    r = drive(app, "POST", "/mcp", headers=legacy, content=rpc("tools/list", modern="2025-11-25"))
    assert r.status_code == 404                       # we issued no session: unknown, not adopted
    modern = mcp_headers(tok, extra={"mcp-session-id": "s-1"})
    r = drive(app, "POST", "/mcp", headers=modern, content=rpc("tools/list"))
    assert r.status_code == 400 and r.json()["error"]["code"] == -32600


def test_modern_header_with_legacy_body_does_not_initialize():
    # a modern-headed request is routed modern even for initialize: no legacy handshake via mixing
    app, store, *_ = make_app()
    h = mcp_headers(mint(store), extra={"mcp-method": "initialize"})
    r = drive(app, "POST", "/mcp", headers=h, content=init_body())   # no modern _meta
    assert "result" not in r.json()


# ---- modern serverInfo stamp on EVERY result ------------------------------------------ #

SERVER_INFO_KEY = "io.modelcontextprotocol/serverInfo"


def test_every_modern_result_carries_server_info_stamp():
    app, store, adapter, _ = make_app()
    tok = mint(store)
    seen = []
    r = drive(app, "POST", "/mcp", headers=mcp_headers(tok, method="server/discover"),
              content=discover_body())
    seen.append(r.json()["result"])
    seen.append(drive(app, "POST", "/mcp", headers=mcp_headers(tok), content=rpc("tools/list")).json()["result"])
    call = rpc("tools/call", params={"name": "sequentialthinking", "arguments": {}})
    seen.append(drive(app, "POST", "/mcp", headers=mcp_headers(tok, method="tools/call"), content=call).json()["result"])
    assert seen[-1]["structuredContent"]["status"] == "approval_required"
    adapter.approved = True
    seen.append(drive(app, "POST", "/mcp", headers=mcp_headers(tok, method="tools/call"), content=call).json()["result"])
    assert seen[-1]["isError"] is False
    for res in seen:
        assert res["_meta"][SERVER_INFO_KEY] == {"name": "agentnode-remote-mcp", "version": "1"}
        assert res["resultType"] == "complete"
    assert "serverInfo" not in seen[0]                # not the deprecated top-level DiscoverResult field


def test_modern_errors_are_not_stamped():
    # the SDK stamps results only; a JSON-RPC error stays a bare error
    app, store, *_ = make_app()
    h = mcp_headers(mint(store), extra={"mcp-method": "prompts/list"})
    r = drive(app, "POST", "/mcp", headers=h, content=rpc("prompts/list"))
    body = r.json()
    assert r.status_code == 404 and "error" in body and "result" not in body and "_meta" not in body["error"]


def test_stamp_merges_existing_meta_and_respects_handler_value():
    from agentnode_sdk.remote_mcp.http_transport import RemoteMCPApp as A
    own = {"x/other": 1}
    out = A._stamp_modern({"jsonrpc": "2.0", "id": 1, "result": {"_meta": own}}, True)
    assert out["result"]["_meta"] == {"x/other": 1, SERVER_INFO_KEY: {"name": "agentnode-remote-mcp", "version": "1"}}
    assert own == {"x/other": 1}                      # the handler's object is not written into
    mine = {SERVER_INFO_KEY: {"name": "handler", "version": "9"}}
    out = A._stamp_modern({"jsonrpc": "2.0", "id": 1, "result": {"_meta": mine}}, True)
    assert out["result"]["_meta"][SERVER_INFO_KEY]["name"] == "handler"
    out = A._stamp_modern({"jsonrpc": "2.0", "id": 1, "result": {"_meta": {SERVER_INFO_KEY: None}}}, True)
    assert out["result"]["_meta"][SERVER_INFO_KEY]["name"] == "agentnode-remote-mcp"
    legacy = {"jsonrpc": "2.0", "id": 1, "result": {}}
    assert A._stamp_modern(legacy, False) is legacy


# ---- modern _meta envelope is TYPED, not merely present ------------------------------- #

def test_invalid_client_capabilities_or_info_refused():
    app, store, *_ = make_app()
    tok = mint(store)
    bad_metas = [
        {PV_META: "2026-07-28", CAPS_META: []},
        {PV_META: "2026-07-28", CAPS_META: "all"},
        {PV_META: "2026-07-28", CAPS_META: {"roots": True}},
        {PV_META: "2026-07-28", CAPS_META: {}, "io.modelcontextprotocol/clientInfo": "probe"},
        {PV_META: "2026-07-28", CAPS_META: {}, "io.modelcontextprotocol/clientInfo": {"name": "x"}},
        {PV_META: "2026-07-28", CAPS_META: {}, "io.modelcontextprotocol/clientInfo": {"name": 1, "version": "1"}},
    ]
    for meta in bad_metas:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": meta}}).encode()
        r = drive(app, "POST", "/mcp", headers=mcp_headers(tok), content=body)
        assert r.status_code == 400 and r.json()["error"]["code"] == -32602, meta


def test_valid_client_info_accepted_and_optional():
    app, store, *_ = make_app()
    tok = mint(store)
    meta = {PV_META: "2026-07-28", CAPS_META: {"roots": {"listChanged": True}},
            "io.modelcontextprotocol/clientInfo": {"name": "probe", "version": "1"}}
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": meta}}).encode()
    assert drive(app, "POST", "/mcp", headers=mcp_headers(tok), content=body).status_code == 200
