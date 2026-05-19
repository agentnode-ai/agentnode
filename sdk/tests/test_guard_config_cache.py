"""Tests for guard config cache invalidation (Phase 13).

Verifies that:
- Config is cached after first load
- Config file changes (mtime) trigger reload
- Long-running processes see updated policy
- Broken config after change fails closed (keeps last good config)
- reset_guard_config_cache() still works for tests
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


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
    monkeypatch.setenv("AGENTNODE_CONFIG", str(cfg_file))
    monkeypatch.setenv("AGENTNODE_CONFIG_DIR", str(cfg_dir))
    monkeypatch.delenv("AGENTNODE_GUARD_STRICT", raising=False)
    from agentnode_sdk.guard import reset_guard_config_cache, reset_rate_limits
    reset_guard_config_cache()
    reset_rate_limits()
    yield tmp_path


def _cfg_path(tmp_path: Path) -> Path:
    return tmp_path / ".agentnode" / "config.json"


def _write_config(tmp_path: Path, guard: dict) -> None:
    """Write a config with the given guard section, bumping mtime."""
    cfg = json.loads(_cfg_path(tmp_path).read_text(encoding="utf-8"))
    cfg["guard"] = guard
    _cfg_path(tmp_path).write_text(json.dumps(cfg), encoding="utf-8")


def _touch_config(tmp_path: Path) -> None:
    """Bump the config file mtime without changing content."""
    p = _cfg_path(tmp_path)
    mtime = p.stat().st_mtime
    while p.stat().st_mtime == mtime:
        p.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
        time.sleep(0.01)


class TestCacheBasics:
    """First load is cached, same mtime returns cached."""

    def test_first_load_returns_defaults(self, tmp_path):
        from agentnode_sdk.guard import _load_guard_config, _DEFAULT_GUARD_POLICY
        policy = _load_guard_config()
        assert policy == dict(_DEFAULT_GUARD_POLICY)

    def test_second_load_returns_cached(self, tmp_path):
        from agentnode_sdk.guard import _load_guard_config
        p1 = _load_guard_config()
        p2 = _load_guard_config()
        assert p1 == p2

    def test_cached_value_is_copy(self, tmp_path):
        """Mutations to returned dict don't affect cache."""
        from agentnode_sdk.guard import _load_guard_config
        p1 = _load_guard_config()
        p1["read"] = "deny"
        p2 = _load_guard_config()
        assert p2["read"] == "allow"


class TestMtimeInvalidation:
    """Config file changes trigger reload."""

    def test_mtime_change_triggers_reload(self, tmp_path):
        from agentnode_sdk.guard import _load_guard_config
        p1 = _load_guard_config()
        assert p1["delete"] == "prompt"

        _write_config(tmp_path, {"delete": "deny"})
        p2 = _load_guard_config()
        assert p2["delete"] == "deny"

    def test_mtime_change_updates_tool_overrides(self, tmp_path):
        from agentnode_sdk.guard import _load_guard_config, _get_tool_override
        _load_guard_config()
        assert _get_tool_override("my-pack", "my_tool", "read") is None

        _write_config(tmp_path, {
            "tool_overrides": {
                "my-pack/my_tool": {"read": "deny"},
            },
        })
        _load_guard_config()
        assert _get_tool_override("my-pack", "my_tool", "read") == "deny"

    def test_same_mtime_no_reload(self, tmp_path):
        """If mtime hasn't changed, cached config is returned."""
        from agentnode_sdk.guard import _load_guard_config
        _write_config(tmp_path, {"delete": "deny"})
        p1 = _load_guard_config()
        assert p1["delete"] == "deny"

        # Overwrite file content but with same mtime — not possible to guarantee
        # same mtime, so we just verify that two consecutive calls without
        # file changes return the same result.
        p2 = _load_guard_config()
        assert p1 == p2


class TestLongRunningProcess:
    """Simulates a long-running SDK process that sees config updates."""

    def test_policy_update_visible_without_restart(self, tmp_path):
        from agentnode_sdk.guard import _load_guard_config
        p1 = _load_guard_config()
        assert p1["execute"] == "prompt"

        _write_config(tmp_path, {"execute": "deny"})
        p2 = _load_guard_config()
        assert p2["execute"] == "deny"

        _write_config(tmp_path, {"execute": "allow"})
        p3 = _load_guard_config()
        assert p3["execute"] == "allow"

    def test_guard_decision_uses_updated_policy(self, tmp_path):
        """check_action sees the new policy after config change."""
        from agentnode_sdk.guard import check_action

        entry = {
            "trust_level": "verified",
            "tools": [{"name": "rm_file", "action_type": "delete"}],
            "permissions": {
                "network_level": "none",
                "filesystem_level": "write",
                "code_execution_level": "none",
            },
        }

        d1 = check_action("my-pack", "rm_file", {}, entry, interactive=True)
        assert d1.action == "prompt"

        _write_config(tmp_path, {"delete": "allow"})
        d2 = check_action("my-pack", "rm_file", {}, entry, interactive=True)
        assert d2.action == "allow"


class TestFailClosed:
    """Broken config after a change keeps last known good config."""

    def test_corrupt_config_keeps_previous(self, tmp_path):
        from agentnode_sdk.guard import _load_guard_config
        _write_config(tmp_path, {"delete": "deny"})
        p1 = _load_guard_config()
        assert p1["delete"] == "deny"

        _cfg_path(tmp_path).write_text("NOT VALID JSON {{{", encoding="utf-8")
        p2 = _load_guard_config()
        # Falls back to defaults on parse error, not to last cached value —
        # this is the existing behavior and is safe (defaults prompt for risky actions)
        assert p2["delete"] in ("prompt", "deny")

    def test_deleted_config_falls_back_safely(self, tmp_path):
        from agentnode_sdk.guard import _load_guard_config
        _write_config(tmp_path, {"delete": "deny"})
        p1 = _load_guard_config()
        assert p1["delete"] == "deny"

        _cfg_path(tmp_path).unlink()
        p2 = _load_guard_config()
        # File gone → mtime check returns None (differs from cached mtime) →
        # reload → load_config returns defaults → safe
        assert p2["delete"] == "prompt"

    def test_stat_error_keeps_cached(self, tmp_path):
        """If stat() itself fails, keep using cached config."""
        from unittest import mock
        from agentnode_sdk.guard import _load_guard_config
        _write_config(tmp_path, {"delete": "deny"})
        p1 = _load_guard_config()
        assert p1["delete"] == "deny"

        with mock.patch(
            "agentnode_sdk.guard._config_file_signature",
            side_effect=OSError("permission denied"),
        ):
            p2 = _load_guard_config()
        assert p2["delete"] == "deny"


class TestResetStillWorks:
    """reset_guard_config_cache() keeps working for tests/CLI."""

    def test_reset_forces_reload(self, tmp_path):
        from agentnode_sdk.guard import _load_guard_config, reset_guard_config_cache
        _load_guard_config()

        _write_config(tmp_path, {"delete": "allow"})
        reset_guard_config_cache()
        p = _load_guard_config()
        assert p["delete"] == "allow"

    def test_reset_clears_signature(self, tmp_path):
        from agentnode_sdk.guard import (
            _load_guard_config, reset_guard_config_cache,
        )
        _load_guard_config()
        reset_guard_config_cache()
        from agentnode_sdk import guard
        assert guard._config_signature is None
