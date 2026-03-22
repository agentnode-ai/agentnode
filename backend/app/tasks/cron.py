"""Background cron tasks for periodic email notifications and cleanup.

Runs as asyncio tasks within the FastAPI process.
No external scheduler needed.
"""

import asyncio
import glob
import logging
import os
import shutil
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.database import async_session_factory

logger = logging.getLogger("agentnode.cron")

# Track milestone thresholds already notified (in-memory, resets on restart)
_notified_milestones: set[str] = set()

MILESTONES = [100, 1_000, 5_000, 10_000, 50_000, 100_000]


# --- Task: Download milestones (every hour) ---

async def check_download_milestones():
    """Check if any packages crossed download milestones and send emails."""
    try:
        from app.packages.models import Package
        from app.shared.email import send_download_milestone_email, get_publisher_email

        async with async_session_factory() as session:
            result = await session.execute(
                select(Package).where(Package.download_count >= MILESTONES[0])
            )
            packages = result.scalars().all()

            for pkg in packages:
                for milestone in MILESTONES:
                    key = f"{pkg.slug}:{milestone}"
                    if key in _notified_milestones:
                        continue
                    if pkg.download_count >= milestone:
                        _notified_milestones.add(key)
                        # Only send for the highest crossed milestone
                        next_milestones = [m for m in MILESTONES if m > milestone]
                        if next_milestones and pkg.download_count >= next_milestones[0]:
                            continue  # Skip, a higher milestone was also reached

                        pub_email = await get_publisher_email(pkg.publisher_id)
                        if pub_email:
                            await send_download_milestone_email(pub_email, pkg.slug, milestone)
                            logger.info(f"Milestone email: {pkg.slug} reached {milestone}")
    except Exception:
        logger.exception("check_download_milestones failed")


# --- Task: Daily admin digest (every 24h) ---

async def send_daily_admin_digest():
    """Send daily stats summary to all admins."""
    try:
        from app.auth.models import User
        from app.packages.models import Package, PackageVersion, PackageReport
        from app.shared.email import send_admin_daily_digest, get_admin_emails

        now = datetime.now(timezone.utc)
        yesterday = now - timedelta(hours=24)

        async with async_session_factory() as session:
            # New users in 24h
            new_users = (await session.execute(
                select(func.count(User.id)).where(User.created_at >= yesterday)
            )).scalar() or 0

            # New packages in 24h
            new_packages = (await session.execute(
                select(func.count(Package.id)).where(Package.created_at >= yesterday)
            )).scalar() or 0

            # Open reports
            open_reports = (await session.execute(
                select(func.count(PackageReport.id)).where(PackageReport.status == "submitted")
            )).scalar() or 0

            # Quarantined versions
            quarantined = (await session.execute(
                select(func.count(PackageVersion.id)).where(PackageVersion.quarantine_status == "quarantined")
            )).scalar() or 0

            # Total downloads (approximate from sum)
            total_dl = (await session.execute(
                select(func.sum(Package.download_count))
            )).scalar() or 0

        stats = {
            "new_users": new_users,
            "new_packages": new_packages,
            "open_reports": open_reports,
            "quarantined": quarantined,
            "downloads_24h": 0,  # Would need a separate counter for daily deltas
        }

        # Only send if there's something to report
        if new_users or new_packages or open_reports or quarantined:
            admin_emails = await get_admin_emails()
            for email in admin_emails:
                await send_admin_daily_digest(email, stats)
            logger.info(f"Daily admin digest sent to {len(admin_emails)} admin(s)")
        else:
            logger.debug("Daily admin digest: nothing to report, skipping")

    except Exception:
        logger.exception("send_daily_admin_digest failed")


# --- Task: Weekly publisher digest (every 7 days) ---

async def send_weekly_publisher_digests():
    """Send weekly stats to all publishers."""
    try:
        from app.packages.models import Package, Installation
        from app.publishers.models import Publisher
        from app.auth.models import User
        from app.shared.email import send_weekly_publisher_digest
        from sqlalchemy.orm import selectinload

        now = datetime.now(timezone.utc)
        week_ago = now - timedelta(days=7)

        async with async_session_factory() as session:
            result = await session.execute(
                select(Publisher).options(selectinload(Publisher.user))
            )
            publishers = result.scalars().all()

            for pub in publishers:
                if not pub.user or not pub.user.email:
                    continue

                # Count packages
                pkg_count = (await session.execute(
                    select(func.count(Package.id)).where(Package.publisher_id == pub.id)
                )).scalar() or 0

                if pkg_count == 0:
                    continue  # Skip publishers with no packages

                # Total downloads
                total_downloads = (await session.execute(
                    select(func.sum(Package.download_count)).where(Package.publisher_id == pub.id)
                )).scalar() or 0

                # New installations this week
                pub_packages = (await session.execute(
                    select(Package.id).where(Package.publisher_id == pub.id)
                )).scalars().all()

                new_installs = 0
                if pub_packages:
                    new_installs = (await session.execute(
                        select(func.count(Installation.id)).where(
                            Installation.package_id.in_(pub_packages),
                            Installation.installed_at >= week_ago,
                        )
                    )).scalar() or 0

                stats = {
                    "downloads": total_downloads,
                    "installs": new_installs,
                    "packages": pkg_count,
                }

                await send_weekly_publisher_digest(pub.user.email, pub.slug, stats)

            logger.info(f"Weekly publisher digests sent to {len(publishers)} publisher(s)")

    except Exception:
        logger.exception("send_weekly_publisher_digests failed")


