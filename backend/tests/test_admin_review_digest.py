"""The daily admin digest names what waits in review, and says whether it
arrived. A count alone and an unconditional "sent" log let a new publisher's
skill wait 16 days unseen while the mail server rejected the admin address."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.shared.email import _waiting_section

# --- the mail section -------------------------------------------------------


def test_no_waiting_versions_adds_nothing():
    assert _waiting_section([]) == ""


def test_waiting_versions_are_listed_and_escaped():
    out = _waiting_section(
        [
            {
                "slug": "demo-skill",
                "version": "1.0.0",
                "reason": "new_publisher_review",
                "days": 16,
            },
            {
                "slug": "x",
                "version": "2",
                "reason": "<script>alert(1)</script>",
                "days": 2,
            },
        ]
    )
    assert "Waiting in review for more than a day: 2" in out
    assert "demo-skill@1.0.0" in out
    assert "16d" in out
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_long_lists_are_cut_with_a_count():
    waiting = [
        {"slug": f"p{i}", "version": "1", "reason": "r", "days": 3} for i in range(25)
    ]
    out = _waiting_section(waiting)
    assert "Waiting in review for more than a day: 25" in out
    assert "p19@1" in out
    assert "p20@1" not in out
    assert "and 5 more" in out


# --- the cron task ----------------------------------------------------------


@pytest_asyncio.fixture
async def session_factory():
    from app.shared.models import Base

    eng = create_async_engine(settings.DATABASE_URL)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


async def _seed(session_factory):
    from app.auth.models import User
    from app.packages.models import Package, PackageVersion
    from app.publishers.models import Publisher

    now = datetime.now(timezone.utc)
    async with session_factory() as s:
        user = User(id=uuid4(), email="p@example.com", username="p", password_hash="x")
        s.add(user)
        await s.flush()
        pub = Publisher(id=uuid4(), user_id=user.id, display_name="P", slug="p")
        s.add(pub)
        await s.flush()
        for slug, age in (
            ("stuck-skill", timedelta(days=16)),
            ("fresh-skill", timedelta(hours=2)),
        ):
            pkg = Package(
                id=uuid4(),
                publisher_id=pub.id,
                slug=slug,
                name=slug,
                package_type="skill",
                summary="s",
            )
            s.add(pkg)
            await s.flush()
            s.add(
                PackageVersion(
                    id=uuid4(),
                    package_id=pkg.id,
                    version_number="0.1.0",
                    manifest_raw={},
                    runtime="none",
                    quarantine_status="quarantined",
                    quarantine_reason="new_publisher_review",
                    quarantined_at=now - age,
                    verification_status="passed",
                )
            )
        await s.commit()


async def _run_digest(session_factory, delivered: bool):
    """Returns (sender mock, logger mock). The app's "agentnode" loggers do not
    propagate to pytest's caplog, so the cron logger itself is observed."""
    sender = AsyncMock(return_value=delivered)
    log = MagicMock()
    with (
        patch("app.tasks.cron.async_session_factory", session_factory),
        patch("app.tasks.cron.logger", log),
        patch(
            "app.shared.email.get_admin_emails",
            AsyncMock(return_value=["admin@example.com"]),
        ),
        patch("app.shared.email.send_admin_daily_digest", sender),
    ):
        from app.tasks.cron import send_daily_admin_digest

        await send_daily_admin_digest()
    return sender, log


def _logged(call) -> str:
    fmt, *args = call.args
    return fmt % tuple(args)


@pytest.mark.asyncio
async def test_digest_lists_only_versions_waiting_more_than_a_day(session_factory):
    await _seed(session_factory)
    sender, log = await _run_digest(session_factory, delivered=True)
    stats = sender.await_args.args[1]
    assert stats["quarantined"] == 2
    assert [w["slug"] for w in stats["waiting"]] == ["stuck-skill"]
    assert stats["waiting"][0]["days"] == 16
    log.error.assert_not_called()
    assert (
        _logged(log.info.call_args)
        == "Daily admin digest delivered to 1 admin(s); 1 version(s) waiting > 24h"
    )


@pytest.mark.asyncio
async def test_undelivered_digest_is_logged_as_an_error(session_factory):
    await _seed(session_factory)
    _, log = await _run_digest(session_factory, delivered=False)
    log.info.assert_not_called()
    assert _logged(log.error.call_args).startswith(
        "Daily admin digest NOT delivered to 1 of 1 admin(s); 1 version(s) waiting > 24h"
    )
