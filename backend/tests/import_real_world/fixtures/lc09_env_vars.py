from langchain.tools import tool
import os

API_KEY = os.getenv("SEARCH_API_KEY", "")
BASE_URL = os.environ.get("SEARCH_BASE_URL", "https://api.example.com")


@tool
def authenticated_search(query: str) -> dict:
    """Search using an authenticated API endpoint."""
    import requests
    headers = {"Authorization": f"Bearer {API_KEY}"}
    resp = requests.get(f"{BASE_URL}/search", params={"q": query}, headers=headers)
    return {"results": resp.json(), "status": resp.status_code}
