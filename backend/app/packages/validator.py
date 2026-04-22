"""ANP manifest validation — all rules from spec section 6. Supports v0.1 and v0.2."""
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.packages.models import CapabilityTaxonomy

# Valid enums
VALID_PACKAGE_TYPES = {"agent", "toolpack", "upgrade"}
VALID_RUNTIMES = {"python", "mcp", "remote"}
VALID_INSTALL_MODES = {"package", "remote_endpoint"}
VALID_HOSTING_TYPES = {"agentnode_hosted"}  # MVP restriction
VALID_CONNECTOR_AUTH_TYPES = {"api_key", "oauth2"}  # NO "custom" in v0.3
VALID_NETWORK_LEVELS = {"none", "restricted", "unrestricted"}
VALID_FS_LEVELS = {"none", "temp", "workspace_read", "workspace_write", "any"}
VALID_EXEC_LEVELS = {"none", "limited_subprocess", "shell"}
VALID_DATA_LEVELS = {"input_only", "connected_accounts", "persistent"}
VALID_APPROVAL_LEVELS = {"always", "high_risk_only", "once", "never"}

SLUG_PATTERN = re.compile(r"^[a-z0-9-]{3,60}$")
SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?(\+[a-zA-Z0-9.]+)?$")

# v0.1: module.path (e.g. pdf_reader_pack.tool)
ENTRYPOINT_PATTERN_V1 = re.compile(r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)+$")
# v0.2 tool-level: module.path:function (e.g. csv_analyzer_pack.tool:describe)
ENTRYPOINT_PATTERN_V2 = re.compile(r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)+:[a-z_][a-z0-9_]*$")

VALID_MANIFEST_VERSIONS = {"0.1", "0.2"}

# Valid JSON Schema types
VALID_JSON_SCHEMA_TYPES = {"string", "integer", "number", "boolean", "array", "object", "null"}

# --- v0.2 Manifest Normalization ---

MANIFEST_DEFAULTS = {
    "runtime": "python",
    "install_mode": "package",
    "hosting_type": "agentnode_hosted",
    "dependencies": [],
    "tags": [],
    "categories": [],
    "permissions": {
        "network": {"level": "none", "allowed_domains": []},
        "filesystem": {"level": "none"},
        "code_execution": {"level": "none"},
        "data_access": {"level": "input_only"},
        "user_approval": {"required": "never"},
        "external_integrations": [],
    },
    "security": {
        "signature": "",
        "provenance": {"source_repo": "", "commit": "", "build_system": "manual"},
    },
    "support": {"homepage": "", "issues": ""},
    "compatibility": {"frameworks": ["generic"]},
    "deprecation_policy": "6-months-notice",
    # v0.2 enrichment defaults
    "env_requirements": [],
    "use_cases": [],
    "examples": [],
}


def normalize_manifest(manifest: dict) -> dict:
    """Apply v0.2 defaults to compact manifests. Only for manifest_version 0.2.

    v0.1 manifests pass through unchanged.
    """
    if manifest.get("manifest_version") != "0.2":
        return manifest

    m = {**manifest}
    for key, default in MANIFEST_DEFAULTS.items():
        if key not in m:
            m[key] = default
        elif isinstance(default, dict) and isinstance(m[key], dict):
            merged = {**default, **m[key]}
            m[key] = merged

    # Ensure capabilities has resources/prompts arrays
    caps = m.get("capabilities", {})
    caps.setdefault("resources", [])
    caps.setdefault("prompts", [])
    m["capabilities"] = caps

    return m


# --- JSON Schema Validation ---

def _validate_json_schema(schema: dict, path: str, errors: list[str]) -> None:
    """Validate that a dict is a valid JSON Schema structure (basic checks)."""
    if not isinstance(schema, dict):
        errors.append(f"{path} must be a JSON Schema object")
        return

    schema_type = schema.get("type")
    if schema_type is not None:
        valid_types = VALID_JSON_SCHEMA_TYPES
        if isinstance(schema_type, str):
            if schema_type not in valid_types:
                errors.append(f"{path}.type '{schema_type}' is not a valid JSON Schema type")
        elif isinstance(schema_type, list):
            for t in schema_type:
                if t not in valid_types:
                    errors.append(f"{path}.type contains invalid type '{t}'")
        else:
            errors.append(f"{path}.type must be a string or array")

    # Validate properties if present
    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, dict):
            errors.append(f"{path}.properties must be an object")
        else:
            for prop_name, prop_schema in properties.items():
                if not isinstance(prop_schema, dict):
                    errors.append(f"{path}.properties.{prop_name} must be a JSON Schema object")

    # Validate required if present
    required = schema.get("required")
    if required is not None:
        if not isinstance(required, list):
            errors.append(f"{path}.required must be an array")
        elif properties and isinstance(properties, dict):
            for req_field in required:
                if req_field not in properties:
                    errors.append(f"{path}.required field '{req_field}' not in properties")


