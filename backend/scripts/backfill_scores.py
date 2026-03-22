"""Backfill verification_score and verification_tier from existing results.

Usage: python -m scripts.backfill_scores [--dry-run]
"""
import asyncio
import sys

from sqlalchemy import select

# Import all models so SQLAlchemy resolves all foreign keys
import app.auth.models  # noqa: F401
import app.publishers.models  # noqa: F401
import app.packages.models  # noqa: F401
import app.blog.models  # noqa: F401

from app.database import async_session_factory
from app.packages.models import PackageVersion
from app.verification.models import VerificationResult
from app.verification.scoring import compute_tool_score


async def backfill(dry_run: bool = False):
    async with async_session_factory() as session:
        results = await session.execute(
            select(VerificationResult).where(
                VerificationResult.verification_score.is_(None),
                VerificationResult.status.in_(["passed", "failed"]),
            )
        )
        rows = results.scalars().all()
        print(f"Found {len(rows)} results to score")

        for vr in rows:
            score, tier, breakdown = compute_tool_score(vr)
            print(f"  {vr.id}: score={score} tier={tier} breakdown={breakdown}")
            if not dry_run:
                vr.verification_score = score
                vr.verification_tier = tier
                vr.score_breakdown = breakdown

        # Also update PackageVersion denormalized fields
        pvs = await session.execute(
            select(PackageVersion).where(
                PackageVersion.latest_verification_result_id.isnot(None),
                PackageVersion.verification_score.is_(None),
            )
        )
        pv_rows = pvs.scalars().all()
        print(f"Found {len(pv_rows)} package versions to update")

        for pv in pv_rows:
            vr = await session.execute(
                select(VerificationResult).where(VerificationResult.id == pv.latest_verification_result_id)
            )
            vr_obj = vr.scalar_one_or_none()
            if vr_obj and vr_obj.verification_score is not None:
                if not dry_run:
                    pv.verification_score = vr_obj.verification_score
                    pv.verification_tier = vr_obj.verification_tier
                print(f"  PV {pv.id}: score={vr_obj.verification_score} tier={vr_obj.verification_tier}")

        if not dry_run:
            await session.commit()
        print(f"{'Would update' if dry_run else 'Updated'} {len(rows)} VRs + {len(pv_rows)} PVs")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    asyncio.run(backfill(dry_run))
