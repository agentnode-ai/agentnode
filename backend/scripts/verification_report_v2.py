"""Report verification distribution after reverification."""
import asyncio
from sqlalchemy import text
from app.config import settings
from sqlalchemy.ext.asyncio import create_async_engine


async def report():
    engine = create_async_engine(settings.DATABASE_URL)
    async with engine.begin() as conn:
        r = await conn.execute(
            text(
                "SELECT DISTINCT ON (vr.package_version_id)"
                " vr.verification_tier, vr.verification_score, vr.confidence,"
                " vr.verification_mode, vr.smoke_reason, p.slug,"
                " vr.tests_status, vr.tests_auto_generated"
                " FROM verification_results vr"
                " JOIN package_versions pv ON vr.package_version_id = pv.id"
                " JOIN packages p ON pv.package_id = p.id"
                " ORDER BY vr.package_version_id, vr.created_at DESC"
            )
        )
        rows = r.fetchall()

        tiers = {}
        for row in rows:
            t = row[0] or "null"
            tiers[t] = tiers.get(t, 0) + 1
        print("=== TIER DISTRIBUTION ===")
        for t in ["gold", "verified", "partial", "unverified"]:
            if t in tiers:
                print(f"  {t}: {tiers[t]}")

        confs = {}
        for row in rows:
            c = row[2] or "null"
            confs[c] = confs.get(c, 0) + 1
        print("\n=== CONFIDENCE ===")
        for c in ["high", "medium", "low"]:
            if c in confs:
                print(f"  {c}: {confs[c]}")

        tests = {}
        for row in rows:
            key = str(row[6]) + (" (auto)" if row[7] else "")
            tests[key] = tests.get(key, 0) + 1
        print("\n=== TESTS STATUS ===")
        for k, v in sorted(tests.items(), key=lambda x: -x[1]):
            print(f"  {k}: {v}")

        print("\n=== GOLD PACKAGES ===")
        for row in sorted(rows, key=lambda r: r[1] or 0, reverse=True):
            if row[0] == "gold":
                print(f"  {row[1]}  {row[5]}  tests={row[6]}")

        print("\n=== VERIFIED (sample) ===")
        verified = [r for r in rows if r[0] == "verified"]
        for row in sorted(verified, key=lambda r: r[1] or 0, reverse=True)[:5]:
            reason = row[4] or "-"
            print(f"  {row[1]}  {row[5]}  tests={row[6]} auto={row[7]}")

        print("\n=== PARTIAL PACKAGES ===")
        for row in sorted(rows, key=lambda r: r[1] or 0, reverse=True):
            if row[0] == "partial":
                reason = row[4] or "-"
                print(f"  {row[1]}  {row[5]:40s}  {reason}")

        print("\n=== UNVERIFIED ===")
        for row in sorted(rows, key=lambda r: r[1] or 0, reverse=True):
            if row[0] == "unverified":
                reason = row[4] or "-"
                print(f"  {row[1]}  {row[5]:40s}  {reason}  tests={row[6]}")
    await engine.dispose()


asyncio.run(report())
