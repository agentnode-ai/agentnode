"""Fix incorrect SDK import patterns in all published blog articles.

Replaces:
  - 'from agentnode import' → 'from agentnode_sdk import'
  - 'import agentnode' (not followed by _) → 'import agentnode_sdk'
  - 'pip install agentnode' (not followed by -) → 'pip install agentnode-sdk'
"""
import asyncio
import re
import sys

sys.path.insert(0, "/opt/agentnode/backend")

from app.database import async_session_factory


REPLACEMENTS = [
    # order matters: most specific first
    (r"from agentnode import", "from agentnode_sdk import"),
    (r"import agentnode(?![_\-])", "import agentnode_sdk"),
    (r"pip install agentnode(?![-_])", "pip install agentnode-sdk"),
]


async def main():
    async with async_session_factory() as session:
        # Fetch all posts with content_html containing old patterns
        result = await session.execute(
            __import__("sqlalchemy").text(
                "SELECT id, slug, content_html FROM blog_posts "
                "WHERE content_html IS NOT NULL"
            )
        )
        rows = result.fetchall()
        print(f"Checking {len(rows)} articles...")

        fixed_count = 0
        for row in rows:
            post_id, slug, html = row
            if not html:
                continue

            new_html = html
            for pattern, replacement in REPLACEMENTS:
                new_html = re.sub(pattern, replacement, new_html)

            if new_html != html:
                await session.execute(
                    __import__("sqlalchemy").text(
                        "UPDATE blog_posts SET content_html = :html WHERE id = :id"
                    ),
                    {"html": new_html, "id": post_id},
                )
                fixed_count += 1
                print(f"  Fixed: {slug}")

        await session.commit()
        print(f"\nDone. Fixed {fixed_count} articles.")


if __name__ == "__main__":
    asyncio.run(main())
