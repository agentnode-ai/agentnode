from crewai_tools import tool
import json

MAX_RETRIES = 3
DEFAULT_TIMEOUT = 30


def _make_request(url: str, retries: int = MAX_RETRIES) -> dict:
    """Make an HTTP request with retries."""
    import requests
    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=DEFAULT_TIMEOUT)
            resp.raise_for_status()
            return {"data": resp.json(), "status": resp.status_code}
        except requests.RequestException:
            if attempt == retries - 1:
                return {"error": "All retries failed", "status": 0}
    return {"error": "Unknown", "status": 0}


def _format_response(raw: dict) -> dict:
    """Format the API response for output."""
    if "error" in raw:
        return raw
    return {"result": json.dumps(raw["data"], indent=2), "status": raw["status"]}


@tool("API Fetcher")
def fetch_api(url: str) -> dict:
    """Fetch data from an API endpoint with automatic retries."""
    raw = _make_request(url)
    return _format_response(raw)
