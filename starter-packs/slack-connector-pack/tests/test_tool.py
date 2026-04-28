"""Tests for slack-connector-pack."""

from unittest.mock import MagicMock, patch

from slack_connector_pack.tool import run


# -- Input validation --

def test_missing_token():
    result = run(token="", operation="list_channels")
    assert result["success"] is False
    assert "token" in result["error"].lower()


def test_unknown_operation():
    result = run(token="xoxb-tok", operation="nuke_channel")
    assert result["success"] is False
    assert "Unknown operation" in result["error"]


def test_send_message_missing_channel():
    result = run(token="xoxb-tok", operation="send_message")
    assert result["success"] is False
    assert "channel" in result["error"].lower()


def test_read_history_missing_channel():
    result = run(token="xoxb-tok", operation="read_history")
    assert result["success"] is False
    assert "channel" in result["error"].lower()


def test_upload_file_missing_channel():
    result = run(token="xoxb-tok", operation="upload_file")
    assert result["success"] is False
    assert "channel" in result["error"].lower()


# -- Mocked send_message --

@patch("slack_connector_pack.tool.WebClient")
def test_send_message(mock_wc_cls):
    mock_client = MagicMock()
    mock_wc_cls.return_value = mock_client
    mock_client.chat_postMessage.return_value = {
        "channel": "C123", "ts": "1234567890.123", "text": "hello",
    }

    result = run(token="xoxb-tok", operation="send_message", channel="C123", text="hello")
    assert result["success"] is True
    assert result["message"]["channel"] == "C123"
    assert result["message"]["ts"] == "1234567890.123"


# -- Mocked send_message missing text --

@patch("slack_connector_pack.tool.WebClient")
def test_send_message_missing_text(mock_wc_cls):
    mock_client = MagicMock()
    mock_wc_cls.return_value = mock_client

    result = run(token="xoxb-tok", operation="send_message", channel="C123")
    assert result["success"] is False
    assert "text" in result["error"].lower()


# -- Mocked list_channels --

@patch("slack_connector_pack.tool.WebClient")
def test_list_channels(mock_wc_cls):
    mock_client = MagicMock()
    mock_wc_cls.return_value = mock_client
    mock_client.conversations_list.return_value = {
        "channels": [{
            "id": "C123", "name": "general", "is_private": False,
            "is_archived": False, "num_members": 50,
            "topic": {"value": "General chat"},
            "purpose": {"value": "Team discussions"},
        }],
    }

    result = run(token="xoxb-tok", operation="list_channels")
    assert result["success"] is True
    assert result["total"] == 1
    assert result["channels"][0]["name"] == "general"


# -- Mocked read_history --

@patch("slack_connector_pack.tool.WebClient")
def test_read_history(mock_wc_cls):
    mock_client = MagicMock()
    mock_wc_cls.return_value = mock_client
    mock_client.conversations_history.return_value = {
        "messages": [
            {"ts": "123", "user": "U1", "text": "hi", "type": "message",
             "thread_ts": "", "reply_count": 0},
        ],
        "has_more": False,
    }

    result = run(token="xoxb-tok", operation="read_history", channel="C123")
    assert result["success"] is True
    assert result["total"] == 1
    assert result["messages"][0]["text"] == "hi"
