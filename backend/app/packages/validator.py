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
            cap_id = tool.get("capability_id", "")
            if not cap_id:
                errors.append(f"tools[{i}].capability_id is required")
            elif valid_cap_ids is not None and cap_id not in valid_cap_ids:
                errors.append(f"tools[{i}].capability_id '{cap_id}' not in capability_taxonomy")
            input_schema = tool.get("input_schema")
            if input_schema is not None and not isinstance(input_schema, dict):
                errors.append(f"tools[{i}].input_schema must be a valid JSON Schema object")

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
    else:
        errors.append("permissions section is required")

    # compatibility.frameworks — must have at least 1
    compat = manifest.get("compatibility", {})
    frameworks = compat.get("frameworks", [])
    if not frameworks:
        errors.append("compatibility.frameworks must have at least 1 entry")

    return len(errors) == 0, errors, warnings