# --- Type Combination Validation (S5) ---

_VALID_COMBINATIONS = {
    # (package_type, runtime, install_mode)
    ("toolpack", "python", "package"),
    ("toolpack", "mcp", "package"),
    ("toolpack", "remote", "remote_endpoint"),
    ("agent", "python", "package"),       # only valid agent combo in v1
    ("upgrade", "python", "package"),     # upgrade only as package
}


_COMBO_HINTS = {
    "toolpack": "Toolpacks support: python+package, mcp+package, or remote+remote_endpoint.",
    "agent": "Agents support only runtime=python and install_mode=package in v0.3.",
    "upgrade": "Upgrades support only runtime=python and install_mode=package.",
}


def _validate_type_combination(manifest: dict) -> list[str]:
    """Block invalid package_type x runtime x install_mode combinations."""
    combo = (
        manifest.get("package_type", "toolpack"),
        manifest.get("runtime", "python"),
        manifest.get("install_mode", "package"),
    )
    if combo not in _VALID_COMBINATIONS:
        hint = _COMBO_HINTS.get(combo[0], "See ANP v0.3 spec for valid combinations.")
        return [
            f"Invalid combination: package_type={combo[0]}, runtime={combo[1]}, "
            f"install_mode={combo[2]}. {hint}"
        ]

    errors = []

    # agent MUST have agent: section
    if combo[0] == "agent" and "agent" not in manifest:
        errors.append("package_type=agent requires an 'agent:' section")

    # connector: section only valid for toolpack
    if "connector" in manifest and combo[0] != "toolpack":
        errors.append("connector: section only valid for package_type=toolpack")

    # S9: upgrade must not have executable sections
    if combo[0] == "upgrade":
        if "agent" in manifest:
            errors.append("upgrade packages must not have an 'agent:' section")
        if "connector" in manifest:
            errors.append("upgrade packages must not have a 'connector:' section")
        caps = manifest.get("capabilities", {})
        if caps.get("tools"):
            errors.append("upgrade packages must not declare executable tools")

    return errors


# --- Prompt Validation (S2, S6, S11) ---

def _validate_prompts(prompts: list, errors: list[str], warnings: list[str]) -> None:
    """Validate capabilities.prompts[] entries."""
    if not isinstance(prompts, list):
        errors.append("capabilities.prompts must be an array")
        return

    for i, prompt in enumerate(prompts):
        if not isinstance(prompt, dict):
            errors.append(f"prompts[{i}] must be an object")
            continue

        if not prompt.get("name"):
            errors.append(f"prompts[{i}].name is required")

        if not prompt.get("capability_id"):
            errors.append(f"prompts[{i}].capability_id is required")

        if not prompt.get("template"):
            errors.append(f"prompts[{i}].template is required")

        # Prompts are NOT executable — reject entrypoint/input_schema
        if "entrypoint" in prompt:
            errors.append(f"prompts[{i}].entrypoint is not allowed (prompts are non-executable)")
        if "input_schema" in prompt:
            errors.append(f"prompts[{i}].input_schema is not allowed (prompts are non-executable)")

        # Validate arguments (S11: typed PromptArgumentSpec)
        arguments = prompt.get("arguments")
        if arguments is not None:
            if not isinstance(arguments, list):
                errors.append(f"prompts[{i}].arguments must be an array")
            else:
                for j, arg in enumerate(arguments):
                    if not isinstance(arg, dict):
                        errors.append(f"prompts[{i}].arguments[{j}] must be an object")
                    elif not arg.get("name"):
                        errors.append(f"prompts[{i}].arguments[{j}].name is required")


# --- Resource Validation (S2, S6, S10) ---

