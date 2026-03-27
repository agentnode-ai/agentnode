#!/usr/bin/env python3
"""Generate pipeline statistics."""
import asyncio
import sys

sys.path.insert(0, "/opt/agentnode/backend")

from app.database import async_session_factory
from sqlalchemy import text


async def stats():
    async with async_session_factory() as s:
        r = await s.execute(text("SELECT COUNT(*) FROM import_candidates"))
        total = r.scalar()

        r = await s.execute(text("SELECT source, COUNT(*) FROM import_candidates GROUP BY source ORDER BY COUNT(*) DESC"))
        sources = r.fetchall()

        r = await s.execute(text("SELECT detected_format, COUNT(*) FROM import_candidates GROUP BY detected_format ORDER BY COUNT(*) DESC"))
        formats = r.fetchall()

        r = await s.execute(text("SELECT outreach_status, COUNT(*) FROM import_candidates GROUP BY outreach_status ORDER BY COUNT(*) DESC"))
        statuses = r.fetchall()

        r = await s.execute(text("SELECT COUNT(*) FROM import_candidates WHERE contact_email IS NOT NULL AND contact_email != ''"))
        with_email = r.scalar()

        r = await s.execute(text("SELECT COUNT(*) FROM import_candidates WHERE admin_notes LIKE :p"), {"p": "%Twitter: @%"})
        with_twitter = r.scalar()

        r = await s.execute(text("SELECT COUNT(*) FROM import_candidates WHERE admin_notes LIKE :p"), {"p": "%Discord:%"})
        with_discord = r.scalar()

        r = await s.execute(text("SELECT COUNT(*) FROM import_candidates WHERE admin_notes LIKE :p"), {"p": "%README email:%"})
        with_readme_email = r.scalar()

        r = await s.execute(text("SELECT COUNT(*) FROM import_candidates WHERE admin_notes LIKE :p"), {"p": "%Website:%"})
        with_website = r.scalar()

        r = await s.execute(text("SELECT COUNT(*) FROM import_candidates WHERE (contact_email IS NOT NULL AND contact_email != '') OR admin_notes LIKE :p"), {"p": "%Twitter: @%"})
        reachable = r.scalar()

        r = await s.execute(text("SELECT COUNT(*) FROM import_candidates WHERE stars >= 10000"))
        stars_10k = r.scalar()
        r = await s.execute(text("SELECT COUNT(*) FROM import_candidates WHERE stars >= 1000 AND stars < 10000"))
        stars_1k = r.scalar()
        r = await s.execute(text("SELECT COUNT(*) FROM import_candidates WHERE stars >= 100 AND stars < 1000"))
        stars_100 = r.scalar()
        r = await s.execute(text("SELECT COUNT(*) FROM import_candidates WHERE stars >= 10 AND stars < 100"))
        stars_10 = r.scalar()
        r = await s.execute(text("SELECT COUNT(*) FROM import_candidates WHERE (stars < 10 OR stars IS NULL)"))
        stars_low = r.scalar()

        r = await s.execute(text("SELECT license_spdx, COUNT(*) FROM import_candidates WHERE license_spdx IS NOT NULL GROUP BY license_spdx ORDER BY COUNT(*) DESC LIMIT 10"))
        licenses = r.fetchall()

        r = await s.execute(text("SELECT COUNT(*) FROM import_candidates WHERE outreach_status = 'skipped'"))
        skipped = r.scalar()
        r = await s.execute(text("SELECT COUNT(*) FROM import_candidates WHERE skip_reason IS NOT NULL"))
        with_skip_reason = r.scalar()

        r = await s.execute(text("SELECT display_name, stars, contact_email, source FROM import_candidates WHERE contact_email IS NOT NULL AND contact_email != '' AND outreach_status = 'discovered' ORDER BY stars DESC NULLS LAST LIMIT 10"))
        top_email = r.fetchall()

        r = await s.execute(text("SELECT display_name, stars, admin_notes, source FROM import_candidates WHERE (contact_email IS NULL OR contact_email = '') AND admin_notes LIKE :p AND outreach_status = 'discovered' ORDER BY stars DESC NULLS LAST LIMIT 10"), {"p": "%Twitter: @%"})
        top_twitter = r.fetchall()

        r = await s.execute(text("SELECT COUNT(*) FROM invite_codes"))
        total_invites = r.scalar()
        r = await s.execute(text("SELECT COUNT(*) FROM candidate_events"))
        total_events = r.scalar()

        r = await s.execute(text("SELECT AVG(stars), PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY stars) FROM import_candidates WHERE stars IS NOT NULL AND stars >= 0"))
        avg_row = r.first()
        avg_stars = avg_row[0] or 0
        median_stars = avg_row[1] or 0

        # Eligible for outreach (discovered + email)
        r = await s.execute(text("SELECT COUNT(*) FROM import_candidates WHERE outreach_status = 'discovered' AND contact_email IS NOT NULL AND contact_email != ''"))
        eligible_email = r.scalar()

        # By stars tiers with email
        r = await s.execute(text("SELECT COUNT(*) FROM import_candidates WHERE outreach_status = 'discovered' AND contact_email IS NOT NULL AND contact_email != '' AND stars >= 10000"))
        elig_10k = r.scalar()
        r = await s.execute(text("SELECT COUNT(*) FROM import_candidates WHERE outreach_status = 'discovered' AND contact_email IS NOT NULL AND contact_email != '' AND stars >= 1000 AND stars < 10000"))
        elig_1k = r.scalar()
        r = await s.execute(text("SELECT COUNT(*) FROM import_candidates WHERE outreach_status = 'discovered' AND contact_email IS NOT NULL AND contact_email != '' AND stars >= 100 AND stars < 1000"))
        elig_100 = r.scalar()

        print("=" * 65)
        print(f"  CREATOR ACQUISITION PIPELINE — STATISTICS")
        print("=" * 65)

        print(f"\n  Total Candidates:  {total}")
        print(f"  Total Invites:     {total_invites}")
        print(f"  Total Events:      {total_events}")

        print(f"\n{'─' * 65}")
        print(f"  BY SOURCE")
        print(f"{'─' * 65}")
        for src, cnt in sources:
            pct = cnt / total * 100
            bar = "█" * int(pct / 2)
            print(f"  {src or '(none)':<30} {cnt:>5}  ({pct:4.1f}%)  {bar}")

        print(f"\n{'─' * 65}")
        print(f"  BY FORMAT")
        print(f"{'─' * 65}")
        for fmt, cnt in formats:
            pct = cnt / total * 100
            bar = "█" * int(pct / 2)
            print(f"  {fmt or '(none)':<30} {cnt:>5}  ({pct:4.1f}%)  {bar}")

        print(f"\n{'─' * 65}")
        print(f"  BY OUTREACH STATUS")
        print(f"{'─' * 65}")
        for st, cnt in statuses:
            pct = cnt / total * 100
            print(f"  {st:<30} {cnt:>5}  ({pct:4.1f}%)")

        print(f"\n{'─' * 65}")
        print(f"  CONTACT COVERAGE")
        print(f"{'─' * 65}")
        print(f"  Email (GitHub profile)         {with_email:>5}  ({with_email/total*100:.1f}%)")
        print(f"  Email (from README)            {with_readme_email:>5}")
        print(f"  Twitter/X                      {with_twitter:>5}  ({with_twitter/total*100:.1f}%)")
        print(f"  Discord                        {with_discord:>5}  ({with_discord/total*100:.1f}%)")
        print(f"  Website/Blog                   {with_website:>5}")
        print(f"  ─────────────────────────────────────")
        print(f"  Reachable (email OR twitter)   {reachable:>5}  ({reachable/total*100:.1f}%)")

        print(f"\n{'─' * 65}")
        print(f"  STARS DISTRIBUTION")
        print(f"{'─' * 65}")
        print(f"  10,000+                        {stars_10k:>5}")
        print(f"   1,000 — 9,999                 {stars_1k:>5}")
        print(f"     100 — 999                   {stars_100:>5}")
        print(f"      10 — 99                    {stars_10:>5}")
        print(f"       < 10 / unknown            {stars_low:>5}")
        print(f"  Average: {avg_stars:,.0f}  |  Median: {median_stars:,.0f}")

        print(f"\n{'─' * 65}")
        print(f"  OUTREACH-READY (discovered + has email)")
        print(f"{'─' * 65}")
        print(f"  Total eligible                 {eligible_email:>5}")
        print(f"    10,000+ stars                {elig_10k:>5}")
        print(f"     1,000+ stars                {elig_1k:>5}")
        print(f"       100+ stars                {elig_100:>5}")

        print(f"\n{'─' * 65}")
        print(f"  TOP LICENSES")
        print(f"{'─' * 65}")
        for lic, cnt in licenses:
            print(f"  {lic:<30} {cnt:>5}")

        print(f"\n{'─' * 65}")
        print(f"  SKIPPED")
        print(f"{'─' * 65}")
        print(f"  Skipped (status)               {skipped:>5}")
        print(f"  With skip reason               {with_skip_reason:>5}")

        print(f"\n{'─' * 65}")
        print(f"  TOP 10 — Ready to Contact (by Stars, with Email)")
        print(f"{'─' * 65}")
        for name, stars, email, src in top_email:
            print(f"  {(name or '?')[:32]:<33} {stars:>7}★  {email[:30]:<31} [{src}]")

        print(f"\n{'─' * 65}")
        print(f"  TOP 10 — Twitter Only (no Email)")
        print(f"{'─' * 65}")
        for name, stars, notes, src in top_twitter:
            tw = ""
            if notes:
                for line in notes.split("\n"):
                    if line.startswith("Twitter: @"):
                        tw = line.replace("Twitter: ", "")
                        break
            print(f"  {(name or '?')[:32]:<33} {stars:>7}★  {tw[:25]:<26} [{src}]")

        print(f"\n{'=' * 65}")


asyncio.run(stats())
