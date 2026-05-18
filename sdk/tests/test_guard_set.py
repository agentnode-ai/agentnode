"""Tests for Phase 7.3: agentnode guard set/reset."""
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
    monkeypatch.setenv("AGENTNODE_CONFIG", str(cfg_file))
    monkeypatch.setenv("AGENTNODE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("AGENTNODE_GUARD_STRICT", raising=False)
    reset_guard_config_cache()
    reset_rate_limits()
    yield
    reset_guard_config_cache()
    reset_rate_limits()


# ---------------------------------------------------------------------------
# cmd_guard_set
# ---------------------------------------------------------------------------

class TestGuardSet:
    def test_set_valid_action(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_set
        ret = cmd_guard_set("delete", "deny")
        assert ret == 0
        out = capsys.readouterr().out
        assert "guard.delete" in out
        assert "deny" in out

    def test_set_persists_to_config(self):
        from agentnode_sdk.cli.commands import cmd_guard_set
        from agentnode_sdk.config import load_config
        cmd_guard_set("execute", "deny")
        reset_guard_config_cache()
        cfg = load_config()
        assert cfg["guard"]["execute"] == "deny"

    def test_set_updates_resolved_policy(self):
        from agentnode_sdk.cli.commands import cmd_guard_set
        from agentnode_sdk.guard import get_resolved_policy
        cmd_guard_set("read", "prompt")
        result = get_resolved_policy()
        assert result["action_policies"]["read"] == "prompt"

    def test_set_case_insensitive(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_set
        ret = cmd_guard_set("DELETE", "DENY")
        assert ret == 0

    def test_set_preserves_other_keys(self):
        from agentnode_sdk.cli.commands import cmd_guard_set
        from agentnode_sdk.config import load_config
        cmd_guard_set("delete", "deny")
        reset_guard_config_cache()
        cfg = load_config()
        assert cfg["guard"]["read"] == "allow"
        assert cfg["guard"]["unknown"] == "prompt"

    def test_invalid_action_type(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_set
        ret = cmd_guard_set("explode", "deny")
        assert ret == 1
        err = capsys.readouterr().err
        assert "Unknown action type" in err

    def test_invalid_value(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_set
        ret = cmd_guard_set("delete", "maybe")
        assert ret == 1
        err = capsys.readouterr().err
        assert "Invalid value" in err

    def test_invalid_action_type_shows_valid_list(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_set
        cmd_guard_set("bad", "deny")
        err = capsys.readouterr().err
        assert "read" in err
        assert "delete" in err

    def test_invalid_value_shows_valid_list(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_set
        cmd_guard_set("delete", "bad")
        err = capsys.readouterr().err
        assert "allow" in err
        assert "prompt" in err
        assert "deny" in err

    def test_all_action_types_accepted(self):
        from agentnode_sdk.cli.commands import cmd_guard_set
        for at in ["read", "compute", "write_local", "write_external",
                    "delete", "execute", "credential_use", "network_egress", "unknown"]:
            ret = cmd_guard_set(at, "prompt")
            assert ret == 0, f"Failed for action type: {at}"

    def test_all_values_accepted(self):
        from agentnode_sdk.cli.commands import cmd_guard_set
        for val in ["allow", "prompt", "deny"]:
            ret = cmd_guard_set("read", val)
            assert ret == 0, f"Failed for value: {val}"


# ---------------------------------------------------------------------------
# cmd_guard_reset
# ---------------------------------------------------------------------------

class TestGuardReset:
    def test_reset_returns_zero(self):
        from agentnode_sdk.cli.commands import cmd_guard_reset
        assert cmd_guard_reset() == 0

    def test_reset_shows_confirmation(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_reset
        cmd_guard_reset()
        out = capsys.readouterr().out
        assert "reset" in out.lower() or "default" in out.lower()

    def test_reset_restores_defaults(self):
        from agentnode_sdk.cli.commands import cmd_guard_set, cmd_guard_reset
        from agentnode_sdk.config import load_config, DEFAULTS

        cmd_guard_set("delete", "allow")
        cmd_guard_set("read", "deny")
        cmd_guard_reset()
        reset_guard_config_cache()

        cfg = load_config()
        for key, default_val in DEFAULTS["guard"].items():
            assert cfg["guard"][key] == default_val, f"Mismatch for {key}"

    def test_reset_updates_resolved_policy(self):
        from agentnode_sdk.cli.commands import cmd_guard_set, cmd_guard_reset
        from agentnode_sdk.guard import get_resolved_policy

        cmd_guard_set("read", "deny")
        cmd_guard_reset()

        result = get_resolved_policy()
        assert result["action_policies"]["read"] == "allow"

    def test_reset_preserves_non_guard_config(self):
        from agentnode_sdk.cli.commands import cmd_guard_reset
        from agentnode_sdk.config import load_config

        cmd_guard_reset()
        reset_guard_config_cache()
        cfg = load_config()
        assert "version" in cfg
        assert "permissions" in cfg or "trust" in cfg


# ---------------------------------------------------------------------------
# CLI routing
# ---------------------------------------------------------------------------

class TestGuardSetResetRouting:
    def test_guard_set_routes(self, capsys):
        from agentnode_sdk.cli.main import main
        ret = main(["guard", "set", "delete", "deny"])
        assert ret == 0

    def test_guard_set_invalid_routes(self, capsys):
        from agentnode_sdk.cli.main import main
        ret = main(["guard", "set", "bad", "deny"])
        assert ret == 1

    def test_guard_reset_routes(self, capsys):
        from agentnode_sdk.cli.main import main
        ret = main(["guard", "reset"])
        assert ret == 0
