from langchain.tools import tool


@tool
def count_words(text: str) -> dict:
    """Count the number of words in text."""
    words = text.split()
    return {"count": len(words), "words": words[:5]}


@tool
def count_chars(text: str, ignore_spaces: bool = True) -> dict:
    """Count characters in text."""
    if ignore_spaces:
        text = text.replace(" ", "")
    return {"count": len(text)}
