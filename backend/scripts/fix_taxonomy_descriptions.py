"""
Fill missing descriptions in capability_taxonomy based on display_name + category context.

Idempotent — only touches rows where description IS NULL.

Usage:
    python -m scripts.fix_taxonomy_descriptions
"""

import asyncio
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/agentnode",
)


def generate_description(cap_id: str, display_name: str, category: str | None) -> str:
    """Generate a short one-line description from display_name and category."""
    dn = display_name.strip()
    cat = (category or "general").replace("-", " ")

    # Common pattern: "Tools for {display_name} in {category} workflows"
    # But we try to be more specific based on category
    category_verbs = {
        "developer-tools": f"Developer tooling for {dn.lower()}",
        "data-analysis": f"Analyze and process data using {dn.lower()}",
        "memory-and-retrieval": f"Store and retrieve information via {dn.lower()}",
        "communication": f"Enable {dn.lower()} for agent communication",
        "web": f"Web-based {dn.lower()} capabilities",
        "file-management": f"Manage files with {dn.lower()}",
        "ai-and-ml": f"AI/ML capabilities for {dn.lower()}",
        "automation": f"Automate workflows with {dn.lower()}",
        "media": f"Process and handle {dn.lower()} media",
        "security": f"Security capabilities for {dn.lower()}",
        "integration": f"Integrate with external systems via {dn.lower()}",
    }

    if category in category_verbs:
        return category_verbs[category]

    return f"Provides {dn.lower()} capabilities for AI agents"


async def main() -> None:
    engine = create_async_engine(DATABASE_URL)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        async with session.begin():
            # Find all entries with NULL description
            result = await session.execute(
                text(
                    "SELECT id, display_name, category FROM capability_taxonomy "
                    "WHERE description IS NULL ORDER BY id"
                )
            )
            rows = result.all()

            if not rows:
                print("All capabilities already have descriptions. Nothing to do.")
                await engine.dispose()
                return

            print(f"Found {len(rows)} capabilities without descriptions:\n")

            for row in rows:
                cap_id, display_name, category = row
                desc = generate_description(cap_id, display_name, category)
                await session.execute(
                    text(
                        "UPDATE capability_taxonomy SET description = :desc "
                        "WHERE id = :cap_id AND description IS NULL"
                    ),
                    {"desc": desc, "cap_id": cap_id},
                )
                print(f"  {cap_id}: {desc}")

        # Verify
        async with session.begin():
            result = await session.execute(
                text("SELECT COUNT(*) FROM capability_taxonomy WHERE description IS NULL")
            )
            null_count = result.scalar()
            print(f"\nRemaining NULL descriptions: {null_count}")

    await engine.dispose()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
