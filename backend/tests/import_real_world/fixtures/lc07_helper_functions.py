from langchain.tools import tool
import re

STOP_WORDS = {"the", "a", "an", "is", "are", "was", "were", "in", "on", "at", "to", "for"}


def _clean_text(text: str) -> str:
    """Remove punctuation and normalize whitespace."""
    text = re.sub(r"[^\w\s]", "", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def _remove_stop_words(words: list) -> list:
    return [w for w in words if w not in STOP_WORDS]


@tool
def analyze_text(text: str) -> dict:
    """Analyze text: clean, remove stop words, and count frequency."""
    cleaned = _clean_text(text)
    words = _remove_stop_words(cleaned.split())
    freq = {}
    for w in words:
        freq[w] = freq.get(w, 0) + 1
    top = sorted(freq.items(), key=lambda x: x[1], reverse=True)[:10]
    return {"word_count": len(words), "top_words": top}
