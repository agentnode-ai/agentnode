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

MILESTONES = [100, 1_000, 5_000, 10_000, 50_000, 100_000]


# --- Task: Download milestones (every hour) ---

async def check_download_milestones():
    """Check if any packages crossed download milestones and send emails."""
    try:
        from app.packages.models import Package
        from app.shared.email import send_download_milestone_email, get_publisher_email

        # Use Redis for milestone tracking (survives restarts).
        # P1-CD6: wrap the Redis client in a try/finally so the TCP connection
        # is always released, even if the DB query raises or an SMTP send
        # bubbles an exception. The previous code only called aclose() on the
        # happy path and leaked a connection per failed iteration.
        from app.config import settings
        import redis.asyncio as aioredis
        redis = aioredis.from_url(settings.REDIS_URL)
        try:
            async with async_session_factory() as session:
                # P1-D6: previously this loaded every package over 100 downloads
                # and iterated all milestones per package (O(packages × milestones)
                # with DB + Redis roundtrips). Instead we query per-milestone
                # once, starting from the highest, and short-circuit: any package
                # that has already crossed a higher milestone also satisfies every
                # lower one, so we can mark lower milestones as notified in bulk.
                for milestone in sorted(MILESTONES, reverse=True):
                    result = await session.execute(
                        select(Package.id, Package.slug, Package.publisher_id)
                        .where(Package.download_count >= milestone)
                    )
                    rows = result.all()
                    if not rows:
                        continue

                    for _pkg_id, slug, publisher_id in rows:
                        key = f"{slug}:{milestone}"
                        if await redis.sismember("notified_milestones", key):
                            continue
                        await redis.sadd("notified_milestones", key)

                        pub_email = await get_publisher_email(publisher_id)
                        if pub_email:
                            await send_download_milestone_email(pub_email, slug, milestone)
                            logger.info(f"Milestone email: {slug} reached {milestone}")
        finally:
            await redis.aclose()
    except Exception:
        logger.exception("check_download_milestones failed")


# --- Task: Daily admin digest (every 24h) ---

