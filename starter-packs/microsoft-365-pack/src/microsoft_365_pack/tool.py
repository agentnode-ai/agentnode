"""Microsoft 365 tool using the Microsoft Graph API via httpx."""

from __future__ import annotations

import httpx

BASE_URL = "https://graph.microsoft.com/v1.0"


def _headers(access_token: str) -> dict:
    """Build authorization headers for Microsoft Graph API."""
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }


# ---------- Mail ----------

def _mail_send(access_token: str, **kwargs) -> dict:
    """Send an email via Outlook."""
    to = kwargs.get("to", "")
    subject = kwargs.get("subject", "")
    body = kwargs.get("body", "")
    content_type = kwargs.get("content_type", "Text")  # "Text" or "HTML"
    cc = kwargs.get("cc", [])
    save_to_sent = kwargs.get("save_to_sent", True)

    if not to or not subject:
        return {"success": False, "error": "Missing required parameters: to, subject"}

    to_recipients = [{"emailAddress": {"address": addr.strip()}} for addr in to.split(",")]
    cc_recipients = [{"emailAddress": {"address": addr.strip()}} for addr in cc] if cc else []

    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": content_type, "content": body},
            "toRecipients": to_recipients,
        },
        "saveToSentItems": save_to_sent,
    }
    if cc_recipients:
        payload["message"]["ccRecipients"] = cc_recipients

    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{BASE_URL}/me/sendMail",
            headers=_headers(access_token),
            json=payload,
        )
        resp.raise_for_status()

    return {"success": True, "message": f"Email sent to {to}"}


