"""
Complex inheritance chain: Tool -> AuthenticatedTool -> RateLimitedTool -> BaseTool.
Users sometimes build base classes for cross-cutting concerns (auth, rate limiting)
and then inherit from those in tool implementations.
"""

import time
from threading import Lock
from typing import Any, ClassVar, Dict, Optional, Type

from langchain.tools import BaseTool
from pydantic import BaseModel, Field


# intermediate base class — not a direct BaseTool subclass
class AuthenticatedBaseTool(BaseTool):
    """Base class adding authentication handling to all subclass tools."""

    api_key: str = Field(default="", description="API key for authentication")
    auth_header_name: str = Field(default="Authorization", description="Header name for auth token")
    auth_prefix: str = Field(default="Bearer", description="Token prefix in auth header")

    def get_auth_headers(self) -> Dict[str, str]:
        if not self.api_key:
            return {}
        return {self.auth_header_name: f"{self.auth_prefix} {self.api_key}"}

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("Subclasses must implement _run")

    async def _arun(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


class RateLimitedTool(AuthenticatedBaseTool):
    """Extends AuthenticatedBaseTool with simple rate limiting."""

    _call_times: ClassVar[list] = []
    _lock: ClassVar[Lock] = Lock()

    max_calls_per_minute: int = Field(default=60, description="Max calls per 60-second window")

    def _check_rate_limit(self) -> None:
        with self._lock:
            now = time.time()
            self.__class__._call_times = [t for t in self._call_times if now - t < 60]
            if len(self._call_times) >= self.max_calls_per_minute:
                oldest = self._call_times[0]
                wait_secs = 60 - (now - oldest)
                if wait_secs > 0:
                    time.sleep(wait_secs)
            self.__class__._call_times.append(time.time())

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


# actual tool at the bottom of the chain
class HackerNewsSearchInput(BaseModel):
    query: str = Field(..., description="Search query")
    story_type: str = Field(default="story", description="HN item type: story, comment, job, poll")
    num_results: int = Field(default=10, ge=1, le=50, description="Results to return")


class HackerNewsSearchTool(RateLimitedTool):
    name: str = "hackernews_search"
    description: str = "Search Hacker News for stories, comments, or jobs matching a query."
    args_schema: Type[BaseModel] = HackerNewsSearchInput

    def _run(self, query: str, story_type: str = "story", num_results: int = 10) -> dict:
        self._check_rate_limit()

        import requests
        headers = self.get_auth_headers()  # from AuthenticatedBaseTool
        url = "https://hn.algolia.com/api/v1/search"
        params = {
            "query": query,
            "tags": story_type,
            "hitsPerPage": num_results,
        }

        try:
            resp = requests.get(url, params=params, headers=headers, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            hits = data.get("hits", [])
            return {
                "query": query,
                "results": [
                    {
                        "title": h.get("title", h.get("story_title", "")),
                        "url": h.get("url", ""),
                        "author": h.get("author", ""),
                        "points": h.get("points", 0),
                        "hn_url": f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                    }
                    for h in hits
                ],
                "total_hits": data.get("nbHits", 0),
                "error": None,
            }
        except Exception as e:
            return {"query": query, "results": [], "error": str(e)}
