"""Typosquatting detection for package slugs."""
from difflib import SequenceMatcher


def check_typosquatting(new_slug: str, existing_slugs: list[str]) -> list[str]:
    """Return list of existing slugs that are suspiciously similar to new_slug."""
    suspicious = []
    norm_new = new_slug.replace("-", "").replace("_", "")

    for existing in existing_slugs:
        if new_slug == existing:
            continue
        # Character-level similarity
        if SequenceMatcher(None, new_slug, existing).ratio() > 0.85:
            suspicious.append(existing)
        # Normalized match (ignore hyphens/underscores)
        if norm_new == existing.replace("-", "").replace("_", ""):
            suspicious.append(existing)

    return list(set(suspicious))
