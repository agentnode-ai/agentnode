"""Tests for email-automation-pack."""

from unittest.mock import MagicMock, patch

from email_automation_pack.tool import run


# -- Input validation --

def test_unknown_operation():
    result = run(operation="forward")
    assert result["success"] is False
    assert "Unknown operation" in result["error"]


def test_send_missing_fields():
    result = run(operation="send")
    assert result["success"] is False
    assert "Missing required" in result["error"]


def test_read_missing_fields():
    result = run(operation="read")
    assert result["success"] is False
    assert "Missing required" in result["error"]


def test_list_folders_missing_fields():
    result = run(operation="list_folders")
    assert result["success"] is False
    assert "Missing required" in result["error"]


# -- Mocked send --

@patch("email_automation_pack.tool.smtplib.SMTP")
def test_send_email(mock_smtp_cls):
    mock_server = MagicMock()
    mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
    mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

    result = run(
        operation="send",
        smtp_host="smtp.example.com",
        smtp_port=587,
        username="user@example.com",
        password="pass",
        to="recipient@example.com",
        subject="Test",
        body="Hello",
    )
    assert result["success"] is True
    assert "Email sent" in result["message"]


# -- Mocked read --

@patch("email_automation_pack.tool.imaplib.IMAP4_SSL")
def test_read_emails(mock_imap_cls):
    mock_conn = MagicMock()
    mock_imap_cls.return_value = mock_conn

    mock_conn.login.return_value = ("OK", [])
    mock_conn.select.return_value = ("OK", [])
    mock_conn.search.return_value = ("OK", [b"1"])

    raw_email = (
        b"From: sender@example.com\r\n"
        b"To: user@example.com\r\n"
        b"Subject: Test Email\r\n"
        b"Date: Mon, 01 Jan 2024 00:00:00 +0000\r\n"
        b"\r\n"
        b"Hello world"
    )
    mock_conn.fetch.return_value = ("OK", [(b"1", raw_email)])
    mock_conn.close.return_value = ("OK", [])
    mock_conn.logout.return_value = ("OK", [])

    result = run(
        operation="read",
        smtp_host="imap.example.com",
        username="user@example.com",
        password="pass",
    )
    assert result["success"] is True
    assert result["total"] == 1
    assert result["messages"][0]["subject"] == "Test Email"


# -- Mocked list_folders --

@patch("email_automation_pack.tool.imaplib.IMAP4_SSL")
def test_list_folders(mock_imap_cls):
    mock_conn = MagicMock()
    mock_imap_cls.return_value = mock_conn
    mock_conn.login.return_value = ("OK", [])
    mock_conn.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "INBOX"'])
    mock_conn.logout.return_value = ("OK", [])

    result = run(
        operation="list_folders",
        smtp_host="imap.example.com",
        username="user@example.com",
        password="pass",
    )
    assert result["success"] is True
    assert "INBOX" in result["folders"]
