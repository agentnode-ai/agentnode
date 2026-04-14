"""AgentNode Policy Kernel — centralized pre-execution policy checks.

Provides ``check_install()``, ``check_run()``, and ``audit_decision()``
as the single source of truth for all install/run permission decisions.
Reads the user config from ``~/.agentnode/config.json`` and enforces
trust levels, permission boundaries, and environment context.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class PolicyResult:
    """Result of a policy check."""

    action: str          # "allow", "deny", "prompt"
    reason: str
    source: str          # Fixed set: trust_level, permission.*, environment.*, default
    details: dict | None = None  # Structured info for tests/UI, NOT written to audit


@dataclass
class EnvironmentContext:
    """Runtime environment signals."""

    has_secrets: bool
    is_ci: bool
    is_container: bool
    privilege: str       # "root" or "user"


# ---------------------------------------------------------------------------
# Environment detection
# ---------------------------------------------------------------------------

_SECRET_PREFIXES = (
    "AWS_", "OPENAI_", "STRIPE_", "GCP_", "AZURE_", "ANTHROPIC_",
    "DATABASE_URL", "SECRET",
)
_CI_VARS = ("CI", "GITHUB_ACTIONS", "GITLAB_CI", "CIRCLECI", "JENKINS_URL", "TRAVIS")


@dataclass
class _StaticEnv:
    """Cached static environment properties (don't change during process)."""
    os_name: str
    is_container: bool
    privilege: str


_cached_static_env: _StaticEnv | None = None


def _detect_static_env() -> _StaticEnv:
    global _cached_static_env
    if _cached_static_env is not None:
        return _cached_static_env

    os_name = sys.platform
    is_container = (
        os.path.exists("/.dockerenv")
        or _check_cgroup_container()
    )
    if hasattr(os, "getuid"):
        privilege = "root" if os.getuid() == 0 else "user"
    else:
        privilege = "user"  # Windows

    _cached_static_env = _StaticEnv(
        os_name=os_name,
        is_container=is_container,
        privilege=privilege,
    )
    return _cached_static_env


def _check_cgroup_container() -> bool:
    try:
        with open("/proc/1/cgroup", "r") as f:
            content = f.read()
            return "docker" in content or "containerd" in content
    except (OSError, FileNotFoundError):
        return False


def _detect_environment() -> EnvironmentContext:
    """Detect current environment. Static parts cached, dynamic parts fresh."""
    try:
        static = _detect_static_env()
    except Exception:
        # Fallback for platforms without getuid (Windows)
        static = _StaticEnv(os_name=sys.platform, is_container=False, privilege="user")

    # Dynamic: checked every time
    env = os.environ
    has_secrets = any(
        any(key.startswith(prefix) or key == prefix for prefix in _SECRET_PREFIXES)
        for key in env
    )
    is_ci = any(env.get(var) for var in _CI_VARS)

    return EnvironmentContext(
        has_secrets=has_secrets,
        is_ci=is_ci,
        is_container=static.is_container,
        privilege=static.privilege,
    )


def _env_summary(ctx: EnvironmentContext) -> str:
    """Compact env string for audit: 'linux/user/ci/secrets' or similar."""
    try:
        static = _detect_static_env()
        os_name = static.os_name
    except Exception:
        os_name = sys.platform
    ci = "ci" if ctx.is_ci else "no_ci"
    secrets = "secrets" if ctx.has_secrets else "no_secrets"
    return f"{os_name}/{ctx.privilege}/{ci}/{secrets}"


# ---------------------------------------------------------------------------
# Trust helpers
# ---------------------------------------------------------------------------

_TRUST_ORDER = ["unverified", "verified", "trusted", "curated"]


def _trust_meets_minimum(package_trust: str, minimum: str) -> bool:
    """Check if package_trust meets or exceeds the minimum trust level."""
    try:
        pkg_idx = _TRUST_ORDER.index(package_trust)
    except ValueError:
        return False
    try:
        min_idx = _TRUST_ORDER.index(minimum)
    except ValueError:
        return False
    return pkg_idx >= min_idx


# ---------------------------------------------------------------------------
# Permission mapping (BD-2)
# ---------------------------------------------------------------------------

_KNOWN_NETWORK_VALUES = {"unrestricted", "restricted", "none"}
_KNOWN_FILESYSTEM_VALUES = {"full", "write", "read", "temp", "none"}
_KNOWN_CODE_EXECUTION_VALUES = {"unrestricted", "subprocess", "none"}


def _check_permission(
    permission_key: str,
    config_value: str,
    package_value: str,
) -> PolicyResult | None:
    """Check a single permission dimension. Returns PolicyResult or None (allow).

    BD-2: Central permission mapping function. Only called with real values
    (not None). Unknown package values are warned and treated as unrestricted.
    """
    # BD-13: Warn on unknown package permission values
    known_sets = {
        "network": _KNOWN_NETWORK_VALUES,
        "filesystem": _KNOWN_FILESYSTEM_VALUES,
        "code_execution": _KNOWN_CODE_EXECUTION_VALUES,
    }
    known = known_sets.get(permission_key, set())
    if package_value not in known:
        logger.warning(
            "Unknown permission value: %s=%s, treating as unrestricted",
            permission_key, package_value,
        )
        # Treat as most permissive for conservative checking
        if permission_key == "network":
            package_value = "unrestricted"
        elif permission_key == "filesystem":
            package_value = "full"
        elif permission_key == "code_execution":
            package_value = "unrestricted"

    # --- Network ---
    if permission_key == "network":
        if config_value == "allow":
            return None  # all allowed
        if config_value == "prompt":
            if package_value in ("unrestricted",):
                return PolicyResult(
                    action="prompt",
                    reason=f"Package requires unrestricted network; config requires approval",
                    source="permission.network",
                    details={"permission": "network", "config": config_value, "package": package_value},
                )
            return None  # restricted/none → allow
        if config_value == "deny":
            if package_value in ("unrestricted", "restricted"):
                return PolicyResult(
                    action="deny",
                    reason=f"Package requires network access ({package_value}); config denies network",
                    source="permission.network",
                    details={"permission": "network", "config": config_value, "package": package_value},
                )
            return None  # none → allow

    # --- Filesystem ---
    if permission_key == "filesystem":
        if config_value == "allow":
            return None
        if config_value == "prompt":
            if package_value in ("full", "write"):
                return PolicyResult(
                    action="prompt",
                    reason=f"Package requires filesystem write access ({package_value}); config requires approval",
                    source="permission.filesystem",
                    details={"permission": "filesystem", "config": config_value, "package": package_value},
                )
            return None  # read/none → allow
        if config_value == "deny":
            if package_value in ("full", "write", "read"):
                return PolicyResult(
                    action="deny",
                    reason=f"Package requires filesystem access ({package_value}); config denies filesystem",
                    source="permission.filesystem",
                    details={"permission": "filesystem", "config": config_value, "package": package_value},
                )
            return None  # none → allow

    # --- Code Execution ---
    if permission_key == "code_execution":
        if config_value == "sandboxed":
            if package_value == "unrestricted":
                return PolicyResult(
                    action="prompt",
                    reason="Package requires unrestricted code execution; config allows only sandboxed",
                    source="permission.code_execution",
                    details={"permission": "code_execution", "config": config_value, "package": package_value},
                )
            return None  # subprocess/none → allow
        if config_value == "prompt":
            if package_value != "none":
                return PolicyResult(
                    action="prompt",
                    reason=f"Package requires code execution ({package_value}); config requires approval",
                    source="permission.code_execution",
                    details={"permission": "code_execution", "config": config_value, "package": package_value},
                )
            return None  # none → allow
        if config_value == "deny":
            if package_value != "none":
                return PolicyResult(
                    action="deny",
                    reason=f"Package requires code execution ({package_value}); config denies code execution",
                    source="permission.code_execution",
                    details={"permission": "code_execution", "config": config_value, "package": package_value},
                )
            return None  # none → allow

    return None


# ---------------------------------------------------------------------------
# Config loading (with fail-closed behavior, BD-7)
# ---------------------------------------------------------------------------

def _load_config_safe(*, interactive: bool) -> dict | None:
    """Load config, returning None on failure.

    BD-7: Broken/missing config → prompt (interactive) or deny (non-interactive).
    Caller must handle None return by producing appropriate PolicyResult.
    """
    try:
        from agentnode_sdk.config import load_config
        cfg = load_config()
        if not isinstance(cfg, dict):
            return None
        return cfg
    except Exception:
        return None


def _config_broken_result(*, interactive: bool) -> PolicyResult:
    """BD-7: Result when config is invalid or missing."""
    strict = os.environ.get("AGENTNODE_GUARD_STRICT", "").lower() == "true"
    if strict or not interactive:
        return PolicyResult(
            action="deny",
            reason="Config invalid or missing",
            source="default",
        )
    return PolicyResult(
        action="prompt",
        reason="Config invalid or missing",
        source="default",
    )


# ---------------------------------------------------------------------------
# Entry normalization (BD-1)
# ---------------------------------------------------------------------------

def _normalize_entry(entry: dict) -> dict:
    """Apply defaults for missing fields. Never crashes on bad input."""
    return {
        "trust_level": entry.get("trust_level", "unverified"),
        "permissions": entry.get("permissions"),  # None is valid
        "runtime": entry.get("runtime", "python"),
        "deprecated": entry.get("deprecated", False),  # Phase A ignores, documented
        "scanner_findings": entry.get("scanner_findings", []),  # Phase A ignores
    }


# ---------------------------------------------------------------------------
# Core policy checks
# ---------------------------------------------------------------------------

def check_install(slug: str, entry: dict, *, interactive: bool = True) -> PolicyResult:
    """Pre-install policy check. Uses existing config.json permissions.

    Args:
        slug: Package slug.
        entry: Lockfile-style entry dict. Missing fields get defaults (BD-1).
        interactive: Whether the caller supports user prompts.
    """
    e = _normalize_entry(entry)

    cfg = _load_config_safe(interactive=interactive)
    if cfg is None:
        return _config_broken_result(interactive=interactive)

    # Trust check
    min_trust = cfg.get("trust", {}).get("minimum_trust_level", "verified")
    pkg_trust = e["trust_level"]
    if not _trust_meets_minimum(pkg_trust, min_trust):
        return PolicyResult(
            action="deny",
            reason=f"Trust level '{pkg_trust}' below minimum '{min_trust}'",
            source="trust_level",
            details={"required_trust": min_trust, "actual_trust": pkg_trust},
        )

    # Permission checks (only if package declares permissions)
    perms = e["permissions"]
    if perms is not None:
        cfg_perms = cfg.get("permissions", {})

        perm_checks = [
            ("network", cfg_perms.get("network", "prompt"), perms.get("network_level", "none")),
            ("filesystem", cfg_perms.get("filesystem", "prompt"), perms.get("filesystem_level", "none")),
            ("code_execution", cfg_perms.get("code_execution", "sandboxed"), perms.get("code_execution_level", "none")),
        ]
        for perm_key, cfg_val, pkg_val in perm_checks:
            result = _check_permission(perm_key, cfg_val, pkg_val)
            if result is not None:
                return result

    return PolicyResult(action="allow", reason="All checks passed", source="default")


def check_run(
    slug: str,
    tool_name: str | None,
    kwargs: dict,
    entry: dict,
    *,
    interactive: bool = True,
) -> PolicyResult:
    """Pre-execution policy check. Uses config + environment context.

    Args:
        slug: Package slug.
        tool_name: Tool name (may be None for single-tool packs).
        kwargs: Tool arguments (Phase A ignores, kept for future extension).
        entry: Lockfile entry dict.
        interactive: Whether the caller supports user prompts.
    """
    e = _normalize_entry(entry)

    cfg = _load_config_safe(interactive=interactive)
    if cfg is None:
        return _config_broken_result(interactive=interactive)

    env = _detect_environment()

    # Trust check
    min_trust = cfg.get("trust", {}).get("minimum_trust_level", "verified")
    pkg_trust = e["trust_level"]
    if not _trust_meets_minimum(pkg_trust, min_trust):
        return PolicyResult(
            action="deny",
            reason=f"Trust level '{pkg_trust}' below minimum '{min_trust}'",
            source="trust_level",
            details={"required_trust": min_trust, "actual_trust": pkg_trust},
        )

    perms = e["permissions"]

    # BD-14: permissions=None + low trust → prompt
    if perms is None:
        if not _trust_meets_minimum(pkg_trust, "verified"):
            return PolicyResult(
                action="prompt",
                reason="Unverified package with no permission declaration",
                source="trust_level",
                details={"actual_trust": pkg_trust},
            )
        # BD-11: permissions=None + has_secrets → prompt
        if env.has_secrets:
            return PolicyResult(
                action="prompt",
                reason="No permission declaration in sensitive environment",
                source="environment.has_secrets",
                details={"has_secrets": True},
            )
        # permissions=None + trust >= verified + no secrets → allow
        return PolicyResult(action="allow", reason="All checks passed", source="default")

    # Permission checks with declared permissions
    cfg_perms = cfg.get("permissions", {})

    perm_checks = [
        ("network", cfg_perms.get("network", "prompt"), perms.get("network_level", "none")),
        ("filesystem", cfg_perms.get("filesystem", "prompt"), perms.get("filesystem_level", "none")),
        ("code_execution", cfg_perms.get("code_execution", "sandboxed"), perms.get("code_execution_level", "none")),
    ]
    for perm_key, cfg_val, pkg_val in perm_checks:
        result = _check_permission(perm_key, cfg_val, pkg_val)
        if result is not None:
            return result

    # Environment-based escalation: has_secrets + network allowed + low trust → prompt
    if (
        env.has_secrets
        and cfg_perms.get("network", "prompt") == "allow"
        and not _trust_meets_minimum(pkg_trust, "trusted")
    ):
        net_level = perms.get("network_level", "none")
        if net_level != "none":
            return PolicyResult(
                action="prompt",
                reason="Network-capable package in sensitive environment with low trust",
                source="environment.has_secrets",
                details={"has_secrets": True, "trust": pkg_trust, "network": net_level},
            )

    return PolicyResult(action="allow", reason="All checks passed", source="default")


# ---------------------------------------------------------------------------
# Audit trail (BD-4, BD-5, BD-6, BD-12)
# ---------------------------------------------------------------------------

_VALID_EVENTS = frozenset({
    "run_tool", "runtime_run", "runtime_install", "client_install", "mcp_run",
    "agent_run",
})


def audit_decision(
    decision: PolicyResult,
    event_type: str,
    slug: str,
    *,
    tool_name: str | None = None,
    trust_level: str | None = None,
    run_id: str | None = None,
) -> None:
    """Log a policy decision to ``~/.agentnode/audit.jsonl``.

    BD-5: Append-only, UTF-8. Never crashes the caller.
    BD-6: Only has_secrets bool in env, no secret key names.
    BD-12: details field NOT written to audit.
    """
    if event_type not in _VALID_EVENTS:
        logger.warning("Unknown audit event type: %s", event_type)

    try:
        env = _detect_environment()
        env_str = _env_summary(env)
    except Exception:
        env_str = "unknown"

    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event_type,
        "slug": slug,
        "tool_name": tool_name,
        "action": decision.action,
        "source": decision.source,
        "reason": decision.reason,
        "trust": trust_level or "unknown",
        "env": env_str,
        "request_id": run_id,  # Correlates with agent run_id when available
    }

    try:
        from agentnode_sdk.config import config_dir
        audit_dir = config_dir()
        audit_dir.mkdir(parents=True, exist_ok=True)
        audit_path = audit_dir / "audit.jsonl"
        with open(audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning("Failed to write audit entry: %s", exc)


# ---------------------------------------------------------------------------
# Legacy API — kept for backward compat with runner.py import
# ---------------------------------------------------------------------------

def resolve_runtime(entry: dict, context: dict | None = None) -> str:
    """Resolve which runtime to use for a package.

    Reads the runtime field from the lockfile entry.
    """
    return entry.get("runtime", "python")
