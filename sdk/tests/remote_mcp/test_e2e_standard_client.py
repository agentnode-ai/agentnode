"""Task #6: the remote MCP end to end, as a real MCP client meets it.

The server is the REAL transport, OAuth authorization server, gateway login and catalog
adapter, served by uvicorn on 127.0.0.1 (real TCP, real HTTP). The client is the MCP SDK's
own Streamable-HTTP client with its own OAuth provider -- the same discovery a client such
as Claude Code performs: 401 + WWW-Authenticate -> protected-resource metadata ->
authorization-server metadata -> client id metadata document (CIMD) -> PKCE authorize ->
browser consent -> token -> initialize -> tools/list -> tools/call.

Test doubles, and only these: the "browser" (a person signed in on the gateway who clicks
Allow/Deny), the CIMD document (served by an injected fetcher, no network), and the worker
that would hand the job to the remote sandbox host (CountingWorker). Synthetic data only.
Runs under mcp 1.x and 2.x.
"""
from __future__ import annotations

import asyncio
import json
import re
import socket
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlsplit

import httpx
import pytest

uvicorn = pytest.importorskip("uvicorn")

try:  # the client API differs between mcp 1.x and 2.x
    from mcp.server.fastmcp import FastMCP  # noqa: F401
    MCP_V1 = True
except ModuleNotFoundError:
    MCP_V1 = False

REDIRECT = "http://127.0.0.1:33418/callback"
CIMD_URL = "https://client.invalid/agentnode-e2e/client.json"
COOKIE = "__Host-agentnode"
TOOL = "regex_builder_pack__data_cleaning"
ARGS = {"pattern": r"\d+", "test_string": "a1 b22", "operation": "findall"}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Served:
    """The stand behind uvicorn on loopback; issuer/resource are this loopback origin."""

    def __init__(self, root: Path, worker=None):
        from tests.remote_mcp.catalog_fixtures import Stand
        from agentnode_sdk.access.sessions import Sessions
        from agentnode_sdk.remote_mcp.accounts_login import GatewayLogin
        from agentnode_sdk.remote_mcp.http_transport import RemoteMCPApp
        from agentnode_sdk.remote_mcp.limits import Limits
        from agentnode_sdk.remote_mcp.oauth_as import AuthorizationServer
        from agentnode_sdk.remote_mcp.prm import ResourceConfig

        self.port = _free_port()
        self.base = "http://127.0.0.1:%d" % self.port
        self.resource = self.base + "/mcp"
        self.st = Stand(root, packs=("regex-builder-pack",), worker=worker)
        published = []
        for imp in self.st.imports.values():
            snap = self.st.catalog.publish(imp.manifest["capability_id"], "1.0.0",
                                           imp.manifest["tools"][0]["name"], actor="e2e")
            published = list(snap.published)
        self.st.catalog.set_default(published, actor="e2e")
        self.cimd_fetches = []

        def cimd(url):
            self.cimd_fetches.append(url)
            return {"client_id": url, "client_name": "AgentNode E2E client",
                    "redirect_uris": [REDIRECT]}

        gl = GatewayLogin(str(self.st.gw))
        self.authz = AuthorizationServer(issuer=self.base, resource=self.resource,
                                         token_store=self.st.store, cimd_fetcher=cimd)
        self.app = RemoteMCPApp(adapter=self.st.adapter, token_store=self.st.store, authz=self.authz,
                                resource=ResourceConfig(resource=self.resource,
                                                        authorization_server=self.base),
                                allowed_hosts=("127.0.0.1",), login_provider=gl, live_authority=gl,
                                limits=Limits())
        self.acct, _ = self.st.account("e2e")          # account + paired device holding RUN
        self.session, _ = Sessions(str(self.st.gw)).open("dev-e2e", label="browser")
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port,
                                                    log_level="warning", lifespan="on"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.monotonic() + 20
        while not self.server.started:
            assert time.monotonic() < deadline, "uvicorn did not start"
            time.sleep(0.05)
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(10)

    def revoke_device(self):
        self.st.devices.clear()
        self.st.state._write_private("tokens.json", json.dumps(self.st.devices))


