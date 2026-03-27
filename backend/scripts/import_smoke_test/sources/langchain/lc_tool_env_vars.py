"""
@tool that relies heavily on os.environ and os.getenv for configuration.
Very common pattern — people externalise all secrets to env vars.
"""

import os
from typing import Optional

import requests
from langchain.tools import tool


# module-level env var reads (common pattern)
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")
NOTION_VERSION = "2022-06-28"


def _notion_headers() -> dict:
    token = os.getenv("NOTION_TOKEN") or NOTION_TOKEN
    if not token:
        raise ValueError("NOTION_TOKEN environment variable is not set")
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


@tool
def query_notion_database(filter_property: Optional[str] = None, filter_value: Optional[str] = None) -> dict:
    """
    Query a Notion database and return page entries.

    Reads NOTION_TOKEN and NOTION_DATABASE_ID from environment variables.

    Args:
        filter_property: Optional property name to filter by
        filter_value: Optional value to filter by (used with filter_property)

    Returns:
        dict with results list and total count
    """
    db_id = os.getenv("NOTION_DATABASE_ID") or NOTION_DATABASE_ID
    if not db_id:
        return {"error": "NOTION_DATABASE_ID environment variable is not set", "results": []}

    try:
        headers = _notion_headers()
    except ValueError as e:
        return {"error": str(e), "results": []}

    payload = {}
    if filter_property and filter_value:
        payload["filter"] = {
            "property": filter_property,
            "rich_text": {"contains": filter_value},
        }

    try:
        resp = requests.post(
            f"https://api.notion.com/v1/databases/{db_id}/query",
            headers=headers,
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        return {
            "results": results,
            "total": len(results),
            "has_more": data.get("has_more", False),
            "error": None,
        }
    except requests.RequestException as e:
        return {"error": str(e), "results": []}
