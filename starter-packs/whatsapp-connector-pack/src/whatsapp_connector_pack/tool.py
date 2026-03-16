"""WhatsApp connector tool using the WhatsApp Business Cloud API via httpx."""

from __future__ import annotations

import httpx

BASE_URL = "https://graph.facebook.com/v18.0"


def _headers(token: str) -> dict:
    """Build authorization headers for the WhatsApp Cloud API."""
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _send_message(token: str, phone_number_id: str, **kwargs) -> dict:
    """Send a text message to a WhatsApp user."""
    to = kwargs.get("to", "")
    text = kwargs.get("text", "")
    preview_url = kwargs.get("preview_url", False)

    if not to:
        return {"success": False, "error": "Missing required parameter: to"}
    if not text:
        return {"success": False, "error": "Missing required parameter: text"}

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {
            "preview_url": preview_url,
            "body": text,
        },
    }

    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{BASE_URL}/{phone_number_id}/messages",
            headers=_headers(token),
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    messages = data.get("messages", [])
    return {
        "success": True,
        "message_id": messages[0]["id"] if messages else "",
        "messaging_product": data.get("messaging_product", ""),
        "contacts": data.get("contacts", []),
    }


def _send_template(token: str, phone_number_id: str, **kwargs) -> dict:
    """Send a template message to a WhatsApp user."""
    to = kwargs.get("to", "")
    template_name = kwargs.get("template_name", "")
    language_code = kwargs.get("language_code", "en_US")
    components = kwargs.get("components", [])

    if not to:
        return {"success": False, "error": "Missing required parameter: to"}
    if not template_name:
        return {"success": False, "error": "Missing required parameter: template_name"}

    template: dict = {
        "name": template_name,
        "language": {"code": language_code},
    }
    if components:
        template["components"] = components

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "template",
        "template": template,
    }

    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{BASE_URL}/{phone_number_id}/messages",
            headers=_headers(token),
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    messages = data.get("messages", [])
    return {
        "success": True,
        "message_id": messages[0]["id"] if messages else "",
        "messaging_product": data.get("messaging_product", ""),
        "contacts": data.get("contacts", []),
    }


def _get_media(token: str, phone_number_id: str, **kwargs) -> dict:
    """Get media URL and metadata for a media ID."""
    media_id = kwargs.get("media_id", "")

    if not media_id:
        return {"success": False, "error": "Missing required parameter: media_id"}

    with httpx.Client(timeout=30) as client:
        # Get media URL
        resp = client.get(
            f"{BASE_URL}/{media_id}",
            headers=_headers(token),
        )
        resp.raise_for_status()
        data = resp.json()

    return {
        "success": True,
        "media": {
            "id": data.get("id", ""),
            "url": data.get("url", ""),
            "mime_type": data.get("mime_type", ""),
            "sha256": data.get("sha256", ""),
            "file_size": data.get("file_size", 0),
        },
    }


_OPERATIONS = {
    "send_message": _send_message,
    "send_template": _send_template,
    "get_media": _get_media,
}


def run(token: str, phone_number_id: str, operation: str, **kwargs) -> dict:
    """Send messages and templates via the WhatsApp Business Cloud API.

    Args:
        token: WhatsApp Cloud API access token (from Meta Developer Portal).
        phone_number_id: WhatsApp Business phone number ID.
        operation: One of 'send_message', 'send_template', 'get_media'.
        **kwargs: Additional arguments depending on operation:
            send_message: to (str, required), text (str, required), preview_url (bool)
            send_template: to (str, required), template_name (str, required),
                           language_code (str), components (list)
            get_media: media_id (str, required)

    Returns:
        dict with operation results.
    """
    if not token:
        return {"success": False, "error": "Missing required parameter: token"}
    if not phone_number_id:
        return {"success": False, "error": "Missing required parameter: phone_number_id"}

    operation = operation.lower().strip()

    if operation not in _OPERATIONS:
        ops = ", ".join(sorted(_OPERATIONS.keys()))
        return {"success": False, "error": f"Unknown operation: {operation}. Available: {ops}"}

    try:
        return _OPERATIONS[operation](token, phone_number_id, **kwargs)
    except httpx.HTTPStatusError as exc:
        error_body = ""
        try:
            error_body = exc.response.json()
        except Exception:
            error_body = exc.response.text
        return {
            "success": False,
            "error": f"WhatsApp API error ({exc.response.status_code}): {error_body}",
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}