def _validate_resources(resources: list, errors: list[str], warnings: list[str]) -> None:
    """Validate capabilities.resources[] entries."""
    if not isinstance(resources, list):
        errors.append("capabilities.resources must be an array")
        return

    for i, resource in enumerate(resources):
        if not isinstance(resource, dict):
            errors.append(f"resources[{i}] must be an object")
            continue

        if not resource.get("name"):
            errors.append(f"resources[{i}].name is required")

        if not resource.get("capability_id"):
            errors.append(f"resources[{i}].capability_id is required")

        uri = resource.get("uri", "")
        if not uri:
            errors.append(f"resources[{i}].uri is required")
        else:
            # S10: URI is identity — only resource:// and https:// allowed
            if not (uri.startswith("resource://") or uri.startswith("https://")):
                errors.append(
                    f"resources[{i}].uri must start with resource:// or https:// "
                    f"(got '{uri[:40]}'). No file:// allowed."
                )

        # Resources are NOT executable — reject entrypoint/input_schema
        if "entrypoint" in resource:
            errors.append(f"resources[{i}].entrypoint is not allowed (resources are non-executable)")
        if "input_schema" in resource:
            errors.append(f"resources[{i}].input_schema is not allowed (resources are non-executable)")

        # Optional: content_path for local content files (v0.4)
        content_path = resource.get("content_path")
        if content_path is not None:
            if not isinstance(content_path, str):
                errors.append(f"resources[{i}].content_path must be a string")
            elif ".." in content_path or content_path.startswith("/"):
                errors.append(f"resources[{i}].content_path must be a relative path without '..'")
            elif not content_path:
                errors.append(f"resources[{i}].content_path must not be empty if specified")


# --- Connector Block Validation (S3, S8) ---

def _validate_connector(connector: dict, errors: list[str], warnings: list[str]) -> None:
    """Validate the optional connector: section."""
    if not isinstance(connector, dict):
        errors.append("connector: must be an object")
        return

    # Required: provider
    if not connector.get("provider"):
        errors.append("connector.provider is required when connector: section is present")

    # auth_type: only api_key or oauth2 (NO "custom" in v0.3)
    auth_type = connector.get("auth_type")
    if auth_type is not None:
        if auth_type not in VALID_CONNECTOR_AUTH_TYPES:
            errors.append(
                f"connector.auth_type must be one of {sorted(VALID_CONNECTOR_AUTH_TYPES)} "
                f"(got '{auth_type}')"
            )

    # scopes: optional list[str]
    scopes = connector.get("scopes")
    if scopes is not None:
        if not isinstance(scopes, list):
            errors.append("connector.scopes must be an array")
        elif not all(isinstance(s, str) for s in scopes):
            errors.append("connector.scopes entries must be strings")

    # token_refresh: optional bool
    token_refresh = connector.get("token_refresh")
    if token_refresh is not None and not isinstance(token_refresh, bool):
        errors.append("connector.token_refresh must be a boolean")

    # health_check: optional object
    health_check = connector.get("health_check")
    if health_check is not None:
        if not isinstance(health_check, dict):
            errors.append("connector.health_check must be an object")
        else:
            if not health_check.get("endpoint"):
                errors.append("connector.health_check.endpoint is required")
            interval = health_check.get("interval_seconds")
            if interval is not None and (not isinstance(interval, int) or interval < 1):
                errors.append("connector.health_check.interval_seconds must be a positive integer")

    # rate_limits: optional object
    rate_limits = connector.get("rate_limits")
    if rate_limits is not None:
        if not isinstance(rate_limits, dict):
            errors.append("connector.rate_limits must be an object")
        else:
            rpm = rate_limits.get("requests_per_minute")
            if rpm is not None and (not isinstance(rpm, int) or rpm < 1):
                errors.append("connector.rate_limits.requests_per_minute must be a positive integer")


# --- Agent Block Validation (S4) ---

