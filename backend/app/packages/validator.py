"""ANP manifest validation — all rules from spec section 6. Supports v0.1 and v0.2."""
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.packages.models import CapabilityTaxonomy

# Valid enums
VALID_PACKAGE_TYPES = {"agent", "toolpack", "upgrade"}
VALID_RUNTIMES = {"python", "mcp", "remote"}
VALID_INSTALL_MODES = {"package"}  # MVP restriction
VALID_HOSTING_TYPES = {"agentnode_hosted"}  # MVP restriction
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

    # install_mode (MVP: only package)
    install_mode = manifest.get("install_mode", "")
    if install_mode not in VALID_INSTALL_MODES:
        errors.append(f"install_mode must be 'package' in MVP (got '{install_mode}')")

    # hosting_type (MVP: only agentnode_hosted)
    hosting_type = manifest.get("hosting_type", "")
    if hosting_type not in VALID_HOSTING_TYPES:
        errors.append(f"hosting_type must be 'agentnode_hosted' in MVP (got '{hosting_type}')")

    # --- Entrypoint validation (version-dependent) ---
    _validate_entrypoints(manifest, manifest_version, errors)

    # capabilities.tools — must have at least 1
    capabilities = manifest.get("capabilities", {})
    tools = capabilities.get("tools", [])
    if not tools:
        errors.append("capabilities.tools must have at least 1 tool")
    else:
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


def validate_artifact_quality(artifact_bytes: bytes, slug: str) -> tuple[list[str], list[str]]:
    """Quality Gate — validate artifact contains tests and required structure.

    Returns (errors, warnings). Errors block publishing.
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

    # Check for test files
    test_files = [
        f for f in normalized
        if (f.startswith("tests/") or f.startswith("test/") or f.startswith("test_"))
        and f.endswith(".py")
        and not f.endswith("__init__.py")
    ]

    if not test_files:
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
