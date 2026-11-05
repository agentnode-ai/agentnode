"""Smoke suite, SDK part (PLAN-2026-11.md section 2, core paths 2-5).

Each test walks one user path through the product's own public entry points, in
process or on 127.0.0.1, with no network, no real credentials and no container
runtime. Where the product would hand work to an isolating sandbox, a declared
test double stands in (named in each test); everything in front of it is real.

Run with the whole suite: ``python scripts/smoke.py`` (see docs/testing/smoke-suite.md).
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

SDK_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = SDK_ROOT / "tests" / "capability" / "fixtures"


def _where(path: str, what: str, detail="") -> str:
    return f"[{path}] {what}: {str(detail)[:400]}"


def test_smoke_runs_against_this_checkout():
    """The suite must test the code next to it, not an installed copy."""
    import agentnode_sdk

    assert Path(agentnode_sdk.__file__).resolve().is_relative_to(SDK_ROOT), (
        f"agentnode_sdk imported from {agentnode_sdk.__file__}, not from {SDK_ROOT}"
    )


# ---------------------------------------------------------------------------
# Core path 2: connect the remote MCP (as Claude Code does) and call a tool
# ---------------------------------------------------------------------------

REDIRECT = "http://127.0.0.1:33418/callback"  # a loopback redirect, as a CLI client uses
COOKIE = "__Host-agentnode"


def _remote_mcp_stand(root: Path):
    """The REAL transport, OAuth server, gateway login and catalog adapter; only the
    worker that would hand the job to the remote sandbox host is a local double
    (tests/remote_mcp/catalog_fixtures.CountingWorker)."""
    from tests.remote_mcp.catalog_fixtures import ISSUER, RESOURCE, HOST, Stand

    from agentnode_sdk.remote_mcp.accounts_login import GatewayLogin
    from agentnode_sdk.remote_mcp.http_transport import RemoteMCPApp
    from agentnode_sdk.remote_mcp.limits import Limits
    from agentnode_sdk.remote_mcp.oauth_as import AuthorizationServer
    from agentnode_sdk.remote_mcp.prm import ResourceConfig

    st = Stand(root, packs=("regex-builder-pack",))
    published = []
    for imp in st.imports.values():
        snap = st.catalog.publish(
            imp.manifest["capability_id"], "1.0.0", imp.manifest["tools"][0]["name"], actor="smoke"
        )
        published = list(snap.published)
    # The operator makes the published tool active for accounts without their own list.
    st.catalog.set_default(published, actor="smoke")
    gl = GatewayLogin(str(st.gw))
    authz = AuthorizationServer(
        issuer=ISSUER,
        resource=RESOURCE,
        token_store=st.store,
        preregistered={"claude-code-smoke": (REDIRECT,)},
    )
    st.app = RemoteMCPApp(
        adapter=st.adapter,
        token_store=st.store,
        authz=authz,
        resource=ResourceConfig(resource=RESOURCE, authorization_server=ISSUER),
        allowed_hosts=(HOST,),
        login_provider=gl,
        live_authority=gl,
        limits=Limits(),
    )
    return st, HOST, RESOURCE


def test_smoke_2_remote_mcp_connect_and_call(tmp_path):
    path = "2 Remote-MCP verbinden + Tool aufrufen"
    from agentnode_sdk.access.sessions import Sessions
    from agentnode_sdk.remote_mcp.oauth_as import pkce_s256_challenge

    st, host, resource = _remote_mcp_stand(tmp_path)
    acct, _ = st.account("smoke")          # account + paired device holding RUN
    sid, _csrf = Sessions(str(st.gw)).open("dev-smoke", label="browser")
    verifier = "smoke-verifier-" + "v" * 50
    authorize = {
        "client_id": "claude-code-smoke",
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "code_challenge_method": "S256",
        "code_challenge": pkce_s256_challenge(verifier),
        "resource": resource,
        "state": "smoke-state",
    }
    tool = "regex_builder_pack__data_cleaning"
    args = {"pattern": r"\d+", "test_string": "a1 b22", "operation": "findall"}

    async def mcp(c, token, method, params, mid):
        r = await c.post(
            "/mcp",
            headers={
                "content-type": "application/json",
                "accept": "application/json, text/event-stream",
                "mcp-protocol-version": "2025-11-25",
                "authorization": f"Bearer {token}",
            },
            content=json.dumps({"jsonrpc": "2.0", "id": mid, "method": method, "params": params}),
        )
        assert r.status_code == 200, _where(path, f"{method} HTTP {r.status_code}", r.text)
        body = r.json()
        assert "error" not in body, _where(path, f"{method} JSON-RPC error", body)
        return body["result"]

    async def go():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=st.app), base_url=f"http://{host}"
        ) as c:
            # Discovery, as the client does before anything else.
            r = await c.get("/.well-known/oauth-authorization-server")
            assert r.status_code == 200, _where(path, "AS metadata", r.text)
            # Browser consent with the signed-in session -> code -> token (PKCE).
            cookie = {"cookie": f"{COOKIE}={sid}"}
            r = await c.get("/authorize", params=authorize, headers=cookie)
            assert r.status_code == 200, _where(path, "consent page", r.text)
            confirm = re.search(r'name="confirm" value="([^"]+)"', r.text)
            assert confirm, _where(path, "consent page has no confirm field", r.text)
            r = await c.post(
                "/authorize",
                data=dict(authorize, confirm=confirm.group(1), decision="allow"),
                headers=cookie,
            )
            assert r.status_code == 302, _where(path, "allow consent", r.text)
            code = parse_qs(urlsplit(r.headers["location"]).query)["code"][0]
            r = await c.post(
                "/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": REDIRECT,
                    "code_verifier": verifier,
                    "client_id": "claude-code-smoke",
                    "resource": resource,
                },
            )
            assert r.status_code == 200, _where(path, "token exchange", r.text)
            token = r.json()["access_token"]

            init = await mcp(
                c, token, "initialize",
                {"protocolVersion": "2025-11-25", "capabilities": {},
                 "clientInfo": {"name": "claude-code", "version": "smoke"}}, 1,
            )
            assert init["protocolVersion"] == "2025-11-25", _where(path, "initialize", init)
            tools = [t["name"] for t in (await mcp(c, token, "tools/list", {}, 2))["tools"]]
            assert tool in tools, _where(path, "catalog tool listed", tools)

            first = await mcp(c, token, "tools/call", {"name": tool, "arguments": args}, 3)
            sc = first.get("structuredContent") or {}
            assert sc.get("status") == "approval_required", _where(path, "first call", first)
            # The person approves exactly this invocation (operator-signed approval file).
            st.approve(sc["invocation_hash"], "local/regex-builder-pack")
            ran = await mcp(c, token, "tools/call", {"name": tool, "arguments": args}, 4)
            assert ran["isError"] is False, _where(path, "approved call", ran)
            got = ran["structuredContent"]["call"]["structuredContent"]
            assert got["count"] == 2, _where(path, "tool result", got)
            return acct

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(go())
    finally:
        loop.close()
    assert st.worker.dispatched == 1, _where(path, "exactly one dispatch", st.worker.dispatched)


# ---------------------------------------------------------------------------
# Core path 3: local MCP -- install a catalog tool, then run it
# ---------------------------------------------------------------------------


def _registry(slug: str, command: list[str]):
    """A registry double that answers with exactly the fields the backend's
    install-info/package-detail responses carry (backend/app/install/schemas.py,
    McpServerInfo: command, transport, npm_package, source_repo, env_keys)."""

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if request.method == "GET" and p == f"/v1/packages/{slug}/install-info":
            return httpx.Response(200, json={
                "slug": slug, "version": "1.0.0", "package_type": "mcp",
                "install_mode": "package", "hosting_type": "agentnode_hosted",
                "runtime": "mcp", "entrypoint": None, "artifact": None,
                "capabilities": [{"name": "add", "capability_id": "math.add",
                                  "capability_type": "tool", "entrypoint": None}],
                "dependencies": [],
                "permissions": {"network_level": "none", "filesystem_level": "none",
                                "code_execution_level": "none", "data_access_level": "input_only",
                                "user_approval_level": "never", "allowed_domains": [],
                                "external_integrations": []},
                "verification_status": "passed", "verification_tier": "verified",
                "mcp_server": {"schema_version": "1", "command": command, "transport": "stdio",
                               "npm_package": None, "source_repo": None, "env_keys": []},
                "env_requirements": [],
            })
        if request.method == "GET" and p == f"/v1/packages/{slug}":
            return httpx.Response(200, json={
                "slug": slug, "name": "Smoke Add", "package_type": "mcp",
                "summary": "adds", "download_count": 0, "is_deprecated": False,
                "publisher": {"slug": "smoke-curator", "trust_level": "curated"},
                "latest_version": {"version_number": "1.0.0", "verification_tier": "verified"},
            })
        if request.method == "POST" and p == f"/v1/packages/{slug}/install":
            return httpx.Response(201, json={"ok": True})
        return httpx.Response(404, json={"error": {"code": "NOT_FOUND", "message": p}})

    return handler


def test_smoke_3_local_mcp_install_and_run(tmp_path, monkeypatch):
    path = "3 lokales MCP: Katalog-Tool installieren + ausfuehren"
    from agentnode_sdk.client import AgentNodeClient
    from agentnode_sdk.installer import read_lockfile
    from agentnode_sdk.runner import run_tool

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("AGENTNODE_CONFIG", str(home / "config.json"))
    lock = tmp_path / "agentnode.lock"
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(lock))
    monkeypatch.chdir(tmp_path)

    slug = "smoke-add-mcp"
    command = [sys.executable, str(FIXTURES / "deterministic_add_server.py")]
    client = AgentNodeClient(base_url="https://registry.invalid", api_key="ank_smoke_placeholder")
    client._client = httpx.Client(
        base_url=client.base_url, transport=httpx.MockTransport(_registry(slug, command))
    )

    result = client.install(slug)
    assert result.installed, _where(path, "install", result.message)
    entry = read_lockfile(lock)["packages"].get(slug)
    assert entry and entry.get("mcp_command") == command, _where(path, "lockfile entry", entry)

    res = run_tool(
        slug, "add", lockfile_path=lock, timeout=60,
        mcp_consent_callback=lambda *a, **k: True, a=2, b=40,
    )
    assert res.success, _where(path, "run", res.error)
    text = json.dumps(res.result)
    assert "42" in text, _where(path, "tool result", res.result)


# ---------------------------------------------------------------------------
# Core paths 4 and 5: a gateway (cloud = we run it, self-hosted = you run it)
# ---------------------------------------------------------------------------


class _Gateway:
    """A real GatewayService behind a real HTTP server on 127.0.0.1. Only the
    container backend is the suite's StandInBackend (tests/test_em3c_gateway.py),
    which records the job and answers "RAN" instead of starting a container."""

    def __enter__(self):
        from agentnode_sdk.gateway.identity import GatewayState
        from agentnode_sdk.gateway.server import GatewayService, make_server
        from tests.test_em3c_gateway import StandInBackend, _store_measurement

        self._td = tempfile.TemporaryDirectory()
        self.state = GatewayState(self._td.name, version="smoke")
        self.backend = StandInBackend()
        self.service = GatewayService(self.state, backend=self.backend)
        _store_measurement(self.service)
        self.service.CONTAINER_APPEAR_SECONDS = 0.5
        self.server = make_server(self.service, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        return self

    def __exit__(self, *exc):
        from agentnode_sdk.gateway.protocol import is_terminal

        self.server.shutdown()
        self.thread.join(timeout=10)
        for _ in range(300):
            if all(is_terminal(r.state) for r in list(self.service.runs.values())):
                break
            time.sleep(0.05)
        self.service.close()
        self._td.cleanup()
        return False


def _run(conn, service, run_id):
    from agentnode_sdk.gateway import client as gc
    from tests import consent
    from tests.test_em3c_gateway import _granted

    try:
        return consent.submit(conn, b"print('smoke')", granted=_granted(service, wall_clock_s=60),
                              run_id=run_id, wall_clock_s=60)
    except gc.GatewayClientError as refused:
        return {"run_id": run_id, "state": "refused", "refusal": str(refused)}


def test_smoke_5_own_server_gateway_start_and_run():
    path = "5 eigener Server: Gateway starten + Lauf"
    from agentnode_sdk.gateway import client as gc

    with _Gateway() as gw:
        hello = gc.hello(gw.base)
        assert hello.get("protocol"), _where(path, "hello", hello)
        conn = gc.pair(gw.base, gw.state.start_pairing(), client_name="smoke-laptop")
        assert conn.token, _where(path, "pairing", conn)
        answer = _run(conn, gw.service, "smoke-run")
        assert answer.get("state") != "refused", _where(path, "submit", answer)
        final = gc.wait_for(conn, "smoke-run", timeout=30)
        assert gc.verify_answer(conn, final) is final, _where(path, "signed answer", final)
        assert final.get("stdout") == "RAN", _where(path, "run output", final)
        assert len(gw.backend.specs) == 1, _where(path, "one job reached the sandbox", gw.backend.specs)


def test_smoke_4_cloud_sandbox_runs_until_the_quota_then_refuses():
    path = "4 Cloud-Sandbox-Lauf bis zur Kontingent-Grenze"
    from agentnode_sdk.gateway import client as gc
    from agentnode_sdk.gateway.allowance import CEILING_SAYS, Allowance, write_allowance

    quota = 2
    with _Gateway() as gw:
        write_allowance(gw.state.root, Allowance(runs_per_window=quota))
        conn = gc.pair(gw.base, gw.state.start_pairing(), client_name="trial-user")
        for n in range(quota):
            answer = _run(conn, gw.service, f"trial-{n}")
            assert answer.get("state") != "refused", _where(path, f"run {n + 1} within quota", answer)
            final = gc.wait_for(conn, f"trial-{n}", timeout=30)
            assert gc.verify_answer(conn, final) is final, _where(path, f"run {n + 1} answer", final)
        over = _run(conn, gw.service, "trial-over")
        assert over.get("state") == "refused", _where(path, "run past the quota", over)
        assert str(over.get("refusal", "")).startswith(CEILING_SAYS), _where(path, "refusal says why", over)
        assert "runs_per_window" in over["refusal"], _where(path, "refusal names the ceiling", over)
        assert len(gw.backend.specs) == quota, _where(path, "nothing ran past the quota", gw.backend.specs)
