"""Tests for discord-connector-pack."""

from unittest.mock import MagicMock, patch

from discord_connector_pack.tool import _headers, run


# -- Pure helper --

def test_headers_format():
    h = _headers("bot-token-123")
    assert h["Authorization"] == "Bot bot-token-123"
    assert "Content-Type" in h


# -- Input validation --

def test_missing_token():
    result = run(token="", operation="list_guilds")
    assert result["success"] is False
    assert "token" in result["error"].lower()


def test_unknown_operation():
    result = run(token="tok", operation="destroy_server")
    assert result["success"] is False
    assert "Unknown operation" in result["error"]


def test_send_message_missing_channel():
    result = run(token="tok", operation="send_message", channel_id="")
    assert result["success"] is False
    assert "channel_id" in result["error"]


# -- Mocked success flows --

def _mock_response(json_data):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


@patch("discord_connector_pack.tool.httpx.Client")
def test_send_message_success(mock_cls):
    mock_client = MagicMock()
    mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_cls.return_value.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = _mock_response({
        "id": "msg1", "channel_id": "ch1", "content": "hello", "timestamp": "2024-01-01",
    })

    result = run(token="tok", operation="send_message", channel_id="ch1", content="hello")
    assert result["success"] is True
    assert result["message"]["id"] == "msg1"


@patch("discord_connector_pack.tool.httpx.Client")
def test_list_guilds_success(mock_cls):
    mock_client = MagicMock()
    mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_cls.return_value.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = _mock_response([
        {"id": "g1", "name": "My Server", "icon": "", "owner": True, "permissions": "0"},
    ])

    result = run(token="tok", operation="list_guilds")
    assert result["success"] is True
    assert result["total"] == 1
    assert result["guilds"][0]["name"] == "My Server"


# -- Error handling --

def test_send_message_missing_content():
    result = run(token="tok", operation="send_message", channel_id="ch1")
    assert result["success"] is False
    assert "content" in result["error"].lower()