# --- Task: Cleanup stale verification dirs (every hour) ---

async def cleanup_stale_verification_dirs():
    """Remove /tmp/agentnode_verify_* directories older than 30 minutes."""
    try:
        import tempfile
        tmp_base = tempfile.gettempdir()
        pattern = os.path.join(tmp_base, "agentnode_verify_*")
        cutoff = time.time() - 1800  # 30 minutes ago
        cleaned = 0

        for path in glob.glob(pattern):
            try:
                if os.path.isdir(path) and os.path.getmtime(path) < cutoff:
                    shutil.rmtree(path, ignore_errors=True)
                    cleaned += 1
            except Exception:
                pass

        if cleaned:
            logger.info(f"Cleaned up {cleaned} stale verification dir(s)")
    except Exception:
        logger.exception("cleanup_stale_verification_dirs failed")


# --- Task: Scheduled reverification (Phase 4C, every 6h) ---

async def scheduled_reverification():
    """Re-verify packages > N days old. Max batch_size per run."""
    try:
        from app.config import settings

        if not settings.VERIFICATION_REVERIFY_ENABLED:
            return

        from app.packages.models import PackageVersion
        from app.verification.pipeline import run_verification

        cutoff = datetime.now(timezone.utc) - timedelta(days=settings.VERIFICATION_REVERIFY_DAYS)
        batch_size = settings.VERIFICATION_REVERIFY_BATCH

        async with async_session_factory() as session:
            result = await session.execute(
                select(PackageVersion)
                .where(
                    PackageVersion.artifact_object_key.isnot(None),
                    PackageVersion.verification_status != "running",
                    PackageVersion.last_verified_at < cutoff,
                )
                .order_by(PackageVersion.last_verified_at.asc().nullsfirst())
                .limit(batch_size)
            )
            versions = result.scalars().all()

            if not versions:
                return

            logger.info(f"Scheduled reverification: {len(versions)} package(s) due")

            for pv in versions:
                try:
                    await run_verification(pv.id, triggered_by="scheduled")
                    await asyncio.sleep(5)  # Spacing between runs
                except Exception:
                    logger.exception(f"Scheduled reverification failed for {pv.id}")

    except Exception:
        logger.exception("scheduled_reverification task failed")


# --- Scheduler loop ---

async def _run_periodic(interval_seconds: int, func, name: str):
    """Run a function periodically with error handling."""
    # Initial delay to let the app start up
    await asyncio.sleep(30)
    logger.info(f"Cron task '{name}' started (interval: {interval_seconds}s)")
    while True:
        try:
            await func()
        except Exception:
            logger.exception(f"Cron task '{name}' failed")
        await asyncio.sleep(interval_seconds)


_tasks: list[asyncio.Task] = []


def start_cron_tasks():
    """Start all background cron tasks. Call from the app lifespan."""
    # Cleanup stale verification dirs from previous process (crash recovery)
    try:
        import tempfile
        tmp_base = tempfile.gettempdir()
        pattern = os.path.join(tmp_base, "agentnode_verify_*")
        stale = [p for p in glob.glob(pattern) if os.path.isdir(p)]
        for path in stale:
            shutil.rmtree(path, ignore_errors=True)
        if stale:
            logger.info(f"Startup: cleaned up {len(stale)} stale verification dir(s)")
    except Exception:
        logger.warning("Startup: failed to cleanup stale verification dirs")

    loop = asyncio.get_event_loop()

    _tasks.append(loop.create_task(
        _run_periodic(3600, check_download_milestones, "download_milestones")
    ))
    _tasks.append(loop.create_task(
        _run_periodic(86400, send_daily_admin_digest, "daily_admin_digest")
    ))
    _tasks.append(loop.create_task(
        _run_periodic(604800, send_weekly_publisher_digests, "weekly_publisher_digest")
    ))

    _tasks.append(loop.create_task(
        _run_periodic(3600, cleanup_stale_verification_dirs, "verification_cleanup")
    ))

    # Phase 4C: Scheduled reverification every 6 hours
    _tasks.append(loop.create_task(
        _run_periodic(21600, scheduled_reverification, "scheduled_reverification")
    ))

    logger.info(f"Started {len(_tasks)} cron tasks")


def stop_cron_tasks():
    """Cancel all background cron tasks."""
    for task in _tasks:
        task.cancel()
    _tasks.clear()
    logger.info("Cron tasks stopped")
