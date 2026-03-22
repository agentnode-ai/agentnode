"""Re-verify only the 16 packages with non-passed smoke status.

Runs each through the updated Phase 1 pipeline and prints results.
Run from /opt/agentnode/backend with venv activated:
    python scripts/reverify_problem16.py
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


async def main():
    from app.packages.models import Package, PackageVersion
    from app.verification.models import VerificationResult
    from app.verification.pipeline import run_verification

    async with async_session_factory() as session:
        # Step 1: Find packages with non-passed smoke
        result = await session.execute(text("""
            SELECT p.slug, pv.id as version_id, pv.artifact_object_key,
                   vr.smoke_status, vr.smoke_log
            FROM packages p
            JOIN package_versions pv ON p.latest_version_id = pv.id
            LEFT JOIN verification_results vr ON pv.latest_verification_result_id = vr.id
            WHERE vr.smoke_status IS NOT NULL AND vr.smoke_status != 'passed'
            ORDER BY p.slug
        """))
        problem_rows = result.fetchall()

        if not problem_rows:
            # Also check for NULL smoke (never ran smoke)
            result2 = await session.execute(text("""
                SELECT p.slug, pv.id as version_id, pv.artifact_object_key,
                       vr.smoke_status, vr.smoke_log
                FROM packages p
                JOIN package_versions pv ON p.latest_version_id = pv.id
                LEFT JOIN verification_results vr ON pv.latest_verification_result_id = vr.id
                WHERE vr.smoke_status IS NULL OR vr.smoke_status != 'passed'
                ORDER BY p.slug
            """))
            problem_rows = result2.fetchall()

        logger.info(f"Found {len(problem_rows)} packages with non-passed smoke")
        for row in problem_rows:
            logger.info(f"  {row.slug}: smoke={row.smoke_status}")

        print("\n" + "=" * 70)
        print("RE-VERIFYING PROBLEM PACKAGES")
        print("=" * 70 + "\n")

        # Step 2: Re-verify each
        for i, row in enumerate(problem_rows, 1):
            slug = row.slug
            version_id = row.version_id
            old_smoke = row.smoke_status

            if not row.artifact_object_key:
                print(f"[{i}/{len(problem_rows)}] SKIP {slug} — no artifact")
                continue

            print(f"[{i}/{len(problem_rows)}] Re-verifying {slug} (was: {old_smoke})...")
            try:
                await run_verification(version_id, triggered_by="admin_reverify")
                print(f"  DONE {slug}")
            except Exception as e:
                print(f"  ERROR {slug}: {e}")

            await asyncio.sleep(2)

    # Step 3: Print results report
    print("\n" + "=" * 70)
    print("RESULTS REPORT")
    print("=" * 70 + "\n")

    async with async_session_factory() as session:
        result = await session.execute(text("""
            SELECT p.slug, vr.smoke_status, vr.smoke_log,
                   vr.install_status, vr.import_status
            FROM packages p
            JOIN package_versions pv ON p.latest_version_id = pv.id
            JOIN verification_results vr ON pv.latest_verification_result_id = vr.id
            WHERE p.slug IN (""" + ",".join(f"'{r.slug}'" for r in problem_rows) + """)
            ORDER BY p.slug
        """))
        new_rows = result.fetchall()

        passed = 0
        inconclusive = 0
        failed = 0

        for row in new_rows:
            status_icon = {"passed": "OK", "inconclusive": "~~", "failed": "XX"}.get(row.smoke_status, "??")
            print(f"[{status_icon}] {row.slug}: smoke={row.smoke_status}")

            # Extract reason from smoke_log
            if row.smoke_log:
                for line in row.smoke_log.splitlines():
                    if line.startswith("[PASS]") or line.startswith("[FAIL]") or line.startswith("[INCONCLUSIVE]"):
                        print(f"     {line}")
                    elif line.startswith("{"):
                        # JSON log line — show reason
                        try:
                            import json
                            entry = json.loads(line)
                            reason = entry.get("reason", "")
                            verdict = entry.get("verdict", "")
                            error_type = entry.get("error_type", "")
                            msg = entry.get("message", "")
                            if msg and len(msg) > 100:
                                msg = msg[:100] + "..."
                            print(f"     candidate {entry.get('candidate_index')}: {reason} ({verdict}) — {error_type}: {msg}")
                        except Exception:
                            pass

            if row.smoke_status == "passed":
                passed += 1
            elif row.smoke_status == "inconclusive":
                inconclusive += 1
            elif row.smoke_status == "failed":
                failed += 1

        print(f"\nSUMMARY: {passed} passed, {inconclusive} inconclusive, {failed} failed")
        print(f"(out of {len(new_rows)} re-verified packages)")


if __name__ == "__main__":
    asyncio.run(main())
