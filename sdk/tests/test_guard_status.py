"""Tests for Phase 7.2: agentnode guard status."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agentnode_sdk.cli.audit import guard_status_summary, read_audit_entries
from agentnode_sdk.guard import reset_guard_config_cache, reset_rate_limits


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _ts(hours_ago: int = 0, days_ago: int = 0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(hours=hours_ago, days=days_ago)
    return dt.isoformat()


def _entry(event="guard_check", slug="test-pack", action="allow",
           source="guard.default", tool_name=None, hours_ago=0, days_ago=0):
    return {
        "ts": _ts(hours_ago=hours_ago, days_ago=days_ago),
        "event": event,
        "slug": slug,
        "tool_name": tool_name,
        "action": action,
        "source": source,
        "reason": f"Test {action}",
        "trust": "verified",
    }


@pytest.fixture(autouse=True)
def _guard_env(tmp_path, monkeypatch):
    cfg = {"version": "1", "guard": {"read": "allow"}}
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


@pytest.fixture
def audit_file(tmp_path, monkeypatch):
    """Write audit entries and return a helper to set content."""
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "config.json"))

    def _write(entries):
        lines = [json.dumps(e) for e in entries]
        audit_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return _write


# ---------------------------------------------------------------------------
# guard_status_summary() unit tests
# ---------------------------------------------------------------------------

class TestGuardStatusSummary:
    def test_empty_entries(self):
        result = guard_status_summary([])
        assert result["total_entries"] == 0
        assert result["period_24h"]["total"] == 0
        assert result["period_7d"]["total"] == 0

    def test_counts_total(self):
        entries = [_entry(hours_ago=1), _entry(hours_ago=2), _entry(hours_ago=3)]
        result = guard_status_summary(entries)
        assert result["total_entries"] == 3

    def test_24h_window(self):
        entries = [
            _entry(hours_ago=1),
            _entry(hours_ago=12),
            _entry(hours_ago=25),
        ]
        result = guard_status_summary(entries)
        assert result["period_24h"]["total"] == 2
        assert result["period_7d"]["total"] == 3

    def test_7d_window(self):
        entries = [
            _entry(hours_ago=1),
            _entry(days_ago=3),
            _entry(days_ago=8),
        ]
        result = guard_status_summary(entries)
        assert result["period_7d"]["total"] == 2

    def test_action_counts(self):
        entries = [
            _entry(action="allow", hours_ago=1),
            _entry(action="allow", hours_ago=2),
            _entry(action="deny", hours_ago=3),
            _entry(action="prompt", hours_ago=4),
        ]
        result = guard_status_summary(entries)
        actions = result["period_24h"]["actions"]
        assert actions["allow"] == 2
        assert actions["deny"] == 1
        assert actions["prompt"] == 1

    def test_top_denied_packages(self):
        entries = [
            _entry(action="deny", slug="bad-pack", hours_ago=1),
            _entry(action="deny", slug="bad-pack", hours_ago=2),
            _entry(action="deny", slug="other-pack", hours_ago=3),
            _entry(action="allow", slug="good-pack", hours_ago=4),
        ]
        result = guard_status_summary(entries)
        top = result["period_24h"]["top_denied"]
        assert len(top) == 2
        assert top[0]["slug"] == "bad-pack"
        assert top[0]["count"] == 2

    def test_top_prompted_action_types(self):
        entries = [
            _entry(action="prompt", source="guard.action_policy.execute", hours_ago=1),
            _entry(action="prompt", source="guard.action_policy.execute", hours_ago=2),
            _entry(action="prompt", source="guard.action_policy.delete", hours_ago=3),
        ]
        result = guard_status_summary(entries)
        top = result["period_24h"]["top_prompted_actions"]
        assert len(top) == 2
        assert top[0]["action_type"] == "execute"
        assert top[0]["count"] == 2

    def test_rate_limit_hits(self):
        entries = [
            _entry(event="guard_rate_limit", action="deny", hours_ago=1),
            _entry(event="guard_rate_limit", action="deny", hours_ago=2),
            _entry(event="guard_check", action="allow", hours_ago=3),
        ]
        result = guard_status_summary(entries)
        assert result["period_24h"]["rate_limit_hits"] == 2

    def test_no_denied_returns_empty_list(self):
        entries = [_entry(action="allow", hours_ago=1)]
        result = guard_status_summary(entries)
        assert result["period_24h"]["top_denied"] == []

    def test_no_prompted_returns_empty_list(self):
        entries = [_entry(action="allow", hours_ago=1)]
        result = guard_status_summary(entries)
        assert result["period_24h"]["top_prompted_actions"] == []

    def test_old_entries_outside_7d(self):
        entries = [_entry(days_ago=10)]
        result = guard_status_summary(entries)
        assert result["total_entries"] == 1
        assert result["period_24h"]["total"] == 0
        assert result["period_7d"]["total"] == 0


# ---------------------------------------------------------------------------
# cmd_guard_status() CLI tests
# ---------------------------------------------------------------------------

class TestCmdGuardStatus:
    def test_no_activity(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_status
        ret = cmd_guard_status()
        assert ret == 0
        out = capsys.readouterr().out
        assert "No guard activity" in out

    def test_no_activity_json(self, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_status
        ret = cmd_guard_status(json_output=True)
        assert ret == 0
        data = json.loads(capsys.readouterr().out)
        assert data["total_entries"] == 0

    def test_shows_header(self, audit_file, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_status
        audit_file([_entry(hours_ago=1)])
        ret = cmd_guard_status()
        assert ret == 0
        out = capsys.readouterr().out
        assert "Guard Status" in out

    def test_shows_period_labels(self, audit_file, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_status
        audit_file([_entry(hours_ago=1)])
        cmd_guard_status()
        out = capsys.readouterr().out
        assert "24 hours" in out
        assert "7 days" in out

    def test_shows_action_counts(self, audit_file, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_status
        audit_file([
            _entry(action="allow", hours_ago=1),
            _entry(action="deny", hours_ago=2),
        ])
        cmd_guard_status()
        out = capsys.readouterr().out
        assert "allow" in out
        assert "deny" in out

    def test_shows_top_denied(self, audit_file, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_status
        audit_file([
            _entry(action="deny", slug="bad-pack", hours_ago=1),
            _entry(action="deny", slug="bad-pack", hours_ago=2),
        ])
        cmd_guard_status()
        out = capsys.readouterr().out
        assert "bad-pack" in out
        assert "denied" in out.lower()

    def test_shows_rate_limit_hits(self, audit_file, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_status
        audit_file([
            _entry(event="guard_rate_limit", action="deny", hours_ago=1),
        ])
        cmd_guard_status()
        out = capsys.readouterr().out
        assert "Rate limit" in out

    def test_json_output(self, audit_file, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_status
        audit_file([
            _entry(action="allow", hours_ago=1),
            _entry(action="deny", hours_ago=2),
        ])
        cmd_guard_status(json_output=True)
        data = json.loads(capsys.readouterr().out)
        assert "period_24h" in data
        assert "period_7d" in data
        assert data["total_entries"] == 2

    def test_json_has_top_denied(self, audit_file, capsys):
        from agentnode_sdk.cli.commands import cmd_guard_status
        audit_file([
            _entry(action="deny", slug="x", hours_ago=1),
        ])
        cmd_guard_status(json_output=True)
        data = json.loads(capsys.readouterr().out)
        assert len(data["period_24h"]["top_denied"]) == 1


# ---------------------------------------------------------------------------
# CLI routing
# ---------------------------------------------------------------------------

class TestGuardStatusRouting:
    def test_guard_status_routes(self, audit_file, capsys):
        from agentnode_sdk.cli.main import main
        audit_file([_entry(hours_ago=1)])
        ret = main(["guard", "status"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "Guard Status" in out

    def test_guard_status_json_routes(self, audit_file, capsys):
        from agentnode_sdk.cli.main import main
        audit_file([_entry(hours_ago=1)])
        ret = main(["guard", "status", "--json"])
        assert ret == 0
        data = json.loads(capsys.readouterr().out)
        assert "total_entries" in data
