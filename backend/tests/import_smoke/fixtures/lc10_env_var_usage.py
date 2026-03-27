import os
from langchain.tools import tool


@tool
def query_api(endpoint: str) -> dict:
    """Query an external API using the configured API key."""
    import requests
    api_key = os.getenv("EXTERNAL_API_KEY")
    base_url = os.environ.get("API_BASE_URL", "https://api.example.com")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    resp = requests.get(f"{base_url}/{endpoint}", headers=headers, timeout=10)
    return {"status": resp.status_code, "data": resp.json()}
