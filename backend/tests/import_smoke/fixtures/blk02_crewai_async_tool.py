from crewai_tools import tool


@tool("Async Fetcher")
async def async_fetch(url: str) -> dict:
    """Fetch URL content asynchronously."""
    import aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            text = await resp.text()
            return {"content": text[:2000], "status": resp.status}
