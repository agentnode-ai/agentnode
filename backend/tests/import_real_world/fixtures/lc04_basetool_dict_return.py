from langchain.tools import BaseTool


class UrlFetcher(BaseTool):
    name = "url_fetcher"
    description = "Fetch the content of a URL and return status + body length"

    def _run(self, url: str) -> dict:
        import requests
        resp = requests.get(url, timeout=10)
        return {
            "status_code": resp.status_code,
            "content_length": len(resp.text),
            "ok": resp.ok,
        }
