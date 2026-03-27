from crewai_tools import tool


@tool("Summarize Document")
def summarize_document(file_path: str, max_length: int = 500) -> dict:
    """Read a document and return a concise summary."""
    with open(file_path) as f:
        content = f.read()
    sentences = content.split(".")
    summary = ". ".join(sentences[:5]).strip()
    if len(summary) > max_length:
        summary = summary[:max_length] + "..."
    return {"summary": summary, "original_length": len(content)}
