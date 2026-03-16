"""Email automation tool using smtplib (send) and imaplib (read/list)."""

from __future__ import annotations

import email
import email.utils
import imaplib
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def _send(
    smtp_host: str,
    smtp_port: int,
    username: str,
    password: str,
    to: str,
    subject: str,
    body: str,
    html: bool = False,
) -> dict:
    """Send an email via SMTP with STARTTLS."""
    if not all([smtp_host, username, password, to, subject]):
        return {"success": False, "error": "Missing required fields: smtp_host, username, password, to, subject"}

    msg = MIMEMultipart("alternative")
    msg["From"] = username
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)

    content_type = "html" if html else "plain"
    msg.attach(MIMEText(body, content_type, "utf-8"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(username, password)
            server.sendmail(username, to.split(","), msg.as_string())
        return {"success": True, "message": f"Email sent to {to}"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _read(
    smtp_host: str,
    username: str,
    password: str,
    folder: str = "INBOX",
    limit: int = 10,
    imap_port: int = 993,
) -> dict:
    """Read emails from an IMAP mailbox."""
    if not all([smtp_host, username, password]):
        return {"success": False, "error": "Missing required fields: smtp_host (imap host), username, password"}

    try:
        conn = imaplib.IMAP4_SSL(smtp_host, imap_port)
        conn.login(username, password)
        conn.select(folder, readonly=True)

        _status, data = conn.search(None, "ALL")
        msg_ids = data[0].split() if data[0] else []

        # Take the most recent messages
        msg_ids = msg_ids[-limit:]
        msg_ids.reverse()

        messages: list[dict] = []
        for mid in msg_ids:
            _status, msg_data = conn.fetch(mid, "(RFC822)")
            if msg_data[0] is None:
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            # Extract plain-text body
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    ctype = part.get_content_type()
                    if ctype == "text/plain":
                        payload = part.get_payload(decode=True)
                        if payload:
                            body = payload.decode(errors="replace")
                        break
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    body = payload.decode(errors="replace")

            messages.append({
                "id": mid.decode(),
                "from": msg.get("From", ""),
                "to": msg.get("To", ""),
                "subject": msg.get("Subject", ""),
                "date": msg.get("Date", ""),
                "body": body[:2000],  # Truncate large bodies
            })

        conn.close()
        conn.logout()

        return {"success": True, "folder": folder, "messages": messages, "total": len(messages)}

    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _list_folders(
    smtp_host: str,
    username: str,
    password: str,
    imap_port: int = 993,
) -> dict:
    """List available IMAP mailbox folders."""
    if not all([smtp_host, username, password]):
        return {"success": False, "error": "Missing required fields: smtp_host (imap host), username, password"}

    try:
        conn = imaplib.IMAP4_SSL(smtp_host, imap_port)
        conn.login(username, password)

        _status, folder_data = conn.list()
        folders: list[str] = []
        if folder_data:
            for item in folder_data:
                if isinstance(item, bytes):
                    # Parse folder name from IMAP LIST response
                    decoded = item.decode(errors="replace")
                    # Format: (\\flags) "delimiter" "name"
                    parts = decoded.rsplit('" ', 1)
                    if len(parts) == 2:
                        fname = parts[1].strip().strip('"')
                        folders.append(fname)
                    else:
                        folders.append(decoded)

        conn.logout()
        return {"success": True, "folders": folders}

    except Exception as exc:
        return {"success": False, "error": str(exc)}


def run(
    operation: str,
    smtp_host: str = "",
    smtp_port: int = 587,
    username: str = "",
    password: str = "",
    **kwargs,
) -> dict:
    """Perform email operations: send, read, or list_folders.

    Args:
        operation: One of 'send', 'read', 'list_folders'.
        smtp_host: SMTP host for sending, IMAP host for reading.
        smtp_port: SMTP port (default 587).
        username: Email account username.
        password: Email account password.
        **kwargs: Additional arguments depending on operation:
            send: to, subject, body, html (bool)
            read: folder (default INBOX), limit (default 10), imap_port (default 993)
            list_folders: imap_port (default 993)

    Returns:
        dict with operation results.
    """
    operation = operation.lower().strip()

    if operation == "send":
        return _send(
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            username=username,
            password=password,
            to=kwargs.get("to", ""),
            subject=kwargs.get("subject", ""),
            body=kwargs.get("body", ""),
            html=kwargs.get("html", False),
        )

    if operation == "read":
        return _read(
            smtp_host=smtp_host,
            username=username,
            password=password,
            folder=kwargs.get("folder", "INBOX"),
            limit=kwargs.get("limit", 10),
            imap_port=kwargs.get("imap_port", 993),
        )

    if operation == "list_folders":
        return _list_folders(
            smtp_host=smtp_host,
            username=username,
            password=password,
            imap_port=kwargs.get("imap_port", 993),
        )

    return {"success": False, "error": f"Unknown operation: {operation}. Use 'send', 'read', or 'list_folders'."}
