"""Re-verify Phase 2A target packages by block."""
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

BLOCK_B = [
    "calendar-manager-pack",
    "crm-connector-pack",
    "home-automation-pack",
    "markdown-notes-pack",
    "scheduler-pack",
    "smart-lights-pack",
    "social-media-pack",
    "task-manager-pack",
    "youtube-analyzer-pack",
]

BLOCK_C = [
    "csv-analyzer-pack",
    "file-converter-pack",
    "ocr-reader-pack",
    "pdf-extractor-pack",
    "pdf-reader-pack",
]


async def reverify(targets, label):
    from app.verification.pipeline import run_verification

    async with async_session_factory() as session:
        placeholders = ",".join(f"'{s}'" for s in targets)
        r = await session.execute(text(f"""
            SELECT p.slug, pv.id as version_id
            FROM packages p
            JOIN package_versions pv ON p.latest_version_id = pv.id
            WHERE p.slug IN ({placeholders})
            ORDER BY p.slug
        """))
        rows = r.fetchall()

    for i, row in enumerate(rows, 1):
        print(f"[{i}/{len(rows)}] {row.slug}...", flush=True)
        await run_verification(row.version_id, triggered_by="admin_reverify")
        await asyncio.sleep(2)

    # Report
    async with async_session_factory() as session:
        placeholders = ",".join(f"'{s}'" for s in targets)
        r = await session.execute(text(f"""
            SELECT p.slug, vr.smoke_status, vr.smoke_log
            FROM packages p
            JOIN package_versions pv ON p.latest_version_id = pv.id
            JOIN verification_results vr ON pv.latest_verification_result_id = vr.id
            WHERE p.slug IN ({placeholders})
            ORDER BY p.slug
        """))
        rows = r.fetchall()

    print()
    print("=" * 60)
    print(f"{label}")
    print("=" * 60)
    for row in rows:
        reasons = []
        if row.smoke_log:
            for line in row.smoke_log.splitlines():
                if line.startswith("{"):
                    try:
                        entry = json.loads(line)
                        reason = entry.get("reason")
                        verdict = entry.get("verdict")
                        ci = entry.get("candidate_index", "?")
                        et = entry.get("error_type", "")
                        msg = (entry.get("message") or "")[:80]
                        reasons.append(f"  c{ci}: {reason} ({verdict}) {et}: {msg}")
                    except Exception:
                        pass
        icon = {"passed": "OK", "inconclusive": "~~", "failed": "XX"}.get(row.smoke_status, "??")
        print(f"[{icon}] {row.slug}: {row.smoke_status}")
        for r in reasons:
            print(r)


async def main():
    block = sys.argv[1] if len(sys.argv) > 1 else "B"
    if block.upper() == "B":
        await reverify(BLOCK_B, "BLOCK B: unsupported_operation_space")
    elif block.upper() == "C":
        await reverify(BLOCK_C, "BLOCK C: invalid_test_input")
    elif block.upper() == "BC":
        await reverify(BLOCK_B, "BLOCK B: unsupported_operation_space")
        await reverify(BLOCK_C, "BLOCK C: invalid_test_input")
    else:
        print(f"Usage: {sys.argv[0]} [B|C|BC]")


if __name__ == "__main__":
    asyncio.run(main())
