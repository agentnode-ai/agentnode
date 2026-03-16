"""Google Workspace tool using google-api-python-client."""

from __future__ import annotations

import base64
import json
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


def _build_credentials(credentials_json: str):
    """Build Google credentials from a JSON string.

    Supports both service account keys and OAuth2 tokens.
    """
    creds_data = json.loads(credentials_json)

    # Service account credentials
    if creds_data.get("type") == "service_account":
        scopes = [
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = service_account.Credentials.from_service_account_info(
            creds_data, scopes=scopes
        )
        subject = creds_data.get("delegated_user")
        if subject:
            creds = creds.with_subject(subject)
        return creds

    # OAuth2 user credentials (from token.json)
    creds = Credentials(
        token=creds_data.get("token"),
        refresh_token=creds_data.get("refresh_token"),
        token_uri=creds_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=creds_data.get("client_id"),
        client_secret=creds_data.get("client_secret"),
        scopes=creds_data.get("scopes"),
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds


# ---------- Gmail ----------

def _gmail_send(creds, **kwargs) -> dict:
    """Send an email via Gmail API."""
    to = kwargs.get("to", "")
    subject = kwargs.get("subject", "")
    body = kwargs.get("body", "")
    html = kwargs.get("html", False)

    if not to or not subject:
        return {"success": False, "error": "Missing required parameters: to, subject"}

    msg = MIMEMultipart("alternative")
    msg["To"] = to
    msg["Subject"] = subject
    sender = kwargs.get("from", "me")
    msg["From"] = sender

    content_type = "html" if html else "plain"
    msg.attach(MIMEText(body, content_type, "utf-8"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

    service = build("gmail", "v1", credentials=creds)
    sent = service.users().messages().send(
        userId="me", body={"raw": raw}
    ).execute()

    return {
        "success": True,
        "message": {
            "id": sent["id"],
            "threadId": sent.get("threadId", ""),
            "labelIds": sent.get("labelIds", []),
        },
    }


def _gmail_read(creds, **kwargs) -> dict:
    """Read emails from Gmail."""
    query = kwargs.get("query", "in:inbox")
    max_results = kwargs.get("max_results", 10)

    service = build("gmail", "v1", credentials=creds)
    results = service.users().messages().list(
        userId="me", q=query, maxResults=max_results
    ).execute()

    messages = []
    for msg_ref in results.get("messages", []):
        msg = service.users().messages().get(
            userId="me", id=msg_ref["id"], format="metadata",
            metadataHeaders=["From", "To", "Subject", "Date"],
        ).execute()

        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        messages.append({
            "id": msg["id"],
            "threadId": msg.get("threadId", ""),
            "snippet": msg.get("snippet", ""),
            "from": headers.get("From", ""),
            "to": headers.get("To", ""),
            "subject": headers.get("Subject", ""),
            "date": headers.get("Date", ""),
            "labelIds": msg.get("labelIds", []),
        })

    return {"success": True, "messages": messages, "total": len(messages)}


# ---------- Calendar ----------

def _calendar_list_events(creds, **kwargs) -> dict:
    """List upcoming calendar events."""
    calendar_id = kwargs.get("calendar_id", "primary")
    max_results = kwargs.get("max_results", 10)
    time_min = kwargs.get("time_min", None)
    time_max = kwargs.get("time_max", None)

    service = build("calendar", "v3", credentials=creds)
    list_kwargs: dict = {
        "calendarId": calendar_id,
        "maxResults": max_results,
        "singleEvents": True,
        "orderBy": "startTime",
    }
    if time_min:
        list_kwargs["timeMin"] = time_min
    if time_max:
        list_kwargs["timeMax"] = time_max

    results = service.events().list(**list_kwargs).execute()
    events = []
    for ev in results.get("items", []):
        events.append({
            "id": ev["id"],
            "summary": ev.get("summary", ""),
            "description": ev.get("description", ""),
            "start": ev.get("start", {}),
            "end": ev.get("end", {}),
            "location": ev.get("location", ""),
            "status": ev.get("status", ""),
            "attendees": [
                {"email": a.get("email", ""), "responseStatus": a.get("responseStatus", "")}
                for a in ev.get("attendees", [])
            ],
            "htmlLink": ev.get("htmlLink", ""),
        })
    return {"success": True, "events": events, "total": len(events)}


def _calendar_create_event(creds, **kwargs) -> dict:
    """Create a new calendar event."""
    summary = kwargs.get("summary", "")
    start = kwargs.get("start", "")
    end = kwargs.get("end", "")
    description = kwargs.get("description", "")
    location = kwargs.get("location", "")
    attendees = kwargs.get("attendees", [])
    calendar_id = kwargs.get("calendar_id", "primary")
    timezone = kwargs.get("timezone", "UTC")

    if not summary or not start or not end:
        return {"success": False, "error": "Missing required parameters: summary, start, end"}

    event_body: dict = {
        "summary": summary,
        "description": description,
        "location": location,
    }

    # Support both dateTime and date formats
    if "T" in start:
        event_body["start"] = {"dateTime": start, "timeZone": timezone}
        event_body["end"] = {"dateTime": end, "timeZone": timezone}
    else:
        event_body["start"] = {"date": start}
        event_body["end"] = {"date": end}

    if attendees:
        event_body["attendees"] = [{"email": a} for a in attendees]

    service = build("calendar", "v3", credentials=creds)
    event = service.events().insert(
        calendarId=calendar_id, body=event_body
    ).execute()

    return {
        "success": True,
        "event": {
            "id": event["id"],
            "summary": event.get("summary", ""),
            "htmlLink": event.get("htmlLink", ""),
            "start": event.get("start", {}),
            "end": event.get("end", {}),
        },
    }


# ---------- Drive ----------

def _drive_list_files(creds, **kwargs) -> dict:
    """List files in Google Drive."""
    query = kwargs.get("query", "")
    page_size = kwargs.get("page_size", 20)
    folder_id = kwargs.get("folder_id", "")
    order_by = kwargs.get("order_by", "modifiedTime desc")

    service = build("drive", "v3", credentials=creds)
    q_parts = []
    if query:
        q_parts.append(f"name contains '{query}'")
    if folder_id:
        q_parts.append(f"'{folder_id}' in parents")
    q_parts.append("trashed = false")

    q = " and ".join(q_parts)

    results = service.files().list(
        q=q,
        pageSize=page_size,
        orderBy=order_by,
        fields="files(id,name,mimeType,size,modifiedTime,createdTime,webViewLink,parents)",
    ).execute()

    files = []
    for f in results.get("files", []):
        files.append({
            "id": f["id"],
            "name": f["name"],
            "mimeType": f.get("mimeType", ""),
            "size": f.get("size", ""),
            "modifiedTime": f.get("modifiedTime", ""),
            "createdTime": f.get("createdTime", ""),
            "webViewLink": f.get("webViewLink", ""),
            "parents": f.get("parents", []),
        })
    return {"success": True, "files": files, "total": len(files)}


def _drive_upload(creds, **kwargs) -> dict:
    """Upload a file to Google Drive."""
    file_path = kwargs.get("file_path", "")
    name = kwargs.get("name", "")
    mime_type = kwargs.get("mime_type", "application/octet-stream")
    folder_id = kwargs.get("folder_id", "")

    if not file_path:
        return {"success": False, "error": "Missing required parameter: file_path"}

    if not name:
        import os
        name = os.path.basename(file_path)

    file_metadata: dict = {"name": name}
    if folder_id:
        file_metadata["parents"] = [folder_id]

    service = build("drive", "v3", credentials=creds)
    media = MediaFileUpload(file_path, mimetype=mime_type)
    uploaded = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id,name,mimeType,size,webViewLink",
    ).execute()

    return {
        "success": True,
        "file": {
            "id": uploaded["id"],
            "name": uploaded["name"],
            "mimeType": uploaded.get("mimeType", ""),
            "size": uploaded.get("size", ""),
            "webViewLink": uploaded.get("webViewLink", ""),
        },
    }


_SERVICE_OPS = {
    "gmail": {
        "send": _gmail_send,
        "read": _gmail_read,
    },
    "calendar": {
        "list_events": _calendar_list_events,
        "create_event": _calendar_create_event,
    },
    "drive": {
        "list_files": _drive_list_files,
        "upload": _drive_upload,
    },
}


def run(credentials_json: str, service: str, operation: str, **kwargs) -> dict:
    """Interact with Gmail, Google Calendar, and Google Drive.

    Args:
        credentials_json: JSON string of Google credentials (service account key or
                          OAuth2 token file contents).
        service: One of 'gmail', 'calendar', 'drive'.
        operation: Operation within the service:
            gmail: 'send' (to, subject, body, html) | 'read' (query, max_results)
            calendar: 'list_events' (calendar_id, max_results, time_min, time_max) |
                      'create_event' (summary, start, end, description, location, attendees)
            drive: 'list_files' (query, page_size, folder_id) |
                   'upload' (file_path, name, mime_type, folder_id)
        **kwargs: Additional arguments depending on service/operation.

    Returns:
        dict with operation results.
    """
    if not credentials_json:
        return {"success": False, "error": "Missing required parameter: credentials_json"}

    service = service.lower().strip()
    operation = operation.lower().strip()

    # Support "gmail/send" style
    if "/" in service:
        parts = service.split("/", 1)
        service = parts[0]
        operation = parts[1]

    if service not in _SERVICE_OPS:
        return {
            "success": False,
            "error": f"Unknown service: {service}. Available: {', '.join(sorted(_SERVICE_OPS.keys()))}",
        }

    ops = _SERVICE_OPS[service]
    if operation not in ops:
        return {
            "success": False,
            "error": f"Unknown operation '{operation}' for service '{service}'. Available: {', '.join(sorted(ops.keys()))}",
        }

    try:
        creds = _build_credentials(credentials_json)
        return ops[operation](creds, **kwargs)
    except json.JSONDecodeError:
        return {"success": False, "error": "Invalid credentials_json: not valid JSON"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
