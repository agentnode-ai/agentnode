from crewai_tools import tool
import requests

TIMEOUT = 15


@tool("URL Health Check")
def check_url(url: str) -> dict:
    """Check if a URL is reachable and return status info."""
    try:
        resp = requests.head(url, timeout=TIMEOUT, allow_redirects=True)
        return {
            "url": url,
            "status_code": resp.status_code,
            "reachable": resp.status_code < 400,
            "content_type": resp.headers.get("Content-Type", "unknown"),
        }
    except requests.RequestException as e:
        return {"url": url, "reachable": False, "error": str(e)}