class Browser:
    """The person at the consent page: signed in on the gateway, clicks Allow or Deny."""

    def __init__(self, served: Served, decision: str = "allow"):
        self.served = served
        self.decision = decision
        self.landed: str | None = None      # where the browser was finally sent
        self.page: str = ""                 # what the person saw instead, if not sent anywhere

    async def open(self, authorize_url: str) -> None:
        cookie = {"cookie": "%s=%s" % (COOKIE, self.served.session)}
        async with httpx.AsyncClient(base_url=self.served.base, timeout=20) as c:
            r = await c.get(authorize_url, headers=cookie)
            assert r.status_code == 200, r.text[:400]
            confirm = re.search(r'name="confirm" value="([^"]+)"', r.text)
            assert confirm, r.text[:400]
            form = dict(parse_qsl(urlsplit(authorize_url).query, keep_blank_values=True))
            r = await c.post("/authorize", data=dict(form, confirm=confirm.group(1),
                                                    decision=self.decision), headers=cookie)
        if r.status_code in (302, 303):
            self.landed = r.headers["location"]
        else:
            self.page = r.text

    async def callback(self):
        assert self.landed, "the client was never called back; the person saw: %s" % self.page[:300]
        q = parse_qs(urlsplit(self.landed).query)
        if "error" in q:
            raise RuntimeError("authorization refused: %s" % q["error"][0])
        code, state = q["code"][0], (q.get("state") or [None])[0]
        if MCP_V1:
            return code, state
        from mcp.client.auth import AuthorizationCodeResult
        return AuthorizationCodeResult(code=code, state=state)


class MemoryStorage:
    def __init__(self):
        self.tokens = None
        self.client_info = None

    async def get_tokens(self):
        return self.tokens

    async def set_tokens(self, tokens):
        self.tokens = tokens

    async def get_client_info(self):
        return self.client_info

    async def set_client_info(self, client_info):
        self.client_info = client_info


async def _with_client(served: Served, browser: Browser, storage: MemoryStorage, steps):
    """Connect with the SDK's own client + OAuth provider and run `steps(session)`."""
    from mcp import ClientSession
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthClientMetadata

    meta = OAuthClientMetadata(client_name="AgentNode E2E client", redirect_uris=[REDIRECT],
                               grant_types=["authorization_code"], response_types=["code"],
                               token_endpoint_auth_method="none", scope="mcp:use")
    provider = OAuthClientProvider(server_url=served.resource, client_metadata=meta, storage=storage,
                                   redirect_handler=browser.open, callback_handler=browser.callback,
                                   client_metadata_url=CIMD_URL)
    if MCP_V1:
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(served.resource, auth=provider) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await steps(session)
    import httpx2
    from mcp.client.streamable_http import streamable_http_client

    async with httpx2.AsyncClient(auth=provider, timeout=30) as http:
        async with streamable_http_client(served.resource, http_client=http) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                return await steps(session)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(asyncio.wait_for(coro, 90))
    finally:
        loop.close()


def _structured(result) -> dict:
    return getattr(result, "structuredContent", None) or getattr(result, "structured_content", None) or {}


def _is_error(result) -> bool:
    return bool(getattr(result, "isError", None) or getattr(result, "is_error", None))


def _token(storage: MemoryStorage) -> str:
    return storage.tokens.access_token


# ---------------------------------------------------------------- success path


def test_a_standard_client_connects_authorizes_and_runs_a_catalog_tool(tmp_path):
    with Served(tmp_path) as srv:
        browser, storage = Browser(srv), MemoryStorage()

        async def steps(s):
            names = [t.name for t in (await s.list_tools()).tools]
            assert TOOL in names, names
            first = await s.call_tool(TOOL, ARGS)
            pending = _structured(first)
            assert pending.get("status") == "approval_required", first
            # The person approves exactly this invocation (operator-signed approval).
            srv.st.approve(pending["invocation_hash"], "local/regex-builder-pack")
            ran = await s.call_tool(TOOL, ARGS)
            assert not _is_error(ran), ran
            return _structured(ran)

        got = _run(_with_client(srv, browser, storage, steps))
        assert got["call"]["structuredContent"]["count"] == 2, got
        assert srv.st.worker.dispatched == 1
        # the client identified itself by CIMD (fetched at consent and again at re-validation;
        # the injected test fetcher is uncached)
        assert srv.cimd_fetches and set(srv.cimd_fetches) == {CIMD_URL}
        assert storage.tokens is not None and storage.tokens.token_type.lower() == "bearer"


# ---------------------------------------------------------------- refusals, clearly reported


