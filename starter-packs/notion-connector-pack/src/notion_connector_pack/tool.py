"""Notion connector tool using the Notion API via httpx."""

from __future__ import annotations

import httpx

BASE_URL = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def _headers(token: str) -> dict:
    """Build authorization headers for the Notion API."""
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION,
    }


def _extract_title(properties: dict) -> str:
    """Extract the title text from a Notion page's properties."""
    for prop in properties.values():
        if prop.get("type") == "title":
            title_parts = prop.get("title", [])
            return "".join(part.get("plain_text", "") for part in title_parts)
    return ""


def _search(token: str, **kwargs) -> dict:
    """Search Notion pages and databases by query string."""
    query = kwargs.get("query", "")
    filter_type = kwargs.get("filter_type", None)  # "page" or "database"
    page_size = kwargs.get("page_size", 20)
    start_cursor = kwargs.get("start_cursor", None)

    payload: dict = {"page_size": page_size}
    if query:
        payload["query"] = query
    if filter_type:
        payload["filter"] = {"value": filter_type, "property": "object"}
    if start_cursor:
        payload["start_cursor"] = start_cursor

    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{BASE_URL}/search",
            headers=_headers(token),
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    results = []
    for item in data.get("results", []):
        entry: dict = {
            "id": item["id"],
            "object": item["object"],
            "url": item.get("url", ""),
            "created_time": item.get("created_time", ""),
            "last_edited_time": item.get("last_edited_time", ""),
        }
        if item["object"] == "page":
            entry["title"] = _extract_title(item.get("properties", {}))
        elif item["object"] == "database":
            title_list = item.get("title", [])
            entry["title"] = "".join(t.get("plain_text", "") for t in title_list)
        results.append(entry)

    return {
        "success": True,
        "results": results,
        "has_more": data.get("has_more", False),
        "next_cursor": data.get("next_cursor", ""),
        "total": len(results),
    }


def _get_page(token: str, **kwargs) -> dict:
    """Retrieve a Notion page and its block children."""
    page_id = kwargs.get("page_id", "")
    if not page_id:
        return {"success": False, "error": "Missing required parameter: page_id"}

    with httpx.Client(timeout=30) as client:
        # Get page metadata
        page_resp = client.get(
            f"{BASE_URL}/pages/{page_id}",
            headers=_headers(token),
        )
        page_resp.raise_for_status()
        page_data = page_resp.json()

        # Get page content (block children)
        blocks_resp = client.get(
            f"{BASE_URL}/blocks/{page_id}/children",
            headers=_headers(token),
            params={"page_size": 100},
        )
        blocks_resp.raise_for_status()
        blocks_data = blocks_resp.json()

    # Extract text from blocks
    blocks = []
    for block in blocks_data.get("results", []):
        block_type = block.get("type", "")
        block_entry: dict = {
            "id": block["id"],
            "type": block_type,
            "has_children": block.get("has_children", False),
        }
        # Extract rich text content from the block
        type_data = block.get(block_type, {})
        if "rich_text" in type_data:
            block_entry["text"] = "".join(
                rt.get("plain_text", "") for rt in type_data["rich_text"]
            )
        elif "text" in type_data:
            block_entry["text"] = "".join(
                rt.get("plain_text", "") for rt in type_data["text"]
            )
        blocks.append(block_entry)

    return {
        "success": True,
        "page": {
            "id": page_data["id"],
            "title": _extract_title(page_data.get("properties", {})),
            "url": page_data.get("url", ""),
            "created_time": page_data.get("created_time", ""),
            "last_edited_time": page_data.get("last_edited_time", ""),
            "archived": page_data.get("archived", False),
        },
        "blocks": blocks,
    }


def _create_page(token: str, **kwargs) -> dict:
    """Create a new Notion page under a parent page or database."""
    parent_id = kwargs.get("parent_id", "")
    title = kwargs.get("title", "")
    content = kwargs.get("content", "")
    parent_type = kwargs.get("parent_type", "page_id")  # "page_id" or "database_id"

    if not parent_id:
        return {"success": False, "error": "Missing required parameter: parent_id"}
    if not title:
        return {"success": False, "error": "Missing required parameter: title"}

    payload: dict = {
        "parent": {parent_type: parent_id},
    }

    if parent_type == "database_id":
        payload["properties"] = {
            "title": {
                "title": [{"text": {"content": title}}],
            },
        }
    else:
        payload["properties"] = {
            "title": {
                "title": [{"text": {"content": title}}],
            },
        }

    # Add content as paragraph blocks
    if content:
        paragraphs = content.split("\n")
        children = []
        for para in paragraphs:
            if para.strip():
                children.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": para}}],
                    },
                })
        if children:
            payload["children"] = children

    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{BASE_URL}/pages",
            headers=_headers(token),
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    return {
        "success": True,
        "page": {
            "id": data["id"],
            "url": data.get("url", ""),
            "created_time": data.get("created_time", ""),
        },
    }


