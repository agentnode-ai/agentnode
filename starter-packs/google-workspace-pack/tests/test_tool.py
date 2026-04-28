"""Tests for google-workspace-pack."""

import json
from unittest.mock import MagicMock, patch

from google_workspace_pack.tool import run


# -- Input validation --

def test_missing_credentials():
    result = run(credentials_json="", service="gmail", operation="read")
    assert result["success"] is False
    assert "credentials_json" in result["error"]


def test_invalid_json():
    result = run(credentials_json="not-json", service="gmail", operation="read")
    assert result["success"] is False
    assert "not valid JSON" in result["error"]


def test_unknown_service():
    creds = json.dumps({"type": "service_account", "project_id": "p"})
    result = run(credentials_json=creds, service="sheets", operation="read")
    assert result["success"] is False
    assert "Unknown service" in result["error"]


def test_unknown_operation():
    creds = json.dumps({"type": "service_account", "project_id": "p"})
    result = run(credentials_json=creds, service="gmail", operation="destroy")
    assert result["success"] is False
    assert "Unknown operation" in result["error"]


def test_slash_style_routing():
    creds = json.dumps({"type": "service_account", "project_id": "p"})
    result = run(credentials_json=creds, service="gmail/destroy", operation="ignored")
    assert result["success"] is False
    assert "Unknown operation" in result["error"]


# -- Gmail send missing params --

@patch("google_workspace_pack.tool._build_credentials")
def test_gmail_send_missing_to(mock_creds):
    mock_creds.return_value = MagicMock()
    creds = json.dumps({"token": "tok"})
    result = run(credentials_json=creds, service="gmail", operation="send")
    assert result["success"] is False
    assert "to" in result["error"].lower()


# -- Calendar create missing params --

@patch("google_workspace_pack.tool._build_credentials")
def test_calendar_create_missing_params(mock_creds):
    mock_creds.return_value = MagicMock()
    creds = json.dumps({"token": "tok"})
    result = run(credentials_json=creds, service="calendar", operation="create_event")
    assert result["success"] is False
    assert "summary" in result["error"].lower()


# -- Drive upload missing file_path --

@patch("google_workspace_pack.tool._build_credentials")
def test_drive_upload_missing_file(mock_creds):
    mock_creds.return_value = MagicMock()
    creds = json.dumps({"token": "tok"})
    result = run(credentials_json=creds, service="drive", operation="upload")
    assert result["success"] is False
    assert "file_path" in result["error"]


# -- Mocked Gmail read --

@patch("google_workspace_pack.tool.build")
@patch("google_workspace_pack.tool._build_credentials")
def test_gmail_read(mock_creds, mock_build):
    mock_creds.return_value = MagicMock()
    creds = json.dumps({"token": "tok"})

    mock_service = MagicMock()
    mock_build.return_value = mock_service

    mock_service.users().messages().list().execute.return_value = {
        "messages": [{"id": "m1"}],
    }
    mock_service.users().messages().get().execute.return_value = {
        "id": "m1", "threadId": "t1", "snippet": "Hello",
        "labelIds": ["INBOX"],
        "payload": {"headers": [
            {"name": "From", "value": "a@b.com"},
            {"name": "To", "value": "c@d.com"},
            {"name": "Subject", "value": "Test"},
            {"name": "Date", "value": "2024-01-01"},
        ]},
    }

    result = run(credentials_json=creds, service="gmail", operation="read")
    assert result["success"] is True
    assert result["total"] == 1
    assert result["messages"][0]["id"] == "m1"
