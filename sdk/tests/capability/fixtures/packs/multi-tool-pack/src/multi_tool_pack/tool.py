"""Word statistics."""

from __future__ import annotations

from .slug import normalise_space


def word_stats(text: str) -> dict:
    clean = normalise_space(text)
    return {
        "words": len(clean.split()) if clean else 0,
        "characters": len(text),
        "lines": text.count("\n") + (1 if text else 0),
    }
