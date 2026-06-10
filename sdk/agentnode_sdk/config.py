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
    },
    "permissions": {
        "network": "prompt",
        "filesystem": "prompt",
        "code_execution": "sandboxed",
    },
    "credentials": {
        "require_before_auto_install": True,
    },
    "risk_policies": {
        "external_write_capable": "log",
    },
    "guard": {
        "delete": "prompt",
        "write_external": "prompt",
        "execute": "prompt",
        "credential_use": "prompt",
        "network_egress": "allow",
        "write_local": "allow",
        "read": "allow",
        "compute": "allow",
        "unknown": "prompt",
    },
    "agent_sandbox": {
        "enabled": False,
    },
}

VALID_VALUES: dict[str, tuple[str, ...]] = {
    "auto_upgrade_policy": ("safe", "off"),
    "install_confirmation": ("auto", "prompt"),
    "trust.minimum_trust_level": ("verified", "trusted", "curated"),
    "permissions.network": ("allow", "prompt", "deny"),
    "permissions.filesystem": ("allow", "prompt", "deny"),
    "permissions.code_execution": ("sandboxed", "prompt", "deny"),
    "credentials.require_before_auto_install": ("true", "false"),
    "risk_policies.external_write_capable": ("allow", "log", "prompt", "deny"),
    "guard.delete": ("allow", "prompt", "deny"),
    "guard.write_external": ("allow", "prompt", "deny"),
    "guard.execute": ("allow", "prompt", "deny"),
    "guard.credential_use": ("allow", "prompt", "deny"),
    "guard.network_egress": ("allow", "prompt", "deny"),
    "guard.write_local": ("allow", "prompt", "deny"),
    "guard.read": ("allow", "prompt", "deny"),
    "guard.compute": ("allow", "prompt", "deny"),
    "guard.unknown": ("allow", "prompt", "deny"),
    "agent_sandbox.enabled": ("true", "false"),
}


CONFIG_DESCRIPTIONS: dict[str, str] = {
    "auto_upgrade_policy": "Controls whether smart run and doctor --fix can auto-install packages",
    "install_confirmation": "Whether to prompt before each installation",
    "trust.minimum_trust_level": "Minimum trust tier required for install and execution",
    "permissions.network": "Policy for packages that require network access",
    "permissions.filesystem": "Policy for packages that require filesystem access",
    "permissions.code_execution": "Policy for packages that execute code",
    "credentials.require_before_auto_install": "Skip packages needing credentials you don't have during auto-install",
    "risk_policies.external_write_capable": "Policy for packages that can write or send data externally",
    "guard.delete": "Guard policy for tools that delete resources",
    "guard.write_external": "Guard policy for tools that write data externally",
    "guard.execute": "Guard policy for tools that execute code",
    "guard.credential_use": "Guard policy for tools that use credentials",
    "guard.network_egress": "Guard policy for tools with network egress",
    "guard.write_local": "Guard policy for tools that write locally",
    "guard.read": "Guard policy for tools that read data",
    "guard.compute": "Guard policy for tools that perform computation",
    "guard.unknown": "Guard policy for tools with unknown action type",
    "agent_sandbox.enabled": "Route community (verified/unverified) agents through the container sandbox",
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
        for k in ("minimum_trust_level",):
            if k in data["trust"]:
                cfg["trust"][k] = data["trust"][k]
    if isinstance(data.get("permissions"), dict):
        for k in ("network", "filesystem", "code_execution"):
            if k in data["permissions"]:
                cfg["permissions"][k] = data["permissions"][k]
    if isinstance(data.get("credentials"), dict):
        for k in ("require_before_auto_install",):
            if k in data["credentials"]:
                cfg["credentials"][k] = data["credentials"][k]
    if isinstance(data.get("risk_policies"), dict):
        for k in cfg["risk_policies"]:
            if k in data["risk_policies"]:
                cfg["risk_policies"][k] = data["risk_policies"][k]
    if isinstance(data.get("guard"), dict):
        for k in cfg["guard"]:
            if k in data["guard"]:
                cfg["guard"][k] = data["guard"][k]
        for extra_key in ("rate_limits", "agent_overrides", "tool_overrides"):
            if extra_key in data["guard"]:
                cfg["guard"][extra_key] = data["guard"][extra_key]
    if isinstance(data.get("agent_sandbox"), dict):
        if "enabled" in data["agent_sandbox"]:
            cfg["agent_sandbox"]["enabled"] = data["agent_sandbox"]["enabled"]
        # The nested llm ceiling (max_calls/max_input_chars/max_output_chars/
        # allowed_models/enabled) holds ints/lists — pass it through verbatim,
        # like the guard extra keys above.
        if isinstance(data["agent_sandbox"].get("llm"), dict):
            cfg["agent_sandbox"]["llm"] = data["agent_sandbox"]["llm"]
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
    """Write config to disk atomically."""
    from agentnode_sdk._fileutil import atomic_write_json

    path = config_path()
    cfg["updated_at"] = datetime.now(timezone.utc).isoformat()
    atomic_write_json(path, cfg)


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

    bool_keys = ("credentials.require_before_auto_install", "agent_sandbox.enabled")
    actual_value: Any = value.lower() == "true" if key in bool_keys else value.lower()

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
