"""Tests for telegram-connector-pack."""

from unittest.mock import MagicMock, patch

from telegram_connector_pack.tool import _api_url, _check_response, run


# -- Pure helpers --

def test_api_url():
    url = _api_url("123:ABC", "sendMessage")
    assert url == "https://api.telegram.org/bot123:ABC/sendMessage"


def test_check_response_ok():
    assert _check_response({"ok": True})["success"] is True


def test_check_response_error():
    result = _check_response({"ok": False, "error_code": 400, "description": "Bad Request"})
    assert result["success"] is False
    assert "400" in result["error"]


# -- Input validation --

def test_missing_token():
    result = run(token="", operation="get_me")
    assert result["success"] is False
    assert "token" in result["error"].lower()


def test_unknown_operation():
    result = run(token="tok", operation="ban_user")
    assert result["success"] is False
    assert "Unknown operation" in result["error"]


def test_send_message_missing_chat_id():
    result = run(token="tok", operation="send_message", chat_id="")
    assert result["success"] is False
    assert "chat_id" in result["error"]


# -- Mocked get_me --

@patch("telegram_connector_pack.tool.httpx.Client")
def test_get_me(mock_cls):
    mock_client = MagicMock()
    mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_cls.return_value.__exit__ = MagicMock(return_value=False)

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "ok": True,
        "result": {
            "id": 12345, "is_bot": True, "first_name": "TestBot",
            "username": "test_bot", "can_join_groups": True,
            "can_read_all_group_messages": False,
            "supports_inline_queries": False,
        },
    }
    mock_client.get.return_value = mock_resp

    result = run(token="tok", operation="get_me")
    assert result["success"] is True
    assert result["bot"]["username"] == "test_bot"


# -- Mocked send_message --

@patch("telegram_connector_pack.tool.httpx.Client")
def test_send_message(mock_cls):
    mock_client = MagicMock()
    mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_cls.return_value.__exit__ = MagicMock(return_value=False)

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "ok": True,
        "result": {"message_id": 1, "chat": {"id": 99}, "text": "hi", "date": 170000},
    }
    mock_client.post.return_value = mock_resp

    result = run(token="tok", operation="send_message", chat_id="99", text="hi")
    assert result["success"] is True
    assert result["message"]["message_id"] == 1


# -- Error: send_message missing text --

def test_send_message_missing_text():
    result = run(token="tok", operation="send_message", chat_id="99")
    assert result["success"] is False
    assert "text" in result["error"].lower()
