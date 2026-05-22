"""Online publisher key verification — TG-2.

Checks key_id status against the registry. Keeps signature.py pure
(offline-only, no network calls per OC-2).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum

import httpx


class OnlineKeyStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    UNKNOWN = "unknown"
    MISMATCH = "mismatch"
    ERROR = "error"


KEY_STATUS_SEVERITY: dict[str, str] = {
    "mismatch": "critical",
    "revoked": "high",
    "unknown": "medium",
    "error": "availability",
    "active": "none",
}


@dataclass
class KeyStatusResult:
    """Result of online key status check."""

    status: OnlineKeyStatus
    key_id: str
    publisher_slug: str | None = None
    error: str | None = None

    @property
    def severity(self) -> str:
        return KEY_STATUS_SEVERITY.get(self.status, "unknown")


def check_key_status(
    publisher_slug: str,
    key_id: str,
    cached_public_key: str | None = None,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    timeout: float = 10.0,
) -> KeyStatusResult:
    """Check key status against the registry.

    Makes a single GET request. Does not use AgentNodeClient to avoid
    circular imports — uses httpx directly (same pattern as trust refresh
    in runner.py).
    """
    base = base_url or os.environ.get(
        "AGENTNODE_API_URL", "https://api.agentnode.net"
    )
    base = base.rstrip("/")
    if not base.endswith("/v1"):
        base = f"{base}/v1"

    url = f"{base}/publishers/{publisher_slug}/keys/{key_id}"
    headers: dict[str, str] = {}
    ak = api_key or os.environ.get("AGENTNODE_API_KEY")
    if ak:
        headers["X-API-Key"] = ak

    try:
        resp = httpx.get(url, headers=headers, timeout=timeout)
    except Exception as exc:
        return KeyStatusResult(
            status=OnlineKeyStatus.ERROR,
            key_id=key_id,
            publisher_slug=publisher_slug,
            error=f"Registry unreachable: {exc}",
        )

    if resp.status_code == 404:
        return KeyStatusResult(
            status=OnlineKeyStatus.UNKNOWN,
            key_id=key_id,
            publisher_slug=publisher_slug,
            error="Key not found in registry",
        )

    if resp.status_code >= 400:
        return KeyStatusResult(
            status=OnlineKeyStatus.ERROR,
            key_id=key_id,
            publisher_slug=publisher_slug,
            error=f"Registry returned {resp.status_code}",
        )

    try:
        data = resp.json()
    except Exception:
        return KeyStatusResult(
            status=OnlineKeyStatus.ERROR,
            key_id=key_id,
            publisher_slug=publisher_slug,
            error="Invalid JSON from registry",
        )

    registry_status = data.get("status", "unknown")
    registry_pub_key = data.get("public_key")

    if registry_status == "revoked":
        return KeyStatusResult(
            status=OnlineKeyStatus.REVOKED,
            key_id=key_id,
            publisher_slug=publisher_slug,
        )

    if registry_status == "active":
        if cached_public_key and registry_pub_key:
            if cached_public_key != registry_pub_key:
                return KeyStatusResult(
                    status=OnlineKeyStatus.MISMATCH,
                    key_id=key_id,
                    publisher_slug=publisher_slug,
                    error="Cached public key does not match registry",
                )
        return KeyStatusResult(
            status=OnlineKeyStatus.ACTIVE,
            key_id=key_id,
            publisher_slug=publisher_slug,
        )

    return KeyStatusResult(
        status=OnlineKeyStatus.UNKNOWN,
        key_id=key_id,
        publisher_slug=publisher_slug,
        error=f"Unknown key status from registry: {registry_status}",
    )
