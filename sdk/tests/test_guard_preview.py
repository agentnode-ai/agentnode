"""Tests for Phase 6.2c: Install-Time Guard Risk Preview."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agentnode_sdk.guard import (
    reset_guard_config_cache,
    reset_rate_limits,
)


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


@pytest.fixture(autouse=True)
def _guard_env(tmp_path, monkeypatch):
    """Default guard config: delete=deny, execute/unknown=prompt, read/compute=allow."""
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
    reset_guard_config_cache()
    reset_rate_limits()
    yield
    reset_guard_config_cache()
    reset_rate_limits()


# ---------------------------------------------------------------------------
# Inspect: Guard Preview with tools
# ---------------------------------------------------------------------------

class TestInspectGuardPreviewWithTools:
    def _make_pkg(self):
        return {
            "version": "1.0.0",
            "package_type": "toolpack",
            "trust_level": "verified",
            "tools": [
                {"name": "run_cmd", "action_type": "execute"},
                {"name": "list_items", "action_type": "read"},
            ],
            "permissions": {},
        }

    def test_shows_guard_preview_header(self, capsys):
        from agentnode_sdk.cli.commands import _inspect_print_guard_preview
        _inspect_print_guard_preview("test-pack", self._make_pkg())
        out = capsys.readouterr().out
        assert "Guard Preview" in out

    def test_shows_tool_names(self, capsys):
        from agentnode_sdk.cli.commands import _inspect_print_guard_preview
        _inspect_print_guard_preview("test-pack", self._make_pkg())
        out = capsys.readouterr().out
        assert "run_cmd" in out
        assert "list_items" in out

    def test_shows_action_types(self, capsys):
        from agentnode_sdk.cli.commands import _inspect_print_guard_preview
        _inspect_print_guard_preview("test-pack", self._make_pkg())
        out = capsys.readouterr().out
        assert "execute" in out
        assert "read" in out

    def test_prompt_shows_reason(self, capsys):
        from agentnode_sdk.cli.commands import _inspect_print_guard_preview
        _inspect_print_guard_preview("test-pack", self._make_pkg())
        out = capsys.readouterr().out
        assert "requires confirmation" in out

    def test_allow_no_detail_line(self, capsys):
        from agentnode_sdk.cli.commands import _inspect_print_guard_preview
        _inspect_print_guard_preview("test-pack", self._make_pkg())
        out = capsys.readouterr().out
        lines = out.splitlines()
        list_items_lines = [l for l in lines if "list_items" in l]
        assert len(list_items_lines) == 1  # no detail line for allow


# ---------------------------------------------------------------------------
# Inspect: Guard Preview without tools (package-level)
# ---------------------------------------------------------------------------

class TestInspectGuardPreviewPackageLevel:
    def test_shows_package_level(self, capsys):
        from agentnode_sdk.cli.commands import _inspect_print_guard_preview
        pkg = {
            "version": "1.0.0",
            "package_type": "toolpack",
            "trust_level": "verified",
            "permissions": {},
        }
        _inspect_print_guard_preview("bare-pack", pkg)
        out = capsys.readouterr().out
        assert "(package-level)" in out
        assert "unknown" in out


# ---------------------------------------------------------------------------
# Inspect: Skills bypass guard
# ---------------------------------------------------------------------------

class TestInspectGuardPreviewSkill:
    def test_skill_shows_bypass(self, capsys):
        from agentnode_sdk.cli.commands import _inspect_print_guard_preview
        pkg = {"package_type": "skill", "trust_level": "curated"}
        _inspect_print_guard_preview("my-skill", pkg)
        out = capsys.readouterr().out
        assert "skills bypass guard" in out

    def test_skill_no_action_types(self, capsys):
        from agentnode_sdk.cli.commands import _inspect_print_guard_preview
        pkg = {"package_type": "skill", "trust_level": "curated"}
        _inspect_print_guard_preview("my-skill", pkg)
        out = capsys.readouterr().out
        assert "execute" not in out
        assert "unknown" not in out


# ---------------------------------------------------------------------------
# Inspect: Upgrade package
# ---------------------------------------------------------------------------

class TestInspectGuardPreviewUpgrade:
    def test_upgrade_shows_no_executable_tools(self, capsys):
        from agentnode_sdk.cli.commands import _inspect_print_guard_preview
        pkg = {"package_type": "upgrade", "trust_level": "trusted"}
        _inspect_print_guard_preview("my-upgrade", pkg)
        out = capsys.readouterr().out
        assert "no executable tools" in out


# ---------------------------------------------------------------------------
# Inspect: JSON output includes guard
# ---------------------------------------------------------------------------

class TestInspectJsonGuardPreview:
    def test_json_includes_guard_key(self):
        from agentnode_sdk.cli.commands import _build_guard_preview
        pkg = {
            "version": "1.0.0",
            "package_type": "toolpack",
            "trust_level": "verified",
            "tools": [{"name": "run_cmd", "action_type": "execute"}],
            "permissions": {},
        }
        result = _build_guard_preview("test-pack", pkg)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["tool_name"] == "run_cmd"

    def test_json_has_guard_action_not_action(self):
        from agentnode_sdk.cli.commands import _build_guard_preview
        pkg = {
            "version": "1.0.0",
            "package_type": "toolpack",
            "trust_level": "verified",
            "tools": [{"name": "run_cmd", "action_type": "execute"}],
            "permissions": {},
        }
        result = _build_guard_preview("test-pack", pkg)
        entry = result[0]
        assert "guard_action" in entry
        assert "action" not in entry

    def test_json_action_types_sorted(self):
        from agentnode_sdk.cli.commands import _build_guard_preview
        pkg = {
            "version": "1.0.0",
            "package_type": "toolpack",
            "trust_level": "verified",
            "tools": [{"name": "send_data", "action_type": "write_external"}],
            "permissions": {"network_level": "restricted"},
        }
        result = _build_guard_preview("test-pack", pkg)
        action_types = result[0]["action_types"]
        assert action_types == sorted(action_types)

    def test_json_skill_returns_string(self):
        from agentnode_sdk.cli.commands import _build_guard_preview
        result = _build_guard_preview("s", {"package_type": "skill"})
        assert result == "skills_bypass_guard"

    def test_json_upgrade_returns_string(self):
        from agentnode_sdk.cli.commands import _build_guard_preview
        result = _build_guard_preview("u", {"package_type": "upgrade"})
        assert result == "no_executable_tools"

    def test_json_package_level_when_no_tools(self):
        from agentnode_sdk.cli.commands import _build_guard_preview
        pkg = {
            "version": "1.0.0",
            "package_type": "toolpack",
            "trust_level": "verified",
            "permissions": {},
        }
        result = _build_guard_preview("bare-pack", pkg)
        assert isinstance(result, list)
        assert result[0]["tool_name"] is None


# ---------------------------------------------------------------------------
# Inspect: Strict mode changes guard_action
# ---------------------------------------------------------------------------

class TestInspectStrictMode:
    def test_strict_changes_preview(self, monkeypatch):
        from agentnode_sdk.cli.commands import _build_guard_preview
        monkeypatch.setenv("AGENTNODE_GUARD_STRICT", "true")
        reset_guard_config_cache()

        pkg = {
            "version": "1.0.0",
            "package_type": "toolpack",
            "trust_level": "verified",
            "tools": [{"name": "send_email", "action_type": "write_external"}],
            "permissions": {},
        }
        result = _build_guard_preview("strict-pack", pkg)
        assert result[0]["guard_action"] == "deny"

    def test_strict_denies_unknown(self, monkeypatch):
        from agentnode_sdk.cli.commands import _build_guard_preview
        monkeypatch.setenv("AGENTNODE_GUARD_STRICT", "true")
        reset_guard_config_cache()

        pkg = {
            "version": "1.0.0",
            "package_type": "toolpack",
            "trust_level": "verified",
            "permissions": {},
        }
        result = _build_guard_preview("unk-pack", pkg)
        assert result[0]["guard_action"] == "deny"


# ---------------------------------------------------------------------------
# Inspect: No side effects
# ---------------------------------------------------------------------------

class TestInspectNoSideEffects:
    def test_inspect_creates_no_audit_entries(self, tmp_path, monkeypatch):
        """Calling guard preview must not write to audit.jsonl."""
        from agentnode_sdk.cli.commands import _inspect_print_guard_preview, _build_guard_preview

        audit_path = tmp_path / "audit.jsonl"
        # Ensure no audit file exists initially
        if audit_path.exists():
            audit_path.unlink()

        pkg = {
            "version": "1.0.0",
            "package_type": "toolpack",
            "trust_level": "verified",
            "tools": [
                {"name": "run_cmd", "action_type": "execute"},
                {"name": "delete_all", "action_type": "delete"},
            ],
            "permissions": {"network_level": "restricted"},
        }

        # Call both preview functions
        _inspect_print_guard_preview("test-pack", pkg)
        _build_guard_preview("test-pack", pkg)

        # audit.jsonl should NOT exist or should be empty
        if audit_path.exists():
            content = audit_path.read_text(encoding="utf-8").strip()
            assert content == "", f"Audit file should be empty but contains: {content}"

    def test_inspect_no_rate_limit_state(self):
        """Guard preview must not modify rate limit tracking."""
        from agentnode_sdk.guard import _rate_limits
        from agentnode_sdk.cli.commands import _build_guard_preview

        initial_keys = set(_rate_limits.keys())

        pkg = {
            "version": "1.0.0",
            "package_type": "toolpack",
            "trust_level": "verified",
            "tools": [{"name": "run_cmd", "action_type": "execute"}],
            "permissions": {},
        }

        _build_guard_preview("side-effect-test", pkg)

        # No new rate limit keys should have been added
        assert set(_rate_limits.keys()) == initial_keys


# ---------------------------------------------------------------------------
# Install: Guard summary line
# ---------------------------------------------------------------------------

class TestInstallGuardSummary:
    def test_summary_shows_action_types(self, tmp_path, monkeypatch, capsys):
        from agentnode_sdk.cli.commands import _print_install_guard_summary

        pkg = {
            "version": "1.0.0",
            "package_type": "toolpack",
            "trust_level": "verified",
            "tools": [{"name": "run_cmd", "action_type": "execute"}],
            "permissions": {"network_level": "restricted"},
        }
        _write_lockfile(tmp_path, {"exec-pack": pkg})
        monkeypatch.setenv("AGENTNODE_LOCKFILE", str(tmp_path / "agentnode.lock"))

        with patch("agentnode_sdk.installer.read_lockfile", return_value={
            "packages": {"exec-pack": pkg},
        }):
            _print_install_guard_summary("exec-pack")

        out = capsys.readouterr().out
        assert "Guard:" in out
        assert "execute" in out

    def test_summary_shows_worst_decision(self, capsys):
        from agentnode_sdk.cli.commands import _print_install_guard_summary

        pkg = {
            "version": "1.0.0",
            "package_type": "toolpack",
            "trust_level": "verified",
            "tools": [
                {"name": "list_items", "action_type": "read"},
                {"name": "delete_all", "action_type": "delete"},
            ],
            "permissions": {},
        }

        with patch("agentnode_sdk.installer.read_lockfile", return_value={
            "packages": {"mixed-pack": pkg},
        }):
            _print_install_guard_summary("mixed-pack")

        out = capsys.readouterr().out
        assert "Guard:" in out
        assert "deny" in out

    def test_skill_no_summary(self, capsys):
        from agentnode_sdk.cli.commands import _print_install_guard_summary

        pkg = {"package_type": "skill", "trust_level": "curated"}

        with patch("agentnode_sdk.installer.read_lockfile", return_value={
            "packages": {"my-skill": pkg},
        }):
            _print_install_guard_summary("my-skill")

        out = capsys.readouterr().out
        assert "Guard:" not in out

    def test_upgrade_no_summary(self, capsys):
        from agentnode_sdk.cli.commands import _print_install_guard_summary

        pkg = {"package_type": "upgrade", "trust_level": "trusted"}

        with patch("agentnode_sdk.installer.read_lockfile", return_value={
            "packages": {"my-upgrade": pkg},
        }):
            _print_install_guard_summary("my-upgrade")

        out = capsys.readouterr().out
        assert "Guard:" not in out
