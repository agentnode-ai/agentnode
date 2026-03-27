from langchain.tools import tool


class TextStats:
    """Simple statistics accumulator for text."""

    def __init__(self, text: str):
        self.words = text.split()
        self.chars = len(text)
        self.lines = text.count("\n") + 1

    def to_dict(self) -> dict:
        return {
            "word_count": len(self.words),
            "char_count": self.chars,
            "line_count": self.lines,
            "avg_word_length": round(sum(len(w) for w in self.words) / max(len(self.words), 1), 2),
        }


@tool
def text_statistics(text: str) -> dict:
    """Compute statistics for the given text."""
    stats = TextStats(text)
    return stats.to_dict()
