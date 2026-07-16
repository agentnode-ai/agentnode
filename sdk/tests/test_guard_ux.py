"""Tests for Phase 6.2a: CLI Guard UX — confirmation callback integration."""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agentnode_sdk.guard import (
    GuardDecision,
    cli_confirmation_callback,
    format_guard_deny,
    format_guard_prompt,
    reset_guard_config_cache,
    reset_rate_limits,
)
from agentnode_sdk.models import RunToolResult
from agentnode_sdk.runner import run_tool


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _write_lockfile(tmp_path: Path, packages: dict) -> Path:
    lf = tmp_path / "agentnode.lock"
    lf.write_text(json.dumps({
        "lockfile_version": "0.1",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "packages": packages,
    }))
    return lf


def _write_verified_lockfile(tmp_path: Path, packages: dict) -> Path:
    """A sealed lockfile (per-entry + structure verified) so the 0.2A-2c runtime
    integrity gate passes through to the guard/policy layer under test."""
    from agentnode_sdk.lock_integrity import seal_entry, seal_structure
    sealed = {slug: seal_entry(e) for slug, e in packages.items()}
    data = seal_structure({
        "lockfile_version": "0.1",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "packages": sealed,
    })
    lf = tmp_path / "agentnode.lock"
    lf.write_text(json.dumps(data))
    return lf