def test_without_a_token_the_server_points_the_client_to_its_authorization_server(tmp_path):
    with Served(tmp_path) as srv:
        r = httpx.post(srv.resource, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                       headers={"accept": "application/json, text/event-stream"})
        assert r.status_code == 401
        challenge = r.headers["www-authenticate"]
        assert 'resource_metadata="%s/.well-known/oauth-protected-resource"' % srv.base in challenge
        prm = httpx.get(srv.base + "/.well-known/oauth-protected-resource").json()
        assert prm["resource"] == srv.resource and prm["authorization_servers"] == [srv.base]
        asm = httpx.get(srv.base + "/.well-known/oauth-authorization-server").json()
        assert asm["client_id_metadata_document_supported"] is True
        assert asm["code_challenge_methods_supported"] == ["S256"]
        assert srv.st.worker.dispatched == 0


def test_a_declined_consent_is_reported_back_to_the_client(tmp_path):
    """RFC 6749 4.1.2.1: when the person denies, the client is sent back to its registered
    redirect_uri with error=access_denied (and its state) -- a CLI client waiting on its
    loopback callback learns of the refusal instead of waiting until it times out."""
    with Served(tmp_path) as srv:
        browser, storage = Browser(srv, decision="deny"), MemoryStorage()

        async def steps(s):
            raise AssertionError("connected although the person declined")

        with pytest.raises(BaseException) as info:
            _run(_with_client(srv, browser, storage, steps))
        assert browser.landed is not None, "declined consent did not return to the client: %s" % browser.page[:200]
        q = parse_qs(urlsplit(browser.landed).query)
        assert browser.landed.startswith(REDIRECT)
        assert q["error"] == ["access_denied"] and "code" not in q
        assert "state" in q
        assert "access_denied" in repr(info.value) or "access_denied" in str(info.getrepr())
        assert storage.tokens is None and srv.st.worker.dispatched == 0


def test_a_forged_token_is_refused_as_invalid(tmp_path):
    with Served(tmp_path) as srv:
        r = httpx.post(srv.resource, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                       headers={"accept": "application/json, text/event-stream",
                                "authorization": "Bearer an-placeholder-not-a-token"})
        assert r.status_code == 401
        assert 'error="invalid_token"' in r.headers["www-authenticate"]


def test_an_unknown_tool_is_an_error_and_runs_nothing(tmp_path):
    with Served(tmp_path) as srv:
        browser, storage = Browser(srv), MemoryStorage()

        async def steps(s):
            try:
                result = await s.call_tool("no_such_tool_here", {})
            except Exception as exc:     # a JSON-RPC error is a clear refusal too
                return "rpc-error: %s" % exc
            assert _is_error(result), result
            return "tool-error"

        assert _run(_with_client(srv, browser, storage, steps))
        assert srv.st.worker.dispatched == 0


def test_a_tool_that_fails_is_reported_as_an_error(tmp_path):
    from tests.remote_mcp.catalog_fixtures import CountingWorker
    from agentnode_sdk.worker import Outcome

    class FailingWorker(CountingWorker):
        def run(self, job):
            self.dispatched += 1
            return Outcome(exit_code=1, stdout="", stderr="synthetic failure")

    with Served(tmp_path, worker=FailingWorker()) as srv:
        browser, storage = Browser(srv), MemoryStorage()

        async def steps(s):
            pending = _structured(await s.call_tool(TOOL, ARGS))
            srv.st.approve(pending["invocation_hash"], "local/regex-builder-pack")
            return await s.call_tool(TOOL, ARGS)

        result = _run(_with_client(srv, browser, storage, steps))
        assert _is_error(result), result
        assert srv.st.worker.dispatched == 1


def test_a_revoked_device_loses_access_at_once(tmp_path):
    with Served(tmp_path) as srv:
        browser, storage = Browser(srv), MemoryStorage()

        async def steps(s):
            return [t.name for t in (await s.list_tools()).tools]

        assert TOOL in _run(_with_client(srv, browser, storage, steps))
        srv.revoke_device()
        r = httpx.post(srv.resource, json={"jsonrpc": "2.0", "id": 9, "method": "initialize",
                                           "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                                                      "clientInfo": {"name": "e2e", "version": "1"}}},
                       headers={"accept": "application/json, text/event-stream",
                                "authorization": "Bearer " + _token(storage)})
        assert r.status_code == 401, r.text
        assert 'error="invalid_token"' in r.headers["www-authenticate"]
        assert srv.st.worker.dispatched == 0
