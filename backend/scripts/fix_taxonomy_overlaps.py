"""
Merge overlapping taxonomy categories and remove the text_translation duplicate.

Idempotent — safe to run multiple times.

Usage:
    python -m scripts.fix_taxonomy_overlaps
"""

import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/agentnode",
)


async def main() -> None:
    engine = create_async_engine(DATABASE_URL)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Category merges: old -> new
    merges = [
        ("development", "developer-tools"),
        ("knowledge", "memory-and-retrieval"),
        ("data-processing", "data-analysis"),
    ]

    async with async_session() as session:
        async with session.begin():
            # 1) Merge overlapping categories
            for old_cat, new_cat in merges:
                result = await session.execute(
                    text(
                        "UPDATE capability_taxonomy SET category = :new_cat "
                        "WHERE category = :old_cat"
                    ),
                    {"old_cat": old_cat, "new_cat": new_cat},
                )
                print(f"  Merged '{old_cat}' -> '{new_cat}': {result.rowcount} rows")

            # 2) Remove text_translation duplicate
            # First, re-point any capabilities FK references from text_translation -> translation
            result = await session.execute(
                text(
                    "UPDATE capabilities SET capability_id = 'translation' "
                    "WHERE capability_id = 'text_translation'"
                )
            )
            print(f"  Re-pointed capabilities text_translation -> translation: {result.rowcount} rows")

            # Then delete the taxonomy entry
            result = await session.execute(
                text(
                    "DELETE FROM capability_taxonomy WHERE id = 'text_translation'"
                )
            )
            print(f"  Deleted taxonomy entry 'text_translation': {result.rowcount} rows")

        # Verify
        result = await session.execute(
            text("SELECT DISTINCT category FROM capability_taxonomy ORDER BY category")
        )
        categories = [row[0] for row in result.all()]
        print(f"\nRemaining categories ({len(categories)}): {', '.join(categories)}")

        result = await session.execute(
            text("SELECT id FROM capability_taxonomy WHERE id IN ('text_translation', 'translation')")
        )
        remaining = [row[0] for row in result.all()]
        print(f"Translation entries: {remaining}")

    await engine.dispose()
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
