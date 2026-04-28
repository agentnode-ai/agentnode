"""Tests for microsoft-365-pack."""

from unittest.mock import MagicMock, patch

from microsoft_365_pack.tool import _headers, run


# -- Pure helper --

def test_headers_format():
    h = _headers("access-tok")
    assert h["Authorization"] == "Bearer access-tok"
    assert h["Content-Type"] == "application/json"


# -- Input validation --

def test_missing_access_token():
    result = run(access_token="", service="mail", operation="list")
    assert result["success"] is False
    assert "access_token" in result["error"]


def test_unknown_service():
    result = run(access_token="tok", service="excel", operation="list")
    assert result["success"] is False
    assert "Unknown service" in result["error"]


def test_unknown_operation():
    result = run(access_token="tok", service="mail", operation="delete_all")
    assert result["success"] is False
    assert "Unknown operation" in result["error"]


def test_mail_send_missing_params():
    result = run(access_token="tok", service="mail", operation="send")
    assert result["success"] is False
    assert "to" in result["error"].lower()


def test_teams_send_missing_ids():
    result = run(access_token="tok", service="teams", operation="send")
    assert result["success"] is False
    assert "team_id" in result["error"]


# -- Slash-style service/operation --

def test_slash_style_routing():
    result = run(access_token="tok", service="mail/send", operation="ignored")
    assert result["success"] is False
    assert "to" in result["error"].lower()


# -- Mocked mail list --

@patch("microsoft_365_pack.tool.httpx.Client")
def test_mail_list(mock_cls):
    mock_client = MagicMock()
    mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_cls.return_value.__exit__ = MagicMock(return_value=False)

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"value": [{
        "id": "m1", "subject": "Hello", "receivedDateTime": "2024-01-01",
        "bodyPreview": "Hi there", "isRead": False,
        "from": {"emailAddress": {"address": "a@b.com"}},
    }]}
    mock_client.get.return_value = mock_resp

    result = run(access_token="tok", service="mail", operation="list")
    assert result["success"] is True
    assert result["total"] == 1
    assert result["messages"][0]["subject"] == "Hello"


# -- Mocked calendar create missing params --

def test_calendar_create_missing_params():
    result = run(access_token="tok", service="calendar", operation="create")
    assert result["success"] is False
    assert "subject" in result["error"].lower()
