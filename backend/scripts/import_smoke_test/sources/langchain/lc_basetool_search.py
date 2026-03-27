"""
Search tool using BaseTool subclass pattern.
Clean implementation — no self references in _run body, returns dict.
"""

from typing import Optional, Type

import requests
from langchain.tools import BaseTool
from pydantic import BaseModel, Field


class SearchInput(BaseModel):
    query: str = Field(..., description="The search query string")
    num_results: int = Field(default=5, description="Number of results to return")


class WebSearchTool(BaseTool):
    name: str = "web_search"
    description: str = (
        "Search the internet for information about a topic. "
        "Returns a list of relevant results with titles, URLs, and snippets."
    )
    args_schema: Type[BaseModel] = SearchInput
    api_key: str = ""

    def _run(self, query: str, num_results: int = 5) -> dict:
        """Execute the web search."""
        url = "https://serpapi.com/search"
        params = {"q": query, "api_key": self.api_key, "num": num_results}

        try:
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            results = [
                {
                    "title": r.get("title", ""),
                    "link": r.get("link", ""),
                    "snippet": r.get("snippet", ""),
                }
                for r in data.get("organic_results", [])
            ]
            return {"query": query, "results": results, "count": len(results)}
        except Exception as e:
            return {"query": query, "results": [], "error": str(e)}

    async def _arun(self, query: str, num_results: int = 5) -> dict:
        raise NotImplementedError("Use _run instead")
