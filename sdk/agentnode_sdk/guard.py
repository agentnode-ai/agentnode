"""AgentNode Guard — pre-execution policy gateway.

Action-type classification, MCP argument inspection, and rate limiting.
Sits between check_run()/check_risk_policies() and the runtime dispatch.

OC-1: No imports from runtime modules (python_runner, mcp_runner, etc.).
OC-2: Decision path is pure in-memory — no file I/O, no network.
OC-3: Internal exceptions → fail-closed (never allow).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ACTION_TYPES = frozenset({
    "read", "compute", "write_local", "write_external",
    "delete", "execute", "credential_use", "network_egress", "unknown",
})

_HIGH_RISK_ACTIONS = frozenset({
    "write_external", "delete", "execute", "credential_use",
})

_DEFAULT_GUARD_POLICY: dict[str, str] = {
    "delete": "prompt",
    "write_external": "prompt",
    "execute": "prompt",
    "credential_use": "prompt",
    "network_egress": "allow",
    "write_local": "allow",
    "read": "allow",
    "compute": "allow",
    "unknown": "prompt",
}

_STRICT_GUARD_POLICY: dict[str, str] = {
    "delete": "deny",
    "write_external": "deny",
    "execute": "deny",
    "credential_use": "prompt",
    "network_egress": "allow",
    "write_local": "prompt",
    "read": "allow",
    "compute": "allow",
    "unknown": "deny",
}

# INV-4: MCP argument inspection recursion limits
MAX_NESTING_DEPTH = 20
MAX_STRING_LENGTH = 1_000_000  # 1MB per string
MAX_TOTAL_KEYS = 10_000
MAX_PAYLOAD_BYTES = 1_000_000  # 1MB total JSON

# INV-3: Rate limit memory bounds
MAX_TRACKED_SLUGS = 10_000

# Name heuristic patterns (AD-2)
_NAME_HEURISTICS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^(delete|remove|drop|purge|destroy|erase)", re.I), "delete"),
    (re.compile(r"^(send|post|publish|notify|email|message|alert)", re.I), "write_external"),
    (re.compile(r"^(create|write|update|set|put|insert|save|store|add)", re.I), "write_local"),
    (re.compile(r"^(get|read|list|fetch|search|find|query|lookup|show|view|describe)", re.I), "read"),
    (re.compile(r"^(run|exec|eval|shell|spawn|invoke|launch)", re.I), "execute"),
    (re.compile(r"^(compute|calculate|transform|parse|convert|format|analyze|count)", re.I), "compute"),
]

# MCP inspection patterns
_PATH_TRAVERSAL_RE = re.compile(r"(^|[\\/])\.\.[\\/]")
_ABSOLUTE_PATH_RE = re.compile(r"^(/|[A-Za-z]:\\)")
_URL_RE = re.compile(r"https?://", re.IGNORECASE)
_SHELL_TOKEN_RE = re.compile(r"[;&|`$]|\b&&\b|\|\|")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class GuardDecision:
    """Result of a guard check."""
    action: str          # "allow", "deny", "prompt"
    reason: str
    action_types: list[str] = field(default_factory=list)
    risk_level: str = "low"
    mitigations: list[str] = field(default_factory=list)
    source: str = "guard"
    guard_chain: list[str] = field(default_factory=list)


@dataclass
class GuardContext:
    """Metadata for confirmation callbacks (INV-5)."""
    slug: str
    tool_name: str | None
    args_preview: dict = field(default_factory=dict)
    request_id: str | None = None
    trust_level: str = "unverified"


# ---------------------------------------------------------------------------
# Config loading (cached, OC-2 compliant)
# ---------------------------------------------------------------------------

_cached_guard_config: dict[str, Any] | None = None
_cached_tool_overrides: dict[str, dict[str, str]] = {}
_config_loaded: bool = False

_VALID_POLICY_VALUES = frozenset({"allow", "prompt", "deny"})


def _parse_tool_overrides(raw: Any) -> dict[str, dict[str, str]]:
    """Parse and validate tool_overrides from config. Unknown keys silently ignored."""
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, str]] = {}
    for tool_key, overrides in raw.items():
        if not isinstance(tool_key, str) or "/" not in tool_key:
            continue
        if not isinstance(overrides, dict):
            continue
        filtered = {
            k: v for k, v in overrides.items()
            if k in ACTION_TYPES and v in _VALID_POLICY_VALUES
        }
        if filtered:
            result[tool_key] = filtered
    return result


def _get_tool_override(
    slug: str, tool_name: str | None, action_type: str,
) -> str | None:
    """Look up a per-tool policy override. Returns None if no override exists."""
    if tool_name is None:
        return None
    tool_key = f"{slug}/{tool_name}"
    return _cached_tool_overrides.get(tool_key, {}).get(action_type)


def _load_guard_config() -> dict[str, str]:
    """Load guard policy from user config. Cached after first call."""
    global _cached_guard_config, _cached_tool_overrides, _config_loaded
    if _config_loaded:
        return dict(_cached_guard_config or _DEFAULT_GUARD_POLICY)
    _config_loaded = True
    try:
        from agentnode_sdk.config import load_config
        cfg = load_config()
        guard_cfg = cfg.get("guard", {})
        if isinstance(guard_cfg, dict):
            merged = dict(_DEFAULT_GUARD_POLICY)
            for k, v in guard_cfg.items():
                if k in ACTION_TYPES and v in ("allow", "prompt", "deny"):
                    merged[k] = v
            _cached_guard_config = merged
            _cached_tool_overrides = _parse_tool_overrides(
                guard_cfg.get("tool_overrides", {}),
            )
            return dict(merged)
    except Exception:
        pass
    _cached_guard_config = dict(_DEFAULT_GUARD_POLICY)
    _cached_tool_overrides = {}
    return dict(_cached_guard_config)


def _is_strict() -> bool:
    return os.environ.get("AGENTNODE_GUARD_STRICT", "").lower() in ("true", "1")


def _get_effective_policy() -> dict[str, str]:
    if _is_strict():
        return dict(_STRICT_GUARD_POLICY)
    return _load_guard_config()


def reset_guard_config_cache() -> None:
    """Reset cached config — for testing only."""
    global _cached_guard_config, _cached_tool_overrides, _config_loaded
    _cached_guard_config = None
    _cached_tool_overrides = {}
    _config_loaded = False


def get_resolved_policy() -> dict[str, Any]:
    """Return the fully resolved guard policy for display purposes.

    Read-only — no side effects, no audit writes.
    """
    strict = _is_strict()
    policy = _get_effective_policy()

    rate_limits_default = _get_rate_limits("__default__")
    rate_limits_agent = _get_rate_limits("__default__", {"package_type": "agent"})
    rate_limits_strict: dict[str, int] | None = None
    if not strict:
        saved = os.environ.get("AGENTNODE_GUARD_STRICT", "")
        os.environ["AGENTNODE_GUARD_STRICT"] = "true"
        rate_limits_strict = _get_rate_limits("__default__")
        if saved:
            os.environ["AGENTNODE_GUARD_STRICT"] = saved
        else:
            os.environ.pop("AGENTNODE_GUARD_STRICT", None)
    else:
        rate_limits_strict = dict(rate_limits_default)

    agent_overrides: dict = {}
    try:
        from agentnode_sdk.config import load_config
        cfg = load_config()
        agent_overrides = cfg.get("guard", {}).get("agent_overrides", {})
    except Exception:
        pass

    _load_guard_config()
    tool_overrides = dict(_cached_tool_overrides)

    return {
        "strict_mode": strict,
        "action_policies": policy,
        "credential_rule": "allow only with declared connector/credential scope; otherwise prompt",
        "rate_limits": {
            "toolpack": rate_limits_default,
            "agent": rate_limits_agent,
            "strict": rate_limits_strict,
        },
        "agent_overrides": agent_overrides,
        "tool_overrides": tool_overrides,
    }


# ---------------------------------------------------------------------------
# Action classification (Spec §3)
# ---------------------------------------------------------------------------

def classify_action(
    tool_name: str | None,
    entry: dict,
) -> list[str]:
    """Classify tool action types from manifest, name heuristic, and permissions."""
    action_types: set[str] = set()

    # 1. Manifest declaration (highest priority)
    tools = entry.get("tools", [])
    if tools and tool_name:
        for t in tools:
            if isinstance(t, dict) and t.get("name") == tool_name:
                declared = t.get("action_type")
                if declared and declared in ACTION_TYPES:
                    action_types.add(declared)
                break

    # 2. Name heuristic (fallback if no manifest declaration)
    if not action_types and tool_name:
        for pattern, action in _NAME_HEURISTICS:
            if pattern.search(tool_name):
                action_types.add(action)
                break

    # 3. Permission signals (additional escalation)
    perms = entry.get("permissions") or {}
    net_level = perms.get("network_level", "none")
    if net_level not in ("none", ""):
        action_types.add("network_egress")

    code_level = perms.get("code_execution_level", "none")
    if code_level not in ("none", ""):
        action_types.add("execute")

    connector = entry.get("connector") or {}
    if connector.get("auth_type"):
        action_types.add("credential_use")

    if not action_types:
        action_types.add("unknown")

    return sorted(action_types)


# ---------------------------------------------------------------------------
# Risk computation (Spec §4)
# ---------------------------------------------------------------------------

def _compute_risk_score(
    action_types: list[str],
    trust_level: str,
    env_has_secrets: bool,
) -> tuple[str, int]:
    """Compute risk level and score from action types + context."""
    score = 0

    if "delete" in action_types:
        score += 20
    if "write_external" in action_types:
        score += 15
    if "execute" in action_types:
        score += 15
    if "credential_use" in action_types:
        score += 10

    has_high = bool(set(action_types) & _HIGH_RISK_ACTIONS)

    if trust_level == "unverified" and has_high:
        score += 20

    if env_has_secrets and "network_egress" in action_types:
        score += 15

    # INV-2: unknown escalation
    if "unknown" in action_types:
        perms_signal = any(a in action_types for a in ("network_egress", "execute", "credential_use"))
        if perms_signal:
            score = max(score, 46)

    # Critical escalation: unverified + high-risk action + secrets (Spec §4)
    if trust_level == "unverified" and has_high and env_has_secrets:
        score = max(score, 71)

    if score >= 71:
        return "critical", score
    if score >= 46:
        return "high", score
    if score >= 21:
        return "medium", score
    return "low", score


# ---------------------------------------------------------------------------
# Core guard check (Spec §7)
# ---------------------------------------------------------------------------

def check_action(
    slug: str,
    tool_name: str | None,
    kwargs: dict,
    entry: dict,
    *,
    interactive: bool = True,
    action_types_override: list[str] | None = None,
) -> GuardDecision:
    """Pre-execution guard check: classify action, compute risk, apply policy.

    OC-3: Never returns allow on internal exception.
    """
    try:
        return _check_action_inner(
            slug, tool_name, kwargs, entry,
            interactive=interactive,
            action_types_override=action_types_override,
        )
    except Exception as exc:
        logger.warning("Guard internal error: %s", exc, exc_info=True)
        action = "deny" if _is_strict() else "prompt"
        return GuardDecision(
            action=action,
            reason=f"Guard internal error: {type(exc).__name__}",
            source="guard.internal_error",
            guard_chain=[f"guard_action:{action}(internal_error)"],
        )


def _check_action_inner(
    slug: str,
    tool_name: str | None,
    kwargs: dict,
    entry: dict,
    *,
    interactive: bool,
    action_types_override: list[str] | None = None,
) -> GuardDecision:
    action_types = list(action_types_override) if action_types_override else classify_action(tool_name, entry)
    trust_level = entry.get("trust_level", "unverified")

    env_has_secrets = _detect_has_secrets()
    risk_level, risk_score = _compute_risk_score(action_types, trust_level, env_has_secrets)

    policy = _get_effective_policy()
    chain: list[str] = []

    # Critical risk is always denied (unoverridable)
    if risk_level == "critical":
        chain.append("guard_action:deny(critical)")
        return GuardDecision(
            action="deny",
            reason=f"Critical risk level (score {risk_score}): action types {action_types}",
            action_types=action_types,
            risk_level=risk_level,
            source="guard.risk_critical",
            guard_chain=chain,
        )

    # Check agent pre_approved_actions (AD-7)
    is_agent = entry.get("package_type") == "agent"
    pre_approved: set[str] | None = None
    if is_agent:
        pre_approved = _get_pre_approved_actions(slug, entry)

    # Evaluate policy per action type
    worst_action = "allow"
    worst_reason = ""
    worst_source = "guard.default"
    strict = _is_strict()

    for at in action_types:
        # credential_use special handling
        if at == "credential_use":
            # Tool override takes priority over connector bypass (§4.2)
            if not strict:
                tool_ov = _get_tool_override(slug, tool_name, at)
                if tool_ov is not None:
                    tool_key = f"{slug}/{tool_name}"
                    chain.append(f"guard_action:{tool_ov}({at}:tool_override[{tool_key}])")
                    if _severity(tool_ov) > _severity(worst_action):
                        worst_action = tool_ov
                        worst_reason = f"credential_use overridden by tool policy [{tool_key}]"
                        worst_source = f"guard.tool_override.{tool_key}"
                    continue
            has_connector_scope = bool(
                (entry.get("connector") or {}).get("auth_type")
            )
            if has_connector_scope and policy.get("credential_use") != "deny":
                chain.append("guard_action:allow(credential_use:connector_declared)")
                continue
            else:
                decision = "prompt" if not strict else "deny"
                chain.append(f"guard_action:{decision}(credential_use:no_connector)")
                if _severity(decision) > _severity(worst_action):
                    worst_action = decision
                    worst_reason = "credential_use without declared Connector scope"
                    worst_source = "guard.credential_use"
                continue

        # Tool override — above global policy and agent pre_approved (§4.1)
        if not strict:
            tool_ov = _get_tool_override(slug, tool_name, at)
            if tool_ov is not None:
                tool_key = f"{slug}/{tool_name}"
                chain.append(f"guard_action:{tool_ov}({at}:tool_override[{tool_key}])")
                if tool_ov == "deny":
                    worst_action = "deny"
                    worst_reason = f"Action type '{at}' denied by tool override [{tool_key}]"
                    worst_source = f"guard.tool_override.{tool_key}"
                    break
                elif _severity(tool_ov) > _severity(worst_action):
                    worst_action = tool_ov
                    worst_reason = f"Action type '{at}' requires confirmation (tool override [{tool_key}])"
                    worst_source = f"guard.tool_override.{tool_key}"
                continue

        at_policy = policy.get(at, "prompt")

        # Agent pre_approved_actions override
        if is_agent and pre_approved is not None:
            if at in pre_approved:
                chain.append(f"guard_action:allow({at}:pre_approved)")
                continue
            elif at in _HIGH_RISK_ACTIONS:
                decision = "deny" if not interactive else "prompt"
                if not interactive:
                    decision = "deny"
                chain.append(f"guard_action:{decision}({at}:agent_not_approved)")
                if _severity(decision) > _severity(worst_action):
                    worst_action = decision
                    worst_reason = f"Agent action '{at}' not in pre_approved_actions"
                    worst_source = f"guard.agent.{at}"
                continue

        if at_policy == "deny":
            chain.append(f"guard_action:deny({at})")
            worst_action = "deny"
            worst_reason = f"Action type '{at}' denied by guard policy"
            worst_source = f"guard.action_policy.{at}"
            break
        elif at_policy == "prompt":
            chain.append(f"guard_action:prompt({at})")
            if _severity("prompt") > _severity(worst_action):
                worst_action = "prompt"
                worst_reason = f"Action type '{at}' requires confirmation"
                worst_source = f"guard.action_policy.{at}"
        else:
            chain.append(f"guard_action:allow({at})")

    if worst_action == "allow":
        chain.append("guard_action:allow")

    mitigations = []
    if worst_action == "prompt":
        mitigations.append("Confirm execution to proceed")
        mitigations.append(f"Or set guard.{action_types[0]}=allow in config")
    elif worst_action == "deny":
        mitigations.append("Change guard policy or use a higher trust package")

    return GuardDecision(
        action=worst_action,
        reason=worst_reason if worst_action != "allow" else "All action types allowed",
        action_types=action_types,
        risk_level=risk_level,
        mitigations=mitigations,
        source=worst_source if worst_action != "allow" else "guard.default",
        guard_chain=chain,
    )


def _get_pre_approved_actions(slug: str, entry: dict) -> set[str]:
    """Get pre_approved_actions for an agent from manifest and config."""
    approved: set[str] = set()

    # From manifest
    agent_section = entry.get("agent") or {}
    manifest_approved = agent_section.get("pre_approved_actions", [])
    if isinstance(manifest_approved, list):
        approved.update(a for a in manifest_approved if a in ACTION_TYPES)

    # From config overrides
    try:
        from agentnode_sdk.config import load_config
        cfg = load_config()
        overrides = cfg.get("guard", {}).get("agent_overrides", {}).get(slug, {})
        config_approved = overrides.get("pre_approved_actions", [])
        if isinstance(config_approved, list):
            approved.update(a for a in config_approved if a in ACTION_TYPES)
    except Exception:
        pass

    return approved


def _severity(action: str) -> int:
    return {"allow": 0, "prompt": 1, "deny": 2}.get(action, 0)


def _detect_has_secrets() -> bool:
    """Lightweight secrets detection (no import from policy to avoid coupling)."""
    prefixes = ("AWS_", "OPENAI_", "STRIPE_", "GCP_", "AZURE_", "ANTHROPIC_", "DATABASE_URL", "SECRET")
    return any(
        any(key.startswith(p) or key == p for p in prefixes)
        for key in os.environ
    )


# ---------------------------------------------------------------------------
# MCP argument inspection (Spec §8)
# ---------------------------------------------------------------------------

def inspect_mcp_args(
    slug: str,
    tool_name: str,
    args: dict,
    entry: dict,
    input_schema: dict | None = None,
) -> GuardDecision:
    """Inspect MCP tool arguments for dangerous patterns and schema violations.

    Returns deny for clear technical violations, prompt for schema/heuristic
    findings. When ``input_schema`` is provided, validates args against the
    schema and uses field context to suppress false-positive heuristics.
    OC-3: Internal exceptions → fail-closed.
    """
    try:
        return _inspect_mcp_args_inner(slug, tool_name, args, entry, input_schema)
    except Exception as exc:
        logger.warning("MCP inspection error: %s", exc, exc_info=True)
        action = "deny" if _is_strict() else "prompt"
        return GuardDecision(
            action=action,
            reason=f"MCP argument inspection error: {type(exc).__name__}",
            source="guard.mcp_inspection_error",
        )


def _inspect_mcp_args_inner(
    slug: str,
    tool_name: str,
    args: dict,
    entry: dict,
    input_schema: dict | None = None,
) -> GuardDecision:
    findings: list[str] = []
    deny_reasons: list[str] = []
    prompt_reasons: list[str] = []

    # Oversized payload check (before deep inspection)
    try:
        payload_size = len(json.dumps(args))
    except (TypeError, ValueError):
        payload_size = 0
    if payload_size > MAX_PAYLOAD_BYTES:
        deny_reasons.append(f"Oversized payload: {payload_size} bytes (limit {MAX_PAYLOAD_BYTES})")
        findings.append("oversized_payload")

    # Schema validation (Phase 6.3)
    schema = _safe_schema(input_schema)
    schema_props = {}
    schema_findings: list[str] = []
    if schema:
        schema_props = schema.get("properties", {})
        schema_findings = _validate_schema(args, schema)
        if schema_findings:
            for sf in schema_findings:
                prompt_reasons.append(sf)
            findings.append("schema_violation")

    # Deep inspection with recursion limits + schema-aware heuristic suppression
    key_count = [0]
    perms = entry.get("permissions") or {}
    net_level = perms.get("network_level", "none")

    def _is_freetext(path: str) -> bool:
        """Check if a field is declared as free-text in schema."""
        if not schema_props:
            return False
        field = path.split(".")[0]
        prop = schema_props.get(field, {})
        if not isinstance(prop, dict) or prop.get("type") != "string":
            return False
        max_len = prop.get("maxLength")
        return max_len is None or max_len >= 1000

    def _is_url_field(path: str) -> bool:
        """Check if schema declares this field as a URL/URI."""
        if not schema_props:
            return False
        field = path.split(".")[0]
        prop = schema_props.get(field, {})
        return isinstance(prop, dict) and prop.get("format") in ("uri", "url")

    def inspect_value(val: Any, depth: int, path: str) -> None:
        if depth > MAX_NESTING_DEPTH:
            deny_reasons.append(f"Nesting depth exceeds {MAX_NESTING_DEPTH} at {path}")
            findings.append("excessive_nesting")
            return

        if isinstance(val, str):
            if len(val) > MAX_STRING_LENGTH:
                deny_reasons.append(f"String at {path} exceeds {MAX_STRING_LENGTH} chars")
                findings.append("oversized_string")
                return

            if _PATH_TRAVERSAL_RE.search(val):
                deny_reasons.append(f"Path traversal at {path}: contains '../'")
                findings.append("path_traversal")

            if _ABSOLUTE_PATH_RE.match(val) and len(val) > 3:
                deny_reasons.append(f"Absolute path escape at {path}")
                findings.append("absolute_path_escape")

            if net_level == "none" and _URL_RE.search(val):
                if not _is_url_field(path):
                    deny_reasons.append(f"URL at {path} but network_level=none")
                    findings.append("url_anomaly")

            if _SHELL_TOKEN_RE.search(val):
                if not _is_freetext(path):
                    prompt_reasons.append(f"Shell token at {path}")
                    findings.append("shell_token")

        elif isinstance(val, dict):
            for k, v in val.items():
                key_count[0] += 1
                if key_count[0] > MAX_TOTAL_KEYS:
                    deny_reasons.append(f"Total keys exceed {MAX_TOTAL_KEYS}")
                    findings.append("excessive_keys")
                    return
                inspect_value(v, depth + 1, f"{path}.{k}")

        elif isinstance(val, (list, tuple)):
            for i, item in enumerate(val):
                inspect_value(item, depth + 1, f"{path}[{i}]")

    for k, v in args.items():
        key_count[0] += 1
        if key_count[0] > MAX_TOTAL_KEYS:
            deny_reasons.append(f"Total keys exceed {MAX_TOTAL_KEYS}")
            findings.append("excessive_keys")
            break
        inspect_value(v, 0, k)

    if deny_reasons:
        return GuardDecision(
            action="deny",
            reason="; ".join(deny_reasons),
            source="guard.mcp_inspection",
            guard_chain=[f"mcp_inspect:deny({','.join(findings)})"],
        )

    if prompt_reasons:
        return GuardDecision(
            action="prompt",
            reason="; ".join(prompt_reasons),
            source="guard.mcp_inspection",
            guard_chain=[f"mcp_inspect:prompt({','.join(findings)})"],
        )

    return GuardDecision(
        action="allow",
        reason="MCP arguments passed inspection",
        source="guard.mcp_inspection",
        guard_chain=["mcp_inspect:allow"],
    )


# ---------------------------------------------------------------------------
# Minimal JSON Schema subset validator (Phase 6.3)
# ---------------------------------------------------------------------------

_SCHEMA_TYPE_MAP: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "array": (list, tuple),
    "object": dict,
}


def _safe_schema(schema: dict | None) -> dict | None:
    """Return schema if it looks valid, None otherwise (malformed → fallback)."""
    if schema is None:
        return None
    if not isinstance(schema, dict):
        logger.debug("Malformed schema: not a dict")
        return None
    if schema.get("type") not in ("object", None):
        logger.debug("Malformed schema: root type is %r", schema.get("type"))
        return None
    props = schema.get("properties")
    if props is not None and not isinstance(props, dict):
        logger.debug("Malformed schema: properties is not a dict")
        return None
    return schema


def _validate_schema(args: dict, schema: dict) -> list[str]:
    """Validate args against a JSON Schema subset.

    Returns a list of human-readable violation strings.
    Schema violations → prompt (not deny).
    """
    violations: list[str] = []
    props = schema.get("properties", {})
    required = schema.get("required", [])

    # Required field check
    if isinstance(required, list):
        for field in required:
            if isinstance(field, str) and field not in args:
                violations.append(f"Missing required field: '{field}'")

    # Per-field validation
    for field_name, value in args.items():
        if field_name not in props:
            continue
        field_schema = props[field_name]
        if not isinstance(field_schema, dict):
            continue

        field_violations = _validate_field(field_name, value, field_schema)
        violations.extend(field_violations)

    return violations


def _validate_field(name: str, value: Any, schema: dict) -> list[str]:
    """Validate a single field value against its schema."""
    violations: list[str] = []

    # Type check
    expected_type = schema.get("type")
    if expected_type and expected_type != "null":
        if expected_type in _SCHEMA_TYPE_MAP:
            expected_python = _SCHEMA_TYPE_MAP[expected_type]
            if value is not None:
                # bool is a subclass of int in Python — reject bools for int/number
                if isinstance(value, bool) and expected_type in ("integer", "number"):
                    violations.append(
                        f"Type mismatch for '{name}': expected {expected_type}, "
                        f"got {type(value).__name__}"
                    )
                    return violations
                if not isinstance(value, expected_python):
                    # int is valid for "number"
                    if not (expected_type == "number" and isinstance(value, int)):
                        violations.append(
                            f"Type mismatch for '{name}': expected {expected_type}, "
                            f"got {type(value).__name__}"
                        )
                        return violations

    # Null check
    if value is None:
        if expected_type and expected_type != "null":
            violations.append(f"Null value for non-nullable field '{name}'")
        return violations

    # Enum check
    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and value not in enum_values:
        violations.append(
            f"Enum violation for '{name}': {value!r} not in {enum_values}"
        )

    # String constraints
    if isinstance(value, str):
        min_len = schema.get("minLength")
        max_len = schema.get("maxLength")
        if isinstance(min_len, int) and len(value) < min_len:
            violations.append(
                f"String too short for '{name}': {len(value)} < minLength {min_len}"
            )
        if isinstance(max_len, int) and len(value) > max_len:
            violations.append(
                f"String too long for '{name}': {len(value)} > maxLength {max_len}"
            )

    # Number constraints
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            violations.append(
                f"Value too small for '{name}': {value} < minimum {minimum}"
            )
        if isinstance(maximum, (int, float)) and value > maximum:
            violations.append(
                f"Value too large for '{name}': {value} > maximum {maximum}"
            )

    # Array constraints
    if isinstance(value, (list, tuple)):
        min_items = schema.get("minItems")
        max_items = schema.get("maxItems")
        if isinstance(min_items, int) and len(value) < min_items:
            violations.append(
                f"Too few items for '{name}': {len(value)} < minItems {min_items}"
            )
        if isinstance(max_items, int) and len(value) > max_items:
            violations.append(
                f"Too many items for '{name}': {len(value)} > maxItems {max_items}"
            )

    return violations


# ---------------------------------------------------------------------------
# Rate limiting (Spec §11, INV-3)
# ---------------------------------------------------------------------------

@dataclass
class _RateLimitBucket:
    timestamps: list[float] = field(default_factory=list)


_rate_limits: dict[str, _RateLimitBucket] = {}
_rate_limit_lock = __import__("threading").Lock()


def check_rate_limit(slug: str, tool_name: str | None = None, entry: dict | None = None) -> GuardDecision:
    """Check per-slug rate limit using sliding window.

    INV-3: monotonic clock, stale cleanup, max tracked keys.
    OC-3: Internal exceptions → fail-closed.
    """
    try:
        return _check_rate_limit_inner(slug, entry)
    except Exception as exc:
        logger.warning("Rate limit error: %s", exc, exc_info=True)
        action = "deny" if _is_strict() else "prompt"
        return GuardDecision(
            action=action,
            reason=f"Rate limit check error: {type(exc).__name__}",
            source="guard.rate_limit_error",
        )


def _check_rate_limit_inner(slug: str, entry: dict | None = None) -> GuardDecision:
    now = time.monotonic()
    limits = _get_rate_limits(slug, entry)
    calls_per_minute = limits.get("calls_per_minute", 60)
    calls_per_hour = limits.get("calls_per_hour", 1000)
    burst_size = limits.get("burst_size", 10)

    with _rate_limit_lock:
        # Evict oldest slug if over max tracked
        if slug not in _rate_limits and len(_rate_limits) >= MAX_TRACKED_SLUGS:
            oldest_key = min(_rate_limits, key=lambda k: _rate_limits[k].timestamps[0] if _rate_limits[k].timestamps else 0)
            del _rate_limits[oldest_key]

        bucket = _rate_limits.setdefault(slug, _RateLimitBucket())

        # Stale cleanup: remove entries older than 1 hour
        cutoff_hour = now - 3600
        bucket.timestamps = [t for t in bucket.timestamps if t > cutoff_hour]

        # Check burst (1 second window)
        cutoff_burst = now - 1.0
        burst_count = sum(1 for t in bucket.timestamps if t > cutoff_burst)
        if burst_count >= burst_size:
            return GuardDecision(
                action="deny",
                reason=f"Rate limit exceeded: {burst_count} calls in 1s (burst limit {burst_size})",
                source="guard.rate_limit",
                guard_chain=[f"rate_limit:deny(burst:{burst_count}/{burst_size})"],
            )

        # Check per-minute
        cutoff_min = now - 60
        min_count = sum(1 for t in bucket.timestamps if t > cutoff_min)
        if min_count >= calls_per_minute:
            return GuardDecision(
                action="deny",
                reason=f"Rate limit exceeded: {min_count} calls/min (limit {calls_per_minute})",
                source="guard.rate_limit",
                guard_chain=[f"rate_limit:deny(minute:{min_count}/{calls_per_minute})"],
            )

        # Check per-hour
        hour_count = len(bucket.timestamps)
        if hour_count >= calls_per_hour:
            return GuardDecision(
                action="deny",
                reason=f"Rate limit exceeded: {hour_count} calls/hour (limit {calls_per_hour})",
                source="guard.rate_limit",
                guard_chain=[f"rate_limit:deny(hour:{hour_count}/{calls_per_hour})"],
            )

        # Record this call
        bucket.timestamps.append(now)

    return GuardDecision(
        action="allow",
        reason="Within rate limits",
        source="guard.rate_limit",
        guard_chain=["rate_limit:allow"],
    )


def _get_rate_limits(slug: str, entry: dict | None = None) -> dict[str, int]:
    """Get rate limit config for a slug.

    Priority: strict mode > agent defaults > base defaults.
    User config overrides all.
    """
    if _is_strict():
        defaults = {"calls_per_minute": 30, "calls_per_hour": 500, "burst_size": 10}
    elif entry and entry.get("package_type") == "agent":
        defaults = {"calls_per_minute": 120, "calls_per_hour": 2000, "burst_size": 20}
    else:
        defaults = {"calls_per_minute": 60, "calls_per_hour": 1000, "burst_size": 10}
    try:
        from agentnode_sdk.config import load_config
        cfg = load_config()
        guard_cfg = cfg.get("guard", {})
        rl_cfg = guard_cfg.get("rate_limits", {})
        if isinstance(rl_cfg, dict):
            slug_cfg = rl_cfg.get(slug, rl_cfg.get("default", {}))
            if isinstance(slug_cfg, dict):
                for k in defaults:
                    if k in slug_cfg and isinstance(slug_cfg[k], int):
                        defaults[k] = slug_cfg[k]
    except Exception:
        pass
    return defaults


def reset_rate_limits() -> None:
    """Reset rate limit state — for testing only."""
    with _rate_limit_lock:
        _rate_limits.clear()


# ---------------------------------------------------------------------------
# CLI Guard UX (Phase 6.2a)
# ---------------------------------------------------------------------------

_RISK_COLORS = {
    "critical": "\033[31m",  # red
    "high": "\033[31m",
    "medium": "\033[33m",    # yellow
    "low": "\033[32m",       # green
}
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"


def format_guard_prompt(
    slug: str,
    tool_name: str | None,
    decision: GuardDecision,
    entry: dict | None = None,
) -> str:
    """Build a human-readable confirmation prompt for guard "prompt" decisions."""
    lines: list[str] = []
    risk_color = _RISK_COLORS.get(decision.risk_level, "")

    lines.append("")
    lines.append(f"  {_BOLD}Guard: confirmation required{_RESET}")
    lines.append(f"  {'─' * 30}")
    lines.append(f"  Package:      {slug}")
    if tool_name:
        lines.append(f"  Tool:         {tool_name}")
    if decision.action_types:
        lines.append(f"  Action types: {', '.join(decision.action_types)}")
    lines.append(f"  Risk level:   {risk_color}{decision.risk_level}{_RESET}")
    lines.append(f"  Reason:       {decision.reason}")

    trust = (entry or {}).get("trust_level", "unknown")
    lines.append(f"  Trust:        {trust}")

    if decision.mitigations:
        lines.append("")
        for m in decision.mitigations:
            lines.append(f"  {_DIM}→ {m}{_RESET}")

    lines.append("")
    lines.append(f"  {_DIM}Default: No (tool will NOT run){_RESET}")
    lines.append("")
    return "\n".join(lines)


def format_guard_deny(
    slug: str,
    tool_name: str | None,
    decision: GuardDecision,
    entry: dict | None = None,
) -> str:
    """Build a human-readable message for guard "deny" decisions."""
    lines: list[str] = []
    risk_color = _RISK_COLORS.get(decision.risk_level, "")

    lines.append("")
    lines.append(f"  {_BOLD}\033[31mGuard: execution blocked{_RESET}")
    lines.append(f"  {'─' * 30}")
    lines.append(f"  Package:      {slug}")
    if tool_name:
        lines.append(f"  Tool:         {tool_name}")
    if decision.action_types:
        lines.append(f"  Action types: {', '.join(decision.action_types)}")
    lines.append(f"  Risk level:   {risk_color}{decision.risk_level}{_RESET}")
    lines.append(f"  Reason:       {decision.reason}")

    trust = (entry or {}).get("trust_level", "unknown")
    lines.append(f"  Trust:        {trust}")

    if decision.mitigations:
        lines.append("")
        for m in decision.mitigations:
            lines.append(f"  {_DIM}→ {m}{_RESET}")

    lines.append("")
    return "\n".join(lines)


def cli_confirmation_callback(
    slug: str,
    tool_name: str | None,
    decision: GuardDecision,
    entry: dict | None = None,
) -> bool:
    """Interactive CLI confirmation callback. Default: No (fail-closed)."""
    import sys

    prompt_text = format_guard_prompt(slug, tool_name, decision, entry)
    print(prompt_text, file=sys.stderr)

    try:
        answer = input("  Proceed? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("", file=sys.stderr)
        return False

    return answer in ("y", "yes")
