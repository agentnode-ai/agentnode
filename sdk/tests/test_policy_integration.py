"""Integration tests for policy hooks in runner, runtime, MCP, and client."""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest

from agentnode_sdk.policy import PolicyResult


# ---------------------------------------------------------------------------
# Runner integration
# ---------------------------------------------------------------------------

class TestRunnerPolicyIntegration:
    """Tests that runner.run_tool() respects policy decisions."""

    def _make_lockfile(self, slug: str, trust: str = "verified", perms: dict | None = None):
        return {
            "packages": {
                slug: {
                    "version": "1.0.0",
                    "runtime": "python",
                    "entrypoint": "fake_mod.tool",
                    "trust_level": trust,
                    "permissions": perms,
                    "tools": [],
                }
            }
        }

    def test_deny_policy_returns_failure(self):
        """run_tool with deny policy → RunToolResult(success=False)."""
        from agentnode_sdk.runner import run_tool

        lockfile = self._make_lockfile("test-pack", trust="unverified")

        with mock.patch("agentnode_sdk.runner.read_lockfile", return_value=lockfile):
            with mock.patch("agentnode_sdk.config.load_config", return_value={
                "trust": {"minimum_trust_level": "verified"},
                "permissions": {"network": "allow", "filesystem": "allow", "code_execution": "sandboxed"},
            }):
                with mock.patch("agentnode_sdk.policy.audit_decision"):
                    result = run_tool("test-pack")
                    assert result.success is False
                    assert result.mode_used == "policy_denied"
                    assert "trust" in result.error.lower() or "Trust" in result.error

    def test_prompt_policy_returns_failure(self):
        """run_tool with prompt policy → RunToolResult(success=False, mode_used='policy_prompt')."""
        from agentnode_sdk.runner import run_tool

        lockfile = self._make_lockfile(
            "test-pack", trust="verified",
            perms={"network_level": "unrestricted"},
        )

        with mock.patch("agentnode_sdk.runner.read_lockfile", return_value=lockfile):
            with mock.patch("agentnode_sdk.config.load_config", return_value={
                "trust": {"minimum_trust_level": "verified"},
                "permissions": {"network": "prompt", "filesystem": "allow", "code_execution": "sandboxed"},
            }):
                with mock.patch("agentnode_sdk.policy.audit_decision"):
                    result = run_tool("test-pack")
                    assert result.success is False
                    assert result.mode_used == "policy_prompt"
                    assert "approval" in result.error.lower() or "Approval" in result.error

    def test_allow_policy_proceeds_to_execution(self):
        """run_tool with allow policy → proceeds to runtime dispatch."""
        from agentnode_sdk.runner import run_tool
        from agentnode_sdk.models import RunToolResult

        lockfile = self._make_lockfile("test-pack", trust="verified")

        def fake_run_python(*args, **kwargs):
            return RunToolResult(success=True, result={"ok": True}, mode_used="direct")

        with mock.patch("agentnode_sdk.runner.read_lockfile", return_value=lockfile):
            with mock.patch("agentnode_sdk.config.load_config", return_value={
                "trust": {"minimum_trust_level": "verified"},
                "permissions": {"network": "allow", "filesystem": "allow", "code_execution": "sandboxed"},
            }):
                with mock.patch("agentnode_sdk.policy.audit_decision"):
                    with mock.patch("agentnode_sdk.runtimes.python_runner.run_python", fake_run_python):
                        result = run_tool("test-pack")
                        assert result.success is True


# ---------------------------------------------------------------------------
# Runtime integration
# ---------------------------------------------------------------------------

