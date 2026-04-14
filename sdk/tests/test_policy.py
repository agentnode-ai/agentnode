"""Unit tests for agentnode_sdk.policy — policy kernel."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from agentnode_sdk.policy import (
    PolicyResult,
    EnvironmentContext,
    check_install,
    check_run,
    audit_decision,
    _check_permission,
    _detect_environment,
    _normalize_entry,
    _trust_meets_minimum,
    _env_summary,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(
    min_trust: str = "verified",
    network: str = "prompt",
    filesystem: str = "prompt",
    code_execution: str = "sandboxed",
) -> dict:
    """Build a minimal config dict."""
    return {
        "version": "1",
        "trust": {"minimum_trust_level": min_trust, "allow_unverified": False},
        "permissions": {
            "network": network,
            "filesystem": filesystem,
            "code_execution": code_execution,
        },
    }


def _patch_config(cfg: dict):
    """Patch load_config to return the given dict."""
    return mock.patch("agentnode_sdk.config.load_config", return_value=cfg)


# ---------------------------------------------------------------------------
# Trust checks
# ---------------------------------------------------------------------------

class TestTrustMeetsMinimum:
    def test_same_level(self):
        assert _trust_meets_minimum("verified", "verified") is True

    def test_higher_level(self):
        assert _trust_meets_minimum("curated", "verified") is True

    def test_lower_level(self):
        assert _trust_meets_minimum("unverified", "verified") is False

    def test_unknown_level(self):
        assert _trust_meets_minimum("unknown", "verified") is False


# ---------------------------------------------------------------------------
# Entry normalization (BD-1)
# ---------------------------------------------------------------------------

class TestNormalizeEntry:
    def test_empty_entry(self):
        e = _normalize_entry({})
        assert e["trust_level"] == "unverified"
        assert e["permissions"] is None
        assert e["runtime"] == "python"
        assert e["deprecated"] is False
        assert e["scanner_findings"] == []

    def test_full_entry(self):
        e = _normalize_entry({
            "trust_level": "trusted",
            "permissions": {"network_level": "unrestricted"},
            "runtime": "mcp",
        })
        assert e["trust_level"] == "trusted"
        assert e["permissions"]["network_level"] == "unrestricted"
        assert e["runtime"] == "mcp"


# ---------------------------------------------------------------------------
# Permission checks (BD-2)
# ---------------------------------------------------------------------------

class TestCheckPermission:
    # Network
    def test_network_allow_all(self):
        assert _check_permission("network", "allow", "unrestricted") is None

    def test_network_prompt_unrestricted(self):
        r = _check_permission("network", "prompt", "unrestricted")
        assert r is not None
        assert r.action == "prompt"
        assert r.source == "permission.network"

    def test_network_prompt_restricted(self):
        assert _check_permission("network", "prompt", "restricted") is None

    def test_network_deny_unrestricted(self):
        r = _check_permission("network", "deny", "unrestricted")
        assert r is not None
        assert r.action == "deny"

    def test_network_deny_restricted(self):
        r = _check_permission("network", "deny", "restricted")
        assert r is not None
        assert r.action == "deny"

    def test_network_deny_none(self):
        assert _check_permission("network", "deny", "none") is None

    # Filesystem
    def test_filesystem_allow_all(self):
        assert _check_permission("filesystem", "allow", "full") is None

    def test_filesystem_prompt_full(self):
        r = _check_permission("filesystem", "prompt", "full")
        assert r.action == "prompt"

    def test_filesystem_prompt_read(self):
        assert _check_permission("filesystem", "prompt", "read") is None

    def test_filesystem_deny_read(self):
        r = _check_permission("filesystem", "deny", "read")
        assert r.action == "deny"

    def test_filesystem_deny_none(self):
        assert _check_permission("filesystem", "deny", "none") is None

    # Code execution
    def test_code_execution_sandboxed_subprocess(self):
        assert _check_permission("code_execution", "sandboxed", "subprocess") is None

    def test_code_execution_sandboxed_unrestricted(self):
        r = _check_permission("code_execution", "sandboxed", "unrestricted")
        assert r.action == "prompt"

    def test_code_execution_prompt_subprocess(self):
        r = _check_permission("code_execution", "prompt", "subprocess")
        assert r.action == "prompt"

    def test_code_execution_deny_subprocess(self):
        r = _check_permission("code_execution", "deny", "subprocess")
        assert r.action == "deny"

    def test_code_execution_deny_none(self):
        assert _check_permission("code_execution", "deny", "none") is None

    # BD-13: Unknown values
    def test_unknown_network_value_treated_as_unrestricted(self):
        with mock.patch("agentnode_sdk.policy.logger") as mock_logger:
            r = _check_permission("network", "deny", "foo_unknown")
            assert r is not None
            assert r.action == "deny"
            mock_logger.warning.assert_called_once()

    def test_unknown_filesystem_value_treated_as_full(self):
        with mock.patch("agentnode_sdk.policy.logger") as mock_logger:
            r = _check_permission("filesystem", "deny", "bar_unknown")
            assert r is not None
            assert r.action == "deny"
            mock_logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# check_install
# ---------------------------------------------------------------------------

class TestCheckInstall:
    def test_trust_denied(self):
        cfg = _make_config(min_trust="verified")
        with _patch_config(cfg):
            r = check_install("pkg", {"trust_level": "unverified"})
            assert r.action == "deny"
            assert r.source == "trust_level"
            assert r.details["required_trust"] == "verified"
            assert r.details["actual_trust"] == "unverified"

    def test_trust_allowed(self):
        cfg = _make_config(min_trust="verified")
        with _patch_config(cfg):
            r = check_install("pkg", {"trust_level": "verified"})
            assert r.action == "allow"

    def test_trust_curated_ok(self):
        cfg = _make_config(min_trust="trusted")
        with _patch_config(cfg):
            r = check_install("pkg", {"trust_level": "curated"})
            assert r.action == "allow"

    def test_permission_deny_network(self):
        cfg = _make_config(network="deny")
        with _patch_config(cfg):
            r = check_install("pkg", {
                "trust_level": "verified",
                "permissions": {"network_level": "unrestricted"},
            })
            assert r.action == "deny"
            assert r.source == "permission.network"

    def test_permission_prompt_filesystem(self):
        cfg = _make_config(filesystem="prompt")
        with _patch_config(cfg):
            r = check_install("pkg", {
                "trust_level": "verified",
                "permissions": {"filesystem_level": "full"},
            })
            assert r.action == "prompt"
            assert r.source == "permission.filesystem"

    def test_no_permissions_block_allows_install(self):
        """Install with no permissions block should be allowed (BD: only check_run blocks)."""
        cfg = _make_config()
        with _patch_config(cfg):
            r = check_install("pkg", {"trust_level": "verified"})
            assert r.action == "allow"

    def test_missing_fields_defaults(self):
        """BD-1: Missing fields in entry should use defaults, never crash."""
        cfg = _make_config()
        with _patch_config(cfg):
            r = check_install("pkg", {})  # empty entry
            # unverified trust default → deny with verified minimum
            assert r.action == "deny"
            assert r.source == "trust_level"

    def test_broken_config_interactive_prompt(self):
        """BD-7: Broken config in interactive path → prompt."""
        with mock.patch(
            "agentnode_sdk.config.load_config",
            side_effect=Exception("config broken"),
        ):
            r = check_install("pkg", {"trust_level": "verified"}, interactive=True)
            assert r.action == "prompt"
            assert "Config invalid" in r.reason

    def test_broken_config_noninteractive_deny(self):
        """BD-7: Broken config in non-interactive path → deny."""
        with mock.patch(
            "agentnode_sdk.config.load_config",
            side_effect=Exception("config broken"),
        ):
            r = check_install("pkg", {"trust_level": "verified"}, interactive=False)
            assert r.action == "deny"

    def test_broken_config_strict_always_deny(self):
        """BD-7: AGENTNODE_GUARD_STRICT=true → always deny."""
        with mock.patch(
            "agentnode_sdk.config.load_config",
            side_effect=Exception("config broken"),
        ):
            with mock.patch.dict(os.environ, {"AGENTNODE_GUARD_STRICT": "true"}):
                r = check_install("pkg", {"trust_level": "verified"}, interactive=True)
                assert r.action == "deny"


# ---------------------------------------------------------------------------
# check_run
# ---------------------------------------------------------------------------

class TestCheckRun:
    def test_trust_denied(self):
        cfg = _make_config(min_trust="verified")
        with _patch_config(cfg):
            r = check_run("pkg", None, {}, {"trust_level": "unverified"})
            assert r.action == "deny"
            assert r.source == "trust_level"

    def test_permission_denied(self):
        cfg = _make_config(network="deny")
        with _patch_config(cfg):
            r = check_run("pkg", None, {}, {
                "trust_level": "verified",
                "permissions": {"network_level": "unrestricted"},
            })
            assert r.action == "deny"
            assert r.source == "permission.network"

    def test_permission_prompt(self):
        cfg = _make_config(network="prompt")
        with _patch_config(cfg):
            r = check_run("pkg", None, {}, {
                "trust_level": "verified",
                "permissions": {"network_level": "unrestricted"},
            })
            assert r.action == "prompt"

    def test_allow(self):
        cfg = _make_config(network="allow")
        with _patch_config(cfg):
            r = check_run("pkg", None, {}, {
                "trust_level": "verified",
                "permissions": {"network_level": "unrestricted"},
            })
            assert r.action == "allow"

    def test_has_secrets_no_permissions_prompt(self):
        """BD-11: permissions=None + has_secrets → prompt."""
        cfg = _make_config()
        with _patch_config(cfg):
            with mock.patch.dict(os.environ, {"AWS_SECRET_ACCESS_KEY": "xxx"}):
                r = check_run("pkg", None, {}, {"trust_level": "verified"})
                assert r.action == "prompt"
                assert r.source == "environment.has_secrets"

    def test_no_permissions_low_trust_prompt(self):
        """BD-14: permissions=None + trust < verified → prompt."""
        cfg = _make_config(min_trust="unverified")
        with _patch_config(cfg):
            # Clear secret env vars to isolate BD-14
            clean_env = {k: v for k, v in os.environ.items()
                         if not any(k.startswith(p) or k == p for p in
                                    ("AWS_", "OPENAI_", "STRIPE_", "GCP_",
                                     "AZURE_", "ANTHROPIC_", "DATABASE_URL", "SECRET"))}
            with mock.patch.dict(os.environ, clean_env, clear=True):
                r = check_run("pkg", None, {}, {"trust_level": "unverified"})
                assert r.action == "prompt"
                assert r.source == "trust_level"
                assert "Unverified package" in r.reason

    def test_no_permissions_verified_no_secrets_allow(self):
        """Package without permissions block + trust >= verified + no secrets → allow."""
        cfg = _make_config()
        with _patch_config(cfg):
            clean_env = {k: v for k, v in os.environ.items()
                         if not any(k.startswith(p) or k == p for p in
                                    ("AWS_", "OPENAI_", "STRIPE_", "GCP_",
                                     "AZURE_", "ANTHROPIC_", "DATABASE_URL", "SECRET"))}
            with mock.patch.dict(os.environ, clean_env, clear=True):
                r = check_run("pkg", None, {}, {"trust_level": "verified"})
                assert r.action == "allow"

    def test_has_secrets_network_allow_low_trust_prompt(self):
        """has_secrets + network=allow + trust < trusted → prompt."""
        cfg = _make_config(network="allow")
        with _patch_config(cfg):
            with mock.patch.dict(os.environ, {"AWS_SECRET_ACCESS_KEY": "xxx"}):
                r = check_run("pkg", None, {}, {
                    "trust_level": "verified",
                    "permissions": {"network_level": "unrestricted"},
                })
                assert r.action == "prompt"
                assert r.source == "environment.has_secrets"


# ---------------------------------------------------------------------------
# Environment detection
# ---------------------------------------------------------------------------

class TestEnvironmentDetection:
    def test_has_secrets_aws(self):
        with mock.patch.dict(os.environ, {"AWS_SECRET_ACCESS_KEY": "xxx"}, clear=False):
            env = _detect_environment()
            assert env.has_secrets is True

    def test_has_secrets_openai(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "xxx"}, clear=False):
            env = _detect_environment()
            assert env.has_secrets is True

    def test_no_secrets(self):
        clean_env = {k: v for k, v in os.environ.items()
                     if not any(k.startswith(p) or k == p for p in
                                ("AWS_", "OPENAI_", "STRIPE_", "GCP_",
                                 "AZURE_", "ANTHROPIC_", "DATABASE_URL", "SECRET"))}
        with mock.patch.dict(os.environ, clean_env, clear=True):
            env = _detect_environment()
            assert env.has_secrets is False

    def test_is_ci_github(self):
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}, clear=False):
            env = _detect_environment()
            assert env.is_ci is True

    def test_not_ci(self):
        clean_env = {k: v for k, v in os.environ.items()
                     if k not in ("CI", "GITHUB_ACTIONS", "GITLAB_CI",
                                  "CIRCLECI", "JENKINS_URL", "TRAVIS")}
        with mock.patch.dict(os.environ, clean_env, clear=True):
            env = _detect_environment()
            assert env.is_ci is False

    def test_no_secret_values_leaked(self):
        """Env context only exposes booleans, not actual secret values."""
        with mock.patch.dict(os.environ, {"AWS_SECRET_ACCESS_KEY": "super-secret-key"}):
            env = _detect_environment()
            # Only boolean, no string data
            assert isinstance(env.has_secrets, bool)
            env_str = _env_summary(env)
            assert "super-secret-key" not in env_str
            assert "AWS_SECRET_ACCESS_KEY" not in env_str


# ---------------------------------------------------------------------------
# Audit decision (BD-4, BD-5, BD-6, BD-12)
# ---------------------------------------------------------------------------

class TestAuditDecision:
    def test_writes_jsonl(self, tmp_path):
        audit_dir = tmp_path / ".agentnode"
        with mock.patch("agentnode_sdk.config.config_dir", return_value=audit_dir):
            decision = PolicyResult(
                action="allow", reason="All checks passed", source="default",
                details={"some": "details"},
            )
            audit_decision(
                decision, "run_tool", "test-pack",
                tool_name="my_tool",
                trust_level="verified",
            )

            audit_file = audit_dir / "audit.jsonl"
            assert audit_file.exists()
            line = audit_file.read_text(encoding="utf-8").strip()
            record = json.loads(line)

            assert record["event"] == "run_tool"
            assert record["slug"] == "test-pack"
            assert record["tool_name"] == "my_tool"
            assert record["action"] == "allow"
            assert record["source"] == "default"
            assert record["trust"] == "verified"
            assert "ts" in record
            assert "env" in record
            assert record["request_id"] is None
            # BD-12: details NOT in audit
            assert "details" not in record
            assert "some" not in record

    def test_audit_never_crashes_caller(self, tmp_path):
        """BD-5: Audit write errors must not crash the tool execution."""
        with mock.patch("agentnode_sdk.config.config_dir", side_effect=OSError("disk full")):
            # Should not raise
            audit_decision(
                PolicyResult(action="deny", reason="test", source="default"),
                "run_tool", "test-pack",
            )

    def test_audit_appends(self, tmp_path):
        audit_dir = tmp_path / ".agentnode"
        with mock.patch("agentnode_sdk.config.config_dir", return_value=audit_dir):
            for i in range(3):
                audit_decision(
                    PolicyResult(action="allow", reason=f"test-{i}", source="default"),
                    "run_tool", f"pack-{i}",
                )
            lines = (audit_dir / "audit.jsonl").read_text(encoding="utf-8").strip().split("\n")
            assert len(lines) == 3

    def test_audit_no_secret_keys(self, tmp_path):
        """BD-6: No secret key names in audit."""
        audit_dir = tmp_path / ".agentnode"
        with mock.patch("agentnode_sdk.config.config_dir", return_value=audit_dir):
            with mock.patch.dict(os.environ, {"AWS_SECRET_ACCESS_KEY": "mysecret"}):
                audit_decision(
                    PolicyResult(action="allow", reason="ok", source="default"),
                    "run_tool", "pkg",
                )
                content = (audit_dir / "audit.jsonl").read_text(encoding="utf-8")
                assert "mysecret" not in content
                assert "AWS_SECRET_ACCESS_KEY" not in content
                # env field should be compact string
                record = json.loads(content.strip())
                assert "/" in record["env"]  # e.g. "win32/user/no_ci/secrets"

    def test_install_events_audited(self, tmp_path):
        """BD-10: Install paths produce audit entries."""
        audit_dir = tmp_path / ".agentnode"
        with mock.patch("agentnode_sdk.config.config_dir", return_value=audit_dir):
            audit_decision(
                PolicyResult(action="allow", reason="ok", source="default"),
                "client_install", "my-pack",
            )
            audit_decision(
                PolicyResult(action="deny", reason="trust", source="trust_level"),
                "runtime_install", "other-pack",
            )
            lines = (audit_dir / "audit.jsonl").read_text(encoding="utf-8").strip().split("\n")
            assert len(lines) == 2
            assert json.loads(lines[0])["event"] == "client_install"
            assert json.loads(lines[1])["event"] == "runtime_install"


# ---------------------------------------------------------------------------
# PolicyResult.details
# ---------------------------------------------------------------------------

class TestPolicyResultDetails:
    def test_details_present_in_result(self):
        r = PolicyResult(
            action="deny", reason="trust too low", source="trust_level",
            details={"required_trust": "verified", "actual_trust": "unverified"},
        )
        assert r.details is not None
        assert r.details["required_trust"] == "verified"

    def test_details_default_none(self):
        r = PolicyResult(action="allow", reason="ok", source="default")
        assert r.details is None