def _validate_agent(agent: dict, errors: list[str], warnings: list[str]) -> None:
    """Validate the agent: section (required for package_type=agent)."""
    if not isinstance(agent, dict):
        errors.append("agent: must be an object")
        return

    # Entrypoint: required unless orchestration.mode=sequential
    orchestration = agent.get("orchestration")
    has_sequential = (
        isinstance(orchestration, dict)
        and orchestration.get("mode") == "sequential"
    )

    entrypoint = agent.get("entrypoint", "")
    if not entrypoint and not has_sequential:
        errors.append("agent.entrypoint is required")
    elif entrypoint and not ENTRYPOINT_PATTERN_V2.match(entrypoint):
        errors.append(
            f"agent.entrypoint must be module.path:function format (got '{entrypoint}')"
        )

    # Required: goal
    if not agent.get("goal"):
        errors.append("agent.goal is required")

    # Optional: tool_access
    tool_access = agent.get("tool_access")
    if tool_access is not None:
        if not isinstance(tool_access, dict):
            errors.append("agent.tool_access must be an object")
        else:
            allowed = tool_access.get("allowed_packages")
            if allowed is not None:
                if not isinstance(allowed, list):
                    errors.append("agent.tool_access.allowed_packages must be an array")
                elif not all(isinstance(s, str) for s in allowed):
                    errors.append("agent.tool_access.allowed_packages entries must be strings")

    # Optional: limits
    limits = agent.get("limits")
    if limits is not None:
        if not isinstance(limits, dict):
            errors.append("agent.limits must be an object")
        else:
            _validate_int_range(limits, "max_iterations", 1, 100, "agent.limits", errors)
            _validate_int_range(limits, "max_tool_calls", 1, 500, "agent.limits", errors)
            _validate_int_range(limits, "max_runtime_seconds", 1, 3600, "agent.limits", errors)

    # Optional: termination
    termination = agent.get("termination")
    if termination is not None:
        if not isinstance(termination, dict):
            errors.append("agent.termination must be an object")
        else:
            stop_final = termination.get("stop_on_final_answer")
            if stop_final is not None and not isinstance(stop_final, bool):
                errors.append("agent.termination.stop_on_final_answer must be a boolean")

            _validate_int_range(
                termination, "stop_on_consecutive_errors", 1, 10,
                "agent.termination", errors,
            )

    # Optional: state
    state = agent.get("state")
    if state is not None:
        if not isinstance(state, dict):
            errors.append("agent.state must be an object")
        else:
            persistence = state.get("persistence")
            if persistence is not None and persistence not in ("none", "session"):
                errors.append(
                    f"agent.state.persistence must be 'none' or 'session' (got '{persistence}')"
                )

    # Orchestration (PR 7: sequential only)
    if orchestration is not None:
        if not isinstance(orchestration, dict):
            errors.append("agent.orchestration must be an object")
        else:
            mode = orchestration.get("mode")
            if mode is not None and mode != "sequential":
                errors.append(
                    f"agent.orchestration.mode must be 'sequential' "
                    f"(got '{mode}'). Only sequential is supported in v0.3."
                )
            if mode == "sequential":
                _validate_orchestration_steps(orchestration, errors, warnings)

    # Optional: isolation (v0.4)
    isolation = agent.get("isolation")
    if isolation is not None and isolation not in ("process", "thread"):
        errors.append(
            f"agent.isolation must be 'process' or 'thread' (got '{isolation}')"
        )

    # DEFERRED fields — reject if present
    for deferred in ("max_tokens", "planning"):
        if deferred in agent:
            errors.append(f"agent.{deferred} is not supported in v0.3")


def _validate_orchestration_steps(
    orchestration: dict, errors: list[str], warnings: list[str],
) -> None:
    """Validate orchestration.steps[] for sequential mode."""
    steps = orchestration.get("steps")
    if not steps:
        errors.append(
            "agent.orchestration.steps is required for sequential mode"
        )
        return
    if not isinstance(steps, list):
        errors.append("agent.orchestration.steps must be an array")
        return

    seen_names: set[str] = set()
    for i, step in enumerate(steps):
        prefix = f"agent.orchestration.steps[{i}]"
        if not isinstance(step, dict):
            errors.append(f"{prefix} must be an object")
            continue

        # Required: tool
        tool = step.get("tool")
        if not tool or not isinstance(tool, str):
            errors.append(f"{prefix}.tool is required and must be a string")

        # Optional: name (must be unique if present)
        name = step.get("name")
        if name is not None:
            if not isinstance(name, str):
                errors.append(f"{prefix}.name must be a string")
            elif name in seen_names:
                errors.append(f"{prefix}.name '{name}' is not unique")
            else:
                seen_names.add(name)

        # Optional: input_mapping
        input_mapping = step.get("input_mapping")
        if input_mapping is not None and not isinstance(input_mapping, dict):
            errors.append(f"{prefix}.input_mapping must be an object")

        # Optional: when condition (v0.4)
        when = step.get("when")
        if when is not None:
            if not isinstance(when, str):
                errors.append(f"{prefix}.when must be a string")
            else:
                _validate_when_expression(when, prefix, errors)


