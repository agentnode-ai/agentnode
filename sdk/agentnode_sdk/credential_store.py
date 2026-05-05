"""Local credential store (~/.agentnode/credentials.json).

Stores user-provided tokens (e.g. GitHub PATs, Slack bot tokens) locally
so that connector packages can be used without an AgentNode account.

File format:
{
  "version": 1,
  "providers": {
    "github": {
      "access_token": "ghp_xxx",
      "auth_type": "oauth2",
      "scopes": ["repo", "read:user"],
      "stored_at": "2026-04-15T10:00:00Z"
    }
  }
}

Security: plaintext JSON + file permissions (industry standard: gh, docker, aws).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("agentnode.credential_store")

CREDENTIALS_FILE = "credentials.json"
CURRENT_VERSION = 1


def _credentials_path() -> Path:
    """Return path to the local credentials file."""
    override = os.environ.get("AGENTNODE_CONFIG")
    if override:
        p = Path(override)
        config_dir = p.parent if p.suffix == ".json" else p
    else:
        config_dir = Path.home() / ".agentnode"
    return config_dir / CREDENTIALS_FILE


def load_credentials() -> dict[str, Any]:
    """Load credentials from disk. Returns empty structure on any error."""
    path = _credentials_path()
    if not path.is_file():
        return {"version": CURRENT_VERSION, "providers": {}}
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            logger.warning("credentials.json is not a JSON object, ignoring")
            return {"version": CURRENT_VERSION, "providers": {}}
        # Ensure required keys exist
        if "version" not in data:
            data["version"] = CURRENT_VERSION
        if not isinstance(data.get("providers"), dict):
            data["providers"] = {}
        return data
    except json.JSONDecodeError as exc:
        logger.warning(
            "Local credentials file is invalid JSON: %s. "
            "Fix or remove it, then retry: %s",
            exc, path,
        )
        return {"version": CURRENT_VERSION, "providers": {}}
    except OSError as exc:
        logger.warning(
            "Failed to read local credentials file %s: %s",
            path, exc,
        )
        return {"version": CURRENT_VERSION, "providers": {}}


def save_credentials(data: dict[str, Any]) -> None:
    """Write credentials to disk atomically with 0600 permissions (Unix)."""
    from agentnode_sdk._fileutil import atomic_write_json

    path = _credentials_path()
    atomic_write_json(path, data, mode=0o600)


def set_credential(
    provider: str,
    access_token: str,
    *,
    auth_type: str = "oauth2",
    scopes: list[str] | None = None,
) -> None:
    """Store a credential for a provider."""
    provider = provider.lower()
    data = load_credentials()
    data["providers"][provider] = {
        "access_token": access_token,
        "auth_type": auth_type,
        "scopes": scopes or [],
        "stored_at": datetime.now(timezone.utc).isoformat(),
    }
    save_credentials(data)


def get_credential(provider: str) -> dict[str, Any] | None:
    """Get stored credential for a provider. Returns None if not found."""
    provider = provider.lower()
    data = load_credentials()
    entry = data.get("providers", {}).get(provider)
    if not isinstance(entry, dict):
        return None
    return entry


def has_credential(provider: str) -> bool:
    """Check if a credential is stored for a provider."""
    return get_credential(provider) is not None


def remove_credential(provider: str) -> bool:
    """Remove a credential. Returns True if it existed."""
    provider = provider.lower()
    data = load_credentials()
    if provider not in data.get("providers", {}):
        return False
    del data["providers"][provider]
    save_credentials(data)
    return True


def list_credentials() -> dict[str, dict[str, Any]]:
    """Return all stored provider credentials (without token values)."""
    data = load_credentials()
    result: dict[str, dict[str, Any]] = {}
    for provider, entry in data.get("providers", {}).items():
        if isinstance(entry, dict):
            result[provider] = {
                "auth_type": entry.get("auth_type", "unknown"),
                "scopes": entry.get("scopes", []),
                "stored_at": entry.get("stored_at", ""),
            }
    return result
