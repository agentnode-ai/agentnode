"""Local credential store (~/.agentnode/credentials.json + OS keychain).

Stores user-provided tokens (GitHub PATs, Slack bot tokens, LLM provider API
keys) locally so packages and the host LLM runtime can use them.

Storage backends (UX-2 vault):
- PRIMARY: the OS keychain via ``keyring`` (Windows Credential Manager, macOS
  Keychain, Linux Secret Service). The secret lives ONLY in the keychain;
  credentials.json keeps non-secret metadata with ``"storage": "keyring"``.
  Honest scope: this protects against other local users and accidental file
  exposure — it does NOT protect against programs running as you.
- FALLBACK (keychain unavailable, e.g. headless Linux/CI): plaintext JSON +
  0600 file permissions (industry standard: gh, docker, aws), marked
  ``"storage": "file"``. Never described as "encrypted" — it is not.

File format:
{
  "version": 1,
  "providers": {
    "github": {
      "access_token": "ghp_xxx",        # FILE storage only — absent for keyring
      "auth_type": "oauth2",
      "scopes": ["repo", "read:user"],
      "stored_at": "2026-04-15T10:00:00Z",
      "storage": "keyring" | "file"     # absent on legacy entries (= file)
    }
  }
}

Migration happens on WRITE only (``set_credential`` moves a legacy plaintext
token into the keychain and strips it from the JSON). Reads never write and
never create keychain entries (no surprise OS prompts mid-run).
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("agentnode.credential_store")

CREDENTIALS_FILE = "credentials.json"
CURRENT_VERSION = 1

# Canonical env vars for the LLM providers the host runtime understands.
# Env always OVERRIDES the stored credential (explicit/CI intent wins).
LLM_PROVIDER_ENV: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}

# One keychain item per provider: service "agentnode:<provider>", a fixed
# username. (NOT service="agentnode" + username=provider — Windows WinVault
# keys items by service/TargetName and mangles multiple usernames.)
_KEYRING_SERVICE_PREFIX = "agentnode:"
_KEYRING_USERNAME = "token"
_PROBE_TIMEOUT_S = 4.0

# One-shot per-process probe cache: None = not probed yet.
_keyring_state: dict[str, Any] = {"available": None}


def _get_keyring_backend():
    """Return the usable keyring module, or None. Seam for tests."""
    try:
        import keyring
        from keyring.backends.fail import Keyring as _FailKeyring

        if isinstance(keyring.get_keyring(), _FailKeyring):
            return None
        return keyring
    except Exception:
        return None


def _keyring_available() -> bool:
    """Whether the OS keychain is usable. Probed ONCE per process, in a daemon
    thread with a timeout — a locked Secret Service / misbehaving D-Bus can
    block indefinitely, and we must degrade to file storage, not hang."""
    if _keyring_state["available"] is not None:
        return _keyring_state["available"]

    result = {"ok": False}

    def _probe() -> None:
        try:
            kr = _get_keyring_backend()
            if kr is None:
                return
            kr.get_password(_KEYRING_SERVICE_PREFIX + "__probe__", _KEYRING_USERNAME)
            result["ok"] = True
        except Exception:
            pass

    t = threading.Thread(target=_probe, daemon=True)
    t.start()
    t.join(timeout=_PROBE_TIMEOUT_S)
    _keyring_state["available"] = bool(result["ok"]) and not t.is_alive()
    if not _keyring_state["available"]:
        logger.debug("OS keychain unavailable; using file storage")
    return _keyring_state["available"]


def _keyring_service(provider: str) -> str:
    return _KEYRING_SERVICE_PREFIX + provider


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
) -> str:
    """Store a credential for a provider. Returns the storage backend used
    ("keyring" or "file").

    Keychain path: secret goes to the OS keychain; the JSON entry keeps only
    metadata. Replacing the whole entry also strips any legacy plaintext token
    (= migration on write). Keychain write first, metadata second — an
    orphaned keychain item is harmless, stale metadata is not.
    """
    provider = provider.lower()
    data = load_credentials()

    entry: dict[str, Any] = {
        "auth_type": auth_type,
        "scopes": scopes or [],
        "stored_at": datetime.now(timezone.utc).isoformat(),
    }

    storage = "file"
    if _keyring_available():
        kr = _get_keyring_backend()
        try:
            kr.set_password(_keyring_service(provider), _KEYRING_USERNAME, access_token)
            storage = "keyring"
        except Exception:
            # Never log the token or the raw backend error (may carry context).
            logger.warning(
                "OS keychain write failed for %s; falling back to file storage",
                provider,
            )

    entry["storage"] = storage
    if storage == "file":
        entry["access_token"] = access_token

    data["providers"][provider] = entry
    save_credentials(data)
    return storage


def get_credential(provider: str) -> dict[str, Any] | None:
    """Get stored credential for a provider (token resolved from the keychain
    when the entry is keyring-backed). Returns None if not found / unreadable.
    Read-only: never writes, never creates keychain entries."""
    provider = provider.lower()
    data = load_credentials()
    entry = data.get("providers", {}).get(provider)
    if not isinstance(entry, dict):
        return None
    if entry.get("storage") == "keyring" and not entry.get("access_token"):
        token = None
        if _keyring_available():
            kr = _get_keyring_backend()
            try:
                token = kr.get_password(_keyring_service(provider), _KEYRING_USERNAME)
            except Exception:
                logger.warning("OS keychain read failed for %s", provider)
        if not token:
            # Metadata claims keyring but the item is gone/unreadable —
            # treat as not configured (auth status surfaces this).
            return None
        out = dict(entry)
        out["access_token"] = token
        return out
    return entry


def has_credential(provider: str) -> bool:
    """Check if a credential is stored for a provider."""
    return get_credential(provider) is not None


def remove_credential(provider: str) -> bool:
    """Remove a credential. Returns True if it existed. Tolerates a missing
    keychain item (orphan-safe)."""
    provider = provider.lower()
    data = load_credentials()
    if provider not in data.get("providers", {}):
        return False
    entry = data["providers"][provider]
    if isinstance(entry, dict) and entry.get("storage") == "keyring" and _keyring_available():
        kr = _get_keyring_backend()
        try:
            kr.delete_password(_keyring_service(provider), _KEYRING_USERNAME)
        except Exception:
            pass  # already gone / locked — metadata removal still proceeds
    del data["providers"][provider]
    save_credentials(data)
    return True


def list_credentials() -> dict[str, dict[str, Any]]:
    """Return all stored provider credentials (without token values).

    Metadata-only by contract: NEVER touches the keychain (no OS prompts)."""
    data = load_credentials()
    result: dict[str, dict[str, Any]] = {}
    for provider, entry in data.get("providers", {}).items():
        if isinstance(entry, dict):
            result[provider] = {
                "auth_type": entry.get("auth_type", "unknown"),
                "scopes": entry.get("scopes", []),
                "stored_at": entry.get("stored_at", ""),
                "storage": entry.get("storage", "file"),
            }
    return result


def get_llm_api_key(provider: str) -> str | None:
    """The plain API key for an LLM provider from the store, or None.

    Used by the host-side LLM runtime (``_auto_detect_llm``) as the LAST
    resolution step — env vars always override the store."""
    entry = get_credential(provider)
    if entry:
        token = entry.get("access_token")
        if isinstance(token, str) and token:
            return token
    return None


def storage_label(storage: str) -> str:
    """Honest, user-facing description of a storage backend."""
    if storage == "keyring":
        return ("OS keychain — protects against other local users and "
                "accidental file exposure; not against programs running as you")
    return "plaintext file (0600) — readable by any process running as you"
