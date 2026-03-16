"""Discord connector tool using the Discord REST API via httpx."""

from __future__ import annotations

import httpx

BASE_URL = "https://discord.com/api/v10"


def _headers(token: str) -> dict:
    """Build authorization headers for Discord Bot token."""
    return {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
        "User-Agent": "AgentNode DiscordConnectorPack/1.0",
    }


def _send_message(token: str, channel_id: str, **kwargs) -> dict:
    """Send a message to a Discord channel."""
    content = kwargs.get("content", "")
    embed = kwargs.get("embed", None)

    if not content and not embed:
        return {"success": False, "error": "Missing required parameter: content (or embed)"}

    payload: dict = {}
    if content:
        payload["content"] = content
    if embed:
        payload["embeds"] = [embed] if isinstance(embed, dict) else embed

    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{BASE_URL}/channels/{channel_id}/messages",
            headers=_headers(token),
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    return {
        "success": True,
        "message": {
            "id": data["id"],
            "channel_id": data["channel_id"],
            "content": data.get("content", ""),
            "timestamp": data.get("timestamp", ""),
        },
    }


def _list_guilds(token: str, **kwargs) -> dict:
    """List guilds (servers) the bot is a member of."""
    limit = kwargs.get("limit", 100)

    with httpx.Client(timeout=30) as client:
        resp = client.get(
            f"{BASE_URL}/users/@me/guilds",
            headers=_headers(token),
            params={"limit": limit},
        )
        resp.raise_for_status()
        guilds_data = resp.json()

    guilds = []
    for g in guilds_data:
        guilds.append({
            "id": g["id"],
            "name": g["name"],
            "icon": g.get("icon", ""),
            "owner": g.get("owner", False),
            "permissions": g.get("permissions", ""),
        })
    return {"success": True, "guilds": guilds, "total": len(guilds)}


def _list_channels(token: str, **kwargs) -> dict:
    """List channels in a guild."""
    guild_id = kwargs.get("guild_id", "")
    if not guild_id:
        return {"success": False, "error": "Missing required parameter: guild_id"}

    with httpx.Client(timeout=30) as client:
        resp = client.get(
            f"{BASE_URL}/guilds/{guild_id}/channels",
            headers=_headers(token),
        )
        resp.raise_for_status()
        channels_data = resp.json()

    # Channel type mapping
    type_map = {
        0: "text",
        2: "voice",
        4: "category",
        5: "announcement",
        13: "stage",
        15: "forum",
    }

    channels = []
    for ch in channels_data:
        channels.append({
            "id": ch["id"],
            "name": ch.get("name", ""),
            "type": type_map.get(ch.get("type", 0), f"unknown({ch.get('type')})"),
            "type_id": ch.get("type", 0),
            "position": ch.get("position", 0),
            "topic": ch.get("topic", "") or "",
            "parent_id": ch.get("parent_id", ""),
        })

    channels.sort(key=lambda c: (c["type"] == "category", c["position"]))
    return {"success": True, "channels": channels, "total": len(channels)}


def _read_messages(token: str, channel_id: str, **kwargs) -> dict:
    """Read recent messages from a Discord channel."""
    limit = min(kwargs.get("limit", 50), 100)
    before = kwargs.get("before", None)
    after = kwargs.get("after", None)

    params: dict = {"limit": limit}
    if before:
        params["before"] = before
    if after:
        params["after"] = after

    with httpx.Client(timeout=30) as client:
        resp = client.get(
            f"{BASE_URL}/channels/{channel_id}/messages",
            headers=_headers(token),
            params=params,
        )
        resp.raise_for_status()
        messages_data = resp.json()

    messages = []
    for msg in messages_data:
        messages.append({
            "id": msg["id"],
            "author": {
                "id": msg["author"]["id"],
                "username": msg["author"].get("username", ""),
                "bot": msg["author"].get("bot", False),
            },
            "content": msg.get("content", ""),
            "timestamp": msg.get("timestamp", ""),
            "edited_timestamp": msg.get("edited_timestamp", ""),
            "attachments": [
                {"id": a["id"], "filename": a["filename"], "url": a["url"]}
                for a in msg.get("attachments", [])
            ],
        })
    return {
        "success": True,
        "channel_id": channel_id,
        "messages": messages,
        "total": len(messages),
    }


_OPERATIONS = {
    "send_message": (_send_message, True),
    "list_guilds": (_list_guilds, False),
    "list_channels": (_list_channels, False),
    "read_messages": (_read_messages, True),
}


def run(token: str, operation: str, channel_id: str = "", **kwargs) -> dict:
    """Send messages, list guilds, and read channels via the Discord REST API.

    Args:
        token: Discord Bot token.
        operation: One of 'send_message', 'list_guilds', 'list_channels', 'read_messages'.
        channel_id: Discord channel ID (required for send_message, read_messages).
        **kwargs: Additional arguments depending on operation:
            send_message: content (str, required), embed (dict)
            list_guilds: limit (int)
            list_channels: guild_id (str, required)
            read_messages: limit (int, max 100), before (str), after (str)

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

    if requires_channel and not channel_id:
        return {
            "success": False,
            "error": f"Operation '{operation}' requires the 'channel_id' parameter.",
        }

    try:
        if requires_channel:
            return handler(token, channel_id=channel_id, **kwargs)
        return handler(token, **kwargs)
    except httpx.HTTPStatusError as exc:
        error_body = ""
        try:
            error_body = exc.response.json()
        except Exception:
            error_body = exc.response.text
        return {
            "success": False,
            "error": f"Discord API error ({exc.response.status_code}): {error_body}",
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}
