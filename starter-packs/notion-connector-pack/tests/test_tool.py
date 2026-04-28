"""Tests for notion-connector-pack."""

from unittest.mock import MagicMock, patch

from notion_connector_pack.tool import _extract_title, run


# -- Pure helper --

def test_extract_title_from_properties():
    props = {
        "Name": {
            "type": "title",
            "title": [{"plain_text": "My Page"}],
        },
    }
    assert _extract_title(props) == "My Page"


def test_extract_title_empty():
    assert _extract_title({}) == ""


def test_extract_title_no_title_type():
    props = {"Status": {"type": "select", "select": {"name": "Done"}}}
    assert _extract_title(props) == ""


# -- Input validation --

def test_missing_token():
    result = run(token="", operation="search")
    assert result["success"] is False
    assert "token" in result["error"].lower()


def test_unknown_operation():
    result = run(token="secret_tok", operation="destroy")
    assert result["success"] is False
    assert "Unknown operation" in result["error"]


def test_get_page_missing_id():
    result = run(token="secret_tok", operation="get_page")
    assert result["success"] is False
    assert "page_id" in result["error"]


def test_create_page_missing_parent():
    result = run(token="secret_tok", operation="create_page")
    assert result["success"] is False
    assert "parent_id" in result["error"]


def test_query_database_missing_id():
    result = run(token="secret_tok", operation="query_database")
    assert result["success"] is False
    assert "database_id" in result["error"]


# -- Mocked search --

@patch("notion_connector_pack.tool.httpx.Client")
def test_search_success(mock_cls):
    mock_client = MagicMock()
    mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_cls.return_value.__exit__ = MagicMock(return_value=False)

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "results": [{
            "id": "page-1", "object": "page", "url": "https://notion.so/page-1",
            "created_time": "2024-01-01", "last_edited_time": "2024-01-02",
            "properties": {"Name": {"type": "title", "title": [{"plain_text": "Test"}]}},
        }],
        "has_more": False,
        "next_cursor": None,
    }
    mock_client.post.return_value = mock_resp

    result = run(token="secret_tok", operation="search", query="test")
    assert result["success"] is True
    assert result["total"] == 1
    assert result["results"][0]["title"] == "Test"
