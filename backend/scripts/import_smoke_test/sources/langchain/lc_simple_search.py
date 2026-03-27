"""
Simple search tool using LangChain @tool decorator.
Typical pattern found in tutorials and starter projects.
"""

import requests
from langchain.tools import tool


SERP_API_KEY = "sk-..."  # users often hardcode this


@tool
def search_web(query: str) -> dict:
    """Search the web for a given query and return top results."""
    url = "https://serpapi.com/search"
    params = {
        "q": query,
        "api_key": SERP_API_KEY,
        "num": 5,
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        results = []
        for item in data.get("organic_results", []):
            results.append({
                "title": item.get("title", ""),
                "link": item.get("link", ""),
                "snippet": item.get("snippet", ""),
            })
        return {"results": results, "query": query, "count": len(results)}
    except requests.RequestException as e:
        return {"error": str(e), "query": query, "results": []}
