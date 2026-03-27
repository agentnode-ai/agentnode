"""
Tool defined using StructuredTool.from_function pattern.
Less common than @tool but appears in more advanced LangChain setups.
"""

from typing import Optional

import requests
from langchain.tools import StructuredTool
from pydantic import BaseModel, Field


class StockLookupInput(BaseModel):
    ticker: str = Field(..., description="Stock ticker symbol, e.g. AAPL, MSFT, TSLA")
    interval: str = Field(default="1d", description="Data interval: 1d, 1wk, 1mo")
    period: str = Field(default="1mo", description="Data period: 1d, 5d, 1mo, 3mo, 1y")


def lookup_stock_price(ticker: str, interval: str = "1d", period: str = "1mo") -> dict:
    """
    Look up historical stock price data using Yahoo Finance.

    Args:
        ticker: Stock ticker symbol
        interval: Data interval
        period: Historical period

    Returns:
        dict with ticker, current_price, change_pct, and history
    """
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    params = {
        "interval": interval,
        "range": period,
        "includeTimestamps": True,
    }
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        result = data.get("chart", {}).get("result", [])
        if not result:
            return {"error": f"No data found for ticker: {ticker}", "ticker": ticker}

        meta = result[0].get("meta", {})
        closes = result[0].get("indicators", {}).get("quote", [{}])[0].get("close", [])
        timestamps = result[0].get("timestamp", [])

        current_price = meta.get("regularMarketPrice")
        prev_close = meta.get("previousClose") or meta.get("chartPreviousClose")
        change_pct = None
        if current_price and prev_close:
            change_pct = round((current_price - prev_close) / prev_close * 100, 2)

        return {
            "ticker": ticker.upper(),
            "current_price": current_price,
            "currency": meta.get("currency", "USD"),
            "change_pct": change_pct,
            "data_points": len(closes),
            "error": None,
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


# StructuredTool.from_function is used instead of @tool decorator
stock_lookup_tool = StructuredTool.from_function(
    func=lookup_stock_price,
    name="stock_lookup",
    description=(
        "Look up stock price information for a given ticker symbol. "
        "Returns current price, change percentage, and historical data."
    ),
    args_schema=StockLookupInput,
    return_direct=False,
)
