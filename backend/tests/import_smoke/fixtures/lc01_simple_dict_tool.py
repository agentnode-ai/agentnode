from langchain.tools import tool


@tool
def reverse_text(text: str) -> dict:
    """Reverse the input text and return it with metadata."""
    reversed_text = text[::-1]
    return {"reversed": reversed_text, "length": len(text)}
