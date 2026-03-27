from langchain.tools import tool


@tool
def summarize(text: str) -> dict:
    """Summarize a long text into key points."""
    sentences = text.split(".")
    summary = ". ".join(sentences[:3])
    return {"summary": summary, "sentence_count": len(sentences)}


@tool
def extract_keywords(text: str, top_n: int = 5) -> dict:
    """Extract the most frequent keywords from text."""
    words = text.lower().split()
    freq = {}
    for w in words:
        freq[w] = freq.get(w, 0) + 1
    sorted_words = sorted(freq.items(), key=lambda x: x[1], reverse=True)
    return {"keywords": [w for w, _ in sorted_words[:top_n]]}
