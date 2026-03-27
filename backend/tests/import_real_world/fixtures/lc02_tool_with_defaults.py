from langchain.tools import tool


@tool
def search_web(query: str, max_results: int = 10, language: str = "en") -> dict:
    """Search the web for a given query."""
    import requests
    params = {"q": query, "limit": max_results, "lang": language}
    resp = requests.get("https://api.example.com/search", params=params)
    return {"results": resp.json().get("items", []), "total": resp.json().get("total", 0)}
