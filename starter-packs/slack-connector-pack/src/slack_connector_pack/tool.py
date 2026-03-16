"""Slack connector tool using slack_sdk WebClient."""

from __future__ import annotations

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError


def _send_message(client: WebClient, channel: str, **kwargs) -> dict:
    """Send a message to a Slack channel."""
    text = kwargs.get("text", "")
    thread_ts = kwargs.get("thread_ts", None)
    blocks = kwargs.get("blocks", None)

    if not text and not blocks:
        return {"success": False, "error": "Missing required parameter: text (or blocks)"}

    post_kwargs: dict = {"channel": channel, "text": text}
    if thread_ts:
        post_kwargs["thread_ts"] = thread_ts
    if blocks:
        post_kwargs["blocks"] = blocks

    resp = client.chat_postMessage(**post_kwargs)
    return {
        "success": True,
        "message": {
            "channel": resp["channel"],
            "ts": resp["ts"],
            "text": text,
        },
    }


def _list_channels(client: WebClient, **kwargs) -> dict:
    """List public and/or private channels in the workspace."""
    limit = kwargs.get("limit", 100)
    types = kwargs.get("types", "public_channel,private_channel")
    exclude_archived = kwargs.get("exclude_archived", True)

    resp = client.conversations_list(
        types=types,
        exclude_archived=exclude_archived,
        limit=limit,
    )
    channels = []
    for ch in resp.get("channels", []):
        channels.append({
            "id": ch["id"],
            "name": ch.get("name", ""),
            "is_private": ch.get("is_private", False),
            "is_archived": ch.get("is_archived", False),
            "num_members": ch.get("num_members", 0),
            "topic": ch.get("topic", {}).get("value", ""),
            "purpose": ch.get("purpose", {}).get("value", ""),
        })
    return {"success": True, "channels": channels, "total": len(channels)}


def _read_history(client: WebClient, channel: str, **kwargs) -> dict:
    """Read message history from a Slack channel."""
    limit = kwargs.get("limit", 20)
    oldest = kwargs.get("oldest", None)
    latest = kwargs.get("latest", None)

    history_kwargs: dict = {"channel": channel, "limit": limit}
    if oldest:
        history_kwargs["oldest"] = str(oldest)
    if latest:
        history_kwargs["latest"] = str(latest)

    resp = client.conversations_history(**history_kwargs)
    messages = []
    for msg in resp.get("messages", []):
        messages.append({
            "ts": msg.get("ts", ""),
            "user": msg.get("user", ""),
            "text": msg.get("text", ""),
            "type": msg.get("type", ""),
            "thread_ts": msg.get("thread_ts", ""),
            "reply_count": msg.get("reply_count", 0),
        })
    return {
        "success": True,
        "channel": channel,
        "messages": messages,
        "has_more": resp.get("has_more", False),
        "total": len(messages),
    }


def _upload_file(client: WebClient, channel: str, **kwargs) -> dict:
    """Upload a file to a Slack channel."""
    file_path = kwargs.get("file_path", "")
    content = kwargs.get("content", None)
    filename = kwargs.get("filename", "")
    title = kwargs.get("title", "")
    initial_comment = kwargs.get("initial_comment", "")

    if not file_path and content is None:
        return {"success": False, "error": "Missing required parameter: file_path or content"}

    upload_kwargs: dict = {"channels": channel}
    if file_path:
        upload_kwargs["file"] = file_path
    elif content is not None:
        upload_kwargs["content"] = content
    if filename:
        upload_kwargs["filename"] = filename
    if title:
        upload_kwargs["title"] = title
    if initial_comment:
        upload_kwargs["initial_comment"] = initial_comment

    resp = client.files_upload_v2(**upload_kwargs)
    file_info = resp.get("file", {})
    return {
        "success": True,
        "file": {
            "id": file_info.get("id", ""),
            "name": file_info.get("name", ""),
            "size": file_info.get("size", 0),
            "url_private": file_info.get("url_private", ""),
        },
    }


_OPERATIONS = {
    "send_message": (_send_message, True),
    "list_channels": (_list_channels, False),
    "read_history": (_read_history, True),
    "upload_file": (_upload_file, True),
}


def run(token: str, operation: str, channel: str = "", **kwargs) -> dict:
    """Send messages, read history, and manage Slack channels.

    Args:
        token: Slack Bot User OAuth token (xoxb-...).
        operation: One of 'send_message', 'list_channels', 'read_history', 'upload_file'.
        channel: Channel ID (e.g., 'C01234567') required for most operations.
        **kwargs: Additional arguments depending on operation:
            send_message: text (str, required), thread_ts (str), blocks (list)
            list_channels: limit (int), types (str), exclude_archived (bool)
            read_history: limit (int), oldest (str), latest (str)
            upload_file: file_path (str) or content (str), filename (str),
                         title (str), initial_comment (str)

    Returns:
        dict with operation results.
    """
    if not token:
        return {"success": False, "error": "Missing required parameter: token"}

    operation = operation.lower().strip()

    if operation not in _OPERATIONS:
        ops = ", ".join(sorted(_OPERATIONS.keys()))
        return {"success": False, "error": f"Unknown operation: {operation}. Available: {ops}"}

    handler, requires_channel = _OPERATIONS[operation]

    if requires_channel and not channel:
        return {
            "success": False,
            "error": f"Operation '{operation}' requires the 'channel' parameter.",
        }

    try:
        client = WebClient(token=token)
        if requires_channel:
            return handler(client, channel=channel, **kwargs)
        return handler(client, **kwargs)
    except SlackApiError as exc:
        return {
            "success": False,
            "error": f"Slack API error: {exc.response.get('error', str(exc))}",
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}
