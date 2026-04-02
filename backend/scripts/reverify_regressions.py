"""Re-verify only the packages that regressed in the batch run."""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.database import async_session_factory
from sqlalchemy import text

settings.VERIFICATION_ENABLED = True

import app.auth.models, app.publishers.models, app.packages.models
import app.verification.models, app.webhooks.models, app.shared.models
import app.admin.models, app.blog.models, app.sitemap.models

TARGETS = [
    "browser-automation-pack",
    "csv-analyzer-pack",
    "database-connector-pack",
    "file-converter-pack",
    "ocr-reader-pack",
    "pdf-extractor-pack",
    "pdf-reader-pack",
    "regex-builder-pack",
    "research-seo-keywords-and-clusters-them-pack",
    "screenshot-capture-pack",
    "text-to-speech-pack",
]


async def main():
    from app.verification.pipeline import run_verification

    async with async_session_factory() as session:
        bind_params = {f"s{i}": s for i, s in enumerate(TARGETS)}
        placeholders = ", ".join(f":s{i}" for i in range(len(TARGETS)))
        r = await session.execute(text(f"""
            SELECT p.slug, pv.id as version_id, pv.artifact_object_key
            FROM packages p
            JOIN package_versions pv ON p.latest_version_id = pv.id
            WHERE p.slug IN ({placeholders})
            ORDER BY p.slug
        """), bind_params)
        rows = r.fetchall()

    for i, row in enumerate(rows, 1):
        print(f"[{i}/{len(rows)}] {row.slug}...", flush=True)
        await run_verification(row.version_id, triggered_by="admin_reverify")
        await asyncio.sleep(2)

    # Report
    async with async_session_factory() as session:
        bind_params = {f"s{i}": s for i, s in enumerate(TARGETS)}
        placeholders = ", ".join(f":s{i}" for i in range(len(TARGETS)))
        r = await session.execute(text(f"""
            SELECT p.slug, vr.smoke_status, vr.smoke_log
            FROM packages p
            JOIN package_versions pv ON p.latest_version_id = pv.id
            JOIN verification_results vr ON pv.latest_verification_result_id = vr.id
            WHERE p.slug IN ({placeholders})
            ORDER BY p.slug
        """), bind_params)
        rows = r.fetchall()

    print("\n" + "=" * 60)
    for row in rows:
        reason = None
        detail = None
        if row.smoke_log:
            for line in row.smoke_log.splitlines():
                if line.startswith("{"):
                    try:
                        entry = json.loads(line)
                        reason = entry.get("reason")
                        etype = entry.get("error_type", "")
                        msg = entry.get("message", "")
                        detail = f"{etype}: {msg[:100]}" if etype else None
                    except Exception:
                        pass
        icon = {"passed": "OK", "inconclusive": "~~", "failed": "XX"}.get(row.smoke_status, "??")
        print(f"[{icon}] {row.slug}: {row.smoke_status} ({reason})")
        if detail:
            print(f"     {detail}")


if __name__ == "__main__":
    asyncio.run(main())
