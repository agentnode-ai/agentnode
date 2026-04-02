"""Typosquatting detection for package slugs.

Uses PostgreSQL pg_trgm for efficient fuzzy matching instead of loading
all slugs into memory.  The pure-Python helpers (check_typosquatting) are
kept for unit tests that don't need a database.
"""
from difflib import SequenceMatcher

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# Common character substitutions used in typosquatting attacks
HOMOGRAPH_MAP = str.maketrans({
    "l": "1", "1": "l",
    "o": "0", "0": "o",
    "i": "1",
    "s": "5", "5": "s",
    "e": "3", "3": "e",
    "a": "4", "4": "a",
    "t": "7", "7": "t",
    "b": "6", "6": "b",
    "g": "9", "9": "g",
})


def _normalize(slug: str) -> str:
    """Normalize a slug for comparison — strip separators."""
    return slug.replace("-", "").replace("_", "")


def _homograph_normalize(slug: str) -> str:
    """Normalize a slug with homograph substitutions."""
    normalized = _normalize(slug.lower())
    # Replace all lookalike chars with their canonical form
    result = []
    for ch in normalized:
        if ch in "10":
            result.append("l")  # Normalize 1/l/i → l
        elif ch == "i":
            result.append("l")
        elif ch in "0o":
            result.append("o")  # Normalize 0/o → o
        elif ch in "5s":
            result.append("s")
        elif ch in "3e":
            result.append("e")
        else:
            result.append(ch)
    return "".join(result)


def check_typosquatting(new_slug: str, existing_slugs: list[str]) -> list[str]:
    """Return list of existing slugs that are suspiciously similar to new_slug."""
    suspicious = []
    norm_new = _normalize(new_slug)
    homo_new = _homograph_normalize(new_slug)

    for existing in existing_slugs:
        if new_slug == existing:
            continue

        # 1. Character-level similarity (SequenceMatcher)
        if SequenceMatcher(None, new_slug, existing).ratio() > 0.85:
            suspicious.append(existing)
            continue

        norm_existing = _normalize(existing)

        # 2. Normalized match (ignore hyphens/underscores)
        if norm_new == norm_existing:
            suspicious.append(existing)
            continue

        # 3. Homograph detection (l/1, o/0, s/5, etc.)
        homo_existing = _homograph_normalize(existing)
        if homo_new == homo_existing and new_slug != existing:
            suspicious.append(existing)
            continue

        # 4. Single character insertion/deletion (Levenshtein distance = 1)
        if abs(len(norm_new) - len(norm_existing)) <= 1:
            # Simple Levenshtein-1 check
            if len(norm_new) == len(norm_existing):
                # Same length: check for single character swap
                diffs = sum(1 for a, b in zip(norm_new, norm_existing) if a != b)
                if diffs == 1:
                    suspicious.append(existing)
                    continue
            elif len(norm_new) > len(norm_existing):
                # One char inserted
                for i in range(len(norm_new)):
                    if norm_new[:i] + norm_new[i + 1:] == norm_existing:
                        suspicious.append(existing)
                        break
            else:
                # One char deleted
                for i in range(len(norm_existing)):
                    if norm_existing[:i] + norm_existing[i + 1:] == norm_new:
                        suspicious.append(existing)
                        break

    return list(set(suspicious))


async def find_similar_slugs_db(
    new_slug: str,
    session: AsyncSession,
    similarity_threshold: float = 0.3,
    limit: int = 20,
) -> list[str]:
    """Find similar slugs using PostgreSQL pg_trgm, then refine with Python checks.

    Two-phase approach:
    1. pg_trgm similarity search + normalized equality check in SQL (fast, indexed)
    2. Python-side homograph/Levenshtein refinement on the small candidate set

    Returns the final list of suspiciously similar existing slugs.
    """
    # Phase 1: SQL — get candidates via trigram similarity OR separator-normalized match
    # The REPLACE-based normalization catches hyphen/underscore equivalence in SQL.
    result = await session.execute(
        text("""
            SELECT slug FROM packages
            WHERE slug != :new_slug
              AND (
                  similarity(slug, :new_slug) > :threshold
                  OR REPLACE(REPLACE(slug, '-', ''), '_', '')
                     = REPLACE(REPLACE(:new_slug, '-', ''), '_', '')
              )
            ORDER BY similarity(slug, :new_slug) DESC
            LIMIT :lim
        """),
        {
            "new_slug": new_slug,
            "threshold": similarity_threshold,
            "lim": limit,
        },
    )
    candidates = [row[0] for row in result.all()]

    if not candidates:
        return []

    # Phase 2: Python — run full check_typosquatting on the small candidate set
    return check_typosquatting(new_slug, candidates)
