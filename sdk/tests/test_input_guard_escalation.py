"""Tests for input guard escalation in runner.py (Phase 11.3).

Verifies that prompt-level input_guard findings escalate to:
- prompt in interactive mode
- deny in non-interactive mode (fail-closed)

Warning-level findings remain non-blocking.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from agentnode_sdk.models import RunToolResult
from agentnode_sdk.policy import PolicyResult


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    cfg_dir = tmp_path / ".agentnode"
    cfg_dir.mkdir()
    cfg_file = cfg_dir / "config.json"
    cfg_file.write_text(json.dumps({
        "version": "0.1",
        "trust": {"minimum_trust_level": "unverified"},
        "permissions": {
            "network": "allow",
            "filesystem": "allow",
            "code_execution": "allow",
        },
    }))
    lock_file = tmp_path / "agentnode.lock"
    lock_file.write_text(json.dumps({
        "lockfile_version": "0.1",
        "packages": {},
    }))
    monkeypatch.setenv("AGENTNODE_CONFIG", str(cfg_file))
    monkeypatch.setenv("AGENTNODE_CONFIG_DIR", str(cfg_dir))
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(lock_file))
    monkeypatch.delenv("AGENTNODE_GUARD_STRICT", raising=False)
    monkeypatch.delenv("AGENTNODE_NON_INTERACTIVE", raising=False)
    from agentnode_sdk.guard import reset_guard_config_cache, reset_rate_limits
    reset_guard_config_cache()
    reset_rate_limits()
    yield tmp_path


def _install_entry(tmp_path: Path, slug: str, **extra) -> None:
    lock_file = tmp_path / "agentnode.lock"
    data = json.loads(lock_file.read_text(encoding="utf-8"))
    data["packages"][slug] = {
        "version": "1.0.0",
        "package_type": "toolpack",
        "runtime": "python",
        "entrypoint": "fake_mod:run",
        "trust_level": "verified",
        "installed_at": "2026-05-19T00:00:00Z",
        "permissions": {
            "network_level": "none",
            "filesystem_level": "none",
            "code_execution_level": "none",
        },
        "tools": [{"name": "get_data", "action_type": "read"}],
        **extra,
    }
    lock_file.write_text(json.dumps(data), encoding="utf-8")


def _read_audit(tmp_path: Path) -> list[dict]:
    audit_path = tmp_path / ".agentnode" / "audit.jsonl"
    if not audit_path.exists():
        return []
    lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines]


class TestPathTraversalEscalation:
    """Path traversal in kwargs → prompt (interactive) or deny (non-interactive)."""

    def test_path_traversal_prompts_interactive(self, tmp_path):
        _install_entry(tmp_path, "test-pack")
        from agentnode_sdk.runner import run_tool

        mock_result = RunToolResult(success=True, result="ok", mode_used="subprocess")
        with mock.patch("agentnode_sdk.runtimes.python_runner.run_python", return_value=mock_result):
            res = run_tool(
                "test-pack", "get_data",
                lockfile_path=tmp_path / "agentnode.lock",
                file_path="../../etc/passwd",
            )

        assert not res.success
        assert res.mode_used == "policy_prompt"
        assert "Input guard" in res.error
        assert ".." in res.error
        assert res.policy["source"] == "input_guard"

    def test_path_traversal_denies_non_interactive(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTNODE_NON_INTERACTIVE", "true")
        _install_entry(tmp_path, "test-pack")
        from agentnode_sdk.runner import run_tool

        res = run_tool(
            "test-pack", "get_data",
            lockfile_path=tmp_path / "agentnode.lock",
            file_path="../../etc/passwd",
        )

        assert not res.success
        assert res.mode_used == "policy_denied"
        assert "Input guard" in res.error
        assert res.policy["source"] == "input_guard"

    def test_path_traversal_audit_entry(self, tmp_path):
        _install_entry(tmp_path, "test-pack")
        from agentnode_sdk.runner import run_tool

        run_tool(
            "test-pack", "get_data",
            lockfile_path=tmp_path / "agentnode.lock",
            file_path="../../etc/passwd",
        )

        entries = _read_audit(tmp_path)
        ig_entries = [e for e in entries if e["source"] == "input_guard"]
        assert len(ig_entries) == 1
        assert ig_entries[0]["event"] == "run_tool"
        assert ".." in ig_entries[0]["reason"]
        # No kwargs in audit
        raw = json.dumps(ig_entries[0])
        assert "passwd" not in raw
        assert "etc" not in raw


class TestUrlAnomalyEscalation:
    """URL with network_level=none → prompt/deny."""

    def test_url_anomaly_prompts_interactive(self, tmp_path):
        _install_entry(tmp_path, "test-pack")
        from agentnode_sdk.runner import run_tool

        res = run_tool(
            "test-pack", "get_data",
            lockfile_path=tmp_path / "agentnode.lock",
            target="https://evil.com/steal",
        )

        assert not res.success
        assert res.mode_used == "policy_prompt"
        assert "Input guard" in res.error
        assert "URL" in res.error

    def test_url_anomaly_denies_non_interactive(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTNODE_NON_INTERACTIVE", "true")
        _install_entry(tmp_path, "test-pack")
        from agentnode_sdk.runner import run_tool

        res = run_tool(
            "test-pack", "get_data",
            lockfile_path=tmp_path / "agentnode.lock",
            target="https://evil.com/steal",
        )

        assert not res.success
        assert res.mode_used == "policy_denied"

    def test_url_with_network_allowed_passes(self, tmp_path):
        _install_entry(tmp_path, "test-pack", permissions={
            "network_level": "unrestricted",
            "filesystem_level": "none",
            "code_execution_level": "none",
        })
        from agentnode_sdk.runner import run_tool

        mock_result = RunToolResult(success=True, result="ok", mode_used="subprocess")
        with mock.patch("agentnode_sdk.runtimes.python_runner.run_python", return_value=mock_result):
            res = run_tool(
                "test-pack", "get_data",
                lockfile_path=tmp_path / "agentnode.lock",
                target="https://api.example.com/data",
            )

        assert res.success


class TestWarningLevelNotBlocking:
    """Warning-level findings must not block execution."""

    def test_oversized_string_does_not_block(self, tmp_path):
        _install_entry(tmp_path, "test-pack")
        from agentnode_sdk.runner import run_tool

        mock_result = RunToolResult(success=True, result="ok", mode_used="subprocess")
        with mock.patch("agentnode_sdk.runtimes.python_runner.run_python", return_value=mock_result):
            res = run_tool(
                "test-pack", "get_data",
                lockfile_path=tmp_path / "agentnode.lock",
                text="x" * 2_000_000,
            )

        assert res.success
        assert "input_warnings" in res.policy
        assert any("MB" in w for w in res.policy["input_warnings"])

    def test_oversized_collection_does_not_block(self, tmp_path):
        _install_entry(tmp_path, "test-pack")
        from agentnode_sdk.runner import run_tool

        mock_result = RunToolResult(success=True, result="ok", mode_used="subprocess")
        with mock.patch("agentnode_sdk.runtimes.python_runner.run_python", return_value=mock_result):
            res = run_tool(
                "test-pack", "get_data",
                lockfile_path=tmp_path / "agentnode.lock",
                items=list(range(20_000)),
            )

        assert res.success
        assert "input_warnings" in res.policy


class TestGuardTakesPrecedence:
    """If guard already denied/prompted, input_guard does not override."""

    def test_guard_deny_not_overridden_by_input_guard(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTNODE_GUARD_STRICT", "true")
        from agentnode_sdk.guard import reset_guard_config_cache
        reset_guard_config_cache()

        lock_file = tmp_path / "agentnode.lock"
        data = json.loads(lock_file.read_text(encoding="utf-8"))
        data["packages"]["exec-pack"] = {
            "version": "1.0.0",
            "package_type": "toolpack",
            "runtime": "python",
            "entrypoint": "fake_mod:run",
            "trust_level": "verified",
            "installed_at": "2026-05-19T00:00:00Z",
            "permissions": {
                "network_level": "none",
                "filesystem_level": "none",
                "code_execution_level": "subprocess",
            },
            "tools": [{"name": "run_cmd", "action_type": "execute"}],
        }
        # Seal the whole lockfile so the 0.2A-2c strict runtime integrity gate
        # passes through to the guard (which denies 'execute' in strict mode).
        from agentnode_sdk.lock_integrity import seal_entry, seal_structure
        data.setdefault("lockfile_version", "0.1")
        data["packages"] = {s: seal_entry(e) for s, e in data["packages"].items()}
        data = seal_structure(data)
        lock_file.write_text(json.dumps(data), encoding="utf-8")

        from agentnode_sdk.runner import run_tool
        res = run_tool(
            "exec-pack", "run_cmd",
            lockfile_path=tmp_path / "agentnode.lock",
            cmd="../../etc/shadow",
        )

        assert not res.success
        # Guard denied because of execute in strict mode, NOT input_guard
        assert res.policy["source"] != "input_guard"


class TestPolicyInfoBackwardCompat:
    """policy_info['input_warnings'] remains list[str] for backward compat."""

    def test_input_warnings_are_strings(self, tmp_path):
        _install_entry(tmp_path, "test-pack")
        from agentnode_sdk.runner import run_tool

        res = run_tool(
            "test-pack", "get_data",
            lockfile_path=tmp_path / "agentnode.lock",
            file_path="../../etc/passwd",
        )

        assert "input_warnings" in res.policy
        for w in res.policy["input_warnings"]:
            assert isinstance(w, str)
