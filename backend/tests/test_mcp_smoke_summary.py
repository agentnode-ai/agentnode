"""Slice 2c-4c — admin/API smoke visibility (_smoke_summary extraction).

Pure test of the helper that surfaces smoke_status / checked_at / expires_at /
recheck_reason for the admin submission list, including the freshness-aware
status (expired/key_mismatch) and the running marker.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.mcp.router import _smoke_summary


def _iso(delta):
    return (datetime.now(timezone.utc) + delta).isoformat()


def test_smoke_summary_from_gate_evidence():
    sv = {
        "gate_result": {
            "gates": [
                {
                    "id": "sandbox_smoke",
                    "reason": "sandbox smoke expired (TTL) — recheck needed",
                    "evidence": {
                        "status": "expired",
                        "checked_at": "2026-07-01T00:00:00+00:00",
                        "expires_at": "2026-07-31T00:00:00+00:00",
                    },
                }
            ]
        }
    }
    r = _smoke_summary(sv)
    assert r["smoke_status"] == "expired"
    assert r["smoke_checked_at"].startswith("2026-07-01")
    assert r["smoke_expires_at"].startswith("2026-07-31")
    assert "recheck needed" in r["smoke_recheck_reason"]


def test_smoke_summary_passed():
    sv = {
        "gate_result": {
            "gates": [
                {"id": "sandbox_smoke", "reason": "", "evidence": {"status": "passed"}}
            ]
        }
    }
    r = _smoke_summary(sv)
    assert r["smoke_status"] == "passed"
    assert r["smoke_recheck_reason"] is None


def test_smoke_summary_fresh_running_marker_wins():
    # G2: a FRESH running marker wins over the gate status.
    sv = {
        "smoke_running": {"started_at": _iso(timedelta(seconds=-5))},
        "gate_result": {
            "gates": [{"id": "sandbox_smoke", "evidence": {"status": "passed"}}]
        },
    }
    assert _smoke_summary(sv)["smoke_status"] == "running"


def test_smoke_summary_stale_running_marker_falls_back():
    # G2: a stale marker (older than the TTL) left by a crashed process must NOT
    # show "running" forever — fall back to the real smoke status.
    sv = {
        "smoke_running": {"started_at": _iso(timedelta(minutes=-20))},
        "gate_result": {
            "gates": [{"id": "sandbox_smoke", "evidence": {"status": "passed"}}]
        },
    }
    assert _smoke_summary(sv)["smoke_status"] == "passed"


def test_smoke_summary_empty():
    r = _smoke_summary({})
    assert r["smoke_status"] is None
    assert r["smoke_checked_at"] is None
    assert r["smoke_recheck_reason"] is None
