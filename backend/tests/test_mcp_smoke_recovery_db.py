"""DB-backed G2/G1 tests: smoke_running startup recovery + post-semaphore dedup.

These exercise the real DB path (own-session recovery, gate recompute) that the
mock tests in test_mcp_smoke_reaper.py can't cover.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from tests.conftest import setup_publisher_user
from app.mcp import smoke_executor as ex
from app.mcp.models import McpSubmission


@pytest.fixture(autouse=True)
def _resources_ok(monkeypatch):
    # G3: these recovery/G1 tests are not about host resources — keep the preflight
    # green so run_and_store_smoke reaches its normal path. (Windows has no
    # /proc/meminfo, so the real check would fail-closed here.)
    monkeypatch.setattr(
        ex,
        "check_host_resources",
        lambda: ex.ResourceCheck(True, 4096, 1024, 50.0, 5, 0.1, None),
    )
    ex._inflight = 0
    yield
    ex._inflight = 0


def _now():
    return datetime.now(timezone.utc)


def _manifest(sfx):
    return {
        "runtime": "mcp",
        "package_id": f"time-{sfx}",
        "mcp_server": {
            "pypi_package": "mcp-server-time",
            "command": ["uvx", "mcp-server-time==2026.7.10"],
        },
    }


def _sv_base():
    return {
        "registry": "pypi",
        "package_name": "mcp-server-time",
        "package_exists": True,
        "resolved_version": "2026.7.10",
        "command_pinning": "pinned",
    }


async def _make_submission(client, session, sfx, server_verification, manifest):
    _token, pub = await setup_publisher_user(
        client, f"{sfx}@t.dev", f"{sfx}u", "TestPass123!", f"pub-{sfx}", f"Pub {sfx}"
    )
    sub = McpSubmission(
        publisher_id=UUID(str(pub["id"])),
        package_name="mcp-server-time",
        package_registry="pypi",
        package_version="2026.7.10",
        manifest_raw=manifest,
        verification_report={},
        server_verification=server_verification,
        status="REVIEW_NEEDED",
    )
    session.add(sub)
    await session.commit()
    return sub


# ---------------------------------------------------------------------------
# smoke_running startup recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recover_clears_stale_marker_preserves_smoke(client, session):
    sv = {
        **_sv_base(),
        "smoke": {
            "status": "passed",
            "runtime": "pypi",
            "package": "mcp-server-time",
            "version": "2026.7.10",
            "initialized": True,
            "tools_count": 2,
        },
        "smoke_running": {"started_at": (_now() - timedelta(minutes=20)).isoformat()},
    }
    sub = await _make_submission(client, session, "rec1", sv, _manifest("rec1"))

    ok, cleared = await ex._recover_stale_smoke_running()
    assert ok is True
    assert cleared >= 1

    await session.refresh(sub)
    out = sub.server_verification
    assert "smoke_running" not in out  # marker cleared
    assert out["smoke"]["status"] == "passed"  # stored smoke preserved
    assert out["smoke"]["tools_count"] == 2
    assert "gate_result" in out  # gate recomputed via the central helper


@pytest.mark.asyncio
async def test_recover_handles_multiple_rows_independently(client, session):
    a = await _make_submission(
        client,
        session,
        "reca",
        {**_sv_base(), "smoke_running": {"started_at": "not-a-timestamp"}},
        _manifest("reca"),
    )
    b = await _make_submission(
        client,
        session,
        "recb",
        {**_sv_base(), "smoke_running": {"started_at": (_now()).isoformat()}},
        _manifest("recb"),
    )

    ok, cleared = await ex._recover_stale_smoke_running()
    assert ok is True
    assert cleared >= 2

    await session.refresh(a)
    await session.refresh(b)
    assert "smoke_running" not in a.server_verification
    assert "smoke_running" not in b.server_verification  # post-restart: all orphaned


# ---------------------------------------------------------------------------
# G1 post-semaphore dedup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g1_skips_when_fresh_matching_passed(client, session, monkeypatch):
    manifest = _manifest("g1a")
    sv_base = _sv_base()
    keys = ex.current_smoke_keys(manifest, sv_base)
    fresh_smoke = {
        "status": "passed",
        **keys,
        "initialized": True,
        "tools_count": 2,
        "checked_at": _now().isoformat(),
        "expires_at": (_now() + timedelta(days=30)).isoformat(),
    }
    sub = await _make_submission(
        client, session, "g1a", {**sv_base, "smoke": fresh_smoke}, manifest
    )

    called = []
    monkeypatch.setattr(ex, "run_smoke", lambda *a, **k: called.append(1) or {})

    await ex.run_and_store_smoke(str(sub.id))

    assert called == []  # dedup returned before running a smoke
    await session.refresh(sub)
    assert "smoke_running" not in sub.server_verification  # no marker set
    assert sub.server_verification["smoke"]["tools_count"] == 2  # unchanged


@pytest.mark.asyncio
async def test_g1_allows_recheck_when_expired(client, session, monkeypatch):
    manifest = _manifest("g1b")
    sv_base = _sv_base()
    keys = ex.current_smoke_keys(manifest, sv_base)
    expired_smoke = {
        "status": "passed",
        **keys,
        "initialized": True,
        "tools_count": 2,
        "checked_at": (_now() - timedelta(days=60)).isoformat(),
        "expires_at": (_now() - timedelta(days=30)).isoformat(),  # expired
    }
    sub = await _make_submission(
        client, session, "g1b", {**sv_base, "smoke": expired_smoke}, manifest
    )

    called = []

    def fake_run(m, sv, **k):
        called.append(1)
        return ex._result(
            "unavailable", failure_reason="disabled", checked_at=_now().isoformat()
        )

    monkeypatch.setattr(ex, "run_smoke", fake_run)

    await ex.run_and_store_smoke(str(sub.id))

    assert called == [1]  # expired stored smoke -> recheck proceeds (no skip)
    await session.refresh(sub)
    assert "smoke_running" not in sub.server_verification  # marker cleared after run
