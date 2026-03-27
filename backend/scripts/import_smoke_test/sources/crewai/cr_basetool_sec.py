"""
SEC 10-K filing tool closely mirroring the real SEC10KTool from crewAI-examples.
Inherits from RagTool (not BaseTool directly), uses sec_api, has __init__ override,
self.get_10k_url_content(), multiple schema definitions.

Should break on import due to:
- RagTool inheritance (not plain BaseTool)
- __init__ override with super() call
- self.get_10k_url_content() method on self
- Third-party dep: sec_api not commonly installed

Source reference: crewAI-examples/stock_analysis/tools/sec_tools.py
"""

import os
from typing import Any, Optional, Type

from crewai_tools import RagTool
from pydantic import BaseModel, Field
from sec_api import QueryApi


SEC_API_KEY = os.getenv("SEC_API_KEY", "")


class FixedQuery10KToolSchema(BaseModel):
    """Use this to get and summarize a 10-K filing for a fixed company."""
    search_query: str = Field(
        ...,
        description=(
            "Mandatory search query you want to use to search the 10-K filing's content. "
            "Example: 'What is the company's revenue in 2023?'"
        ),
    )


class Search10KToolSchema(FixedQuery10KToolSchema):
    """Use this to get and summarize a 10-K filing for any company you choose."""
    stock_ticker: str = Field(
        ...,
        description=(
            "Mandatory valid stock ticker you want to search for. "
            "Example: 'AAPL' for Apple or 'GOOGL' for Alphabet."
        ),
    )


class SEC10KTool(RagTool):
    name: str = "Search 10-K filings"
    description: str = (
        "A tool that can be used to semantic search a query from a 10-K form "
        "for a given stock and return relevant excerpts."
    )
    args_schema: Type[BaseModel] = Search10KToolSchema
    stock_ticker: Optional[str] = None
    sec_api_key: str = Field(default_factory=lambda: os.getenv("SEC_API_KEY", ""))

    def __init__(self, stock_ticker: Optional[str] = None, **kwargs: Any):
        super().__init__(**kwargs)
        if stock_ticker:
            self.stock_ticker = stock_ticker
            self.description = (
                f"A tool that can be used to semantic search a query from {stock_ticker}'s "
                "latest 10-K SEC filing and return relevant excerpts."
            )
            self.args_schema = FixedQuery10KToolSchema
            self._generate_description()

    def _run(self, search_query: str, stock_ticker: Optional[str] = None) -> Any:
        """Fetch and search 10-K content for a given ticker."""
        ticker = stock_ticker or self.stock_ticker
        if not ticker:
            return "Error: stock_ticker is required"

        try:
            filing_url = self._get_10k_url(ticker)
            if not filing_url:
                return f"Error: could not find 10-K filing for {ticker}"

            # loads content into RagTool's internal vector store
            content = self.get_10k_url_content(filing_url)
            return super()._run(query=search_query)
        except Exception as e:
            return f"Error processing 10-K for {ticker}: {e}"

    def _get_10k_url(self, ticker: str) -> Optional[str]:
        """Query SEC EDGAR for the most recent 10-K filing URL."""
        api_key = self.sec_api_key or SEC_API_KEY
        if not api_key:
            raise ValueError("SEC_API_KEY is not set")

        query_api = QueryApi(api_key=api_key)
        query = {
            "query": {
                "query_string": {
                    "query": f'ticker:"{ticker}" AND formType:"10-K"',
                }
            },
            "from": "0",
            "size": "1",
            "sort": [{"filedAt": {"order": "desc"}}],
        }

        filings = query_api.get_filings(query)
        hits = filings.get("hits", {}).get("hits", [])
        if not hits:
            return None

        return hits[0].get("_source", {}).get("linkToFilingDetails")

    def get_10k_url_content(self, url: str) -> str:
        """Fetch raw 10-K content and add to RAG store (self reference)."""
        import requests
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        content = resp.text

        # self.add() is from RagTool
        self.add(content)
        return content
