from langchain.tools import tool
from .utils import sanitize_input, validate_query


@tool
def safe_search(query: str) -> dict:
    """Perform a sanitized search."""
    clean = sanitize_input(query)
    if not validate_query(clean):
        return {"error": "Invalid query", "results": []}
    return {"results": [f"Result for: {clean}"], "query": clean}
