"""Check capability schemas for word-counter and webpage-extractor."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import async_session_factory
from sqlalchemy import text

import app.auth.models, app.publishers.models, app.packages.models, app.verification.models
import app.webhooks.models, app.shared.models, app.admin.models, app.blog.models, app.sitemap.models


async def check():
    async with async_session_factory() as session:
        result = await session.execute(text(
            "SELECT p.slug, c.name, c.entrypoint, c.input_schema "
            "FROM capabilities c "
            "JOIN package_versions pv ON c.package_version_id = pv.id "
            "JOIN packages p ON pv.id = p.latest_version_id "
            "WHERE p.slug IN ('word-counter-pack', 'webpage-extractor-pack')"
        ))
        for row in result.fetchall():
            print(f"{row.slug} / {row.name}: ep={row.entrypoint}, schema={row.input_schema}")


if __name__ == "__main__":
    asyncio.run(check())
