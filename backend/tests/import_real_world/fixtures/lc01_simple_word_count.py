from langchain.tools import tool


@tool
def word_count(text: str) -> dict:
    """Count the number of words in a text."""
    words = text.split()
    return {"count": len(words), "words": words}
