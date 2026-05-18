"""Tests for Phase 8.2: Per-tool guard policy CLI."""
from __future__ import annotations

import json

import pytest

from agentnode_sdk.guard import get_resolved_policy, reset_guard_config_cache, reset_rate_limits


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _guard_env(tmp_path, monkeypatch):
    cfg = {
        "version": "1",
        "guard": {
            "delete": "prompt",
            "write_external": "prompt",
            "execute": "prompt",
            "credential_use": "prompt",
            "network_egress": "allow",
            "write_local": "allow",
            "read": "allow",
            "compute": "allow",
            "unknown": "prompt",
        },
    }
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(cfg))
    monkeypatch.setenv("AGENTNODE_CONFIG", str(cfg_file))
    monkeypatch.setenv("AGENTNODE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("AGENTNODE_GUARD_STRICT", raising=False)
    reset_guard_config_cache()
    reset_rate_limits()
    yield
    reset_guard_config_cache()
    reset_rate_limits()


# ---------------------------------------------------------------------------
# guard set --tool
# ---------------------------------------------------------------------------

class TestGuardSetTool:
    def test_set_tool_returns_zero(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_set
        ret = cmd_guard_set("delete", "deny", tool_key="pkg/tool")
        assert ret == 0

    def test_set_tool_shows_path(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_set
        cmd_guard_set("delete", "deny", tool_key="pkg/tool")
        out = capsys.readouterr().out
        assert "tool_overrides" in out
        assert "pkg/tool" in out
        assert "deny" in out

    def test_set_tool_persists(self):
        from agentnode_sdk.cli.commands import cmd_guard_set
        from agentnode_sdk.config import load_config
        cmd_guard_set("delete", "deny", tool_key="pkg/my_tool")
        reset_guard_config_cache()
        cfg = load_config()
        assert cfg["guard"]["tool_overrides"]["pkg/my_tool"]["delete"] == "deny"

    def test_set_tool_takes_effect_after_cache_reset(self):
        from agentnode_sdk.cli.commands import cmd_guard_set
        from agentnode_sdk.guard import check_action
        cmd_guard_set("read", "deny", tool_key="pkg/reader")
        entry = {
            "trust_level": "verified",
            "permissions": {"network_level": "none", "filesystem_level": "none", "code_execution_level": "none"},
            "package_type": "toolpack",
            "tools": [{"name": "reader", "action_type": "read"}],
        }
        result = check_action("pkg", "reader", {}, entry)
        assert result.action == "deny"

    def test_set_tool_preserves_global(self):
        from agentnode_sdk.cli.commands import cmd_guard_set
        from agentnode_sdk.config import load_config
        cmd_guard_set("delete", "deny", tool_key="pkg/tool")
        reset_guard_config_cache()
        cfg = load_config()
        assert cfg["guard"]["delete"] == "prompt"

    def test_set_tool_preserves_other_tool_overrides(self):
        from agentnode_sdk.cli.commands import cmd_guard_set
        from agentnode_sdk.config import load_config
        cmd_guard_set("delete", "deny", tool_key="pkg/tool_a")
        cmd_guard_set("read", "prompt", tool_key="pkg/tool_b")
        reset_guard_config_cache()
        cfg = load_config()
        assert cfg["guard"]["tool_overrides"]["pkg/tool_a"]["delete"] == "deny"
        assert cfg["guard"]["tool_overrides"]["pkg/tool_b"]["read"] == "prompt"

    def test_set_tool_invalid_key_no_slash(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_set
        ret = cmd_guard_set("delete", "deny", tool_key="noslash")
        assert ret == 1
        err = capsys.readouterr().err
        assert "Invalid tool key" in err

    def test_set_tool_invalid_action_type(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_set
        ret = cmd_guard_set("fake", "deny", tool_key="pkg/tool")
        assert ret == 1
        err = capsys.readouterr().err
        assert "Unknown action type" in err

    def test_set_tool_invalid_value(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_set
        ret = cmd_guard_set("delete", "maybe", tool_key="pkg/tool")
        assert ret == 1
        err = capsys.readouterr().err
        assert "Invalid value" in err

    def test_set_tool_case_insensitive(self):
        from agentnode_sdk.cli.commands import cmd_guard_set
        ret = cmd_guard_set("DELETE", "DENY", tool_key="pkg/tool")
        assert ret == 0

    def test_set_multiple_actions_same_tool(self):
        from agentnode_sdk.cli.commands import cmd_guard_set
        from agentnode_sdk.config import load_config
        cmd_guard_set("delete", "deny", tool_key="pkg/tool")
        cmd_guard_set("execute", "deny", tool_key="pkg/tool")
        reset_guard_config_cache()
        cfg = load_config()
        assert cfg["guard"]["tool_overrides"]["pkg/tool"]["delete"] == "deny"
        assert cfg["guard"]["tool_overrides"]["pkg/tool"]["execute"] == "deny"


# ---------------------------------------------------------------------------
# guard unset --tool
# ---------------------------------------------------------------------------

class TestGuardUnset:
    def test_unset_full_tool_block(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_set, cmd_guard_unset
        from agentnode_sdk.config import load_config
        cmd_guard_set("delete", "deny", tool_key="pkg/tool")
        cmd_guard_set("read", "deny", tool_key="pkg/tool")
        ret = cmd_guard_unset(tool_key="pkg/tool")
        assert ret == 0
        reset_guard_config_cache()
        cfg = load_config()
        assert "pkg/tool" not in cfg["guard"].get("tool_overrides", {})

    def test_unset_single_action_type(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_set, cmd_guard_unset
        from agentnode_sdk.config import load_config
        cmd_guard_set("delete", "deny", tool_key="pkg/tool")
        cmd_guard_set("read", "deny", tool_key="pkg/tool")
        ret = cmd_guard_unset(tool_key="pkg/tool", action_type="delete")
        assert ret == 0
        reset_guard_config_cache()
        cfg = load_config()
        tool_ov = cfg["guard"]["tool_overrides"]["pkg/tool"]
        assert "delete" not in tool_ov
        assert tool_ov["read"] == "deny"

    def test_unset_last_action_removes_tool_block(self):
        from agentnode_sdk.cli.commands import cmd_guard_set, cmd_guard_unset
        from agentnode_sdk.config import load_config
        cmd_guard_set("delete", "deny", tool_key="pkg/tool")
        cmd_guard_unset(tool_key="pkg/tool", action_type="delete")
        reset_guard_config_cache()
        cfg = load_config()
        assert "pkg/tool" not in cfg["guard"].get("tool_overrides", {})

    def test_unset_nonexistent_tool_is_noop(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_unset
        ret = cmd_guard_unset(tool_key="pkg/nonexistent")
        assert ret == 0
        out = capsys.readouterr().out
        assert "No overrides" in out

    def test_unset_invalid_tool_key(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_unset
        ret = cmd_guard_unset(tool_key="noslash")
        assert ret == 1
        err = capsys.readouterr().err
        assert "Invalid tool key" in err

    def test_unset_invalid_action_type(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_unset
        ret = cmd_guard_unset(tool_key="pkg/tool", action_type="fake")
        assert ret == 1
        err = capsys.readouterr().err
        assert "Unknown action type" in err

    def test_unset_shows_confirmation(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_set, cmd_guard_unset
        cmd_guard_set("delete", "deny", tool_key="pkg/tool")
        cmd_guard_unset(tool_key="pkg/tool")
        out = capsys.readouterr().out
        assert "pkg/tool" in out

    def test_unset_invalidates_cache(self):
        from agentnode_sdk.cli.commands import cmd_guard_set, cmd_guard_unset
        cmd_guard_set("read", "deny", tool_key="pkg/tool")
        result1 = get_resolved_policy()
        assert "pkg/tool" in result1["tool_overrides"]
        cmd_guard_unset(tool_key="pkg/tool")
        result2 = get_resolved_policy()
        assert "pkg/tool" not in result2.get("tool_overrides", {})


# ---------------------------------------------------------------------------
# guard policy — tool overrides display
# ---------------------------------------------------------------------------

class TestGuardPolicyDisplay:
    def test_policy_shows_tool_overrides_section(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_set, cmd_guard_policy
        cmd_guard_set("delete", "deny", tool_key="pkg/tool")
        cmd_guard_policy()
        out = capsys.readouterr().out
        assert "Tool Overrides" in out
        assert "pkg/tool" in out
        assert "deny" in out

    def test_policy_omits_tool_overrides_when_empty(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_policy
        cmd_guard_policy()
        out = capsys.readouterr().out
        assert "Tool Overrides" not in out

    def test_policy_json_includes_tool_overrides(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_set, cmd_guard_policy
        cmd_guard_set("delete", "deny", tool_key="pkg/tool")
        capsys.readouterr()
        cmd_guard_policy(json_output=True)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "tool_overrides" in data
        assert data["tool_overrides"]["pkg/tool"]["delete"] == "deny"

    def test_policy_json_empty_overrides(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_policy
        cmd_guard_policy(json_output=True)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["tool_overrides"] == {}

    def test_policy_multiple_tools_sorted(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_set, cmd_guard_policy
        cmd_guard_set("delete", "deny", tool_key="z-pkg/tool")
        cmd_guard_set("read", "prompt", tool_key="a-pkg/tool")
        capsys.readouterr()
        cmd_guard_policy()
        out = capsys.readouterr().out
        pos_a = out.index("a-pkg/tool")
        pos_z = out.index("z-pkg/tool")
        assert pos_a < pos_z


# ---------------------------------------------------------------------------
# guard reset — clears tool overrides
# ---------------------------------------------------------------------------

class TestGuardResetClearsOverrides:
    def test_reset_clears_tool_overrides(self):
        from agentnode_sdk.cli.commands import cmd_guard_set, cmd_guard_reset
        from agentnode_sdk.config import load_config
        cmd_guard_set("delete", "deny", tool_key="pkg/tool")
        cmd_guard_reset()
        reset_guard_config_cache()
        cfg = load_config()
        assert "tool_overrides" not in cfg["guard"]

    def test_reset_invalidates_cache_for_tool_overrides(self):
        from agentnode_sdk.cli.commands import cmd_guard_set, cmd_guard_reset
        cmd_guard_set("delete", "deny", tool_key="pkg/tool")
        result1 = get_resolved_policy()
        assert "pkg/tool" in result1["tool_overrides"]
        cmd_guard_reset()
        result2 = get_resolved_policy()
        assert result2["tool_overrides"] == {}


# ---------------------------------------------------------------------------
# CLI routing
# ---------------------------------------------------------------------------

class TestGuardToolOverrideRouting:
    def test_guard_set_tool_routes(self, capsys):
        from agentnode_sdk.cli.main import main
        ret = main(["guard", "set", "delete", "deny", "--tool", "pkg/tool"])
        assert ret == 0

    def test_guard_set_tool_invalid_routes(self, capsys):
        from agentnode_sdk.cli.main import main
        ret = main(["guard", "set", "delete", "deny", "--tool", "noslash"])
        assert ret == 1

    def test_guard_unset_tool_routes(self, capsys):
        from agentnode_sdk.cli.main import main
        main(["guard", "set", "delete", "deny", "--tool", "pkg/tool"])
        ret = main(["guard", "unset", "--tool", "pkg/tool"])
        assert ret == 0

    def test_guard_unset_action_tool_routes(self, capsys):
        from agentnode_sdk.cli.main import main
        main(["guard", "set", "delete", "deny", "--tool", "pkg/tool"])
        ret = main(["guard", "unset", "delete", "--tool", "pkg/tool"])
        assert ret == 0
