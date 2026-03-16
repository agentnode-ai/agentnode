"""Typosquatting detection for package slugs."""
from difflib import SequenceMatcher


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
