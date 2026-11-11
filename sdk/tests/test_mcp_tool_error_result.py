"""Task #5 (part 1): a tool error reported by a local MCP server is not a success.

MCP reports a failed tool call as a normal JSON-RPC result with ``isError: true``
(only protocol faults are JSON-RPC errors). ``run_tool`` returned ``success=True``
for those, so an agent took the error text for an answer. The remote path already
checks ``isError``. A synthetic stdio server, run on the host as a curated MCP.
"""

from __future__ import annotations

import sys
import textwrap

import pytest

SERVER = textwrap.dedent(
    '''
    import json, sys
    for raw in sys.stdin:
        msg = json.loads(raw) if raw.strip() else {}
        mid, method = msg.get("id"), msg.get("method")
        if method == "initialize":
            out = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                   "serverInfo": {"name": "err", "version": "1"}}
        elif method == "tools/list":
            out = {"tools": [{"name": "divide", "inputSchema": {"type": "object"}}]}
        elif method == "tools/call":
            b = (msg.get("params") or {}).get("arguments", {}).get("b")
            if b == 0:
                out = {"isError": True, "content": [{"type": "text", "text": "division by zero"}]}
            else:
                out = {"isError": False, "content": [{"type": "text", "text": "ok"}]}
        else:
            continue
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": out}) + "\\n")
        sys.stdout.flush()
    '''
)


@pytest.fixture
def lockfile(tmp_path, monkeypatch):
    from agentnode_sdk import installer
    from agentnode_sdk.lock_integrity import seal_entry

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("AGENTNODE_CONFIG", str(home / "config.json"))
    server = tmp_path / "err_server.py"
    server.write_text(SERVER, encoding="utf-8")
    entry = seal_entry({
        "version": "1.0.0", "package_type": "mcp", "runtime": "mcp",
        "trust_level": "curated", "mcp_command": [sys.executable, str(server)],
        "artifact_hash": "", "permissions": {},
        "tools": [{"name": "divide", "action_type": "compute"}],
    })
    lf = tmp_path / "agentnode.lock"
    installer.update_lockfile("err-mcp", entry, path=lf)
    return lf


def _run(lockfile, **args):
    from agentnode_sdk.runner import run_tool

    return run_tool("err-mcp", "divide", lockfile_path=lockfile, timeout=60,
                    mcp_consent_callback=lambda *a, **k: True, **args)


def test_a_tool_error_is_reported_as_a_failure(lockfile):
    res = _run(lockfile, a=1, b=0)
    assert res.success is False
    assert "division by zero" in (res.error or "")


def test_a_successful_call_stays_a_success(lockfile):
    res = _run(lockfile, a=1, b=2)
    assert res.success is True, res.error
