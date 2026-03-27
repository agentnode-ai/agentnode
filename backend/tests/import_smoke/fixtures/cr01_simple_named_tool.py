from crewai_tools import tool


@tool("Markdown Formatter")
def format_markdown(title: str, content: str) -> dict:
    """Format content as a Markdown document with a title."""
    doc = f"# {title}\n\n{content}"
    return {"markdown": doc, "length": len(doc)}
