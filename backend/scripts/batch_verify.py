"""Run verification for all packages that haven't been verified yet.

Processes packages sequentially to avoid overloading the VPS.
Run from /opt/agentnode/backend with venv activated:
    python scripts/batch_verify.py
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.database import async_session_factory
from sqlalchemy import select, text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Force verification enabled
settings.VERIFICATION_ENABLED = True

# Import ALL models so SQLAlchemy mapper registry is complete
import app.auth.models  # noqa: F401
import app.publishers.models  # noqa: F401
import app.packages.models  # noqa: F401
import app.verification.models  # noqa: F401
import app.webhooks.models  # noqa: F401
import app.shared.models  # noqa: F401
import app.admin.models  # noqa: F401
import app.blog.models  # noqa: F401
import app.sitemap.models  # noqa: F401


async def batch_verify():
    from app.packages.models import Package, PackageVersion
    from app.verification.pipeline import run_verification

    async with async_session_factory() as session:
        # Get all latest versions (re-verify all to fill smoke + tests)
        result = await session.execute(text("""
            SELECT p.slug, pv.id as version_id, pv.artifact_object_key
            FROM packages p
            JOIN package_versions pv ON p.latest_version_id = pv.id
            ORDER BY p.slug
        """))
        rows = result.fetchall()
        logger.info(f"Found {len(rows)} packages without verification")

        for i, row in enumerate(rows, 1):
            slug = row.slug
            version_id = row.version_id
            has_artifact = bool(row.artifact_object_key)

            if not has_artifact:
                logger.info(f"  [{i}/{len(rows)}] SKIP {slug} — no artifact")
                continue

            logger.info(f"  [{i}/{len(rows)}] Verifying {slug}...")
            try:
                await run_verification(version_id, triggered_by="admin_reverify")
                logger.info(f"  [{i}/{len(rows)}] DONE {slug}")
            except Exception as e:
                logger.error(f"  [{i}/{len(rows)}] FAIL {slug}: {e}")

            # Small delay between packages
            await asyncio.sleep(2)

    logger.info("Batch verification complete!")


if __name__ == "__main__":
    asyncio.run(batch_verify())
