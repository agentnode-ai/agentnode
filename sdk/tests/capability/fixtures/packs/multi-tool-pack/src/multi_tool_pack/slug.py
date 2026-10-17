"""A stable ASCII slug."""

from __future__ import annotations

import re
import unicodedata


def normalise_space(text: str) -> str:
    return " ".join(text.split())


def slugify(text: str) -> dict:
    ascii_text = (
        unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return {"slug": slug}