@pytest.fixture(autouse=True)
def _guard_env(tmp_path, monkeypatch):
    """Set up guard config where unknown=prompt and delete=deny for testing."""
    cfg = {
        "version": "1",
        "trust": {"minimum_trust_level": "verified"},
        "permissions": {"network": "allow", "filesystem": "allow", "code_execution": "sandboxed"},
        "risk_policies": {"external_write_capable": "allow"},
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
    monkeypatch.delenv("AGENTNODE_NON_INTERACTIVE", raising=False)
    reset_guard_config_cache()
    reset_rate_limits()
    yield
    reset_guard_config_cache()
    reset_rate_limits()


# ---------------------------------------------------------------------------
# Test: format_guard_prompt output
# ---------------------------------------------------------------------------

class TestFormatGuardPrompt:
    def test_contains_package_name(self):
        d = GuardDecision(action="prompt", reason="test", action_types=["execute"], risk_level="medium")
        text = format_guard_prompt("my-pack", "run_cmd", d, {"trust_level": "unverified"})
        assert "my-pack" in text

    def test_contains_tool_name(self):
        d = GuardDecision(action="prompt", reason="test", action_types=["execute"], risk_level="medium")
        text = format_guard_prompt("my-pack", "run_cmd", d)
        assert "run_cmd" in text

    def test_contains_action_types(self):
        d = GuardDecision(action="prompt", reason="test", action_types=["execute", "network_egress"], risk_level="high")
        text = format_guard_prompt("p", None, d)
        assert "execute" in text
        assert "network_egress" in text

    def test_contains_risk_level(self):
        d = GuardDecision(action="prompt", reason="test", action_types=["execute"], risk_level="high")
        text = format_guard_prompt("p", None, d)
        assert "high" in text

    def test_contains_reason(self):
        d = GuardDecision(action="prompt", reason="Action type 'delete' requires confirmation", action_types=["delete"])
        text = format_guard_prompt("p", None, d)
        assert "requires confirmation" in text

    def test_contains_trust_level(self):
        d = GuardDecision(action="prompt", reason="test", action_types=["execute"])
        text = format_guard_prompt("p", None, d, {"trust_level": "verified"})
        assert "verified" in text

    def test_default_no(self):
        d = GuardDecision(action="prompt", reason="test", action_types=["execute"])
        text = format_guard_prompt("p", None, d)
        assert "Default: No" in text

    def test_no_tool_name_omits_tool_line(self):
        d = GuardDecision(action="prompt", reason="test", action_types=["read"])
        text = format_guard_prompt("p", None, d)
        assert "Tool:" not in text


# ---------------------------------------------------------------------------
# Test: format_guard_deny output
# ---------------------------------------------------------------------------

class TestFormatGuardDeny:
    def test_contains_blocked_header(self):
        d = GuardDecision(action="deny", reason="critical risk", action_types=["delete"], risk_level="critical")
        text = format_guard_deny("pkg", None, d)
        assert "blocked" in text.lower()

    def test_contains_reason(self):
        d = GuardDecision(action="deny", reason="Action type 'delete' denied by policy", action_types=["delete"])
        text = format_guard_deny("pkg", None, d)
        assert "denied by policy" in text


# ---------------------------------------------------------------------------
# Test: confirmation_callback → user confirms → tool runs
# ---------------------------------------------------------------------------

class TestUserConfirmsToolRuns:
    @patch("agentnode_sdk.runtimes.python_runner.load_tool")
    def test_callback_yes_proceeds(self, mock_load, tmp_path):
        mock_fn = MagicMock(return_value={"ok": True})
        mock_load.return_value = mock_fn

        # trusted → host direct path so the (mocked) tool actually executes after
        # the guard callback approves. Community tiers route to the container,
        # which doesn't use load_tool; this test targets the confirm→execute flow.
        lf = _write_lockfile(tmp_path, {
            "exec-pack": {
                "version": "1.0",
                "entrypoint": "exec_pack.tool",
                "trust_level": "trusted",
                "tools": [{"name": "run_cmd", "action_type": "execute"}],
            },
        })

        callback = MagicMock(return_value=True)
        result = run_tool(
            "exec-pack", "run_cmd",
            mode="direct",
            lockfile_path=lf,
            confirmation_callback=callback,
            cmd="echo hi",
        )

        callback.assert_called_once()
        assert result.success is True
        assert result.result == {"ok": True}
        mock_fn.assert_called_once_with(cmd="echo hi")

    @patch("agentnode_sdk.runtimes.python_runner.load_tool")
    def test_callback_receives_guard_decision(self, mock_load, tmp_path):
        mock_fn = MagicMock(return_value={"ok": True})
        mock_load.return_value = mock_fn

        lf = _write_lockfile(tmp_path, {
            "exec-pack": {
                "version": "1.0",
                "entrypoint": "exec_pack.tool",
                "trust_level": "verified",
                "tools": [{"name": "run_cmd", "action_type": "execute"}],
            },
        })

        callback = MagicMock(return_value=True)
        run_tool(
            "exec-pack", "run_cmd",
            mode="direct",
            lockfile_path=lf,
            confirmation_callback=callback,
            cmd="echo hi",
        )

        call_args = callback.call_args
        slug_arg = call_args[0][0]
        tool_arg = call_args[0][1]
        decision_arg = call_args[0][2]
        entry_arg = call_args[0][3]

        assert slug_arg == "exec-pack"
        assert tool_arg == "run_cmd"
        assert isinstance(decision_arg, GuardDecision)
        assert decision_arg.action == "prompt"
        assert "execute" in decision_arg.action_types
        assert isinstance(entry_arg, dict)


# ---------------------------------------------------------------------------
# Test: confirmation_callback → user rejects → tool blocked
# ---------------------------------------------------------------------------

class TestUserRejectsToolBlocked:
    def test_callback_no_blocks(self, tmp_path):
        lf = _write_lockfile(tmp_path, {
            "exec-pack": {
                "version": "1.0",
                "entrypoint": "exec_pack.tool",
                "trust_level": "verified",
                "tools": [{"name": "run_cmd", "action_type": "execute"}],
            },
        })

        callback = MagicMock(return_value=False)
        result = run_tool(
            "exec-pack", "run_cmd",
            mode="direct",
            lockfile_path=lf,
            confirmation_callback=callback,
        )

        callback.assert_called_once()
        assert result.success is False
        assert result.mode_used == "policy_denied"
        assert "rejected" in result.error.lower()


# ---------------------------------------------------------------------------
# Test: non-interactive → deny (no callback, no prompt)
# ---------------------------------------------------------------------------

class TestNonInteractiveDeny:
    def test_no_callback_returns_prompt(self, tmp_path):
        """Without callback, prompt decisions return policy_prompt."""
        lf = _write_lockfile(tmp_path, {
            "exec-pack": {
                "version": "1.0",
                "entrypoint": "exec_pack.tool",
                "trust_level": "verified",
                "tools": [{"name": "run_cmd", "action_type": "execute"}],
            },
        })

        result = run_tool(
            "exec-pack", "run_cmd",
            mode="direct",
            lockfile_path=lf,
            confirmation_callback=None,
        )

        assert result.success is False
        assert result.mode_used == "policy_prompt"

    def test_non_interactive_env_escalates_to_deny(self, tmp_path, monkeypatch):
        """AGENTNODE_NON_INTERACTIVE=true escalates prompt→deny in guard."""
        monkeypatch.setenv("AGENTNODE_NON_INTERACTIVE", "true")

        lf = _write_lockfile(tmp_path, {
            "agent-pack": {
                "version": "1.0",
                "entrypoint": "agent_pack.tool",
                "trust_level": "verified",
                "package_type": "agent",
                "agent": {"pre_approved_actions": ["read", "compute"]},
                "tools": [{"name": "run_cmd", "action_type": "execute"}],
            },
        })

        result = run_tool(
            "agent-pack", "run_cmd",
            mode="direct",
            lockfile_path=lf,
            confirmation_callback=None,
        )

        assert result.success is False
        assert result.mode_used in ("policy_denied", "policy_prompt")


# ---------------------------------------------------------------------------
# Test: deny → no prompt, direct block
# ---------------------------------------------------------------------------

class TestDenyDirectBlock:
    def test_deny_skips_callback(self, tmp_path):
        """When guard policy says deny, callback is never called."""
        lf = _write_lockfile(tmp_path, {
            "del-pack": {
                "version": "1.0",
                "entrypoint": "del_pack.tool",
                "trust_level": "verified",
                "tools": [{"name": "delete_all", "action_type": "delete"}],
            },
        })

        callback = MagicMock(return_value=True)
        result = run_tool(
            "del-pack", "delete_all",
            mode="direct",
            lockfile_path=lf,
            confirmation_callback=callback,
        )

        callback.assert_not_called()
        assert result.success is False
        assert result.mode_used == "policy_denied"

    def test_deny_contains_reason(self, tmp_path):
        lf = _write_lockfile(tmp_path, {
            "del-pack": {
                "version": "1.0",
                "entrypoint": "del_pack.tool",
                "trust_level": "verified",
                "tools": [{"name": "delete_all", "action_type": "delete"}],
            },
        })

        result = run_tool(
            "del-pack", "delete_all",
            mode="direct",
            lockfile_path=lf,
        )

        assert result.error is not None
        assert "delete" in result.error.lower()


# ---------------------------------------------------------------------------
# Test: strict mode
# ---------------------------------------------------------------------------

class TestStrictMode:
    def test_strict_denies_write_external(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTNODE_GUARD_STRICT", "true")
        reset_guard_config_cache()

        lf = _write_verified_lockfile(tmp_path, {
            "send-pack": {
                "version": "1.0",
                "entrypoint": "send_pack.tool",
                "trust_level": "verified",
                "tools": [{"name": "send_email", "action_type": "write_external"}],
            },
        })

        callback = MagicMock(return_value=True)
        result = run_tool(
            "send-pack", "send_email",
            mode="direct",
            lockfile_path=lf,
            confirmation_callback=callback,
        )

        callback.assert_not_called()
        assert result.success is False
        assert result.mode_used == "policy_denied"

    def test_strict_denies_unknown(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTNODE_GUARD_STRICT", "true")
        reset_guard_config_cache()

        lf = _write_verified_lockfile(tmp_path, {
            "unk-pack": {
                "version": "1.0",
                "entrypoint": "unk_pack.tool",
                "trust_level": "verified",
            },
        })

        result = run_tool(
            "unk-pack",
            mode="direct",
            lockfile_path=lf,
        )

        assert result.success is False
        assert result.mode_used == "policy_denied"


# ---------------------------------------------------------------------------
# Test: cli_confirmation_callback
# ---------------------------------------------------------------------------

class TestCliConfirmationCallback:
    def test_yes_returns_true(self, monkeypatch):
        d = GuardDecision(action="prompt", reason="test", action_types=["execute"], risk_level="medium")
        monkeypatch.setattr("builtins.input", lambda _: "y")
        assert cli_confirmation_callback("pkg", "tool", d) is True

    def test_no_returns_false(self, monkeypatch):
        d = GuardDecision(action="prompt", reason="test", action_types=["execute"], risk_level="medium")
        monkeypatch.setattr("builtins.input", lambda _: "n")
        assert cli_confirmation_callback("pkg", "tool", d) is False

    def test_empty_returns_false(self, monkeypatch):
        """Default (just pressing Enter) is No — fail-closed."""
        d = GuardDecision(action="prompt", reason="test", action_types=["execute"], risk_level="medium")
        monkeypatch.setattr("builtins.input", lambda _: "")
        assert cli_confirmation_callback("pkg", "tool", d) is False

    def test_eof_returns_false(self, monkeypatch):
        """Ctrl+D (EOF) is treated as No."""
        d = GuardDecision(action="prompt", reason="test", action_types=["execute"], risk_level="medium")
        def raise_eof(_):
            raise EOFError
        monkeypatch.setattr("builtins.input", raise_eof)
        assert cli_confirmation_callback("pkg", "tool", d) is False

    def test_keyboard_interrupt_returns_false(self, monkeypatch):
        """Ctrl+C is treated as No."""
        d = GuardDecision(action="prompt", reason="test", action_types=["execute"], risk_level="medium")
        def raise_interrupt(_):
            raise KeyboardInterrupt
        monkeypatch.setattr("builtins.input", raise_interrupt)
        assert cli_confirmation_callback("pkg", "tool", d) is False
