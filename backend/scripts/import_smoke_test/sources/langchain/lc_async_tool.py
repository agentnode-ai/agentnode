"""
Async @tool function using aiohttp.
Some users write async tools expecting LangChain to handle them natively.
Should be flagged — async tools require different handling in the import pipeline.
"""

import asyncio
from typing import List, Optional

import aiohttp
from langchain.tools import tool


@tool
async def fetch_multiple_urls(urls: List[str], timeout: int = 10) -> dict:
    """
    Fetch multiple URLs concurrently and return their status and content length.

    Args:
        urls: List of URLs to fetch
        timeout: Timeout per request in seconds

    Returns:
        dict with results list containing url, status_code, content_length, error
    """
    async def _fetch_one(session: aiohttp.ClientSession, url: str) -> dict:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                body = await resp.read()
                return {
                    "url": url,
                    "status_code": resp.status,
                    "content_length": len(body),
                    "content_type": resp.headers.get("Content-Type", ""),
                    "error": None,
                }
        except asyncio.TimeoutError:
            return {"url": url, "status_code": None, "content_length": 0, "error": "timeout"}
        except Exception as e:
            return {"url": url, "status_code": None, "content_length": 0, "error": str(e)}

    async with aiohttp.ClientSession() as session:
        tasks = [_fetch_one(session, url) for url in urls]
        results = await asyncio.gather(*tasks)

    return {
        "results": list(results),
        "total": len(results),
        "successful": sum(1 for r in results if r.get("error") is None),
    }
