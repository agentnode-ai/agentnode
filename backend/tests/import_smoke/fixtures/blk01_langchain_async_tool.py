from langchain.tools import tool


@tool
async def fetch_multiple(urls: str) -> dict:
    """Fetch multiple URLs concurrently."""
    import aiohttp
    url_list = urls.split(",")
    results = []
    async with aiohttp.ClientSession() as session:
        for url in url_list:
            async with session.get(url.strip()) as resp:
                results.append({"url": url.strip(), "status": resp.status})
    return {"results": results, "count": len(results)}
