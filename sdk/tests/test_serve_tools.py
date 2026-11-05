"""Task #5c: ``agentnode serve`` covers what ``adapter-mcp`` offered, without its bypass.

adapter-mcp exposed installed pack tools by importing the pack's Python function
into the server process and calling it -- no sandbox, no guard, no host-trust
policy. ``agentnode serve`` now lists the same tools (same names, same four
platform tools with the same contracts) but runs every pack tool through
``runner.run_tool``. Failures and refusals come back as MCP errors.
Synthetic packs, synthetic registry; no network.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import httpx
import pytest

from agentnode_sdk.models import RunToolResult

SDK = Path(__file__).resolve().parents[1]

PLATFORM = ("agentnode_search", "agentnode_resolve", "agentnode_explain", "agentnode_capabilities")


@pytest.fixture
def lock(tmp_path, monkeypatch):
    from agentnode_sdk import installer
    from agentnode_sdk.lock_integrity import seal_entry

    home = tmp_path / "home"
    home.mkdir()
    for k in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(k, str(home))
    monkeypatch.setenv("AGENTNODE_CONFIG", str(home / "config.json"))
    lf = tmp_path / "agentnode.lock"
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(lf))

    def add(slug, **entry):
        base = {"version": "1.0.0", "package_type": "toolpack", "runtime": "python",
                "trust_level": "curated", "artifact_hash": "", "permissions": {},
                "entrypoint": "", "tools": []}
        base.update(entry)
        installer.update_lockfile(slug, seal_entry(base), path=lf)

    return add


# ---------------------------------------------------------------- listing


def test_platform_tools_keep_their_contracts():
    from agentnode_sdk import mcp_tools

    tools = {t["name"]: t for t in mcp_tools.list_tools()}
    for name in PLATFORM:
        assert name in tools, name
    assert tools["agentnode_resolve"]["inputSchema"]["required"] == ["capabilities"]
    assert tools["agentnode_explain"]["inputSchema"]["required"] == ["package_slug"]
    assert set(tools["agentnode_search"]["inputSchema"]["properties"]) == {
        "query", "capability_id", "framework", "trust_level"}
    assert set(tools["agentnode_capabilities"]["inputSchema"]["properties"]) == {"category"}


def test_pack_tools_are_listed_under_the_adapter_names_with_their_schema(lock):
    from agentnode_sdk import mcp_tools

    schema = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
    lock("one-tool-pack", tools=[{"name": "clean", "entrypoint": "one.tool:clean",
                                  "input_schema": schema}])
    lock("two-tool-pack", tools=[{"name": "a", "entrypoint": "two.tool:a"},
                                 {"name": "b", "entrypoint": "two.tool:b"}])
    lock("a-skill", package_type="skill", runtime="none", tools=[])
    names = {t["name"]: t for t in mcp_tools.list_tools()}
    assert names["one-tool-pack"]["inputSchema"] == schema       # single tool: the slug
    assert "two-tool-pack/a" in names and "two-tool-pack/b" in names   # several: slug/tool
    assert not any(n.startswith("a-skill") for n in names)       # skills stay prompts


def test_packs_filter_limits_the_pack_tools(lock):
    from agentnode_sdk import mcp_tools

    lock("keep-pack", tools=[{"name": "k", "entrypoint": "k.tool:k"}])
    lock("drop-pack", tools=[{"name": "d", "entrypoint": "d.tool:d"}])
    names = [t["name"] for t in mcp_tools.list_tools(packs={"keep-pack"})]
    assert "keep-pack" in names and "drop-pack" not in names
    assert all(p in names for p in PLATFORM)


# ---------------------------------------------------------------- pack tools run through run_tool


def test_a_pack_tool_call_goes_through_run_tool(lock, monkeypatch):
    from agentnode_sdk import mcp_tools

    lock("num-pack", tools=[{"name": "double", "entrypoint": "num.tool:double",
                             "input_schema": {"type": "object",
                                              "properties": {"n": {"type": "integer"}}}}])
    seen = {}

    def fake_run_tool(slug, tool_name=None, **kwargs):
        seen.update(slug=slug, tool=tool_name, kwargs=kwargs)
        return RunToolResult(success=True, result={"value": 42})

    monkeypatch.setattr("agentnode_sdk.runner.run_tool", fake_run_tool)
    out = mcp_tools.call_tool("num-pack", {"n": "21"})
    assert seen["slug"] == "num-pack" and seen["tool"] == "double"
    assert seen["kwargs"]["n"] == 21                       # coerced by schema, as before
    assert out.is_error is False and json.loads(out.text) == {"value": 42}


def test_a_failed_or_refused_run_is_an_mcp_error(lock, monkeypatch):
    from agentnode_sdk import mcp_tools

    lock("deny-pack", tools=[{"name": "go", "entrypoint": "deny.tool:go"}])
    monkeypatch.setattr(
        "agentnode_sdk.runner.run_tool",
        lambda *a, **k: RunToolResult(success=False, error="Sandbox required: refused"),
    )
    out = mcp_tools.call_tool("deny-pack", {})
    assert out.is_error is True
    assert "Sandbox required: refused" in out.text


def test_an_unknown_tool_is_refused(lock):
    from agentnode_sdk import mcp_tools

    with pytest.raises(mcp_tools.UnknownTool):
        mcp_tools.call_tool("nothing-installed-here", {})


def test_serve_never_asks_on_stdin(lock, monkeypatch):
    """stdin is the MCP channel: a tool that needs confirmation is refused, never asked."""
    from agentnode_sdk import mcp_tools

    lock("ask-pack", tools=[{"name": "frobnicate", "entrypoint": "ask.tool:frobnicate"}])

    def no_input(*a, **k):
        raise AssertionError("serve read stdin for a confirmation")

    monkeypatch.setattr("builtins.input", no_input)
    monkeypatch.delenv("AGENTNODE_NON_INTERACTIVE", raising=False)
    out = mcp_tools.call_tool("ask-pack", {})
    assert out.is_error is True, out.text


# ---------------------------------------------------------------- no bypass


MARKER_MODULE = textwrap.dedent(
    """
    import os, pathlib
    pathlib.Path(os.environ["SMOKE_MARKER"]).write_text("imported")
    def run(**kwargs):
        pathlib.Path(os.environ["SMOKE_MARKER"]).write_text("ran")
        return {"ran": True}
    """
)


def test_an_unisolated_pack_is_refused_and_never_imported(lock, tmp_path, monkeypatch):
    """The adapter would have imported and called this function in-process. serve must
    route it to run_tool, which refuses an unverified pack when no sandbox exists."""
    from agentnode_sdk import mcp_tools
    from agentnode_sdk.sandbox import set_default_backend

    pkg = tmp_path / "pkgs"
    (pkg / "bypass_marker_pack").mkdir(parents=True)
    (pkg / "bypass_marker_pack" / "__init__.py").write_text("")
    (pkg / "bypass_marker_pack" / "tool.py").write_text(MARKER_MODULE)
    marker = tmp_path / "marker.txt"
    monkeypatch.setenv("SMOKE_MARKER", str(marker))
    monkeypatch.syspath_prepend(str(pkg))                   # importable, so a bypass WOULD work
    set_default_backend(None)                               # the real probe, not the suite's double
    monkeypatch.setenv("PATH", "")                          # ... which finds no container runtime
    monkeypatch.delenv("PATHEXT", raising=False)

    lock("bypass-marker-pack", trust_level="unverified",
         entrypoint="bypass_marker_pack.tool",
         tools=[{"name": "run", "entrypoint": "bypass_marker_pack.tool:run"}])
    out = mcp_tools.call_tool("bypass-marker-pack", {})
    assert out.is_error is True, out.text
    assert not marker.exists(), "the pack code ran or was imported in the server process"
    assert "bypass_marker_pack.tool" not in sys.modules


def test_the_marker_would_catch_an_import(tmp_path):
    """Counter-check: importing the marker module does leave the marker."""
    pkg = tmp_path / "p"
    (pkg / "bypass_marker_pack").mkdir(parents=True)
    (pkg / "bypass_marker_pack" / "__init__.py").write_text("")
    (pkg / "bypass_marker_pack" / "tool.py").write_text(MARKER_MODULE)
    marker = tmp_path / "m.txt"
    import os

    # The full environment: a stripped one (no SystemDrive) makes Windows write its shell
    # caches under a literal "%SystemDrive%" directory in the working directory.
    subprocess.run([sys.executable, "-c", "import bypass_marker_pack.tool"], check=True,
                   cwd=str(tmp_path),
                   env=dict(os.environ, PYTHONPATH=str(pkg), SMOKE_MARKER=str(marker)))
    assert marker.read_text() == "imported"


def test_no_in_process_import_path_exists_in_serve():
    for f in ("mcp_tools.py", "mcp_server.py"):
        src = (SDK / "agentnode_sdk" / f).read_text(encoding="utf-8")
        for needle in ("importlib", "import_module", "__import__("):
            assert needle not in src, f"{f} contains {needle}"


# ---------------------------------------------------------------- platform tools


def _platform(monkeypatch, handler):
    from agentnode_sdk import mcp_tools

    monkeypatch.setenv("AGENTNODE_API_URL", "https://registry.invalid")
    monkeypatch.setenv("AGENTNODE_API_KEY", "ank_placeholder")
    real = httpx.Client

    def client(**kw):
        return real(transport=httpx.MockTransport(handler), **kw)

    monkeypatch.setattr(mcp_tools, "_http_client", client)
    return mcp_tools


def test_search_keeps_its_request_and_answer_shape(monkeypatch):
    seen = {}

    def handler(req):
        seen["path"], seen["body"] = req.url.path, json.loads(req.content)
        seen["key"] = req.headers.get("x-api-key")
        return httpx.Response(200, json={"total": 1, "hits": [{
            "slug": "pdf-pack", "name": "PDF", "summary": "s", "trust_level": "verified",
            "frameworks": ["generic"], "download_count": 3}]})

    out = _platform(monkeypatch, handler).call_tool(
        "agentnode_search", {"query": "pdf", "trust_level": "verified"})
    assert seen == {"path": "/v1/search", "body": {"q": "pdf", "trust_level": "verified"},
                    "key": "ank_placeholder"}
    assert out.is_error is False
    assert json.loads(out.text) == {"total": 1, "packages": [{
        "slug": "pdf-pack", "name": "PDF", "summary": "s", "trust_level": "verified",
        "frameworks": ["generic"], "downloads": 3, "install": "agentnode install pdf-pack"}]}


@pytest.mark.parametrize("name, args, method, path", [
    ("agentnode_resolve", {"capabilities": ["pdf_extraction"]}, "POST", "/v1/resolve"),
    ("agentnode_explain", {"package_slug": "pdf-pack"}, "GET", "/v1/packages/pdf-pack"),
    ("agentnode_capabilities", {"category": "web"}, "GET", "/v1/capabilities"),
])
def test_other_platform_tools_call_the_same_endpoints(monkeypatch, name, args, method, path):
    seen = {}

    def handler(req):
        seen.update(method=req.method, path=req.url.path, query=dict(req.url.params))
        if req.method == "POST":
            seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"ok": True})

    out = _platform(monkeypatch, handler).call_tool(name, args)
    assert (seen["method"], seen["path"]) == (method, path)
    if name == "agentnode_resolve":
        assert seen["body"] == {"capabilities": ["pdf_extraction"], "limit": 5}
    if name == "agentnode_capabilities":
        assert seen["query"] == {"category": "web"}
    assert out.is_error is False and json.loads(out.text) == {"ok": True}


def test_a_platform_failure_is_an_mcp_error(monkeypatch):
    out = _platform(monkeypatch, lambda req: httpx.Response(500, json={"x": 1})).call_tool(
        "agentnode_explain", {"package_slug": "x"})
    assert out.is_error is True and "error" in json.loads(out.text)
