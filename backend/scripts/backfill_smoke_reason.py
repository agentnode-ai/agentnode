"""Backfill smoke_reason from existing smoke_log entries.

Usage: python -m scripts.backfill_smoke_reason [--dry-run]
"""
import asyncio
import json
import sys

from sqlalchemy import select, update

# Import all models so SQLAlchemy resolves all foreign keys
import app.auth.models  # noqa: F401
import app.publishers.models  # noqa: F401
import app.packages.models  # noqa: F401
import app.blog.models  # noqa: F401

from app.database import async_session_factory
from app.verification.models import VerificationResult


async def backfill(dry_run: bool = False):
    async with async_session_factory() as session:
        results = await session.execute(
            select(VerificationResult).where(
                VerificationResult.smoke_reason.is_(None),
                VerificationResult.smoke_log.isnot(None),
                VerificationResult.smoke_status.isnot(None),
            )
        )
        rows = results.scalars().all()
        print(f"Found {len(rows)} results to backfill")

        updated = 0
        for vr in rows:
            reason = _extract_reason_from_log(vr.smoke_log, vr.smoke_status)
            if reason:
                if not dry_run:
                    vr.smoke_reason = reason
                updated += 1
                print(f"  {vr.id}: {vr.smoke_status} → smoke_reason={reason}")

        if not dry_run:
            await session.commit()

        print(f"{'Would update' if dry_run else 'Updated'} {updated} rows")


def _extract_reason_from_log(smoke_log: str, smoke_status: str) -> str | None:
    """Extract dominant reason from structured smoke_log JSON lines."""
    if not smoke_log:
        return None

    reasons = []
    for line in smoke_log.splitlines():
        line = line.strip()
        if not line or line.startswith("["):
            continue
        try:
            entry = json.loads(line)
            reason = entry.get("reason")
            if reason:
                reasons.append(reason)
        except (json.JSONDecodeError, ValueError):
            continue

    if not reasons:
        if smoke_status == "passed":
            return "ok"
        return None

    # Use same logic as _dominant_reason in steps.py
    from app.verification.smoke_context import FATAL_REASONS
    if len(set(reasons)) == 1:
        return reasons[0]
    for r in reasons:
        if r in FATAL_REASONS:
            return r
    for r in reasons:
        if r not in ("ok", "acceptable_external_dependency", "unknown_smoke_condition"):
            return r
    return reasons[0]


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    asyncio.run(backfill(dry_run))
