"""
Simple CrewAI tool using @tool("Name") decorator with a named label.
Most basic pattern from crewAI docs and tutorials.
"""

import requests
from crewai_tools import tool


@tool("DuckDuckGo Search")
def search_duckduckgo(query: str) -> dict:
    """
    Search the web using DuckDuckGo Instant Answer API.

    Args:
        query: The search query to look up

    Returns:
        dict with abstract, related_topics, and answer
    """
    url = "https://api.duckduckgo.com/"
    params = {
        "q": query,
        "format": "json",
        "no_html": 1,
        "skip_disambig": 1,
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        related = [
            {"text": t.get("Text", ""), "url": t.get("FirstURL", "")}
            for t in data.get("RelatedTopics", [])
            if isinstance(t, dict) and t.get("Text")
        ][:5]

        return {
            "query": query,
            "abstract": data.get("AbstractText", ""),
            "abstract_source": data.get("AbstractSource", ""),
            "answer": data.get("Answer", ""),
            "answer_type": data.get("AnswerType", ""),
            "related_topics": related,
            "error": None,
        }
    except Exception as e:
        return {"query": query, "abstract": "", "related_topics": [], "error": str(e)}
