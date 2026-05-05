"""Tests for Phase 3: Security & Policy Hardening."""
import json
import os
import warnings
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    lock_file = tmp_path / "agentnode.lock"
    monkeypatch.setenv("AGENTNODE_CONFIG", str(cfg_file))
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(lock_file))
    monkeypatch.delenv("AGENTNODE_NON_INTERACTIVE", raising=False)
    return tmp_path


# --- Task 3.1: load_tool() warning ---


def test_load_tool_warns_on_external_call(isolated_env):
    lock_path = isolated_env / "agentnode.lock"
    lock_path.write_text(json.dumps({
        "lockfile_version": "0.1",
        "updated_at": "",
        "packages": {
            "test-pack": {
                "version": "1.0.0",
                "entrypoint": "nonexistent.module",
                "capability_ids": [],
            }
        },
    }), encoding="utf-8")

    from agentnode_sdk.installer import load_tool
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        try:
            load_tool("test-pack")
        except (ImportError, ModuleNotFoundError):
            pass
        runtime_warnings = [x for x in w if issubclass(x.category, RuntimeWarning)]
        assert len(runtime_warnings) == 1
        assert "bypasses policy" in str(runtime_warnings[0].message)


def test_load_tool_internal_no_warning(isolated_env):
    lock_path = isolated_env / "agentnode.lock"
    lock_path.write_text(json.dumps({
        "lockfile_version": "0.1",
        "updated_at": "",
        "packages": {
            "test-pack": {
                "version": "1.0.0",
                "entrypoint": "nonexistent.module",
                "capability_ids": [],
            }
        },
    }), encoding="utf-8")

    from agentnode_sdk.installer import load_tool
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        try:
            load_tool("test-pack", _internal=True)
        except (ImportError, ModuleNotFoundError):
            pass
        runtime_warnings = [x for x in w if issubclass(x.category, RuntimeWarning)]
        assert len(runtime_warnings) == 0


# --- Task 3.3: Non-Interactive Mode ---


def test_resolve_interactive_default():
    from agentnode_sdk.policy import _resolve_interactive
    with patch.dict(os.environ, {}, clear=False):
        env = os.environ.copy()
        env.pop("AGENTNODE_NON_INTERACTIVE", None)
        with patch.dict(os.environ, env, clear=True):
            assert _resolve_interactive() is True


def test_resolve_interactive_non_interactive_true(monkeypatch):
    from agentnode_sdk.policy import _resolve_interactive
    monkeypatch.setenv("AGENTNODE_NON_INTERACTIVE", "true")
    assert _resolve_interactive() is False


def test_resolve_interactive_non_interactive_1(monkeypatch):
    from agentnode_sdk.policy import _resolve_interactive
    monkeypatch.setenv("AGENTNODE_NON_INTERACTIVE", "1")
    assert _resolve_interactive() is False


def test_non_interactive_config_broken_denies(monkeypatch):
    from agentnode_sdk.policy import _config_broken_result
    monkeypatch.setenv("AGENTNODE_NON_INTERACTIVE", "true")
    result = _config_broken_result(interactive=False)
    assert result.action == "deny"


# --- Task 3.2: Trust TTL Revalidation ---


def test_trust_refresh_skipped_when_fresh(isolated_env):
    from agentnode_sdk.runner import _maybe_refresh_trust
    entry = {
        "trust_level": "verified",
        "last_trust_check": datetime.now(timezone.utc).isoformat(),
    }
    result = _maybe_refresh_trust("test-pack", entry, None)
    assert result["trust_level"] == "verified"


def test_trust_refresh_triggered_when_stale(isolated_env):
    from agentnode_sdk.runner import _maybe_refresh_trust

    old_time = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    entry = {
        "trust_level": "verified",
        "last_trust_check": old_time,
        "version": "1.0.0",
    }

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "publisher": {"trust_level": "trusted"},
    }

    lock_path = isolated_env / "agentnode.lock"
    lock_path.write_text(json.dumps({
        "lockfile_version": "0.1",
        "updated_at": "",
        "packages": {"test-pack": entry.copy()},
    }), encoding="utf-8")

    with patch("httpx.get", return_value=mock_response) as mock_get:
        result = _maybe_refresh_trust("test-pack", entry, lock_path)

    assert result["trust_level"] == "trusted"
    mock_get.assert_called_once()


def test_trust_refresh_applies_downgrade(isolated_env):
    """Backend downgrade must be applied, not ignored."""
    from agentnode_sdk.runner import _maybe_refresh_trust

    old_time = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    entry = {
        "trust_level": "trusted",
        "last_trust_check": old_time,
        "version": "1.0.0",
    }

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "publisher": {"trust_level": "unverified"},
    }

    lock_path = isolated_env / "agentnode.lock"
    lock_path.write_text(json.dumps({
        "lockfile_version": "0.1",
        "updated_at": "",
        "packages": {"test-pack": entry.copy()},
    }), encoding="utf-8")

    with patch("httpx.get", return_value=mock_response):
        result = _maybe_refresh_trust("test-pack", entry, lock_path)

    assert result["trust_level"] == "unverified"


def test_trust_refresh_fails_gracefully(isolated_env):
    from agentnode_sdk.runner import _maybe_refresh_trust

    old_time = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    entry = {
        "trust_level": "verified",
        "last_trust_check": old_time,
    }

    with patch("httpx.get", side_effect=Exception("Network error")):
        result = _maybe_refresh_trust("test-pack", entry, None)

    assert result["trust_level"] == "verified"


def test_trust_refresh_no_timestamp_skips():
    from agentnode_sdk.runner import _maybe_refresh_trust
    entry = {"trust_level": "verified"}
    result = _maybe_refresh_trust("test-pack", entry, None)
    assert result["trust_level"] == "verified"
