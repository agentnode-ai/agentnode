"""Re-verify webpage-extractor-pack and word-counter-pack."""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.database import async_session_factory
from sqlalchemy import text

settings.VERIFICATION_ENABLED = True
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

import app.auth.models, app.publishers.models, app.packages.models, app.verification.models
import app.webhooks.models, app.shared.models, app.admin.models, app.blog.models, app.sitemap.models


async def go():
    from app.verification.pipeline import run_verification

    async with async_session_factory() as session:
        result = await session.execute(text(
            "SELECT p.slug, pv.id as version_id "
            "FROM packages p "
            "JOIN package_versions pv ON p.latest_version_id = pv.id "
            "WHERE p.slug IN ('webpage-extractor-pack', 'word-counter-pack')"
        ))
        for row in result.fetchall():
            print(f"Verifying {row.slug}...")
            await run_verification(row.version_id, triggered_by="admin_reverify")
            print(f"Done {row.slug}")
            await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(go())
