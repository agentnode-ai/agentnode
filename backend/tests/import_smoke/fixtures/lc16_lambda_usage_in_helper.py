from langchain.tools import tool
import re


STOP_WORDS = {"the", "a", "an", "is", "are", "was", "in", "on", "at", "to", "of", "for"}


def _extract_keywords(text: str, top_n: int = 10) -> list:
    """Extract top keywords by frequency, excluding stop words."""
    words = re.findall(r"\w+", text.lower())
    filtered = [w for w in words if w not in STOP_WORDS and len(w) > 2]
    freq = {}
    for w in filtered:
        freq[w] = freq.get(w, 0) + 1
    ranked = sorted(freq.items(), key=lambda x: x[1], reverse=True)
    return [word for word, _ in ranked[:top_n]]


@tool
def keyword_extraction(text: str, top_n: int = 10) -> dict:
    """Extract the most important keywords from text."""
    keywords = _extract_keywords(text, top_n)
    return {"keywords": keywords, "count": len(keywords)}
