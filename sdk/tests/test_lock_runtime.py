"""Tests for lockfile integrity checks at runtime — Phase 15.4 / 15.5.

Tests verify that _check_entry_integrity() correctly:
- Succeeds with verified integrity (no warning, no audit)
- Succeeds with missing integrity (migration compat, no warning, no audit)
- Warns on mismatch in default mode (check logger output)
- Audits mismatch as lock_integrity_check
- Audit entry contains only safe metadata (no entry content)
- Does NOT warn or audit on verified entry
- Denies in strict mode on mismatch
- Continues in strict mode for missing and verified
"""
import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from agentnode_sdk.lock_integrity import seal_entry
from agentnode_sdk.runner import _check_entry_integrity


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_entry(**overrides) -> dict:
    entry = {
        "version": "1.0.0",
        "package_type": "toolpack",
        "runtime": "python",
        "entrypoint": "my_pack.tool",
        "artifact_hash": "sha256:abc123",
        "tools": [],
        "permissions": {"network_level": "none"},
        "installed_at": "2026-05-20T00:00:00+00:00",
        "trust_level": "trusted",
        "source": "sdk",
        "capability_ids": [],
        "prompts": [],
        "resources": [],
        "connector": None,
        "agent": None,
    }
    entry.update(overrides)
    return entry


def _tampered_entry():
    entry = seal_entry(_make_entry())
    entry["entrypoint"] = "evil.module"
    return entry


# ---------------------------------------------------------------------------
# _check_entry_integrity — verified (default mode)
# ---------------------------------------------------------------------------

class TestVerifiedIntegrity:
    def test_no_warning_on_verified(self, caplog):
        entry = seal_entry(_make_entry())
        with caplog.at_level(logging.WARNING, logger="agentnode_sdk.runner"):
            result = _check_entry_integrity("test-pack", entry)
        assert result is None
        assert "integrity mismatch" not in caplog.text

    def test_no_audit_on_verified(self):
        entry = seal_entry(_make_entry())
        with patch("agentnode_sdk.runner.audit_decision") as mock_audit:
            _check_entry_integrity("test-pack", entry)
            for call in mock_audit.call_args_list:
                assert call[0][1] != "lock_integrity_check"


# ---------------------------------------------------------------------------
# _check_entry_integrity — missing (default mode)
# ---------------------------------------------------------------------------

class TestMissingIntegrity:
    def test_no_warning_on_missing(self, caplog):
        entry = _make_entry()
        with caplog.at_level(logging.WARNING, logger="agentnode_sdk.runner"):
            result = _check_entry_integrity("test-pack", entry)
        assert result is None
        assert "integrity mismatch" not in caplog.text

    def test_no_audit_on_missing(self):
        entry = _make_entry()
        with patch("agentnode_sdk.runner.audit_decision") as mock_audit:
            _check_entry_integrity("test-pack", entry)
            for call in mock_audit.call_args_list:
                assert call[0][1] != "lock_integrity_check"


# ---------------------------------------------------------------------------
# _check_entry_integrity — mismatch (default mode)
# ---------------------------------------------------------------------------

class TestMismatchIntegrity:
    def test_warns_on_mismatch(self, caplog):
        entry = _tampered_entry()
        with caplog.at_level(logging.WARNING, logger="agentnode_sdk.runner"):
            _check_entry_integrity("bad-pack", entry)
        assert "integrity mismatch" in caplog.text.lower()
        assert "bad-pack" in caplog.text

    def test_returns_none_in_default_mode(self):
        entry = _tampered_entry()
        result = _check_entry_integrity("bad-pack", entry)
        assert result is None

    def test_audits_mismatch(self):
        entry = _tampered_entry()
        with patch("agentnode_sdk.runner.audit_decision") as mock_audit:
            _check_entry_integrity("bad-pack", entry)
            integrity_calls = [
                c for c in mock_audit.call_args_list
                if c[0][1] == "lock_integrity_check"
            ]
            assert len(integrity_calls) == 1

    def test_audit_event_fields(self):
        entry = _tampered_entry()
        with patch("agentnode_sdk.runner.audit_decision") as mock_audit:
            _check_entry_integrity("bad-pack", entry)
            integrity_calls = [
                c for c in mock_audit.call_args_list
                if c[0][1] == "lock_integrity_check"
            ]
            call = integrity_calls[0]
            decision = call[0][0]
            assert decision.action == "warn"
            assert decision.reason == "lockfile_integrity_mismatch"
            assert decision.source == "lock_integrity"
            assert call[0][2] == "bad-pack"

    def test_audit_extra_metadata(self):
        entry = _tampered_entry()
        with patch("agentnode_sdk.runner.audit_decision") as mock_audit:
            _check_entry_integrity("bad-pack", entry)
            integrity_calls = [
                c for c in mock_audit.call_args_list
                if c[0][1] == "lock_integrity_check"
            ]
            extra = integrity_calls[0][1].get("extra", {})
            assert extra["integrity_status"] == "mismatch"
            assert extra["canonical_version"] == 1

    def test_audit_has_no_entry_content(self):
        entry = _tampered_entry()
        with patch("agentnode_sdk.runner.audit_decision") as mock_audit:
            _check_entry_integrity("bad-pack", entry)
            integrity_calls = [
                c for c in mock_audit.call_args_list
                if c[0][1] == "lock_integrity_check"
            ]
            call_str = str(integrity_calls[0])
            assert "evil.module" not in call_str
            assert "my_pack.tool" not in call_str
            assert "sha256:abc123" not in call_str

    def test_mismatch_does_not_block(self):
        entry = _tampered_entry()
        _check_entry_integrity("bad-pack", entry)


