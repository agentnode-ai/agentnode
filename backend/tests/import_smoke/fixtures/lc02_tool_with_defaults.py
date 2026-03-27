from langchain.tools import tool


@tool
def search_items(query: str, max_results: int = 10, include_metadata: bool = False) -> dict:
    """Search for items matching the query."""
    results = [{"title": f"Result {i}", "score": 1.0 / (i + 1)} for i in range(max_results)]
    if include_metadata:
        for r in results:
            r["query"] = query
    return {"results": results, "total": len(results)}