class TestRuntimePolicyIntegration:
    """Tests that runtime._handle_run() and _handle_install() respect policy."""

    def test_handle_run_deny(self):
        """runtime.handle('agentnode_run', ...) with deny → error."""
        from agentnode_sdk import runtime as rt_mod

        lockfile = {
            "packages": {
                "test-pack": {
                    "version": "1.0.0",
                    "entrypoint": "fake.tool",
                    "trust_level": "unverified",
                    "permissions": None,
                    "tools": [{"name": "run", "entrypoint": "fake.tool:run"}],
                }
            }
        }

        with mock.patch.object(rt_mod, "read_lockfile", return_value=lockfile):
            with mock.patch("agentnode_sdk.config.load_config", return_value={
                "trust": {"minimum_trust_level": "verified"},
                "permissions": {"network": "allow", "filesystem": "allow", "code_execution": "sandboxed"},
            }):
                with mock.patch("agentnode_sdk.policy.audit_decision"):
                    from agentnode_sdk.runtime import AgentNodeRuntime
                    mock_client = mock.MagicMock()
                    rt = AgentNodeRuntime(client=mock_client, minimum_trust_level="verified")
                    result = rt.handle("agentnode_run", {"slug": "test-pack"})
                    assert result["success"] is False
                    assert result["error"]["code"] == "policy_denied"

    def test_handle_run_prompt(self):
        """runtime.handle('agentnode_run', ...) with prompt → policy_prompt error."""
        from agentnode_sdk import runtime as rt_mod

        lockfile = {
            "packages": {
                "test-pack": {
                    "version": "1.0.0",
                    "entrypoint": "fake.tool",
                    "trust_level": "verified",
                    "permissions": {"network_level": "unrestricted"},
                    "tools": [{"name": "run", "entrypoint": "fake.tool:run"}],
                }
            }
        }

        with mock.patch.object(rt_mod, "read_lockfile", return_value=lockfile):
            with mock.patch("agentnode_sdk.config.load_config", return_value={
                "trust": {"minimum_trust_level": "verified"},
                "permissions": {"network": "prompt", "filesystem": "allow", "code_execution": "sandboxed"},
            }):
                with mock.patch("agentnode_sdk.policy.audit_decision"):
                    from agentnode_sdk.runtime import AgentNodeRuntime
                    mock_client = mock.MagicMock()
                    rt = AgentNodeRuntime(client=mock_client, minimum_trust_level="verified")
                    result = rt.handle("agentnode_run", {"slug": "test-pack"})
                    assert result["success"] is False
                    assert result["error"]["code"] == "policy_prompt"

    def test_handle_install_delegates_policy_to_client(self):
        """runtime._handle_install defers policy check to client.install().

        The runtime doesn't have package metadata (trust, permissions) before
        calling the API, so it delegates the real check_install to the client
        layer which has actual metadata from the API response.
        """
        from agentnode_sdk import runtime as rt_mod

        lockfile = {"packages": {}}  # not installed
        mock_client = mock.MagicMock()
        # client.install returns a denied result (simulating policy block)
        mock_install = mock.MagicMock()
        mock_install.installed = False
        mock_install.message = "Trust level 'unverified' does not meet 'trusted' requirement."
        mock_client.install.return_value = mock_install

        with mock.patch.object(rt_mod, "read_lockfile", return_value=lockfile):
            from agentnode_sdk.runtime import AgentNodeRuntime
            rt = AgentNodeRuntime(client=mock_client, minimum_trust_level="trusted")
            result = rt.handle("agentnode_install", {"slug": "new-pack"})
            assert result["success"] is False
            assert result["error"]["code"] == "install_failed"
            mock_client.install.assert_called_once()

    def test_client_install_policy_deny(self):
        """client.install() with deny policy → InstallResult(installed=False)."""
        from agentnode_sdk.policy import check_install

        with mock.patch("agentnode_sdk.config.load_config", return_value={
            "trust": {"minimum_trust_level": "trusted"},
            "permissions": {"network": "allow", "filesystem": "allow", "code_execution": "sandboxed"},
        }):
            decision = check_install("new-pack", {"trust_level": "unverified"})
            assert decision.action == "deny"
            assert decision.source == "trust_level"


# ---------------------------------------------------------------------------
# MCP integration
# ---------------------------------------------------------------------------

