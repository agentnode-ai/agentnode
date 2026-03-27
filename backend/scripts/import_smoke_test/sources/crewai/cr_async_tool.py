"""
CrewAI @tool with async def.
Users sometimes write async tools expecting support — should be flagged/broken.
crewAI's @tool decorator does not support async functions in the standard way.
"""

import asyncio
from typing import Dict, List

import aiohttp
from crewai_tools import tool


@tool("Async Batch Fetcher")
async def fetch_urls_async(urls: List[str], timeout_seconds: int = 10) -> Dict:
    """
    Fetch multiple URLs concurrently using asyncio and aiohttp.

    This tool is intended for batch URL retrieval in async agent contexts.

    Args:
        urls: List of URLs to fetch concurrently
        timeout_seconds: Timeout for each individual request

    Returns:
        dict with results list and summary stats
    """
    async def _get(session: aiohttp.ClientSession, url: str) -> dict:
        try:
            timeout = aiohttp.ClientTimeout(total=timeout_seconds)
            async with session.get(url, timeout=timeout) as resp:
                text = await resp.text()
                return {
                    "url": url,
                    "status": resp.status,
                    "length": len(text),
                    "ok": resp.status < 400,
                    "error": None,
                }
        except asyncio.TimeoutError:
            return {"url": url, "status": None, "length": 0, "ok": False, "error": "timeout"}
        except aiohttp.ClientError as e:
            return {"url": url, "status": None, "length": 0, "ok": False, "error": str(e)}

    connector = aiohttp.TCPConnector(limit=10)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [_get(session, url) for url in urls]
        results = await asyncio.gather(*tasks, return_exceptions=False)

    ok_count = sum(1 for r in results if r.get("ok"))
    return {
        "results": list(results),
        "total": len(results),
        "successful": ok_count,
        "failed": len(results) - ok_count,
    }
