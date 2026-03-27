"""
Two @tool functions in a single CrewAI file.
Users pack multiple related tools together — common in domain-specific toolkits.
"""

import os
from typing import Optional

import requests
from crewai_tools import tool


ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_API_KEY", "demo")


@tool("Stock Price Lookup")
def get_stock_quote(symbol: str) -> dict:
    """
    Get the latest stock quote for a given ticker symbol using Alpha Vantage.

    Args:
        symbol: Stock ticker symbol (e.g. 'AAPL', 'TSLA', 'MSFT')

    Returns:
        dict with price, change, volume, and market cap info
    """
    api_key = os.getenv("ALPHA_VANTAGE_API_KEY") or ALPHA_VANTAGE_KEY
    url = "https://www.alphavantage.co/query"
    params = {
        "function": "GLOBAL_QUOTE",
        "symbol": symbol.upper(),
        "apikey": api_key,
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        quote = data.get("Global Quote", {})
        if not quote:
            return {"error": f"No data found for symbol: {symbol}", "symbol": symbol}

        return {
            "symbol": quote.get("01. symbol", symbol),
            "price": float(quote.get("05. price", 0)),
            "change": float(quote.get("09. change", 0)),
            "change_pct": quote.get("10. change percent", "0%"),
            "volume": int(quote.get("06. volume", 0)),
            "latest_trading_day": quote.get("07. latest trading day", ""),
            "prev_close": float(quote.get("08. previous close", 0)),
            "error": None,
        }
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


@tool("Stock News Fetcher")
def get_stock_news(symbol: str, limit: int = 5) -> dict:
    """
    Fetch recent news articles for a stock symbol using Alpha Vantage News Sentiment API.

    Args:
        symbol: Stock ticker symbol (e.g. 'AAPL', 'TSLA')
        limit: Number of articles to return (max 20)

    Returns:
        dict with articles list and overall sentiment summary
    """
    api_key = os.getenv("ALPHA_VANTAGE_API_KEY") or ALPHA_VANTAGE_KEY
    limit = max(1, min(limit, 20))

    url = "https://www.alphavantage.co/query"
    params = {
        "function": "NEWS_SENTIMENT",
        "tickers": symbol.upper(),
        "limit": limit,
        "apikey": api_key,
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        feed = data.get("feed", [])
        articles = [
            {
                "title": item.get("title", ""),
                "source": item.get("source", ""),
                "published": item.get("time_published", ""),
                "url": item.get("url", ""),
                "summary": item.get("summary", "")[:300],
                "sentiment": item.get("overall_sentiment_label", ""),
                "sentiment_score": item.get("overall_sentiment_score", 0),
            }
            for item in feed[:limit]
        ]

        return {
            "symbol": symbol.upper(),
            "articles": articles,
            "count": len(articles),
            "error": None,
        }
    except Exception as e:
        return {"error": str(e), "symbol": symbol, "articles": []}
