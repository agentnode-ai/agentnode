"""Update enrichment fields for all packages with high-quality content.

Reads enrichment data from enrichment_data.json and updates the DB.
Run from /opt/agentnode/backend with venv activated:
    python scripts/update_enrichment.py
"""
import asyncio
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import engine
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def update():
    data_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "enrichment_data.json")
    with open(data_path, "r", encoding="utf-8") as f:
        packages = json.load(f)

    logger.info(f"Loaded enrichment data for {len(packages)} packages")

    async with engine.begin() as conn:
        for pkg in packages:
            slug = pkg["slug"]

            # Get the latest version ID for this package
            result = await conn.execute(text("""
                SELECT pv.id as version_id
                FROM packages p
                JOIN package_versions pv ON p.latest_version_id = pv.id
                WHERE p.slug = :slug
            """), {"slug": slug})
            row = result.fetchone()
            if not row:
                logger.warning(f"  SKIP {slug} — not found in DB")
                continue

            version_id = str(row.version_id)

            # Build update
            updates = {}
            params = {"vid": version_id}

            if "use_cases" in pkg:
                updates["use_cases"] = json.dumps(pkg["use_cases"])
            if "examples" in pkg:
                updates["examples"] = json.dumps(pkg["examples"])
            if "readme_md" in pkg:
                updates["readme_md"] = pkg["readme_md"]
            if "env_requirements" in pkg:
                updates["env_requirements"] = json.dumps(pkg["env_requirements"])

            if not updates:
                logger.info(f"  SKIP {slug} — no updates")
                continue

            set_clauses = []
            for key, value in updates.items():
                set_clauses.append(f"{key} = :{key}")
                params[key] = value

            sql = f"UPDATE package_versions SET {', '.join(set_clauses)} WHERE id = :vid"
            await conn.execute(text(sql), params)
            logger.info(f"  OK {slug} — updated {list(updates.keys())}")

    await engine.dispose()
    logger.info("Update complete!")


if __name__ == "__main__":
    asyncio.run(update())