# ---------------------------------------------------------------------------
# _check_entry_integrity — strict mode (Phase 15.5)
# ---------------------------------------------------------------------------

class TestStrictMode:
    def test_strict_mismatch_returns_deny(self, monkeypatch):
        monkeypatch.setenv("AGENTNODE_GUARD_STRICT", "true")
        entry = _tampered_entry()
        result = _check_entry_integrity("bad-pack", entry)
        assert result is not None
        assert result.success is False

    def test_strict_mismatch_mode_used(self, monkeypatch):
        monkeypatch.setenv("AGENTNODE_GUARD_STRICT", "true")
        entry = _tampered_entry()
        result = _check_entry_integrity("bad-pack", entry)
        assert result.mode_used == "integrity_denied"

    def test_strict_mismatch_error_message(self, monkeypatch):
        monkeypatch.setenv("AGENTNODE_GUARD_STRICT", "true")
        entry = _tampered_entry()
        result = _check_entry_integrity("bad-pack", entry)
        assert "strict mode" in result.error
        assert "bad-pack" in result.error

    def test_strict_mismatch_audits_deny(self, monkeypatch):
        monkeypatch.setenv("AGENTNODE_GUARD_STRICT", "true")
        entry = _tampered_entry()
        with patch("agentnode_sdk.runner.audit_decision") as mock_audit:
            _check_entry_integrity("bad-pack", entry)
            integrity_calls = [
                c for c in mock_audit.call_args_list
                if c[0][1] == "lock_integrity_check"
            ]
            assert len(integrity_calls) == 1
            assert integrity_calls[0][0][0].action == "deny"

    def test_strict_missing_continues(self, monkeypatch):
        monkeypatch.setenv("AGENTNODE_GUARD_STRICT", "true")
        entry = _make_entry()
        result = _check_entry_integrity("test-pack", entry)
        assert result is None

    def test_strict_verified_continues(self, monkeypatch):
        monkeypatch.setenv("AGENTNODE_GUARD_STRICT", "true")
        entry = seal_entry(_make_entry())
        result = _check_entry_integrity("test-pack", entry)
        assert result is None

    def test_default_mismatch_still_continues(self, monkeypatch):
        monkeypatch.delenv("AGENTNODE_GUARD_STRICT", raising=False)
        entry = _tampered_entry()
        result = _check_entry_integrity("bad-pack", entry)
        assert result is None


# ---------------------------------------------------------------------------
# Integration: run_tool() respects integrity deny
# ---------------------------------------------------------------------------

class TestRunToolIntegration:
    def test_run_tool_returns_deny_on_strict_mismatch(self, tmp_path, monkeypatch):
        from agentnode_sdk.runner import run_tool

        monkeypatch.setenv("AGENTNODE_GUARD_STRICT", "true")
        sealed = seal_entry(_make_entry())
        sealed["entrypoint"] = "tampered.module"
        lf = tmp_path / "agentnode.lock"
        lf.write_text(json.dumps({
            "lockfile_version": "0.1",
            "updated_at": "2026-05-20T00:00:00+00:00",
            "packages": {"tampered-pack": sealed},
        }), encoding="utf-8")

        result = run_tool("tampered-pack", lockfile_path=lf)
        assert result.success is False
        assert result.mode_used == "integrity_denied"
