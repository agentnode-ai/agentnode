"""ANP manifest validation — all rules from spec section 6."""
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.packages.models import CapabilityTaxonomy

# Valid enums
VALID_PACKAGE_TYPES = {"agent", "toolpack", "upgrade"}
VALID_RUNTIMES = {"python"}  # MVP restriction
VALID_INSTALL_MODES = {"package"}  # MVP restriction
VALID_HOSTING_TYPES = {"agentnode_hosted"}  # MVP restriction
VALID_NETWORK_LEVELS = {"none", "restricted", "unrestricted"}
VALID_FS_LEVELS = {"none", "temp", "workspace_read", "workspace_write", "any"}
VALID_EXEC_LEVELS = {"none", "limited_subprocess", "shell"}
VALID_DATA_LEVELS = {"input_only", "connected_accounts", "persistent"}
VALID_APPROVAL_LEVELS = {"always", "high_risk_only", "once", "never"}

SLUG_PATTERN = re.compile(r"^[a-z0-9-]{3,60}$")
SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?(\+[a-zA-Z0-9.]+)?$")
ENTRYPOINT_PATTERN = re.compile(r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)+$")

# Valid JSON Schema types
VALID_JSON_SCHEMA_TYPES = {"string", "integer", "number", "boolean", "array", "object", "null"}


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


async def validate_manifest(manifest: dict, session: AsyncSession | None = None) -> tuple[bool, list[str], list[str]]:
    """Validate an ANP manifest dict.

    Returns (valid, errors, warnings).
    """
    errors: list[str] = []
    warnings: list[str] = []

    # manifest_version
    if manifest.get("manifest_version") != "0.1":
        errors.append("manifest_version MUST be '0.1'")

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

    # description — optional but recommended
    if not manifest.get("description"):
        warnings.append("description is recommended for discoverability")

    # runtime (MVP: only python)
    runtime = manifest.get("runtime", "")
    if runtime not in VALID_RUNTIMES:
        errors.append(f"runtime must be 'python' in MVP (got '{runtime}')")

    # install_mode (MVP: only package)
    install_mode = manifest.get("install_mode", "")
    if install_mode not in VALID_INSTALL_MODES:
        errors.append(f"install_mode must be 'package' in MVP (got '{install_mode}')")

    # hosting_type (MVP: only agentnode_hosted)
    hosting_type = manifest.get("hosting_type", "")
    if hosting_type not in VALID_HOSTING_TYPES:
        errors.append(f"hosting_type must be 'agentnode_hosted' in MVP (got '{hosting_type}')")

    # entrypoint — required, must be a valid Python module path
    entrypoint = manifest.get("entrypoint", "")
    if not entrypoint:
        errors.append("entrypoint is required (e.g. 'my_pack.tool')")
    elif not ENTRYPOINT_PATTERN.match(entrypoint):
        errors.append(f"entrypoint must be a valid Python module path (got '{entrypoint}')")

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
                errors.append(f"tools[{i}].capability_id '{cap_id}' not in capability_taxonomy")

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

    # compatibility.frameworks — must have at least 1
    compat = manifest.get("compatibility", {})
    frameworks = compat.get("frameworks", [])
    if not frameworks:
        errors.append("compatibility.frameworks must have at least 1 entry")

    # Optional but valuable fields — generate warnings
    if not manifest.get("tags"):
        warnings.append("tags are recommended for search discoverability")

    if not manifest.get("categories"):
        warnings.append("categories are recommended for browsing")

    # Upgrade-specific validations
    if pkg_type == "upgrade":
        if not manifest.get("upgrade_roles"):
            warnings.append("upgrade_roles is recommended for upgrade packages")
        if not manifest.get("recommended_for"):
            warnings.append("recommended_for is recommended for upgrade packages")

    return len(errors) == 0, errors, warnings


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
