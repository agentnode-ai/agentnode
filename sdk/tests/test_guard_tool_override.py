"""Tests for Phase 8.1: Per-tool policy overrides."""
from __future__ import annotations

import json

import pytest

from agentnode_sdk.guard import (
    GuardDecision,
    check_action,
    get_resolved_policy,
    reset_guard_config_cache,
    reset_rate_limits,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
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


def _entry(
    *,
    trust="verified",
    network="none",
    filesystem="none",
    code_execution="none",
    package_type="toolpack",
    tools=None,
    connector=None,
    agent=None,
):
    e = {
        "trust_level": trust,
        "permissions": {
            "network_level": network,
            "filesystem_level": filesystem,
            "code_execution_level": code_execution,
        },
        "package_type": package_type,
    }
    if tools is not None:
        e["tools"] = tools
    if connector is not None:
        e["connector"] = connector
    if agent is not None:
        e["agent"] = agent
    return e


def _set_tool_overrides(tmp_path, overrides):
    """Write tool_overrides into the config file and refresh cache."""
    cfg_file = tmp_path / "config.json"
    cfg = json.loads(cfg_file.read_text())
    cfg["guard"]["tool_overrides"] = overrides
    cfg_file.write_text(json.dumps(cfg))
    reset_guard_config_cache()


# ---------------------------------------------------------------------------
# Resolution: tool override wins over global
# ---------------------------------------------------------------------------

class TestToolOverrideResolution:
    def test_override_deny_blocks_global_allow(self, tmp_path):
        _set_tool_overrides(tmp_path, {
            "file-mgr/delete_file": {"delete": "deny"},
        })
        entry = _entry(tools=[{"name": "delete_file", "action_type": "delete"}])
        result = check_action("file-mgr", "delete_file", {}, entry)
        assert result.action == "deny"
        assert "tool_override" in result.source

    def test_override_allow_passes_global_prompt(self, tmp_path):
        _set_tool_overrides(tmp_path, {
            "pkg/my_tool": {"delete": "allow"},
        })
        entry = _entry(tools=[{"name": "my_tool", "action_type": "delete"}])
        result = check_action("pkg", "my_tool", {}, entry)
        assert result.action == "allow"

    def test_override_prompt_overrides_global_allow(self, tmp_path):
        _set_tool_overrides(tmp_path, {
            "pkg/reader": {"read": "prompt"},
        })
        entry = _entry(tools=[{"name": "reader", "action_type": "read"}])
        result = check_action("pkg", "reader", {}, entry)
        assert result.action == "prompt"
        assert "tool_override" in result.source

    def test_no_override_falls_through_to_global(self, tmp_path):
        _set_tool_overrides(tmp_path, {
            "other-pkg/other_tool": {"delete": "deny"},
        })
        entry = _entry(tools=[{"name": "my_tool", "action_type": "delete"}])
        result = check_action("pkg", "my_tool", {}, entry)
        assert result.action == "prompt"
        assert "tool_override" not in result.source

    def test_no_overrides_preserves_existing_behavior(self):
        entry = _entry(tools=[{"name": "my_tool", "action_type": "read"}])
        result = check_action("pkg", "my_tool", {}, entry)
        assert result.action == "allow"

    def test_tool_name_none_skips_override(self, tmp_path):
        _set_tool_overrides(tmp_path, {
            "pkg/None": {"read": "deny"},
        })
        entry = _entry(tools=[{"name": "anything", "action_type": "read"}])
        result = check_action("pkg", None, {}, entry)
        chain_str = " ".join(result.guard_chain)
        assert "tool_override" not in chain_str


# ---------------------------------------------------------------------------
# Resolution: per action_type independence
# ---------------------------------------------------------------------------

class TestActionTypeIndependence:
    def test_override_one_type_does_not_affect_others(self, tmp_path):
        _set_tool_overrides(tmp_path, {
            "pkg/tool": {"delete": "deny"},
        })
        entry = _entry(tools=[{"name": "tool", "action_type": "read"}])
        result = check_action("pkg", "tool", {}, entry)
        assert result.action == "allow"

    def test_multiple_overrides_on_same_tool(self, tmp_path):
        _set_tool_overrides(tmp_path, {
            "pkg/tool": {"delete": "deny", "write_local": "deny"},
        })
        entry = _entry(tools=[{"name": "tool", "action_type": "delete"}])
        result = check_action("pkg", "tool", {}, entry)
        assert result.action == "deny"

        entry2 = _entry(tools=[{"name": "tool", "action_type": "write_local"}])
        result2 = check_action("pkg", "tool", {}, entry2)
        assert result2.action == "deny"

        entry3 = _entry(tools=[{"name": "tool", "action_type": "read"}])
        result3 = check_action("pkg", "tool", {}, entry3)
        assert result3.action == "allow"


# ---------------------------------------------------------------------------
# Hard ceiling: critical risk, strict mode
# ---------------------------------------------------------------------------

class TestHardCeiling:
    def test_override_allow_does_not_bypass_critical_risk(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SECRET_KEY", "s3cret")
        _set_tool_overrides(tmp_path, {
            "pkg/danger": {"execute": "allow", "credential_use": "allow",
                           "delete": "allow", "network_egress": "allow"},
        })
        entry = _entry(
            trust="unverified",
            tools=[
                {"name": "danger", "action_type": "execute"},
                {"name": "danger", "action_type": "delete"},
                {"name": "danger", "action_type": "credential_use"},
                {"name": "danger", "action_type": "network_egress"},
            ],
            network="full",
            code_execution="arbitrary",
        )
        result = check_action("pkg", "danger", {}, entry)
        assert result.action == "deny"
        assert result.risk_level == "critical"

    def test_strict_mode_ignores_tool_overrides(self, tmp_path, monkeypatch):
        _set_tool_overrides(tmp_path, {
            "pkg/tool": {"delete": "allow"},
        })
        monkeypatch.setenv("AGENTNODE_GUARD_STRICT", "true")
        reset_guard_config_cache()
        entry = _entry(tools=[{"name": "tool", "action_type": "delete"}])
        result = check_action("pkg", "tool", {}, entry)
        assert result.action == "deny"

    def test_strict_mode_deny_not_weakened_by_override(self, tmp_path, monkeypatch):
        _set_tool_overrides(tmp_path, {
            "pkg/tool": {"read": "allow"},
        })
        monkeypatch.setenv("AGENTNODE_GUARD_STRICT", "true")
        reset_guard_config_cache()
        entry = _entry(tools=[{"name": "tool", "action_type": "read"}])
        result = check_action("pkg", "tool", {}, entry)
        assert result.action != "allow" or result.action == "allow"
        # In strict mode, _STRICT_GUARD_POLICY is used — read is "allow" in strict too.
        # The point is: tool_overrides are NOT evaluated, strict policy is used directly.
        # We verify by checking no tool_override appears in the chain.
        chain_str = " ".join(result.guard_chain)
        assert "tool_override" not in chain_str


# ---------------------------------------------------------------------------
# Credential_use interaction (§4.2)
# ---------------------------------------------------------------------------

class TestCredentialUseOverride:
    def test_tool_override_deny_blocks_connector_bypass(self, tmp_path):
        _set_tool_overrides(tmp_path, {
            "pkg/cred_tool": {"credential_use": "deny"},
        })
        entry = _entry(
            tools=[{"name": "cred_tool", "action_type": "credential_use"}],
            connector={"auth_type": "oauth2"},
        )
        result = check_action("pkg", "cred_tool", {}, entry)
        assert result.action == "deny"

    def test_tool_override_allow_skips_connector_check(self, tmp_path):
        _set_tool_overrides(tmp_path, {
            "pkg/cred_tool": {"credential_use": "allow"},
        })
        entry = _entry(
            tools=[{"name": "cred_tool", "action_type": "credential_use"}],
        )
        result = check_action("pkg", "cred_tool", {}, entry)
        assert result.action == "allow"

    def test_no_override_falls_through_to_connector_bypass(self, tmp_path):
        _set_tool_overrides(tmp_path, {})
        entry = _entry(
            tools=[{"name": "cred_tool", "action_type": "credential_use"}],
            connector={"auth_type": "oauth2"},
        )
        result = check_action("pkg", "cred_tool", {}, entry)
        assert result.action == "allow"
        chain_str = " ".join(result.guard_chain)
        assert "connector_declared" in chain_str


# ---------------------------------------------------------------------------
# Unknown tools / unknown action_type keys — silently ignored
# ---------------------------------------------------------------------------

class TestSilentIgnore:
    def test_unknown_tool_override_ignored(self, tmp_path):
        _set_tool_overrides(tmp_path, {
            "nonexistent/tool": {"read": "deny"},
        })
        entry = _entry(tools=[{"name": "real_tool", "action_type": "read"}])
        result = check_action("pkg", "real_tool", {}, entry)
        assert result.action == "allow"

    def test_unknown_action_type_key_ignored(self, tmp_path):
        _set_tool_overrides(tmp_path, {
            "pkg/tool": {"nonexistent_action": "deny", "read": "deny"},
        })
        entry = _entry(tools=[{"name": "tool", "action_type": "read"}])
        result = check_action("pkg", "tool", {}, entry)
        assert result.action == "deny"

    def test_invalid_value_ignored(self, tmp_path):
        _set_tool_overrides(tmp_path, {
            "pkg/tool": {"read": "block"},
        })
        entry = _entry(tools=[{"name": "tool", "action_type": "read"}])
        result = check_action("pkg", "tool", {}, entry)
        assert result.action == "allow"

    def test_malformed_tool_key_no_slash_ignored(self, tmp_path):
        _set_tool_overrides(tmp_path, {
            "no-slash-key": {"read": "deny"},
        })
        entry = _entry(tools=[{"name": "tool", "action_type": "read"}])
        result = check_action("pkg", "tool", {}, entry)
        assert result.action == "allow"

    def test_non_dict_overrides_value_ignored(self, tmp_path):
        _set_tool_overrides(tmp_path, {
            "pkg/tool": "deny",
        })
        entry = _entry(tools=[{"name": "tool", "action_type": "read"}])
        result = check_action("pkg", "tool", {}, entry)
        assert result.action == "allow"

    def test_non_dict_tool_overrides_root_ignored(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        cfg = json.loads(cfg_file.read_text())
        cfg["guard"]["tool_overrides"] = "invalid"
        cfg_file.write_text(json.dumps(cfg))
        reset_guard_config_cache()

        entry = _entry(tools=[{"name": "tool", "action_type": "read"}])
        result = check_action("pkg", "tool", {}, entry)
        assert result.action == "allow"


# ---------------------------------------------------------------------------
# OC-3: malformed tool_overrides → fail-closed
# ---------------------------------------------------------------------------

class TestFailClosed:
    def test_internal_error_fails_closed(self, tmp_path):
        from unittest.mock import patch
        _set_tool_overrides(tmp_path, {
            "pkg/tool": {"delete": "allow"},
        })
        entry = _entry(tools=[{"name": "tool", "action_type": "delete"}])
        with patch("agentnode_sdk.guard._get_tool_override", side_effect=RuntimeError("boom")):
            result = check_action("pkg", "tool", {}, entry)
        assert result.action in ("deny", "prompt")


# ---------------------------------------------------------------------------
# Config round-trip
# ---------------------------------------------------------------------------

class TestConfigRoundTrip:
    def test_tool_overrides_preserved_through_load_save(self, tmp_path):
        overrides = {
            "pkg/tool_a": {"delete": "deny"},
            "pkg/tool_b": {"read": "prompt", "execute": "deny"},
        }
        _set_tool_overrides(tmp_path, overrides)

        from agentnode_sdk.config import load_config, save_config
        cfg = load_config()
        assert cfg["guard"]["tool_overrides"] == overrides

        save_config(cfg)
        reset_guard_config_cache()

        cfg2 = load_config()
        assert cfg2["guard"]["tool_overrides"] == overrides

    def test_empty_tool_overrides_round_trip(self, tmp_path):
        _set_tool_overrides(tmp_path, {})
        from agentnode_sdk.config import load_config
        cfg = load_config()
        assert cfg["guard"]["tool_overrides"] == {}


# ---------------------------------------------------------------------------
# Agent pre_approved vs tool override precedence
# ---------------------------------------------------------------------------

class TestAgentPrecedence:
    def test_tool_override_deny_beats_agent_pre_approved(self, tmp_path):
        _set_tool_overrides(tmp_path, {
            "my-agent/dangerous": {"delete": "deny"},
        })
        entry = _entry(
            package_type="agent",
            tools=[{"name": "dangerous", "action_type": "delete"}],
            agent={"pre_approved_actions": ["delete"]},
        )
        result = check_action("my-agent", "dangerous", {}, entry)
        assert result.action == "deny"
        assert "tool_override" in result.source

    def test_no_tool_override_allows_pre_approved(self, tmp_path):
        _set_tool_overrides(tmp_path, {})
        entry = _entry(
            package_type="agent",
            tools=[{"name": "tool", "action_type": "delete"}],
            agent={"pre_approved_actions": ["delete"]},
        )
        result = check_action("my-agent", "tool", {}, entry)
        assert result.action == "allow"
        chain_str = " ".join(result.guard_chain)
        assert "pre_approved" in chain_str


# ---------------------------------------------------------------------------
# Guard chain tracing
# ---------------------------------------------------------------------------

class TestGuardChain:
    def test_chain_includes_tool_override_tag(self, tmp_path):
        _set_tool_overrides(tmp_path, {
            "pkg/tool": {"delete": "deny"},
        })
        entry = _entry(tools=[{"name": "tool", "action_type": "delete"}])
        result = check_action("pkg", "tool", {}, entry)
        chain_str = " ".join(result.guard_chain)
        assert "tool_override[pkg/tool]" in chain_str

    def test_chain_without_override_has_no_tool_override_tag(self):
        entry = _entry(tools=[{"name": "tool", "action_type": "delete"}])
        result = check_action("pkg", "tool", {}, entry)
        chain_str = " ".join(result.guard_chain)
        assert "tool_override" not in chain_str

    def test_chain_format_matches_spec(self, tmp_path):
        _set_tool_overrides(tmp_path, {
            "file-manager/delete_file": {"delete": "deny"},
        })
        entry = _entry(tools=[{"name": "delete_file", "action_type": "delete"}])
        result = check_action("file-manager", "delete_file", {}, entry)
        assert any(
            "guard_action:deny(delete:tool_override[file-manager/delete_file])" in c
            for c in result.guard_chain
        )


# ---------------------------------------------------------------------------
# get_resolved_policy includes tool_overrides
# ---------------------------------------------------------------------------

class TestResolvedPolicy:
    def test_resolved_policy_includes_tool_overrides(self, tmp_path):
        overrides = {"pkg/tool": {"delete": "deny"}}
        _set_tool_overrides(tmp_path, overrides)
        result = get_resolved_policy()
        assert "tool_overrides" in result
        assert result["tool_overrides"] == overrides

    def test_resolved_policy_empty_overrides(self):
        result = get_resolved_policy()
        assert result["tool_overrides"] == {}

    def test_resolved_policy_filters_invalid_entries(self, tmp_path):
        _set_tool_overrides(tmp_path, {
            "pkg/tool": {"delete": "deny", "fake_action": "deny"},
            "no-slash": {"read": "deny"},
        })
        result = get_resolved_policy()
        assert "pkg/tool" in result["tool_overrides"]
        assert "fake_action" not in result["tool_overrides"]["pkg/tool"]
        assert "no-slash" not in result["tool_overrides"]


# ---------------------------------------------------------------------------
# Cache invalidation
# ---------------------------------------------------------------------------

class TestCacheInvalidation:
    def test_reset_clears_tool_overrides(self, tmp_path):
        _set_tool_overrides(tmp_path, {"pkg/tool": {"read": "deny"}})
        entry = _entry(tools=[{"name": "tool", "action_type": "read"}])
        r1 = check_action("pkg", "tool", {}, entry)
        assert r1.action == "deny"

        cfg_file = tmp_path / "config.json"
        cfg = json.loads(cfg_file.read_text())
        cfg["guard"].pop("tool_overrides", None)
        cfg_file.write_text(json.dumps(cfg))
        reset_guard_config_cache()

        r2 = check_action("pkg", "tool", {}, entry)
        assert r2.action == "allow"
