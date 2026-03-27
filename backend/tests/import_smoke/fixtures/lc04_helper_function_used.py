from langchain.tools import tool


def _normalize(text: str) -> str:
    """Lowercase and strip whitespace."""
    return text.lower().strip()


def _tokenize(text: str) -> list:
    """Split text into tokens."""
    return _normalize(text).split()


@tool
def token_frequency(text: str) -> dict:
    """Calculate token frequency for the given text."""
    tokens = _tokenize(text)
    freq = {}
    for t in tokens:
        freq[t] = freq.get(t, 0) + 1
    sorted_freq = sorted(freq.items(), key=lambda x: x[1], reverse=True)
    return {"frequencies": dict(sorted_freq[:20]), "total_tokens": len(tokens)}
