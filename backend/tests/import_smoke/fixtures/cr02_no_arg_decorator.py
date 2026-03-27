from crewai_tools import tool


@tool
def word_count(text: str) -> dict:
    """Count words in the provided text."""
    words = text.split()
    return {"count": len(words), "unique": len(set(words))}