def _validate_when_expression(expr: str, prefix: str, errors: list[str]) -> None:
    """Validate a when condition expression.

    Allowed:
    - $ref == value
    - $ref != value
    - $ref is null
    - $ref is not null

    Anything else is a hard error.
    """
    expr = expr.strip()
    if not expr:
        errors.append(f"{prefix}.when must not be empty")
        return

    # Check "is not null"
    if expr.endswith(" is not null"):
        ref = expr[: -len(" is not null")].strip()
        if not ref.startswith("$"):
            errors.append(
                f"{prefix}.when: left side must be a $reference (got '{ref}')"
            )
        return

    # Check "is null"
    if expr.endswith(" is null"):
        ref = expr[: -len(" is null")].strip()
        if not ref.startswith("$"):
            errors.append(
                f"{prefix}.when: left side must be a $reference (got '{ref}')"
            )
        return

    # Check == / !=
    for op in ("!=", "=="):
        if f" {op} " in expr:
            ref_part, _ = expr.split(f" {op} ", 1)
            ref_part = ref_part.strip()
            if not ref_part.startswith("$"):
                errors.append(
                    f"{prefix}.when: left side must be a $reference (got '{ref_part}')"
                )
            return

    errors.append(
        f"{prefix}.when: invalid syntax '{expr}'. "
        "Allowed: '$ref == value', '$ref != value', '$ref is null', '$ref is not null'"
    )


def _validate_int_range(
    obj: dict, key: str, min_val: int, max_val: int,
    prefix: str, errors: list[str],
) -> None:
    """Validate an optional integer field is within range."""
    val = obj.get(key)
    if val is None:
        return
    if not isinstance(val, int) or isinstance(val, bool):
        errors.append(f"{prefix}.{key} must be an integer")
    elif val < min_val or val > max_val:
        errors.append(f"{prefix}.{key} must be between {min_val} and {max_val} (got {val})")


# --- Main Validation ---