def _mail_list(access_token: str, **kwargs) -> dict:
    """List emails from Outlook inbox."""
    top = kwargs.get("top", 10)
    folder = kwargs.get("folder", "inbox")
    filter_str = kwargs.get("filter", "")
    select = kwargs.get("select", "id,subject,from,receivedDateTime,bodyPreview,isRead")

    params: dict = {
        "$top": top,
        "$select": select,
        "$orderby": "receivedDateTime desc",
    }
    if filter_str:
        params["$filter"] = filter_str

    with httpx.Client(timeout=30) as client:
        resp = client.get(
            f"{BASE_URL}/me/mailFolders/{folder}/messages",
            headers=_headers(access_token),
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()

    messages = []
    for msg in data.get("value", []):
        messages.append({
            "id": msg.get("id", ""),
            "subject": msg.get("subject", ""),
            "from": msg.get("from", {}).get("emailAddress", {}).get("address", ""),
            "receivedDateTime": msg.get("receivedDateTime", ""),
            "bodyPreview": msg.get("bodyPreview", ""),
            "isRead": msg.get("isRead", False),
        })
    return {"success": True, "messages": messages, "total": len(messages)}


# ---------- Calendar ----------

def _calendar_list(access_token: str, **kwargs) -> dict:
    """List calendar events."""
    top = kwargs.get("top", 10)
    start_datetime = kwargs.get("start_datetime", None)
    end_datetime = kwargs.get("end_datetime", None)

    if start_datetime and end_datetime:
        # Use calendarView for a time range
        params: dict = {
            "startDateTime": start_datetime,
            "endDateTime": end_datetime,
            "$top": top,
            "$orderby": "start/dateTime",
            "$select": "id,subject,start,end,location,organizer,bodyPreview,isAllDay",
        }
        url = f"{BASE_URL}/me/calendarView"
    else:
        params = {
            "$top": top,
            "$orderby": "start/dateTime",
            "$select": "id,subject,start,end,location,organizer,bodyPreview,isAllDay",
        }
        url = f"{BASE_URL}/me/events"

    with httpx.Client(timeout=30) as client:
        resp = client.get(url, headers=_headers(access_token), params=params)
        resp.raise_for_status()
        data = resp.json()

    events = []
    for ev in data.get("value", []):
        events.append({
            "id": ev.get("id", ""),
            "subject": ev.get("subject", ""),
            "start": ev.get("start", {}),
            "end": ev.get("end", {}),
            "location": ev.get("location", {}).get("displayName", ""),
            "organizer": ev.get("organizer", {}).get("emailAddress", {}).get("address", ""),
            "bodyPreview": ev.get("bodyPreview", ""),
            "isAllDay": ev.get("isAllDay", False),
        })
    return {"success": True, "events": events, "total": len(events)}


def _calendar_create(access_token: str, **kwargs) -> dict:
    """Create a calendar event."""
    subject = kwargs.get("subject", "")
    start = kwargs.get("start", "")
    end = kwargs.get("end", "")
    timezone = kwargs.get("timezone", "UTC")
    body = kwargs.get("body", "")
    location = kwargs.get("location", "")
    attendees = kwargs.get("attendees", [])
    is_all_day = kwargs.get("is_all_day", False)

    if not subject or not start or not end:
        return {"success": False, "error": "Missing required parameters: subject, start, end"}

    payload: dict = {
        "subject": subject,
        "start": {"dateTime": start, "timeZone": timezone},
        "end": {"dateTime": end, "timeZone": timezone},
        "isAllDay": is_all_day,
    }
    if body:
        payload["body"] = {"contentType": "Text", "content": body}
    if location:
        payload["location"] = {"displayName": location}
    if attendees:
        payload["attendees"] = [
            {
                "emailAddress": {"address": a},
                "type": "required",
            }
            for a in attendees
        ]

    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{BASE_URL}/me/events",
            headers=_headers(access_token),
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    return {
        "success": True,
        "event": {
            "id": data.get("id", ""),
            "subject": data.get("subject", ""),
            "start": data.get("start", {}),
            "end": data.get("end", {}),
            "webLink": data.get("webLink", ""),
        },
    }


# ---------- OneDrive ----------

def _onedrive_list(access_token: str, **kwargs) -> dict:
    """List files and folders in OneDrive."""
    folder_path = kwargs.get("folder_path", "root")
    top = kwargs.get("top", 20)

    if folder_path == "root" or not folder_path:
        url = f"{BASE_URL}/me/drive/root/children"
    else:
        url = f"{BASE_URL}/me/drive/root:/{folder_path}:/children"

    params: dict = {
        "$top": top,
        "$select": "id,name,size,file,folder,lastModifiedDateTime,webUrl,createdDateTime",
    }

    with httpx.Client(timeout=30) as client:
        resp = client.get(url, headers=_headers(access_token), params=params)
        resp.raise_for_status()
        data = resp.json()

    items = []
    for item in data.get("value", []):
        entry: dict = {
            "id": item.get("id", ""),
            "name": item.get("name", ""),
            "size": item.get("size", 0),
            "lastModifiedDateTime": item.get("lastModifiedDateTime", ""),
            "createdDateTime": item.get("createdDateTime", ""),
            "webUrl": item.get("webUrl", ""),
            "type": "folder" if "folder" in item else "file",
        }
        if "file" in item:
            entry["mimeType"] = item["file"].get("mimeType", "")
        if "folder" in item:
            entry["childCount"] = item["folder"].get("childCount", 0)
        items.append(entry)

    return {"success": True, "items": items, "total": len(items)}


# ---------- Teams ----------

def _teams_send(access_token: str, **kwargs) -> dict:
    """Send a message to a Microsoft Teams channel."""
    team_id = kwargs.get("team_id", "")
    channel_id = kwargs.get("channel_id", "")
    content = kwargs.get("content", "")
    content_type = kwargs.get("content_type", "text")  # "text" or "html"

    if not team_id or not channel_id:
        return {"success": False, "error": "Missing required parameters: team_id, channel_id"}
    if not content:
        return {"success": False, "error": "Missing required parameter: content"}

    payload = {
        "body": {
            "contentType": content_type,
            "content": content,
        },
    }

    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{BASE_URL}/teams/{team_id}/channels/{channel_id}/messages",
            headers=_headers(access_token),
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    return {
        "success": True,
        "message": {
            "id": data.get("id", ""),
            "createdDateTime": data.get("createdDateTime", ""),
            "webUrl": data.get("webUrl", ""),
        },
    }


_SERVICE_OPS = {
    "mail": {
        "send": _mail_send,
        "list": _mail_list,
    },
    "calendar": {
        "list": _calendar_list,
        "create": _calendar_create,
    },
    "onedrive": {
        "list": _onedrive_list,
    },
    "teams": {
        "send": _teams_send,
    },
}


def run(access_token: str, service: str, operation: str, **kwargs) -> dict:
    """Interact with Outlook Mail, Calendar, OneDrive, and Teams via Microsoft Graph.

    Args:
        access_token: Microsoft Graph API access token (OAuth2 Bearer token).
        service: One of 'mail', 'calendar', 'onedrive', 'teams'.
        operation: Operation within the service:
            mail: 'send' (to, subject, body, content_type, cc) | 'list' (top, folder, filter)
            calendar: 'list' (top, start_datetime, end_datetime) |
                      'create' (subject, start, end, timezone, body, location, attendees)
            onedrive: 'list' (folder_path, top)
            teams: 'send' (team_id, channel_id, content, content_type)
        **kwargs: Additional arguments depending on service/operation.

    Returns:
        dict with operation results.
    """
    if not access_token:
        return {"success": False, "error": "Missing required parameter: access_token"}

    service = service.lower().strip()
    operation = operation.lower().strip()

    # Support "mail/send" style
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
        return ops[operation](access_token, **kwargs)
    except httpx.HTTPStatusError as exc:
        error_body = ""
        try:
            error_body = exc.response.json()
        except Exception:
            error_body = exc.response.text
        return {
            "success": False,
            "error": f"Microsoft Graph API error ({exc.response.status_code}): {error_body}",
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}
