"""AgentNode user configuration (~/.agentnode/config.json)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_DIR = Path.home() / ".agentnode"
DEFAULT_CONFIG_FILE = "config.json"

DEFAULTS: dict[str, Any] = {
    "version": "1",
    "created_at": "",
    "updated_at": "",
    "auto_upgrade_policy": "safe",
    "install_confirmation": "auto",
    "trust": {
        "minimum_trust_level": "verified",
        "allow_unverified": False,
    },
    "permissions": {
        "network": "prompt",
        "filesystem": "prompt",
        "code_execution": "sandboxed",
    },
}

VALID_VALUES: dict[str, tuple[str, ...]] = {
    "auto_upgrade_policy": ("safe", "off"),
    "install_confirmation": ("auto", "prompt"),
    "trust.minimum_trust_level": ("verified", "trusted", "curated"),
    "trust.allow_unverified": ("true", "false"),
    "permissions.network": ("allow", "prompt", "deny"),
    "permissions.filesystem": ("allow", "prompt", "deny"),
    "permissions.code_execution": ("sandboxed", "prompt", "deny"),
}


def config_dir() -> Path:
    override = os.environ.get("AGENTNODE_CONFIG")
    if override:
        p = Path(override)
        return p.parent if p.suffix == ".json" else p
    return DEFAULT_CONFIG_DIR


def config_path() -> Path:
    override = os.environ.get("AGENTNODE_CONFIG")
    if override:
        p = Path(override)
        return p if p.suffix == ".json" else p / DEFAULT_CONFIG_FILE
    return DEFAULT_CONFIG_DIR / DEFAULT_CONFIG_FILE


def default_config() -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    cfg: dict[str, Any] = json.loads(json.dumps(DEFAULTS))
    cfg["created_at"] = now
    cfg["updated_at"] = now
    return cfg


def _merge_defaults(data: dict) -> dict[str, Any]:
    """Ensure all default keys exist in loaded config."""
    cfg: dict[str, Any] = json.loads(json.dumps(DEFAULTS))
    for key in ("version", "created_at", "updated_at", "auto_upgrade_policy", "install_confirmation"):
        if key in data:
            cfg[key] = data[key]
    if isinstance(data.get("trust"), dict):
        for k in ("minimum_trust_level", "allow_unverified"):
            if k in data["trust"]:
                cfg["trust"][k] = data["trust"][k]
    if isinstance(data.get("permissions"), dict):
        for k in ("network", "filesystem", "code_execution"):
            if k in data["permissions"]:
                cfg["permissions"][k] = data["permissions"][k]
    return cfg


def load_config() -> dict[str, Any]:
    """Load config from disk. Returns defaults on any error."""
    path = config_path()
    if not path.is_file():
        return default_config()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return default_config()
        return _merge_defaults(data)
    except (json.JSONDecodeError, OSError):
        return default_config()


def save_config(cfg: dict[str, Any]) -> None:
    """Write config to disk."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


def delete_config() -> bool:
    """Delete config file. Returns True if file existed."""
    path = config_path()
    if path.is_file():
        path.unlink()
        return True
    return False


def config_exists() -> bool:
    return config_path().is_file()


def get_value(cfg: dict, key: str) -> Any:
    """Get a value using dot notation."""
    parts = key.split(".")
    current: Any = cfg
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            raise KeyError(f"Unknown config key: {key}")
        current = current[part]
    return current


def set_value(cfg: dict[str, Any], key: str, value: str) -> dict[str, Any]:
    """Set a value using dot notation with strict validation."""
    if key not in VALID_VALUES:
        known = ", ".join(sorted(VALID_VALUES.keys()))
        raise KeyError(f"Unknown config key: {key}\n\nValid keys: {known}")

    allowed = VALID_VALUES[key]
    if value.lower() not in allowed:
        raise ValueError(
            f"Invalid value '{value}' for {key}.\n"
            f"Allowed: {', '.join(allowed)}"
        )

    actual_value: Any = value.lower() == "true" if key == "trust.allow_unverified" else value.lower()

    parts = key.split(".")
    current: Any = cfg
    for part in parts[:-1]:
        current = current[part]
    current[parts[-1]] = actual_value
    return cfg


def installation_behavior_label(cfg: dict) -> str:
    """Map config to human-readable installation behavior label."""
    policy = cfg.get("auto_upgrade_policy", "safe")
    if policy == "off":
        return "manual only"
    confirm = cfg.get("install_confirmation", "auto")
    if confirm == "prompt":
        return "review before install"
    return "automatic"