async def validate_manifest(manifest: dict, session: AsyncSession | None = None) -> tuple[bool, list[str], list[str]]:
    """Validate an ANP manifest dict. Supports v0.1 and v0.2.

    Returns (valid, errors, warnings).
    """
    errors: list[str] = []
    warnings: list[str] = []

    # manifest_version
    manifest_version = manifest.get("manifest_version")
    if manifest_version not in VALID_MANIFEST_VERSIONS:
        errors.append("manifest_version MUST be '0.1' or '0.2'")

    # package_id
    pkg_id = manifest.get("package_id", "")
    if not SLUG_PATTERN.match(pkg_id):
        errors.append("package_id must match [a-z0-9-], 3-60 chars")

    # package_type
    pkg_type = manifest.get("package_type", "")
    if pkg_type not in VALID_PACKAGE_TYPES:
        errors.append(f"package_type must be one of {VALID_PACKAGE_TYPES}")

    # name
    name = manifest.get("name", "")
    if not name or len(name) > 100:
        errors.append("name must be 1-100 chars")
    elif len(name) < 3:
        errors.append("name must be at least 3 characters")

    # publisher
    publisher = manifest.get("publisher", "")
    if not publisher:
        errors.append("publisher is required")

    # version
    version = manifest.get("version", "")
    if not SEMVER_PATTERN.match(version):
        errors.append("version must be valid semver (e.g. 1.0.0)")

    # summary
    summary = manifest.get("summary", "")
    if not summary or len(summary) > 200:
        errors.append("summary must be 1-200 chars")
    elif len(summary) < 20:
        errors.append("summary must be at least 20 characters")

    # description — optional but recommended
    description = manifest.get("description", "")
    if not description:
        warnings.append("description is recommended for discoverability")
    elif description == summary:
        warnings.append("description should provide more detail than summary")

    # runtime (MVP: only python)
    runtime = manifest.get("runtime", "")
    if runtime not in VALID_RUNTIMES:
        errors.append(f"runtime must be one of {VALID_RUNTIMES} (got '{runtime}')")

    # install_mode
    install_mode = manifest.get("install_mode", "")
    if install_mode not in VALID_INSTALL_MODES:
        errors.append(f"install_mode must be one of {VALID_INSTALL_MODES} (got '{install_mode}')")

    # hosting_type (MVP: only agentnode_hosted)
    hosting_type = manifest.get("hosting_type", "")
    if hosting_type not in VALID_HOSTING_TYPES:
        errors.append(f"hosting_type must be 'agentnode_hosted' in MVP (got '{hosting_type}')")

    # --- Type combination validation (S5) ---
    combo_errors = _validate_type_combination(manifest)
    errors.extend(combo_errors)

    # --- Entrypoint validation (version-dependent, skip for agent/upgrade) ---
    if pkg_type not in ("agent", "upgrade"):
        _validate_entrypoints(manifest, manifest_version, errors)

    # capabilities.tools — conditional on package_type
    capabilities = manifest.get("capabilities", {})
    tools = capabilities.get("tools", [])
    if pkg_type == "toolpack" and not tools:
        errors.append("capabilities.tools must have at least 1 tool for toolpack packages")
    elif pkg_type == "upgrade" and tools:
        pass  # Already caught by _validate_type_combination / S9
    elif tools:
        valid_cap_ids = None
        if session:
            result = await session.execute(select(CapabilityTaxonomy.id))
            valid_cap_ids = {row[0] for row in result.all()}

        for i, tool in enumerate(tools):
            if not tool.get("name"):
                errors.append(f"tools[{i}].name must be non-empty")

            if not tool.get("description"):
                warnings.append(f"tools[{i}].description is recommended")

            cap_id = tool.get("capability_id", "")
            if not cap_id:
                errors.append(f"tools[{i}].capability_id is required")
            elif valid_cap_ids is not None and cap_id not in valid_cap_ids:
                warnings.append(f"tools[{i}].capability_id '{cap_id}' is new — will be added as uncategorized")

            # Validate input_schema as JSON Schema
            input_schema = tool.get("input_schema")
            if input_schema is not None:
                if not isinstance(input_schema, dict):
                    errors.append(f"tools[{i}].input_schema must be a valid JSON Schema object")
                else:
                    _validate_json_schema(input_schema, f"tools[{i}].input_schema", errors)

            # Validate output_schema if present
            output_schema = tool.get("output_schema")
            if output_schema is not None:
                if not isinstance(output_schema, dict):
                    errors.append(f"tools[{i}].output_schema must be a valid JSON Schema object")
                else:
                    _validate_json_schema(output_schema, f"tools[{i}].output_schema", errors)

    # --- Prompt validation (S2, S6, S11) ---
    prompts = capabilities.get("prompts")
    if prompts:
        _validate_prompts(prompts, errors, warnings)

    # --- Resource validation (S2, S6, S10) ---
    resources = capabilities.get("resources")
    if resources:
        _validate_resources(resources, errors, warnings)

    # --- Connector block validation (S3, S8) ---
    connector = manifest.get("connector")
    if connector is not None:
        _validate_connector(connector, errors, warnings)

    # --- Agent block validation (S4) ---
    agent_section = manifest.get("agent")
    if agent_section is not None and pkg_type == "agent":
        _validate_agent(agent_section, errors, warnings)

    # permissions
    perms = manifest.get("permissions", {})
    if perms:
        network = perms.get("network", {})
        if network.get("level", "") not in VALID_NETWORK_LEVELS:
            errors.append(f"permissions.network.level must be one of {VALID_NETWORK_LEVELS}")

        fs = perms.get("filesystem", {})
        if fs.get("level", "") not in VALID_FS_LEVELS:
            errors.append(f"permissions.filesystem.level must be one of {VALID_FS_LEVELS}")

        exec_lvl = perms.get("code_execution", {})
        if exec_lvl.get("level", "") not in VALID_EXEC_LEVELS:
            errors.append(f"permissions.code_execution.level must be one of {VALID_EXEC_LEVELS}")

        data = perms.get("data_access", {})
        if data.get("level", "") not in VALID_DATA_LEVELS:
            errors.append(f"permissions.data_access.level must be one of {VALID_DATA_LEVELS}")

        approval = perms.get("user_approval", {})
        if approval.get("required", "") not in VALID_APPROVAL_LEVELS:
            errors.append(f"permissions.user_approval.required must be one of {VALID_APPROVAL_LEVELS}")

        # Warn about high-risk permissions
        if network.get("level") == "unrestricted":
            warnings.append("permissions.network.level is 'unrestricted' — packages with unrestricted network access receive lower trust scores")
        if exec_lvl.get("level") == "shell":
            warnings.append("permissions.code_execution.level is 'shell' — shell access is flagged in policy checks")
    else:
        errors.append("permissions section is required")

    # compatibility.frameworks — default to ["generic"] if missing
    compat = manifest.get("compatibility", {})
    frameworks = compat.get("frameworks", [])
    if not frameworks:
        manifest.setdefault("compatibility", {})["frameworks"] = ["generic"]

    # Optional but valuable fields — generate warnings
    if not manifest.get("tags"):
        warnings.append("tags are recommended for search discoverability")

    if not manifest.get("categories"):
        warnings.append("categories are recommended for browsing")

    # Upgrade-specific validations
    if pkg_type == "upgrade":
        upgrade_meta = manifest.get("upgrade_metadata", {})
        rec_for = upgrade_meta.get("recommended_for", [])
        replaces = upgrade_meta.get("replaces", [])
        slug_re = re.compile(r"^[a-z0-9][a-z0-9-]{1,58}[a-z0-9]$")

        if not rec_for:
            errors.append("upgrade_metadata.recommended_for is required for upgrade packages")
        elif not isinstance(rec_for, list):
            errors.append("upgrade_metadata.recommended_for must be an array of package slugs")
        else:
            for slug_val in rec_for:
                if not isinstance(slug_val, str) or not slug_re.match(slug_val):
                    errors.append(f"upgrade_metadata.recommended_for contains invalid slug: {slug_val!r}")
                    break

        if replaces and isinstance(replaces, list):
            for slug_val in replaces:
                if not isinstance(slug_val, str) or not slug_re.match(slug_val):
                    errors.append(f"upgrade_metadata.replaces contains invalid slug: {slug_val!r}")
                    break

        if not upgrade_meta.get("roles"):
            warnings.append("upgrade_metadata.roles is recommended for upgrade packages")

    # --- v0.2 enrichment field validation (optional) ---
    _validate_enrichment_fields(manifest, errors, warnings)

    return len(errors) == 0, errors, warnings


