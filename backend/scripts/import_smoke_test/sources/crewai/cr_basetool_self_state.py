"""
BaseTool with Pydantic Field(default_factory) for state and heavy self.xxx usage in _run.
Pattern seen in CrewAI tools that maintain per-instance state.
Should break on import — self references throughout _run body + stateful pattern.
"""

import time
from typing import Any, Dict, List, Optional, Type

import requests
from crewai_tools import BaseTool
from pydantic import BaseModel, Field


class ScrapeRequest(BaseModel):
    url: str = Field(..., description="URL to scrape")
    css_selector: Optional[str] = Field(default=None, description="CSS selector to extract")
    wait_seconds: float = Field(default=0.5, description="Seconds to wait before scraping")


class StatefulScraperTool(BaseTool):
    name: str = "Stateful Web Scraper"
    description: str = (
        "Scrapes web pages and maintains a history of visited URLs. "
        "Uses configurable delays and headers stored on the instance."
    )
    args_schema: Type[BaseModel] = ScrapeRequest

    # instance state via Field(default_factory)
    visited_urls: List[str] = Field(default_factory=list)
    scraped_content: Dict[str, str] = Field(default_factory=dict)
    request_headers: Dict[str, str] = Field(
        default_factory=lambda: {
            "User-Agent": "Mozilla/5.0 (compatible; CrewAI-Agent/1.0)",
            "Accept": "text/html,application/xhtml+xml,*/*",
        }
    )
    rate_limit_delay: float = Field(default=1.0)
    max_cache_size: int = Field(default=100)

    def _run(self, url: str, css_selector: Optional[str] = None, wait_seconds: float = 0.5) -> dict:
        """Scrape a URL using self.request_headers and update self.visited_urls."""
        # check self cache
        if url in self.scraped_content:
            return {
                "url": url,
                "content": self.scraped_content[url],
                "cached": True,
                "visited_count": len(self.visited_urls),
            }

        # rate limiting using self.rate_limit_delay
        if self.visited_urls:
            time.sleep(max(wait_seconds, self.rate_limit_delay))

        try:
            from bs4 import BeautifulSoup

            resp = requests.get(url, headers=self.request_headers, timeout=15)
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")

            if css_selector:
                elements = soup.select(css_selector)
                content = "\n".join(el.get_text(strip=True) for el in elements)
            else:
                for tag in soup(["script", "style"]):
                    tag.decompose()
                content = soup.get_text(separator="\n", strip=True)

            # update self state
            self.visited_urls.append(url)
            if len(self.scraped_content) < self.max_cache_size:
                self.scraped_content[url] = content

            return {
                "url": url,
                "content": content[:5000],  # truncate
                "content_length": len(content),
                "cached": False,
                "visited_count": len(self.visited_urls),
                "selector_used": css_selector,
            }
        except Exception as e:
            return {"url": url, "content": "", "error": str(e)}
