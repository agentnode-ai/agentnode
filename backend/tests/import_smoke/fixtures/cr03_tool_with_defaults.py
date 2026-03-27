from crewai_tools import tool


@tool("Text Truncator")
def truncate_text(text: str, max_length: int = 200, suffix: str = "...") -> dict:
    """Truncate text to a maximum length."""
    if len(text) <= max_length:
        return {"text": text, "truncated": False}
    return {"text": text[:max_length] + suffix, "truncated": True}