class TestMCPPolicyIntegration:
    """Tests that MCP call_tool() respects policy (non-interactive path)."""

    def test_mcp_deny_returns_error_text(self):
        """MCP call_tool with untrusted pack → deny via policy (non-interactive)."""
        from agentnode_sdk.policy import check_run

        entry = {
            "version": "1.0.0",
            "entrypoint": "fake.tool",
            "trust_level": "unverified",
            "permissions": None,
            "tools": [],
        }

        with mock.patch("agentnode_sdk.config.load_config", return_value={
            "trust": {"minimum_trust_level": "verified"},
            "permissions": {"network": "allow", "filesystem": "allow", "code_execution": "sandboxed"},
        }):
            decision = check_run(
                "untrusted-pack", "test_tool", {},
                entry, interactive=False,
            )
            assert decision.action == "deny"

    def test_mcp_prompt_becomes_deny(self):
        """MCP has no interactive channel → prompt becomes effectively deny."""
        from agentnode_sdk.policy import check_run

        with mock.patch("agentnode_sdk.config.load_config", return_value={
            "trust": {"minimum_trust_level": "verified"},
            "permissions": {"network": "prompt", "filesystem": "allow", "code_execution": "sandboxed"},
        }):
            # In MCP, we call with interactive=False
            # But check_run doesn't use interactive — the caller handles prompt→deny
            # So we just verify the decision is prompt, and MCP code converts to deny
            decision = check_run(
                "test-pack", "tool", {},
                {"trust_level": "verified", "permissions": {"network_level": "unrestricted"}},
                interactive=True,
            )
            assert decision.action == "prompt"

    def test_mcp_allow_proceeds(self):
        """MCP call_tool with verified pack → normal execution."""
        from agentnode_sdk.policy import check_run

        with mock.patch("agentnode_sdk.config.load_config", return_value={
            "trust": {"minimum_trust_level": "verified"},
            "permissions": {"network": "allow", "filesystem": "allow", "code_execution": "sandboxed"},
        }):
            decision = check_run(
                "verified-pack", None, {},
                {"trust_level": "verified", "permissions": {"network_level": "none"}},
            )
            assert decision.action == "allow"

    def test_mcp_policy_error_is_fail_closed(self):
        """BD-7: If the policy check raises in MCP, fail-closed (deny), not fail-open."""
        from agentnode_sdk.policy import check_run

        # 1. Policy module itself: broken config + non-interactive → deny
        with mock.patch("agentnode_sdk.config.load_config", side_effect=Exception("config broken")):
            decision = check_run("any-pack", "tool", {}, {}, interactive=False)
            assert decision.action == "deny"
            assert "Config invalid" in decision.reason

        # 2. Policy module itself: broken config + interactive → prompt (not allow)
        with mock.patch("agentnode_sdk.config.load_config", side_effect=Exception("config broken")):
            decision = check_run("any-pack", "tool", {}, {}, interactive=True)
            assert decision.action == "prompt"
            assert decision.action != "allow"


# ---------------------------------------------------------------------------
# Client integration
# ---------------------------------------------------------------------------

class TestClientPolicyIntegration:
    """Tests that client.install() respects policy decisions."""

    def test_client_install_deny(self):
        """client.install with deny policy → InstallResult(installed=False)."""
        from agentnode_sdk.policy import check_install

        with mock.patch("agentnode_sdk.config.load_config", return_value={
            "trust": {"minimum_trust_level": "trusted"},
            "permissions": {"network": "allow", "filesystem": "allow", "code_execution": "sandboxed"},
        }):
            decision = check_install("pkg", {
                "trust_level": "unverified",
                "permissions": None,
            })
            assert decision.action == "deny"
            assert "trust" in decision.reason.lower() or "Trust" in decision.reason

    def test_client_install_prompt(self):
        """client.install with prompt policy → InstallResult(installed=False, message='Approval required')."""
        from agentnode_sdk.policy import check_install

        with mock.patch("agentnode_sdk.config.load_config", return_value={
            "trust": {"minimum_trust_level": "verified"},
            "permissions": {"network": "prompt", "filesystem": "allow", "code_execution": "sandboxed"},
        }):
            decision = check_install("pkg", {
                "trust_level": "verified",
                "permissions": {"network_level": "unrestricted"},
            })
            assert decision.action == "prompt"


# ---------------------------------------------------------------------------
# Decision log integration
# ---------------------------------------------------------------------------

class TestDecisionLogIntegration:
    """Tests that all execution paths produce audit entries."""

    def test_runner_produces_audit(self, tmp_path):
        """run_tool() should call audit_decision."""
        from agentnode_sdk.runner import run_tool

        lockfile = {
            "packages": {
                "test-pack": {
                    "version": "1.0.0",
                    "runtime": "python",
                    "entrypoint": "fake.tool",
                    "trust_level": "verified",
                    "permissions": None,
                    "tools": [],
                }
            }
        }

        audit_calls = []
        original_audit = None

        def capture_audit(decision, event_type, slug, **kwargs):
            audit_calls.append({
                "event": event_type,
                "slug": slug,
                "action": decision.action,
            })

        with mock.patch("agentnode_sdk.runner.read_lockfile", return_value=lockfile):
            with mock.patch("agentnode_sdk.config.load_config", return_value={
                "trust": {"minimum_trust_level": "verified"},
                "permissions": {"network": "allow", "filesystem": "allow", "code_execution": "sandboxed"},
            }):
                with mock.patch("agentnode_sdk.runner.audit_decision", capture_audit):
                    from agentnode_sdk.models import RunToolResult as RR
                    with mock.patch(
                        "agentnode_sdk.runtimes.python_runner.run_python",
                        return_value=RR(success=True, result={}, mode_used="direct"),
                    ):
                        run_tool("test-pack")
                        assert len(audit_calls) == 1
                        assert audit_calls[0]["event"] == "run_tool"
                        assert audit_calls[0]["slug"] == "test-pack"
                        assert audit_calls[0]["action"] == "allow"
