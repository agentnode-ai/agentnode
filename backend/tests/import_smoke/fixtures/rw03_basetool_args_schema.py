"""Real-world: BaseTool with explicit args_schema and clean _run body.
Realistic pattern from LangChain docs / repos.
"""
from langchain.tools import BaseTool
from pydantic import BaseModel, Field


class SearchInput(BaseModel):
    query: str = Field(description="Search query")
    max_results: int = Field(default=5, description="Maximum results to return")


class SearchTool(BaseTool):
    name = "web_search"
    description = "Search the web for information"
    args_schema = SearchInput

    def _run(self, query: str, max_results: int = 5) -> dict:
        """Execute web search and return results."""
        import requests
        resp = requests.get(
            "https://api.example.com/search",
            params={"q": query, "n": max_results},
        )
        return {"results": resp.json(), "count": max_results}
