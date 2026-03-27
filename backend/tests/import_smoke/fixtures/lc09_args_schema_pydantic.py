from langchain.tools import BaseTool
from pydantic import BaseModel, Field


class SearchInput(BaseModel):
    query: str = Field(description="The search query")
    max_results: int = Field(default=5, description="Maximum results to return")


class WebSearchTool(BaseTool):
    name = "web_search"
    description = "Search the web for information"
    args_schema = SearchInput

    def _run(self, query: str, max_results: int = 5) -> dict:
        results = [{"title": f"Result for {query} #{i}", "url": f"https://example.com/{i}"} for i in range(max_results)]
        return {"query": query, "results": results, "total": max_results}
