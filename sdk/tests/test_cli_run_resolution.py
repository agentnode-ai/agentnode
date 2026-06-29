"""CLI `run` resolution (0.11.2): `slug:tool` is split at the CLI boundary so the
real slug feeds trust/lockfile resolution, and the tool name is forwarded."""
from agentnode_sdk.cli import commands
from agentnode_sdk.models import RunToolResult


def _spy_run_tool(captured):
    def fake(slug, tool_name=None, confirmation_callback=None, mcp_consent_callback=None, **kwargs):
        captured["slug"] = slug
        captured["tool_name"] = tool_name
        captured["kwargs"] = kwargs
        return RunToolResult(success=True, result={"ok": True}, mode_used="subprocess")
    return fake


def test_cmd_run_splits_slug_tool(monkeypatch):
    """`run <slug>:<tool>` → run_tool(real_slug, tool_name=tool). The real slug
    is what reaches run_tool, so trust/lockfile resolve on it (not the colon
    string treated as an unknown unverified package)."""
    captured = {}
    monkeypatch.setattr("agentnode_sdk.runner.run_tool", _spy_run_tool(captured))
    rc = commands.cmd_run("word-counter-pack:count_words", input_data='{"text":"a b"}', json_output=True)
    assert rc == 0
    assert captured["slug"] == "word-counter-pack"
    assert captured["tool_name"] == "count_words"
    assert captured["kwargs"] == {"text": "a b"}


def test_cmd_run_bare_slug_has_no_tool(monkeypatch):
    """Bare `run <slug>` still forwards tool_name=None (unchanged)."""
    captured = {}
    monkeypatch.setattr("agentnode_sdk.runner.run_tool", _spy_run_tool(captured))
    rc = commands.cmd_run("word-counter-pack", input_data='{"text":"a"}', json_output=True)
    assert rc == 0
    assert captured["slug"] == "word-counter-pack"
    assert captured["tool_name"] is None