def _validate_entrypoints(manifest: dict, manifest_version: str | None, errors: list[str]) -> None:
    """Validate entrypoints based on manifest version.

    v0.1: package-level entrypoint required, must be module.path format.
    v0.2: per-tool entrypoints supported. Multi-tool packs MUST have tool-level entrypoints.
    """
    pkg_entrypoint = manifest.get("entrypoint", "")
    tools = manifest.get("capabilities", {}).get("tools", [])

    if manifest_version == "0.2":
        # v0.2 entrypoint rules
        if pkg_entrypoint:
            # Package-level entrypoint uses v1 format (module.path, no :function)
            if not ENTRYPOINT_PATTERN_V1.match(pkg_entrypoint):
                errors.append(
                    f"entrypoint must be a valid Python module path (got '{pkg_entrypoint}')"
                )

        if len(tools) > 1:
            # Multi-tool: every tool MUST have its own entrypoint
            for i, tool in enumerate(tools):
                tool_ep = tool.get("entrypoint", "")
                if not tool_ep:
                    errors.append(
                        f"tools[{i}].entrypoint is required when pack has multiple tools"
                    )
                elif not ENTRYPOINT_PATTERN_V2.match(tool_ep):
                    errors.append(
                        f"tools[{i}].entrypoint must be module.path:function (got '{tool_ep}')"
                    )
        elif len(tools) == 1:
            # Single tool: package-level entrypoint OR tool-level entrypoint
            tool_ep = tools[0].get("entrypoint", "")
            if tool_ep and not ENTRYPOINT_PATTERN_V2.match(tool_ep):
                errors.append(
                    f"tools[0].entrypoint must be module.path:function (got '{tool_ep}')"
                )
            if not tool_ep and not pkg_entrypoint:
                errors.append("Either package-level or tool-level entrypoint is required")
        else:
            # 0 tools — package-level entrypoint required (will be caught by tools validation)
            if not pkg_entrypoint:
                errors.append("Package-level entrypoint required when no tools define their own")

    else:
        # v0.1: package-level entrypoint required, old format
        if not pkg_entrypoint:
            errors.append("entrypoint is required (e.g. 'my_pack.tool')")
        elif not ENTRYPOINT_PATTERN_V1.match(pkg_entrypoint):
            errors.append(f"entrypoint must be a valid Python module path (got '{pkg_entrypoint}')")