async def send_daily_admin_digest():
    """Send daily stats summary to all admins."""
    try:
        from app.auth.models import User
        from app.packages.models import Installation, Package, PackageVersion, PackageReport
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

            # Installations in last 24h (proxy for downloads)
            downloads_24h = (await session.execute(
                select(func.count(Installation.id)).where(Installation.installed_at >= yesterday)
            )).scalar() or 0

        stats = {
            "new_users": new_users,
            "new_packages": new_packages,
            "open_reports": open_reports,
            "quarantined": quarantined,
            "downloads_24h": downloads_24h,
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
        from app.shared.email import send_weekly_publisher_digest
        from sqlalchemy.orm import selectinload

        now = datetime.now(timezone.utc)
        week_ago = now - timedelta(days=7)

        # P1-CD7: collect everything we need inside the session, then EXIT the
        # session context before any SMTP send. Holding a DB connection over
        # a network SMTP call (which can take seconds to minutes on timeout)
        # pins a pooled connection and starves the rest of the app.
        digest_payload: list[tuple[str, str, dict]] = []
        async with async_session_factory() as session:
            result = await session.execute(
                select(Publisher).options(selectinload(Publisher.user))
            )
            publishers = result.scalars().all()

            valid_pubs = [p for p in publishers if p.user and p.user.email]
            if not valid_pubs:
                return

            pub_ids = [p.id for p in valid_pubs]

            pkg_stats_result = await session.execute(
                select(
                    Package.publisher_id,
                    func.count(Package.id).label("pkg_count"),
                    func.coalesce(func.sum(Package.download_count), 0).label("total_downloads"),
                )
                .where(Package.publisher_id.in_(pub_ids))
                .group_by(Package.publisher_id)
            )
            pkg_stats = {row.publisher_id: (row.pkg_count, row.total_downloads) for row in pkg_stats_result.all()}

            install_result = await session.execute(
                select(
                    Package.publisher_id,
                    func.count(Installation.id).label("new_installs"),
                )
                .join(Installation, Installation.package_id == Package.id)
                .where(
                    Package.publisher_id.in_(pub_ids),
                    Installation.installed_at >= week_ago,
                )
                .group_by(Package.publisher_id)
            )
            install_stats = {row.publisher_id: row.new_installs for row in install_result.all()}

            # Build a plain-data payload with nothing that still references the session.
            for pub in valid_pubs:
                pkg_count, total_downloads = pkg_stats.get(pub.id, (0, 0))
                if pkg_count == 0:
                    continue
                digest_payload.append((
                    pub.user.email,
                    pub.slug,
                    {
                        "downloads": int(total_downloads),
                        "installs": int(install_stats.get(pub.id, 0)),
                        "packages": int(pkg_count),
                    },
                ))

        # Session is now closed. Safe to do slow SMTP work.
        sent = 0
        for email, slug, stats in digest_payload:
            try:
                await send_weekly_publisher_digest(email, slug, stats)
                sent += 1
            except Exception:
                logger.exception("weekly digest send failed for publisher %s", slug)

        logger.info(f"Weekly publisher digests sent to {sent} publisher(s)")

    except Exception:
        logger.exception("send_weekly_publisher_digests failed")


# --- Task: Reconcile install counts (daily) ---

async def reconcile_install_counts():
    """Reconcile denormalized install_count from installations table.

    Only updates packages with drift. Whitelist: status IN ('installed', 'active').

    P1-D7: takes a Postgres advisory lock before running. When multiple API
    processes (or a manual admin run) invoke this simultaneously, the advisory
    lock ensures only one reconciler runs at a time — otherwise two writers
    can race, each reading their own aggregate snapshot, and the losing writer
    stamps a stale count over a fresh one. 0x52_49_43_31 = "RIC1" = Reconcile
    Install Counts v1; arbitrary but stable.
    """
    # 0x5249_4331 — 'R' 'I' 'C' '1'
    LOCK_KEY = 0x52494331
    try:
        from sqlalchemy import text as sa_text
        from app.packages.models import Package, Installation

        async with async_session_factory() as session:
            # pg_try_advisory_lock returns true/false — no wait, just skip the
            # run if another worker is already reconciling.
            lock_res = await session.execute(
                sa_text("SELECT pg_try_advisory_lock(:k)").bindparams(k=LOCK_KEY)
            )
            got_lock = bool(lock_res.scalar())
            if not got_lock:
                logger.info("reconcile_install_counts: another worker holds the lock, skipping")
                return

            try:
                # Get actual counts from installations table
                result = await session.execute(
                    select(
                        Installation.package_id,
                        func.count(Installation.id).label("cnt"),
                    )
                    .where(Installation.status.in_(("installed", "active")))
                    .group_by(Installation.package_id)
                )
                actual_counts = {row.package_id: row.cnt for row in result.all()}

                # Get current denormalized counts
                pkg_result = await session.execute(
                    select(Package.id, Package.slug, Package.install_count)
                )
                packages = pkg_result.all()

                updated = 0
                for pkg_id, slug, current_count in packages:
                    actual = actual_counts.get(pkg_id, 0)
                    if actual != current_count:
                        drift = actual - current_count
                        logger.warning(
                            "install_count drift: %s old=%d computed=%d drift=%d",
                            slug, current_count, actual, drift,
                        )
                        from sqlalchemy import update
                        await session.execute(
                            update(Package)
                            .where(Package.id == pkg_id)
                            .values(install_count=actual)
                        )
                        updated += 1

                if updated:
                    await session.commit()
                    logger.info("Reconciled install_count for %d package(s)", updated)
            finally:
                # Always release the advisory lock, even if we rolled back.
                await session.execute(
                    sa_text("SELECT pg_advisory_unlock(:k)").bindparams(k=LOCK_KEY)
                )

    except Exception:
        logger.exception("reconcile_install_counts failed")


# --- Task: Cleanup stale verification dirs (every hour) ---

def _pid_is_live(pid: int) -> bool:
    """Return True if a process with this pid is still alive on this host."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but is owned by someone else — treat as live.
        return True
    except OSError:
        return False
    return True


async def cleanup_stale_verification_dirs():
    """Remove /tmp/agentnode_verify_* directories older than 30 minutes.

    P1-L9: the sandbox drops a .pid file when it is created. If the pid
    points at a still-running process we keep the dir, even if its mtime
    has crossed the age cutoff, to avoid yanking files out from under an
    in-progress verification. Only dirs with no pidfile, a stale pidfile,
    or an unreadable pidfile are removed.
    """
    try:
        import tempfile
        tmp_base = tempfile.gettempdir()
        pattern = os.path.join(tmp_base, "agentnode_verify_*")
        cutoff = time.time() - 1800  # 30 minutes ago
        cleaned = 0
        skipped_live = 0

        for path in glob.glob(pattern):
            try:
                if not os.path.isdir(path):
                    continue
                if os.path.getmtime(path) >= cutoff:
                    continue

                pidfile = os.path.join(path, ".pid")
                if os.path.isfile(pidfile):
                    try:
                        with open(pidfile) as pf:
                            pid = int(pf.read().strip() or "0")
                    except (ValueError, OSError):
                        pid = 0
                    if pid and _pid_is_live(pid):
                        skipped_live += 1
                        continue

                shutil.rmtree(path, ignore_errors=True)
                cleaned += 1
            except Exception as e:
                logger.debug(f"Could not clean up {path}: {e}")

        if cleaned or skipped_live:
            logger.info(
                "Verification dir cleanup: removed %d, skipped %d live",
                cleaned, skipped_live,
            )
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

    loop = asyncio.get_running_loop()

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

    # Daily install count reconciliation
    _tasks.append(loop.create_task(
        _run_periodic(86400, reconcile_install_counts, "reconcile_install_counts")
    ))

    logger.info(f"Started {len(_tasks)} cron tasks")


async def stop_cron_tasks():
    """Cancel all background cron tasks and wait for them to exit.

    P1-CD8: previously this was a sync function that fired `task.cancel()` and
    returned without awaiting, so on shutdown the event loop could tear down
    mid-iteration, leaking DB/Redis connections and printing CancelledError
    tracebacks. We now gather the cancelled tasks and swallow CancelledError.
    """
    if not _tasks:
        return
    for task in _tasks:
        task.cancel()
    # return_exceptions=True so a CancelledError (expected) does not abort.
    await asyncio.gather(*_tasks, return_exceptions=True)
    _tasks.clear()
    logger.info("Cron tasks stopped")
