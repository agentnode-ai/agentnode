"""Tests for agentnode_sdk.guard — Phase 6.1 Guard MVP."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from agentnode_sdk.guard import (
    GuardDecision,
    check_action,
    check_rate_limit,
    classify_action,
    inspect_mcp_args,
    reset_guard_config_cache,
    reset_rate_limits,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    """Provide isolated config/lockfile for every test."""
    cfg = {
        "version": "1",
        "trust": {"minimum_trust_level": "verified"},
        "permissions": {
            "network": "prompt",
            "filesystem": "prompt",
            "code_execution": "sandboxed",
        },
        "risk_policies": {"external_write_capable": "log"},
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
    lock_file = tmp_path / "agentnode.lock"
    lock_file.write_text(json.dumps({"version": "0.1", "packages": {}}))
    monkeypatch.setenv("AGENTNODE_CONFIG", str(cfg_file))
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(lock_file))
    monkeypatch.setenv("AGENTNODE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("AGENTNODE_GUARD_STRICT", raising=False)
    monkeypatch.delenv("AGENTNODE_NON_INTERACTIVE", raising=False)
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


# ---------------------------------------------------------------------------
# Action Classification
# ---------------------------------------------------------------------------

class TestClassifyAction:
    def test_manifest_declaration(self):
        entry = _entry(tools=[{"name": "remove-item", "action_type": "delete"}])
        types = classify_action("remove-item", entry)
        assert "delete" in types

    def test_name_heuristic_delete(self):
        assert "delete" in classify_action("delete-file", _entry())

    def test_name_heuristic_read(self):
        assert "read" in classify_action("get-status", _entry())

    def test_name_heuristic_write_external(self):
        assert "write_external" in classify_action("send-email", _entry())

    def test_name_heuristic_write_local(self):
        assert "write_local" in classify_action("create-record", _entry())

    def test_name_heuristic_execute(self):
        assert "execute" in classify_action("run-script", _entry())

    def test_name_heuristic_compute(self):
        assert "compute" in classify_action("calculate-hash", _entry())

    def test_unknown_fallback(self):
        assert "unknown" in classify_action("xyzzy", _entry())

    def test_no_tool_name(self):
        assert "unknown" in classify_action(None, _entry())

    def test_permission_escalation_network(self):
        types = classify_action("get-data", _entry(network="unrestricted"))
        assert "network_egress" in types

    def test_permission_escalation_code(self):
        types = classify_action("get-data", _entry(code_execution="subprocess"))
        assert "execute" in types

    def test_connector_adds_credential_use(self):
        types = classify_action("get-data", _entry(connector={"auth_type": "oauth2"}))
        assert "credential_use" in types

    def test_manifest_overrides_heuristic(self):
        entry = _entry(tools=[{"name": "delete-user", "action_type": "read"}])
        types = classify_action("delete-user", entry)
        assert "read" in types


# ---------------------------------------------------------------------------
# check_action — allow / prompt / deny
# ---------------------------------------------------------------------------

class TestCheckAction:
    def test_allow_read_action(self):
        d = check_action("pkg", "get-status", {}, _entry())
        assert d.action == "allow"
        assert "read" in d.action_types

    def test_prompt_delete_action(self):
        d = check_action("pkg", "delete-file", {}, _entry())
        assert d.action == "prompt"
        assert "delete" in d.action_types

    def test_prompt_write_external(self):
        d = check_action("pkg", "send-email", {}, _entry())
        assert d.action == "prompt"

    def test_prompt_execute(self):
        d = check_action("pkg", "run-script", {}, _entry())
        assert d.action == "prompt"

    def test_prompt_unknown(self):
        d = check_action("pkg", "xyzzy", {}, _entry())
        assert d.action == "prompt"

    def test_allow_compute(self):
        d = check_action("pkg", "calculate-hash", {}, _entry())
        assert d.action == "allow"

    def test_allow_write_local(self):
        d = check_action("pkg", "create-file", {}, _entry())
        assert d.action == "allow"

    def test_deny_critical_risk(self, monkeypatch):
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "fake")
        d = check_action(
            "pkg", "delete-everything", {},
            _entry(trust="unverified", network="unrestricted"),
        )
        assert d.action == "deny"
        assert d.risk_level == "critical"

    def test_guard_chain_populated(self):
        d = check_action("pkg", "get-data", {}, _entry())
        assert len(d.guard_chain) > 0


# ---------------------------------------------------------------------------
# Strict mode
# ---------------------------------------------------------------------------

class TestStrictMode:
    def test_strict_denies_delete(self, monkeypatch):
        monkeypatch.setenv("AGENTNODE_GUARD_STRICT", "true")
        reset_guard_config_cache()
        d = check_action("pkg", "delete-file", {}, _entry())
        assert d.action == "deny"

    def test_strict_denies_unknown(self, monkeypatch):
        monkeypatch.setenv("AGENTNODE_GUARD_STRICT", "true")
        reset_guard_config_cache()
        d = check_action("pkg", "xyzzy", {}, _entry())
        assert d.action == "deny"

    def test_strict_allows_read(self, monkeypatch):
        monkeypatch.setenv("AGENTNODE_GUARD_STRICT", "true")
        reset_guard_config_cache()
        d = check_action("pkg", "get-status", {}, _entry())
        assert d.action == "allow"

    def test_strict_internal_error_denies(self, monkeypatch):
        monkeypatch.setenv("AGENTNODE_GUARD_STRICT", "true")
        reset_guard_config_cache()
        with patch("agentnode_sdk.guard._check_action_inner", side_effect=RuntimeError("boom")):
            d = check_action("pkg", "get-data", {}, _entry())
        assert d.action == "deny"
        assert "internal error" in d.reason.lower()


# ---------------------------------------------------------------------------
# Fail-closed on internal error (OC-3)
# ---------------------------------------------------------------------------

class TestFailClosed:
    def test_internal_error_prompts_default(self):
        with patch("agentnode_sdk.guard._check_action_inner", side_effect=RuntimeError("boom")):
            d = check_action("pkg", "get-data", {}, _entry())
        assert d.action == "prompt"
        assert "internal error" in d.reason.lower()

    def test_mcp_inspection_error_prompts(self):
        with patch("agentnode_sdk.guard._inspect_mcp_args_inner", side_effect=RuntimeError("boom")):
            d = inspect_mcp_args("pkg", "tool", {}, _entry())
        assert d.action == "prompt"

    def test_rate_limit_error_prompts(self):
        with patch("agentnode_sdk.guard._check_rate_limit_inner", side_effect=RuntimeError("boom")):
            d = check_rate_limit("pkg")
        assert d.action == "prompt"


# ---------------------------------------------------------------------------
# credential_use
# ---------------------------------------------------------------------------

class TestCredentialUse:
    def test_with_connector_allow(self):
        d = check_action(
            "pkg", "get-data", {},
            _entry(connector={"auth_type": "oauth2"}),
        )
        assert d.action == "allow"

    def test_without_connector_prompt(self):
        entry = _entry(tools=[{"name": "use-key", "action_type": "credential_use"}])
        d = check_action("pkg", "use-key", {}, entry)
        assert d.action == "prompt"
        assert "credential_use" in d.reason


# ---------------------------------------------------------------------------
# Agent pre_approved_actions (AD-7)
# ---------------------------------------------------------------------------

class TestAgentPreApproved:
    def test_pre_approved_allow(self):
        d = check_action(
            "my-agent", "delete-file", {},
            _entry(
                package_type="agent",
                trust="trusted",
                agent={"pre_approved_actions": ["delete", "read"]},
            ),
        )
        assert d.action == "allow"

    def test_undeclared_high_risk_denied_non_interactive(self):
        d = check_action(
            "my-agent", "delete-file", {},
            _entry(
                package_type="agent",
                trust="trusted",
                agent={"pre_approved_actions": ["read", "compute"]},
            ),
            interactive=False,
        )
        assert d.action == "deny"
        assert "not in pre_approved" in d.reason

    def test_critical_always_denied(self, monkeypatch):
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "fake")
        d = check_action(
            "my-agent", "delete-all", {},
            _entry(
                package_type="agent",
                trust="unverified",
                network="unrestricted",
                agent={"pre_approved_actions": ["delete", "network_egress"]},
            ),
        )
        assert d.action == "deny"
        assert d.risk_level == "critical"


# ---------------------------------------------------------------------------
# MCP Argument Inspection
# ---------------------------------------------------------------------------

class TestMCPInspection:
    def test_path_traversal_denied(self):
        d = inspect_mcp_args("pkg", "tool", {"path": "../../etc/passwd"}, _entry())
        assert d.action == "deny"
        assert "path_traversal" in d.reason.lower() or "traversal" in d.reason.lower()

    def test_absolute_path_denied(self):
        d = inspect_mcp_args("pkg", "tool", {"path": "/etc/passwd"}, _entry())
        assert d.action == "deny"

    def test_oversized_denied(self):
        d = inspect_mcp_args("pkg", "tool", {"data": "x" * 2_000_000}, _entry())
        assert d.action == "deny"
        assert "oversized" in d.reason.lower()

    def test_url_no_network_denied(self):
        d = inspect_mcp_args(
            "pkg", "tool",
            {"target": "https://evil.com/data"},
            _entry(network="none"),
        )
        assert d.action == "deny"

    def test_url_with_network_allowed(self):
        d = inspect_mcp_args(
            "pkg", "tool",
            {"target": "https://api.example.com/data"},
            _entry(network="unrestricted"),
        )
        assert d.action == "allow"

    def test_shell_tokens_prompt_not_deny(self):
        d = inspect_mcp_args("pkg", "tool", {"cmd": "ls; rm -rf /"}, _entry())
        assert d.action == "prompt"
        assert d.action != "deny"

    def test_clean_args_allowed(self):
        d = inspect_mcp_args("pkg", "tool", {"query": "hello world"}, _entry())
        assert d.action == "allow"

    def test_excessive_nesting_denied(self):
        nested = {"a": "b"}
        for _ in range(25):
            nested = {"nested": nested}
        d = inspect_mcp_args("pkg", "tool", nested, _entry())
        assert d.action == "deny"
        assert "nesting" in d.reason.lower()

    def test_excessive_keys_denied(self):
        big = {str(i): str(i) for i in range(15_000)}
        d = inspect_mcp_args("pkg", "tool", big, _entry())
        assert d.action == "deny"
        assert "keys" in d.reason.lower()


# ---------------------------------------------------------------------------
# Rate Limiting
# ---------------------------------------------------------------------------

class TestRateLimit:
    def test_within_limits(self):
        d = check_rate_limit("pkg")
        assert d.action == "allow"

    def test_burst_exceeded(self):
        for _ in range(10):
            check_rate_limit("pkg")
        d = check_rate_limit("pkg")
        assert d.action == "deny"
        assert "burst" in d.reason.lower() or "rate limit" in d.reason.lower()

    def test_different_slugs_independent(self):
        for _ in range(10):
            check_rate_limit("pkg-a")
        d = check_rate_limit("pkg-b")
        assert d.action == "allow"

    def test_reset_clears_state(self):
        for _ in range(10):
            check_rate_limit("pkg")
        reset_rate_limits()
        d = check_rate_limit("pkg")
        assert d.action == "allow"

    def test_strict_mode_lower_limits(self, monkeypatch):
        monkeypatch.setenv("AGENTNODE_GUARD_STRICT", "true")
        reset_guard_config_cache()
        from agentnode_sdk.guard import _get_rate_limits
        limits = _get_rate_limits("pkg")
        assert limits["calls_per_minute"] == 30
        assert limits["calls_per_hour"] == 500
        assert limits["burst_size"] == 10

    def test_agent_higher_limits(self):
        from agentnode_sdk.guard import _get_rate_limits
        agent_entry = {"package_type": "agent", "trust_level": "trusted"}
        limits = _get_rate_limits("agent-pkg", agent_entry)
        assert limits["calls_per_minute"] == 120
        assert limits["calls_per_hour"] == 2000
        assert limits["burst_size"] == 20

    def test_strict_overrides_agent(self, monkeypatch):
        monkeypatch.setenv("AGENTNODE_GUARD_STRICT", "true")
        reset_guard_config_cache()
        from agentnode_sdk.guard import _get_rate_limits
        agent_entry = {"package_type": "agent", "trust_level": "trusted"}
        limits = _get_rate_limits("agent-pkg", agent_entry)
        assert limits["calls_per_minute"] == 30
        assert limits["calls_per_hour"] == 500

    def test_agent_burst_allows_more(self):
        agent_entry = {"package_type": "agent", "trust_level": "trusted"}
        for _ in range(15):
            check_rate_limit("agent-burst", entry=agent_entry)
        d = check_rate_limit("agent-burst", entry=agent_entry)
        assert d.action == "allow"

    def test_toolpack_burst_at_default(self):
        for _ in range(10):
            check_rate_limit("tp-burst")
        d = check_rate_limit("tp-burst")
        assert d.action == "deny"


# ---------------------------------------------------------------------------
# Audit event written (via runner integration)
# ---------------------------------------------------------------------------

class TestAuditEvent:
    def test_guard_check_is_valid_event(self):
        from agentnode_sdk.policy import _VALID_EVENTS
        assert "guard_check" in _VALID_EVENTS
        assert "guard_rate_limit" in _VALID_EVENTS


# ---------------------------------------------------------------------------
# Skills bypass (guard must not affect skills)
# ---------------------------------------------------------------------------

class TestSkillsBypass:
    def test_skill_entry_not_guarded_in_runner(self, tmp_path, monkeypatch):
        """Skills should bypass guard entirely in runner.py."""
        lf = tmp_path / "agentnode.lock"
        lf.write_text(json.dumps({
            "lockfile_version": "0.1",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "packages": {
                "my-skill": {
                    "trust_level": "verified",
                    "package_type": "skill",
                    "runtime": "none",
                    "install_mode": "prompt_only",
                    "permissions": {
                        "network_level": "none",
                        "filesystem_level": "none",
                        "code_execution_level": "none",
                    },
                },
            },
        }))
        monkeypatch.setenv("AGENTNODE_LOCKFILE", str(lf))

        from agentnode_sdk.runner import run_tool
        result = run_tool("my-skill", lockfile_path=lf)
        assert result.policy is not None
        assert "guard_action_types" not in (result.policy or {})


# ---------------------------------------------------------------------------
# Guard config merging
# ---------------------------------------------------------------------------

class TestGuardConfig:
    def test_default_config_has_guard(self):
        from agentnode_sdk.config import DEFAULTS
        assert "guard" in DEFAULTS
        assert DEFAULTS["guard"]["delete"] == "prompt"
        assert DEFAULTS["guard"]["read"] == "allow"

    def test_merge_preserves_guard(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({
            "version": "1",
            "guard": {"delete": "deny", "read": "allow"},
        }))
        from agentnode_sdk.config import _merge_defaults
        merged = _merge_defaults(json.loads(cfg_file.read_text()))
        assert merged["guard"]["delete"] == "deny"
        assert merged["guard"]["read"] == "allow"
        assert merged["guard"]["write_external"] == "prompt"

    def test_merge_preserves_rate_limits(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({
            "version": "1",
            "guard": {
                "rate_limits": {"default": {"calls_per_minute": 30}},
            },
        }))
        from agentnode_sdk.config import _merge_defaults
        merged = _merge_defaults(json.loads(cfg_file.read_text()))
        assert merged["guard"]["rate_limits"]["default"]["calls_per_minute"] == 30

    def test_merge_preserves_agent_overrides(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({
            "version": "1",
            "guard": {
                "agent_overrides": {
                    "my-agent": {"pre_approved_actions": ["read"]},
                },
            },
        }))
        from agentnode_sdk.config import _merge_defaults
        merged = _merge_defaults(json.loads(cfg_file.read_text()))
        assert "my-agent" in merged["guard"]["agent_overrides"]
