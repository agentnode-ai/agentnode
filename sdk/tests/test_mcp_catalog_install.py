"""Task #3: installing an MCP package from the catalog records its tools.

An MCP package's tools have no Python entrypoint -- the server process provides
them -- so the client used to drop every one of them and the lockfile entry had
``tools: []``. Running without naming a tool then called a tool named after the
package, and the argument guard never saw an input schema. Synthetic registry,
synthetic stdio MCP server; no network.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

FIXTURES = Path(__file__).resolve().parent / "capability" / "fixtures"
SLUG = "catalog-add-mcp"
ADD_SCHEMA = {
    "type": "object",
    "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
    "required": ["a", "b"],
}


def _install_info(mcp_server: dict) -> dict:
    return {
        "slug": SLUG, "version": "1.0.0", "package_type": "mcp",
        "install_mode": "package", "hosting_type": "agentnode_hosted",
        "runtime": "mcp", "entrypoint": None, "artifact": None,
        "capabilities": [
            {"name": "add", "capability_id": "math.add", "capability_type": "tool",
             "entrypoint": None, "input_schema": ADD_SCHEMA},
            {"name": "notes", "capability_id": "math.notes", "capability_type": "resource",
             "entrypoint": None},
        ],
        "dependencies": [],
        "permissions": {"network_level": "none", "filesystem_level": "none",
                        "code_execution_level": "none", "data_access_level": "input_only",
                        "user_approval_level": "never", "allowed_domains": []},
        "mcp_server": mcp_server,
        "env_requirements": [],
    }


def _client(info: dict):
    from agentnode_sdk.client import AgentNodeClient

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p == f"/v1/packages/{SLUG}/install-info":
            return httpx.Response(200, json=info)
        if p == f"/v1/packages/{SLUG}" and request.method == "GET":
            return httpx.Response(200, json={
                "slug": SLUG, "name": "Add", "package_type": "mcp", "summary": "s",
                "download_count": 0, "is_deprecated": False,
                "publisher": {"slug": "curator", "trust_level": "curated"},
                "latest_version": {"version_number": "1.0.0"},
            })
        if p == f"/v1/packages/{SLUG}/install":
            return httpx.Response(201, json={"ok": True})
        return httpx.Response(404, json={"error": {"code": "NOT_FOUND", "message": p}})

    c = AgentNodeClient(base_url="https://registry.invalid", api_key="ank_placeholder")
    c._client = httpx.Client(base_url=c.base_url, transport=httpx.MockTransport(handler))
    return c


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("AGENTNODE_CONFIG", str(home / "config.json"))
    lock = tmp_path / "agentnode.lock"
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(lock))
    monkeypatch.chdir(tmp_path)
    return lock


def test_install_forwards_the_mcp_tools_with_their_schema(isolated, monkeypatch):
    captured = {}

    def fake_install_package(**kw):
        captured.update(kw)
        return {"slug": SLUG, "version": "1.0.0", "installed": True, "message": "ok"}

    monkeypatch.setattr("agentnode_sdk.client.install_package", fake_install_package)
    _client(_install_info({"command": ["x"], "transport": "stdio"})).install(SLUG)
    assert captured["tools"] == [{"name": "add", "input_schema": ADD_SCHEMA}]


def test_install_forwards_descriptor_and_allowlist_from_the_catalog(isolated, monkeypatch):
    captured = {}

    def fake_install_package(**kw):
        captured.update(kw)
        return {"slug": SLUG, "version": "1.0.0", "installed": True, "message": "ok"}

    monkeypatch.setattr("agentnode_sdk.client.install_package", fake_install_package)
    install = {"manager": "npm", "package": "@scope/srv", "version": "1.2.3"}
    _client(_install_info({"command": ["x"], "transport": "stdio", "install": install,
                           "allowed_domains": ["api.example.com"]})).install(SLUG)
    assert captured["mcp_install"] == install
    assert captured["mcp_allowed_domains"] == ["api.example.com"]


def test_lockfile_records_the_tools_and_a_run_without_a_tool_name_uses_them(isolated):
    from agentnode_sdk.installer import read_lockfile
    from agentnode_sdk.runner import run_tool

    command = [sys.executable, str(FIXTURES / "deterministic_add_server.py")]
    result = _client(_install_info({"command": command, "transport": "stdio"})).install(SLUG)
    assert result.installed, result.message

    entry = read_lockfile(isolated)["packages"][SLUG]
    assert [t["name"] for t in entry["tools"]] == ["add"]
    assert entry["tools"][0]["input_schema"] == ADD_SCHEMA

    res = run_tool(SLUG, lockfile_path=isolated, timeout=60,
                   mcp_consent_callback=lambda *a, **k: True, a=20, b=22)
    assert res.success, res.error
    assert "42" in json.dumps(res.result)
