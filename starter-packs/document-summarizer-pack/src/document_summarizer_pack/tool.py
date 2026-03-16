"""Extractive text summarization using word frequency scoring."""

from __future__ import annotations

import re
import string
from collections import Counter


# Common English stop words (kept minimal to avoid large lists)
_STOP_WORDS: set[str] = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "it", "was", "are", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can", "need",
    "that", "this", "these", "those", "i", "me", "my", "we", "our", "you",
    "your", "he", "him", "his", "she", "her", "they", "them", "their",
    "what", "which", "who", "whom", "when", "where", "why", "how", "not",
    "no", "nor", "so", "if", "then", "than", "too", "very", "just", "about",
    "above", "after", "again", "all", "also", "am", "any", "as", "because",
    "before", "between", "both", "each", "few", "get", "got", "here",
    "into", "its", "let", "like", "make", "many", "more", "most", "much",
    "must", "now", "only", "other", "over", "own", "same", "some", "still",
    "such", "take", "tell", "there", "through", "under", "until", "up",
    "us", "use", "well", "while",
}


def _tokenize(text: str) -> list[str]:
    """Lowercase and split text into word tokens, stripping punctuation."""
    text = text.lower()
    words = re.findall(r"[a-z0-9]+(?:'[a-z]+)?", text)
    return [w for w in words if w not in _STOP_WORDS and len(w) > 1]


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences using a simple regex heuristic."""
    # Split on sentence-ending punctuation followed by whitespace or end
    raw = re.split(r"(?<=[.!?])\s+", text.strip())
    sentences: list[str] = []
    for s in raw:
        s = s.strip()
        if s:
            sentences.append(s)
    return sentences


def run(
    text: str,
    max_sentences: int = 5,
    method: str = "extractive",
) -> dict:
    """Summarize text using extractive summarization.

    Scores each sentence by the sum of its word frequencies, then
    returns the top N sentences in their original order.

    Args:
        text: The input text to summarize.
        max_sentences: Maximum number of sentences in the summary.
        method: Summarization method (currently only "extractive").

    Returns:
        Dictionary with summary, original_length, summary_length,
        and compression_ratio.
    """
    if not text or not text.strip():
        return {
            "summary": "",
            "original_length": 0,
            "summary_length": 0,
            "compression_ratio": 0.0,
        }

    method = method.strip().lower()
    if method != "extractive":
        raise ValueError(
            f"Unsupported method '{method}'. Only 'extractive' is available."
        )

    sentences = _split_sentences(text)

    if len(sentences) <= max_sentences:
        summary = " ".join(sentences)
        return {
            "summary": summary,
            "original_length": len(text),
            "summary_length": len(summary),
            "compression_ratio": round(len(summary) / max(len(text), 1), 4),
        }

    # Build word frequency table
    all_tokens = _tokenize(text)
    freq = Counter(all_tokens)

    # Normalise frequencies by max frequency
    max_freq = max(freq.values()) if freq else 1
    for word in freq:
        freq[word] = freq[word] / max_freq

    # Score each sentence
    scored: list[tuple[int, float, str]] = []
    for idx, sentence in enumerate(sentences):
        tokens = _tokenize(sentence)
        score = sum(freq.get(t, 0) for t in tokens)
        scored.append((idx, score, sentence))

    # Pick top sentences by score
    scored.sort(key=lambda x: x[1], reverse=True)
    top = scored[:max_sentences]

    # Restore original order
    top.sort(key=lambda x: x[0])

    summary = " ".join(item[2] for item in top)
    original_length = len(text)
    summary_length = len(summary)

    return {
        "summary": summary,
        "original_length": original_length,
        "summary_length": summary_length,
        "compression_ratio": round(summary_length / max(original_length, 1), 4),
    }
