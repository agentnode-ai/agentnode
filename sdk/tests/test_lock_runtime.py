"""Tests for lockfile integrity checks at runtime — Phase 15.4.

Tests verify that run_tool() / _check_entry_integrity() correctly:
- Succeeds with verified integrity (no warning, no audit)
- Succeeds with missing integrity (migration compat, no warning, no audit)
- Warns on mismatch (check logger output)
- Audits mismatch as lock_integrity_check
- Audit entry contains only safe metadata (no entry content)
- Does NOT warn on verified entry
- Does NOT audit on verified entry
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


# ---------------------------------------------------------------------------
# _check_entry_integrity — verified
# ---------------------------------------------------------------------------

class TestVerifiedIntegrity:
    def test_no_warning_on_verified(self, caplog):
        entry = seal_entry(_make_entry())
        with caplog.at_level(logging.WARNING, logger="agentnode_sdk.runner"):
            _check_entry_integrity("test-pack", entry)
        assert "integrity mismatch" not in caplog.text

    def test_no_audit_on_verified(self):
        entry = seal_entry(_make_entry())
        with patch("agentnode_sdk.runner.audit_decision") as mock_audit:
            _check_entry_integrity("test-pack", entry)
            for call in mock_audit.call_args_list:
                assert call[0][1] != "lock_integrity_check"


# ---------------------------------------------------------------------------
# _check_entry_integrity — missing
# ---------------------------------------------------------------------------

class TestMissingIntegrity:
    def test_no_warning_on_missing(self, caplog):
        entry = _make_entry()
        with caplog.at_level(logging.WARNING, logger="agentnode_sdk.runner"):
            _check_entry_integrity("test-pack", entry)
        assert "integrity mismatch" not in caplog.text

    def test_no_audit_on_missing(self):
        entry = _make_entry()
        with patch("agentnode_sdk.runner.audit_decision") as mock_audit:
            _check_entry_integrity("test-pack", entry)
            for call in mock_audit.call_args_list:
                assert call[0][1] != "lock_integrity_check"


# ---------------------------------------------------------------------------
# _check_entry_integrity — mismatch
# ---------------------------------------------------------------------------

class TestMismatchIntegrity:
    def _tampered_entry(self):
        entry = seal_entry(_make_entry())
        entry["entrypoint"] = "evil.module"
        return entry

    def test_warns_on_mismatch(self, caplog):
        entry = self._tampered_entry()
        with caplog.at_level(logging.WARNING, logger="agentnode_sdk.runner"):
            _check_entry_integrity("bad-pack", entry)
        assert "integrity mismatch" in caplog.text.lower()
        assert "bad-pack" in caplog.text

    def test_audits_mismatch(self):
        entry = self._tampered_entry()
        with patch("agentnode_sdk.runner.audit_decision") as mock_audit:
            _check_entry_integrity("bad-pack", entry)
            integrity_calls = [
                c for c in mock_audit.call_args_list
                if c[0][1] == "lock_integrity_check"
            ]
            assert len(integrity_calls) == 1

    def test_audit_event_fields(self):
        entry = self._tampered_entry()
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
        entry = self._tampered_entry()
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
        """Audit must never contain tool arguments, entry fields, or hashes."""
        entry = self._tampered_entry()
        with patch("agentnode_sdk.runner.audit_decision") as mock_audit:
            _check_entry_integrity("bad-pack", entry)
            integrity_calls = [
                c for c in mock_audit.call_args_list
                if c[0][1] == "lock_integrity_check"
            ]
            call = integrity_calls[0]
            call_str = str(call)
            assert "evil.module" not in call_str
            assert "my_pack.tool" not in call_str
            assert "sha256:abc123" not in call_str

    def test_mismatch_does_not_block(self):
        """Phase 15.4: mismatch warns but never raises."""
        entry = self._tampered_entry()
        _check_entry_integrity("bad-pack", entry)


# ---------------------------------------------------------------------------
# Integration: _get_lockfile_entry calls _check_entry_integrity
# ---------------------------------------------------------------------------

class TestGetLockfileEntryIntegration:
    def test_get_lockfile_entry_calls_integrity_check(self, tmp_path):
        from agentnode_sdk.runner import _get_lockfile_entry

        sealed = seal_entry(_make_entry())
        sealed["entrypoint"] = "tampered.module"
        lf = tmp_path / "agentnode.lock"
        lf.write_text(json.dumps({
            "lockfile_version": "0.1",
            "updated_at": "2026-05-20T00:00:00+00:00",
            "packages": {"tampered-pack": sealed},
        }), encoding="utf-8")

        with patch("agentnode_sdk.runner._check_entry_integrity") as mock_check:
            _get_lockfile_entry("tampered-pack", lf)
            mock_check.assert_called_once_with("tampered-pack", sealed)

    def test_get_lockfile_entry_skips_check_for_missing_slug(self, tmp_path):
        from agentnode_sdk.runner import _get_lockfile_entry

        lf = tmp_path / "agentnode.lock"
        lf.write_text(json.dumps({
            "lockfile_version": "0.1",
            "updated_at": "2026-05-20T00:00:00+00:00",
            "packages": {},
        }), encoding="utf-8")

        with patch("agentnode_sdk.runner._check_entry_integrity") as mock_check:
            _get_lockfile_entry("nonexistent", lf)
            mock_check.assert_not_called()
