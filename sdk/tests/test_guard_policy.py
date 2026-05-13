"""Tests for Phase 7.1: agentnode guard policy."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentnode_sdk.guard import (
    get_resolved_policy,
    reset_guard_config_cache,
    reset_rate_limits,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _guard_env(tmp_path, monkeypatch):
    """Default guard config for policy tests."""
    cfg = {
        "version": "1",
        "trust": {"minimum_trust_level": "verified"},
        "permissions": {"network": "allow", "filesystem": "allow", "code_execution": "sandboxed"},
        "guard": {
            "delete": "deny",
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
# get_resolved_policy() unit tests
# ---------------------------------------------------------------------------

class TestGetResolvedPolicy:
    def test_returns_dict(self):
        result = get_resolved_policy()
        assert isinstance(result, dict)

    def test_has_required_keys(self):
        result = get_resolved_policy()
        assert "strict_mode" in result
        assert "action_policies" in result
        assert "credential_rule" in result
        assert "rate_limits" in result
        assert "agent_overrides" in result

    def test_default_not_strict(self):
        result = get_resolved_policy()
        assert result["strict_mode"] is False

    def test_strict_mode_active(self, monkeypatch):
        monkeypatch.setenv("AGENTNODE_GUARD_STRICT", "true")
        reset_guard_config_cache()
        result = get_resolved_policy()
        assert result["strict_mode"] is True

    def test_action_policies_from_config(self):
        result = get_resolved_policy()
        assert result["action_policies"]["delete"] == "deny"
        assert result["action_policies"]["read"] == "allow"
        assert result["action_policies"]["write_external"] == "prompt"

    def test_strict_overrides_action_policies(self, monkeypatch):
        monkeypatch.setenv("AGENTNODE_GUARD_STRICT", "true")
        reset_guard_config_cache()
        result = get_resolved_policy()
        assert result["action_policies"]["delete"] == "deny"
        assert result["action_policies"]["execute"] == "deny"
        assert result["action_policies"]["read"] == "allow"

    def test_rate_limits_has_three_tiers(self):
        result = get_resolved_policy()
        rl = result["rate_limits"]
        assert "toolpack" in rl
        assert "agent" in rl
        assert "strict" in rl

    def test_rate_limits_toolpack_defaults(self):
        result = get_resolved_policy()
        tp = result["rate_limits"]["toolpack"]
        assert tp["calls_per_minute"] == 60
        assert tp["calls_per_hour"] == 1000
        assert tp["burst_size"] == 10

    def test_rate_limits_agent_defaults(self):
        result = get_resolved_policy()
        agent = result["rate_limits"]["agent"]
        assert agent["calls_per_minute"] == 120
        assert agent["calls_per_hour"] == 2000
        assert agent["burst_size"] == 20

    def test_rate_limits_strict_defaults(self):
        result = get_resolved_policy()
        strict = result["rate_limits"]["strict"]
        assert strict["calls_per_minute"] == 30
        assert strict["calls_per_hour"] == 500

    def test_agent_overrides_empty_by_default(self):
        result = get_resolved_policy()
        assert result["agent_overrides"] == {}

    def test_agent_overrides_from_config(self, tmp_path, monkeypatch):
        cfg = {
            "version": "1",
            "guard": {
                "read": "allow",
                "agent_overrides": {
                    "my-agent": {"pre_approved_actions": ["read", "compute"]},
                },
            },
        }
        cfg_file = tmp_path / "config2.json"
        cfg_file.write_text(json.dumps(cfg))
        monkeypatch.setenv("AGENTNODE_CONFIG", str(cfg_file))
        reset_guard_config_cache()
        result = get_resolved_policy()
        assert "my-agent" in result["agent_overrides"]
        assert result["agent_overrides"]["my-agent"]["pre_approved_actions"] == ["read", "compute"]

    def test_credential_rule_present(self):
        result = get_resolved_policy()
        assert "connector" in result["credential_rule"]

    def test_no_side_effects(self):
        """get_resolved_policy() must not modify guard state."""
        get_resolved_policy()
        get_resolved_policy()
        result = get_resolved_policy()
        assert result["strict_mode"] is False


# ---------------------------------------------------------------------------
# cmd_guard_policy() CLI output tests
# ---------------------------------------------------------------------------

class TestCmdGuardPolicy:
    def test_text_shows_guard_policy_header(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_policy
        cmd_guard_policy()
        out = capsys.readouterr().out
        assert "Guard Policy" in out

    def test_text_shows_mode(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_policy
        cmd_guard_policy()
        out = capsys.readouterr().out
        assert "default" in out

    def test_text_shows_strict_mode(self, capsys, monkeypatch):
        from agentnode_sdk.cli.commands import cmd_guard_policy
        monkeypatch.setenv("AGENTNODE_GUARD_STRICT", "true")
        reset_guard_config_cache()
        cmd_guard_policy()
        out = capsys.readouterr().out
        assert "STRICT" in out

    def test_text_shows_action_types(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_policy
        cmd_guard_policy()
        out = capsys.readouterr().out
        assert "read" in out
        assert "delete" in out
        assert "execute" in out
        assert "unknown" in out

    def test_text_shows_policy_values(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_policy
        cmd_guard_policy()
        out = capsys.readouterr().out
        assert "allow" in out
        assert "prompt" in out
        assert "deny" in out

    def test_text_shows_credential_rule(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_policy
        cmd_guard_policy()
        out = capsys.readouterr().out
        assert "connector" in out

    def test_text_shows_rate_limits(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_policy
        cmd_guard_policy()
        out = capsys.readouterr().out
        assert "Rate Limits" in out
        assert "Toolpack" in out
        assert "Agent" in out
        assert "Strict" in out

    def test_text_shows_agent_overrides(self, capsys, tmp_path, monkeypatch):
        from agentnode_sdk.cli.commands import cmd_guard_policy
        cfg = {
            "version": "1",
            "guard": {
                "read": "allow",
                "agent_overrides": {
                    "my-agent": {"pre_approved_actions": ["read", "compute"]},
                },
            },
        }
        cfg_file = tmp_path / "config2.json"
        cfg_file.write_text(json.dumps(cfg))
        monkeypatch.setenv("AGENTNODE_CONFIG", str(cfg_file))
        reset_guard_config_cache()
        cmd_guard_policy()
        out = capsys.readouterr().out
        assert "Agent Overrides" in out
        assert "my-agent" in out

    def test_text_no_overrides_section_when_empty(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_policy
        cmd_guard_policy()
        out = capsys.readouterr().out
        assert "Agent Overrides" not in out

    def test_json_output(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_policy
        cmd_guard_policy(json_output=True)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "strict_mode" in data
        assert "action_policies" in data
        assert "rate_limits" in data

    def test_json_has_all_action_types(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_policy
        cmd_guard_policy(json_output=True)
        data = json.loads(capsys.readouterr().out)
        expected = {"read", "compute", "write_local", "write_external",
                    "delete", "execute", "credential_use", "network_egress", "unknown"}
        assert set(data["action_policies"].keys()) == expected

    def test_returns_zero(self):
        from agentnode_sdk.cli.commands import cmd_guard_policy
        assert cmd_guard_policy() == 0

    def test_returns_zero_json(self):
        from agentnode_sdk.cli.commands import cmd_guard_policy
        assert cmd_guard_policy(json_output=True) == 0


# ---------------------------------------------------------------------------
# CLI integration via main()
# ---------------------------------------------------------------------------

class TestGuardCLIRouting:
    def test_guard_policy_routes(self, capsys):
        from agentnode_sdk.cli.main import main
        ret = main(["guard", "policy"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "Guard Policy" in out

    def test_guard_policy_json_routes(self, capsys):
        from agentnode_sdk.cli.main import main
        ret = main(["guard", "policy", "--json"])
        assert ret == 0
        data = json.loads(capsys.readouterr().out)
        assert "action_policies" in data

    def test_guard_no_subcommand_shows_help(self, capsys):
        from agentnode_sdk.cli.main import main
        ret = main(["guard"])
        assert ret == 1
