"""Tests for Phase 9: agentnode guard check (policy dry-run)."""
from __future__ import annotations

import json

import pytest

from agentnode_sdk.guard import reset_guard_config_cache, reset_rate_limits


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

    lockfile = {
        "version": "0.1",
        "packages": {
            "file-manager": {
                "trust_level": "verified",
                "permissions": {
                    "network_level": "none",
                    "filesystem_level": "read_write",
                    "code_execution_level": "none",
                },
                "package_type": "toolpack",
                "tools": [
                    {"name": "list_files", "action_type": "read"},
                    {"name": "delete_file", "action_type": "delete"},
                ],
            },
            "my-skill": {
                "trust_level": "verified",
                "package_type": "skill",
            },
        },
    }
    lock_file = tmp_path / "agentnode.lock"
    lock_file.write_text(json.dumps(lockfile))

    monkeypatch.setenv("AGENTNODE_CONFIG", str(cfg_file))
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(lock_file))
    monkeypatch.setenv("AGENTNODE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("AGENTNODE_GUARD_STRICT", raising=False)
    reset_guard_config_cache()
    reset_rate_limits()
    yield
    reset_guard_config_cache()
    reset_rate_limits()


# ---------------------------------------------------------------------------
# Text output
# ---------------------------------------------------------------------------

class TestGuardCheckText:
    def test_shows_all_fields(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_check
        ret = cmd_guard_check("file-manager/delete_file")
        assert ret == 0
        out = capsys.readouterr().out
        assert "Guard Check" in out
        assert "file-manager/delete_file" in out
        assert "delete" in out
        assert "manifest" in out
        assert "verified" in out
        assert "Decision" in out
        assert "Source" in out

    def test_read_tool_shows_allow(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_check
        cmd_guard_check("file-manager/list_files")
        out = capsys.readouterr().out
        assert "allow" in out
        assert "read" in out

    def test_delete_tool_shows_prompt(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_check
        cmd_guard_check("file-manager/delete_file")
        out = capsys.readouterr().out
        assert "prompt" in out


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------

class TestGuardCheckJson:
    def test_json_valid(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_check
        ret = cmd_guard_check("file-manager/delete_file", json_output=True)
        assert ret == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, dict)

    def test_json_has_all_fields(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_check
        cmd_guard_check("file-manager/delete_file", json_output=True)
        out = capsys.readouterr().out
        data = json.loads(out)
        for key in ("slug", "tool_name", "action_types", "action_types_source",
                     "trust_level", "risk_level", "decision", "source",
                     "reason", "guard_chain", "mitigations", "strict_mode"):
            assert key in data, f"Missing key: {key}"

    def test_json_values(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_check
        cmd_guard_check("file-manager/delete_file", json_output=True)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["slug"] == "file-manager"
        assert data["tool_name"] == "delete_file"
        assert "delete" in data["action_types"]
        assert data["action_types_source"] == "manifest"
        assert data["decision"] == "prompt"
        assert data["strict_mode"] is False


# ---------------------------------------------------------------------------
# Tool override visible in chain
# ---------------------------------------------------------------------------

class TestGuardCheckToolOverride:
    def test_tool_override_in_chain(self, tmp_path, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_set, cmd_guard_check
        cmd_guard_set("delete", "deny", tool_key="file-manager/delete_file")
        capsys.readouterr()
        cmd_guard_check("file-manager/delete_file", json_output=True)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["decision"] == "deny"
        chain_str = " ".join(data["guard_chain"])
        assert "tool_override[file-manager/delete_file]" in chain_str

    def test_global_policy_no_tool_override_in_chain(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_check
        cmd_guard_check("file-manager/delete_file", json_output=True)
        out = capsys.readouterr().out
        data = json.loads(out)
        chain_str = " ".join(data["guard_chain"])
        assert "tool_override" not in chain_str


# ---------------------------------------------------------------------------
# --action override
# ---------------------------------------------------------------------------

class TestGuardCheckActionOverride:
    def test_action_override_changes_classification(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_check
        cmd_guard_check("file-manager/list_files", action="delete", json_output=True)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["action_types"] == ["delete"]
        assert data["action_types_source"] == "override"
        assert data["decision"] == "prompt"

    def test_action_override_does_not_persist(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_check
        cmd_guard_check("file-manager/list_files", action="delete", json_output=True)
        capsys.readouterr()
        cmd_guard_check("file-manager/list_files", json_output=True)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "read" in data["action_types"]
        assert data["action_types_source"] == "manifest"

    def test_invalid_action_type(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_check
        ret = cmd_guard_check("file-manager/list_files", action="explode")
        assert ret == 1
        err = capsys.readouterr().err
        assert "Unknown action type" in err


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

class TestGuardCheckErrors:
    def test_non_installed_slug(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_check
        ret = cmd_guard_check("nonexistent/tool")
        assert ret == 1
        err = capsys.readouterr().err
        assert "not installed" in err

    def test_missing_slash(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_check
        ret = cmd_guard_check("noslash")
        assert ret == 1
        err = capsys.readouterr().err
        assert "slug/tool_name" in err

    def test_skill_bypass(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_check
        ret = cmd_guard_check("my-skill/anything")
        assert ret == 0
        out = capsys.readouterr().out
        assert "skill" in out.lower()
        assert "bypass" in out.lower()

    def test_unknown_tool_name_warns(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_check
        ret = cmd_guard_check("file-manager/nonexistent_tool")
        assert ret == 0
        err = capsys.readouterr().err
        assert "not found" in err
        assert "heuristic" in err


# ---------------------------------------------------------------------------
# Strict mode
# ---------------------------------------------------------------------------

class TestGuardCheckStrictMode:
    def test_strict_mode_shown_in_text(self, capsys, monkeypatch):
        from agentnode_sdk.cli.commands import cmd_guard_check
        monkeypatch.setenv("AGENTNODE_GUARD_STRICT", "true")
        reset_guard_config_cache()
        cmd_guard_check("file-manager/delete_file")
        out = capsys.readouterr().out
        assert "STRICT" in out

    def test_strict_mode_in_json(self, capsys, monkeypatch):
        from agentnode_sdk.cli.commands import cmd_guard_check
        monkeypatch.setenv("AGENTNODE_GUARD_STRICT", "true")
        reset_guard_config_cache()
        cmd_guard_check("file-manager/delete_file", json_output=True)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["strict_mode"] is True
        assert data["decision"] == "deny"


# ---------------------------------------------------------------------------
# CLI routing
# ---------------------------------------------------------------------------

class TestGuardCheckRouting:
    def test_check_routes(self, capsys):
        from agentnode_sdk.cli.main import main
        ret = main(["guard", "check", "file-manager/delete_file"])
        assert ret == 0

    def test_check_json_routes(self, capsys):
        from agentnode_sdk.cli.main import main
        ret = main(["guard", "check", "file-manager/delete_file", "--json"])
        assert ret == 0

    def test_check_action_routes(self, capsys):
        from agentnode_sdk.cli.main import main
        ret = main(["guard", "check", "file-manager/list_files", "--action", "delete"])
        assert ret == 0
