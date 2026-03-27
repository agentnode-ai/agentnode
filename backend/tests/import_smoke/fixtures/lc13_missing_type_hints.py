from langchain.tools import tool


@tool
def process_items(items, separator, limit=100):
    """Process a list of items and join them."""
    parts = str(items).split(separator)
    trimmed = parts[:limit]
    return {"result": separator.join(trimmed), "count": len(trimmed)}
