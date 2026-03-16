"""Telegram connector tool using the Telegram Bot API via httpx."""

from __future__ import annotations

import httpx

BASE_URL = "https://api.telegram.org/bot{token}"


def _api_url(token: str, method: str) -> str:
    """Build the full Telegram Bot API URL for a method."""
    return f"{BASE_URL.format(token=token)}/{method}"


def _check_response(data: dict) -> dict:
    """Check a Telegram API response and raise on error."""
    if not data.get("ok", False):
        return {
            "success": False,
            "error": f"Telegram API error ({data.get('error_code', '?')}): {data.get('description', 'Unknown error')}",
        }
    return {"success": True}


def _send_message(token: str, chat_id: str, **kwargs) -> dict:
    """Send a text message to a chat."""
    text = kwargs.get("text", "")
    parse_mode = kwargs.get("parse_mode", None)
    reply_to_message_id = kwargs.get("reply_to_message_id", None)

    if not text:
        return {"success": False, "error": "Missing required parameter: text"}

    payload: dict = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id

    with httpx.Client(timeout=30) as client:
        resp = client.post(_api_url(token, "sendMessage"), json=payload)
        resp.raise_for_status()
        data = resp.json()

    check = _check_response(data)
    if not check["success"]:
        return check

    msg = data["result"]
    return {
        "success": True,
        "message": {
            "message_id": msg["message_id"],
            "chat_id": msg["chat"]["id"],
            "text": msg.get("text", ""),
            "date": msg.get("date", 0),
        },
    }


def _get_updates(token: str, **kwargs) -> dict:
    """Get recent updates (messages) sent to the bot."""
    limit = kwargs.get("limit", 20)
    offset = kwargs.get("offset", None)
    timeout_secs = kwargs.get("timeout", 0)

    payload: dict = {"limit": limit, "timeout": timeout_secs}
    if offset is not None:
        payload["offset"] = offset

    with httpx.Client(timeout=max(30, timeout_secs + 5)) as client:
        resp = client.post(_api_url(token, "getUpdates"), json=payload)
        resp.raise_for_status()
        data = resp.json()

    check = _check_response(data)
    if not check["success"]:
        return check

    updates = []
    for upd in data["result"]:
        entry: dict = {
            "update_id": upd["update_id"],
        }
        if "message" in upd:
            msg = upd["message"]
            entry["message"] = {
                "message_id": msg["message_id"],
                "from": {
                    "id": msg.get("from", {}).get("id", ""),
                    "first_name": msg.get("from", {}).get("first_name", ""),
                    "username": msg.get("from", {}).get("username", ""),
                },
                "chat_id": msg["chat"]["id"],
                "chat_type": msg["chat"].get("type", ""),
                "text": msg.get("text", ""),
                "date": msg.get("date", 0),
            }
        updates.append(entry)

    return {"success": True, "updates": updates, "total": len(updates)}


def _send_photo(token: str, chat_id: str, **kwargs) -> dict:
    """Send a photo to a chat by URL or file_id."""
    photo_url = kwargs.get("photo_url", "")
    caption = kwargs.get("caption", "")
    parse_mode = kwargs.get("parse_mode", None)

    if not photo_url:
        return {"success": False, "error": "Missing required parameter: photo_url"}

    payload: dict = {"chat_id": chat_id, "photo": photo_url}
    if caption:
        payload["caption"] = caption
    if parse_mode:
        payload["parse_mode"] = parse_mode

    with httpx.Client(timeout=30) as client:
        resp = client.post(_api_url(token, "sendPhoto"), json=payload)
        resp.raise_for_status()
        data = resp.json()

    check = _check_response(data)
    if not check["success"]:
        return check

    msg = data["result"]
    return {
        "success": True,
        "message": {
            "message_id": msg["message_id"],
            "chat_id": msg["chat"]["id"],
            "date": msg.get("date", 0),
            "photo_sizes": len(msg.get("photo", [])),
            "caption": msg.get("caption", ""),
        },
    }


def _get_me(token: str, **kwargs) -> dict:
    """Get information about the bot."""
    with httpx.Client(timeout=30) as client:
        resp = client.get(_api_url(token, "getMe"))
        resp.raise_for_status()
        data = resp.json()

    check = _check_response(data)
    if not check["success"]:
        return check

    bot = data["result"]
    return {
        "success": True,
        "bot": {
            "id": bot["id"],
            "is_bot": bot.get("is_bot", True),
            "first_name": bot.get("first_name", ""),
            "username": bot.get("username", ""),
            "can_join_groups": bot.get("can_join_groups", False),
            "can_read_all_group_messages": bot.get("can_read_all_group_messages", False),
            "supports_inline_queries": bot.get("supports_inline_queries", False),
        },
    }


_OPERATIONS = {
    "send_message": (_send_message, True),
    "get_updates": (_get_updates, False),
    "send_photo": (_send_photo, True),
    "get_me": (_get_me, False),
}


def run(token: str, operation: str, chat_id: str = "", **kwargs) -> dict:
    """Send messages, photos, and receive updates via the Telegram Bot API.

    Args:
        token: Telegram Bot API token (from @BotFather).
        operation: One of 'send_message', 'get_updates', 'send_photo', 'get_me'.
        chat_id: Telegram chat ID (required for send_message, send_photo).
        **kwargs: Additional arguments depending on operation:
            send_message: text (str, required), parse_mode (str), reply_to_message_id (int)
            get_updates: limit (int), offset (int), timeout (int)
            send_photo: photo_url (str, required), caption (str), parse_mode (str)
            get_me: (no extra params)

    Returns:
        dict with operation results.
    """
    if not token:
        return {"success": False, "error": "Missing required parameter: token"}

    operation = operation.lower().strip()

    if operation not in _OPERATIONS:
        ops = ", ".join(sorted(_OPERATIONS.keys()))
        return {"success": False, "error": f"Unknown operation: {operation}. Available: {ops}"}

    handler, requires_chat = _OPERATIONS[operation]

    if requires_chat and not chat_id:
        return {
            "success": False,
            "error": f"Operation '{operation}' requires the 'chat_id' parameter.",
        }

    try:
        if requires_chat:
            return handler(token, chat_id=chat_id, **kwargs)
        return handler(token, **kwargs)
    except httpx.HTTPStatusError as exc:
        error_body = ""
        try:
            error_body = exc.response.json()
        except Exception:
            error_body = exc.response.text
        return {
            "success": False,
            "error": f"Telegram API error ({exc.response.status_code}): {error_body}",
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}
