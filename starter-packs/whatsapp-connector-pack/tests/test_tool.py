"""Tests for whatsapp-connector-pack."""

from unittest.mock import MagicMock, patch

from whatsapp_connector_pack.tool import _headers, run


# -- Pure helper --

def test_headers_format():
    h = _headers("tok123")
    assert h["Authorization"] == "Bearer tok123"
    assert h["Content-Type"] == "application/json"


# -- Input validation --

def test_missing_token():
    result = run(token="", phone_number_id="pn1", operation="send_message")
    assert result["success"] is False
    assert "token" in result["error"].lower()


def test_missing_phone_number_id():
    result = run(token="tok", phone_number_id="", operation="send_message")
    assert result["success"] is False
    assert "phone_number_id" in result["error"]


def test_unknown_operation():
    result = run(token="tok", phone_number_id="pn1", operation="delete_chat")
    assert result["success"] is False
    assert "Unknown operation" in result["error"]


def test_send_message_missing_to():
    result = run(token="tok", phone_number_id="pn1", operation="send_message", text="hi")
    assert result["success"] is False
    assert "to" in result["error"].lower()


def test_send_message_missing_text():
    result = run(token="tok", phone_number_id="pn1", operation="send_message", to="+1234")
    assert result["success"] is False
    assert "text" in result["error"].lower()


def test_send_template_missing_template():
    result = run(token="tok", phone_number_id="pn1", operation="send_template", to="+1234")
    assert result["success"] is False
    assert "template_name" in result["error"]


def test_get_media_missing_id():
    result = run(token="tok", phone_number_id="pn1", operation="get_media")
    assert result["success"] is False
    assert "media_id" in result["error"]


# -- Mocked send_message --

@patch("whatsapp_connector_pack.tool.httpx.Client")
def test_send_message_success(mock_cls):
    mock_client = MagicMock()
    mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_cls.return_value.__exit__ = MagicMock(return_value=False)

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "messaging_product": "whatsapp",
        "messages": [{"id": "wamid.123"}],
        "contacts": [{"wa_id": "+1234"}],
    }
    mock_client.post.return_value = mock_resp

    result = run(token="tok", phone_number_id="pn1", operation="send_message",
                 to="+1234", text="Hello")
    assert result["success"] is True
    assert result["message_id"] == "wamid.123"


# -- Mocked get_media --

@patch("whatsapp_connector_pack.tool.httpx.Client")
def test_get_media_success(mock_cls):
    mock_client = MagicMock()
    mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_cls.return_value.__exit__ = MagicMock(return_value=False)

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "id": "m1", "url": "https://media.example.com/m1",
        "mime_type": "image/jpeg", "sha256": "abc", "file_size": 1024,
    }
    mock_client.get.return_value = mock_resp

    result = run(token="tok", phone_number_id="pn1", operation="get_media", media_id="m1")
    assert result["success"] is True
    assert result["media"]["mime_type"] == "image/jpeg"