def _query_database(token: str, **kwargs) -> dict:
    """Query a Notion database with optional filter and sorts."""
    database_id = kwargs.get("database_id", "")
    if not database_id:
        return {"success": False, "error": "Missing required parameter: database_id"}

    filter_obj = kwargs.get("filter", None)
    sorts = kwargs.get("sorts", None)
    page_size = kwargs.get("page_size", 50)
    start_cursor = kwargs.get("start_cursor", None)

    payload: dict = {"page_size": page_size}
    if filter_obj:
        payload["filter"] = filter_obj
    if sorts:
        payload["sorts"] = sorts
    if start_cursor:
        payload["start_cursor"] = start_cursor

    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{BASE_URL}/databases/{database_id}/query",
            headers=_headers(token),
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    rows = []
    for item in data.get("results", []):
        row: dict = {
            "id": item["id"],
            "url": item.get("url", ""),
            "created_time": item.get("created_time", ""),
            "last_edited_time": item.get("last_edited_time", ""),
            "properties": {},
        }
        # Flatten properties for easier consumption
        for prop_name, prop_val in item.get("properties", {}).items():
            prop_type = prop_val.get("type", "")
            if prop_type == "title":
                row["properties"][prop_name] = "".join(
                    t.get("plain_text", "") for t in prop_val.get("title", [])
                )
            elif prop_type == "rich_text":
                row["properties"][prop_name] = "".join(
                    t.get("plain_text", "") for t in prop_val.get("rich_text", [])
                )
            elif prop_type == "number":
                row["properties"][prop_name] = prop_val.get("number")
            elif prop_type == "select":
                sel = prop_val.get("select")
                row["properties"][prop_name] = sel.get("name", "") if sel else ""
            elif prop_type == "multi_select":
                row["properties"][prop_name] = [
                    s.get("name", "") for s in prop_val.get("multi_select", [])
                ]
            elif prop_type == "checkbox":
                row["properties"][prop_name] = prop_val.get("checkbox", False)
            elif prop_type == "date":
                date_val = prop_val.get("date")
                row["properties"][prop_name] = date_val if date_val else None
            elif prop_type == "url":
                row["properties"][prop_name] = prop_val.get("url", "")
            elif prop_type == "email":
                row["properties"][prop_name] = prop_val.get("email", "")
            elif prop_type == "status":
                st = prop_val.get("status")
                row["properties"][prop_name] = st.get("name", "") if st else ""
            else:
                row["properties"][prop_name] = f"({prop_type})"
        rows.append(row)

    return {
        "success": True,
        "results": rows,
        "has_more": data.get("has_more", False),
        "next_cursor": data.get("next_cursor", ""),
        "total": len(rows),
    }


_OPERATIONS = {
    "search": _search,
    "get_page": _get_page,
    "create_page": _create_page,
    "query_database": _query_database,
}


def run(token: str, operation: str, **kwargs) -> dict:
    """Search, read, and create pages and query databases in Notion.

    Args:
        token: Notion integration token (secret_...).
        operation: One of 'search', 'get_page', 'create_page', 'query_database'.
        **kwargs: Additional arguments depending on operation:
            search: query (str), filter_type ('page'/'database'), page_size (int),
                    start_cursor (str)
            get_page: page_id (str, required)
            create_page: parent_id (str, required), title (str, required), content (str),
                         parent_type ('page_id'/'database_id')
            query_database: database_id (str, required), filter (dict), sorts (list),
                            page_size (int), start_cursor (str)

    Returns:
        dict with operation results.
    """
    if not token:
        return {"success": False, "error": "Missing required parameter: token"}

    operation = operation.lower().strip()

    if operation not in _OPERATIONS:
        ops = ", ".join(sorted(_OPERATIONS.keys()))
        return {"success": False, "error": f"Unknown operation: {operation}. Available: {ops}"}

    try:
        return _OPERATIONS[operation](token, **kwargs)
    except httpx.HTTPStatusError as exc:
        error_body = ""
        try:
            error_body = exc.response.json()
        except Exception:
            error_body = exc.response.text
        return {
            "success": False,
            "error": f"Notion API error ({exc.response.status_code}): {error_body}",
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}
