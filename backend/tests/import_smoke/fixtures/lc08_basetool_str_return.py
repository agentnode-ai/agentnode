from langchain.tools import BaseTool


class PageFetcher(BaseTool):
    name = "page_fetcher"
    description = "Fetch the text content of a webpage"

    def _run(self, url: str) -> str:
        import requests
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        return resp.text[:5000]