def _validate_enrichment_fields(manifest: dict, errors: list[str], warnings: list[str]) -> None:
    """Validate optional v0.2 enrichment fields."""
    # env_requirements
    env_reqs = manifest.get("env_requirements")
    if env_reqs is not None:
        if not isinstance(env_reqs, list):
            errors.append("env_requirements must be an array")
        else:
            for i, req in enumerate(env_reqs):
                if not isinstance(req, dict):
                    errors.append(f"env_requirements[{i}] must be an object")
                elif not req.get("name"):
                    errors.append(f"env_requirements[{i}].name is required")

    # use_cases
    use_cases = manifest.get("use_cases")
    if use_cases is not None:
        if not isinstance(use_cases, list):
            errors.append("use_cases must be an array")
        else:
            for i, uc in enumerate(use_cases):
                if not isinstance(uc, str):
                    errors.append(f"use_cases[{i}] must be a string")
                elif len(uc.split()) < 2:
                    warnings.append(f"use_cases[{i}]: use 'verb + object' format (e.g. 'Read Excel files')")

    # examples
    examples = manifest.get("examples")
    if examples is not None:
        if not isinstance(examples, list):
            errors.append("examples must be an array")
        else:
            for i, ex in enumerate(examples):
                if not isinstance(ex, dict):
                    errors.append(f"examples[{i}] must be an object")
                else:
                    if not ex.get("title"):
                        errors.append(f"examples[{i}].title is required")
                    if not ex.get("code"):
                        errors.append(f"examples[{i}].code is required")

    # URL fields — must be http(s) to prevent javascript: XSS
    from app.shared.validators import is_safe_url
    for url_field in ("homepage_url", "docs_url", "source_url"):
        val = manifest.get(url_field)
        if val is not None:
            if not isinstance(val, str):
                errors.append(f"{url_field} must be a string")
            elif val and not is_safe_url(val):
                errors.append(f"{url_field} must start with https:// or http://")


def validate_artifact_quality(artifact_bytes: bytes, slug: str, *, package_type: str = "package") -> tuple[list[str], list[str]]:
    """Quality Gate — validate artifact contains tests and required structure.

    Returns (errors, warnings). Errors block publishing.
    Agents are exempt from the test-file requirement (verification generates auto-tests).
    """
    import io
    import tarfile

    errors: list[str] = []
    warnings: list[str] = []

    try:
        with tarfile.open(fileobj=io.BytesIO(artifact_bytes), mode="r:gz") as tar:
            names = tar.getnames()
    except (tarfile.TarError, EOFError):
        errors.append("Artifact is not a valid .tar.gz archive")
        return errors, warnings

    # Normalize paths (remove leading pack-name prefix if present)
    normalized = []
    for n in names:
        parts = n.split("/", 1)
        normalized.append(parts[1] if len(parts) > 1 else parts[0])

    # Check for test files (agents exempt — verification generates auto-tests)
    test_files = [
        f for f in normalized
        if (f.startswith("tests/") or f.startswith("test/") or f.startswith("test_"))
        and f.endswith(".py")
        and not f.endswith("__init__.py")
    ]

    if not test_files and package_type != "agent":
        errors.append(
            "Quality Gate: No test files found. "
            "Add a tests/ directory with at least one test_*.py file."
        )

    # Check for pyproject.toml or setup.py
    has_project_file = any(
        f in ("pyproject.toml", "setup.py", "setup.cfg") for f in normalized
    )
    if not has_project_file:
        warnings.append("No pyproject.toml or setup.py found — recommended for proper packaging")

    # Check for agentnode.yaml manifest in artifact
    has_manifest = any(f == "agentnode.yaml" for f in normalized)
    if not has_manifest:
        warnings.append("No agentnode.yaml found in artifact — recommended to include manifest")

    return errors, warnings
