"""Generate a full verification status report for all packages."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import async_session_factory
from sqlalchemy import text

import app.auth.models, app.publishers.models, app.packages.models, app.verification.models
import app.webhooks.models, app.shared.models, app.admin.models, app.blog.models, app.sitemap.models


async def report():
    async with async_session_factory() as session:
        result = await session.execute(text(
            "SELECT p.slug, "
            "vr.install_status, vr.import_status, vr.smoke_status, vr.tests_status, "
            "vr.smoke_log, vr.status as overall "
            "FROM packages p "
            "JOIN package_versions pv ON p.latest_version_id = pv.id "
            "LEFT JOIN verification_results vr ON pv.latest_verification_result_id = vr.id "
            "ORDER BY vr.smoke_status, p.slug"
        ))
        rows = result.fetchall()

        stats = {"install": {}, "import": {}, "smoke": {}, "tests": {}, "overall": {}}
        inconclusive_details = []

        for row in rows:
            for step in ["install", "import", "smoke", "tests", "overall"]:
                val = getattr(row, step + "_status" if step != "overall" else "overall")
                stats[step][val] = stats[step].get(val, 0) + 1

            if row.smoke_status == "inconclusive" and row.smoke_log:
                for line in row.smoke_log.splitlines():
                    if "[INCONCLUSIVE]" in line:
                        if "api_key" in line.lower() or "token is required" in line.lower():
                            reason = "needs_credentials"
                        elif "Unknown operation" in line or "Unsupported" in line:
                            reason = "invalid_test_input"
                        elif "timed out" in line.lower():
                            reason = "network_timeout"
                        elif "type mismatch" in line.lower() or "auto-schema" in line.lower():
                            reason = "type_mismatch"
                        else:
                            reason = "other"
                        inconclusive_details.append((row.slug, reason, line.strip()))

        print("=== VERIFICATION STATUS ALLER 78 PACKAGES ===")
        print()
        for step in ["install", "import", "smoke", "tests", "overall"]:
            counts = sorted(stats[step].items(), key=lambda x: -x[1])
            line = ", ".join(f"{s}={c}" for s, c in counts)
            print(f"  {step.upper()}: {line}")

        print()
        print("=== INCONCLUSIVE SMOKE: GRUENDE ===")
        reason_counts = {}
        for slug, reason, detail in inconclusive_details:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

        for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
            print(f"  {reason}: {count} packages")

        print()
        print("=== INCONCLUSIVE DETAILS ===")
        for slug, reason, detail in sorted(inconclusive_details):
            print(f"  {slug}: [{reason}] {detail[:120]}")

        print()
        print("=== PACKAGES MIT NICHT-PASSED SMOKE ===")
        for row in rows:
            if row.smoke_status and row.smoke_status not in ("passed", "skipped"):
                print(f"  {row.slug}: smoke={row.smoke_status}, tests={row.tests_status}")


if __name__ == "__main__":
    asyncio.run(report())
