# MCP Verification Report Schema

The JSON output of `agentnode mcp verify --json` follows this stable schema.
This is the foundation for the future Submit API.

## Top-Level Fields

| Field | Type | Description |
|---|---|---|
| `status` | string | `INVALID`, `RESOLVED`, `TESTED`, `REVIEW_NEEDED`, `MAINTAINER_ACTION_REQUIRED` |
| `summary` | string | One-sentence human-readable summary |
| `manifest_version` | string | Manifest version (e.g. `"0.3"`) |
| `checked_at` | string | ISO 8601 timestamp of verification |

## Status Values

| Status | Meaning | Has Actions? |
|---|---|---|
| `INVALID` | Manifest or package has blocking errors | Yes (high) |
| `RESOLVED` | Package found on registry, no protocol test run | Maybe (medium) |
| `TESTED` | Protocol test passed, no issues | No |
| `REVIEW_NEEDED` | Protocol passed but warnings present | Yes (medium) |
| `MAINTAINER_ACTION_REQUIRED` | High-severity issues must be fixed | Yes (high) |

## Package

```json
{
  "registry": "npm",
  "name": "@playwright/mcp",
  "version": "0.0.75",
  "shasum": "20f3a1eb...",
  "integrity": "sha512-...",
  "maintainers": ["pavelfeldman", "mxschmitt"],
  "registry_repo_url": "git+https://github.com/microsoft/playwright-mcp.git"
}
```

## Source

```json
{
  "declared": "https://github.com/microsoft/playwright-mcp",
  "registry": "git+https://github.com/microsoft/playwright-mcp.git"
}
```

## Checks

Array of individual verification results:

```json
[
  {"name": "schema", "passed": true, "detail": "manifest v0.3, runtime=mcp"},
  {"name": "package_exists", "passed": true, "detail": "@playwright/mcp on npm"},
  {"name": "version_exists", "passed": true, "detail": "0.0.75 -- shasum: 20f3a1eb..."},
  {"name": "version_pinned", "passed": true, "detail": "npx -y @playwright/mcp@0.0.75"},
  {"name": "owner_verified", "passed": true, "detail": "microsoft/playwright-mcp matches registry"},
  {"name": "protocol_test", "passed": true, "detail": "23 tools discovered"},
  {"name": "permission_honesty", "passed": true, "detail": "declarations match detected capabilities"}
]
```

## Actions

Actionable items for the maintainer. Only present when issues are found.

```json
[
  {
    "severity": "high",
    "code": "OWNER_METADATA_MISMATCH",
    "title": "Source repo does not match registry",
    "detail": "declared=ahujasid/blender-mcp, registry=yourusername/blender-mcp",
    "fix": "Update source_repo to match the repository URL in your package metadata."
  }
]
```

### Severity Levels

| Level | Meaning | Effect on Status |
|---|---|---|
| `high` | Must fix before catalog inclusion | Triggers `MAINTAINER_ACTION_REQUIRED` |
| `medium` | Should fix, may trigger review | Triggers `REVIEW_NEEDED` (with protocol test) |
| `low` | Informational risk signal | No status change |

### Action Codes

| Code | Trigger | Severity |
|---|---|---|
| `SCHEMA_INVALID` | Manifest schema errors | high |
| `PACKAGE_NOT_FOUND` | Package not on npm/PyPI | high |
| `VERSION_NOT_FOUND` | Pinned version doesn't exist | high |
| `VERSION_NOT_PINNED` | Command missing @version | medium |
| `OWNER_METADATA_MISMATCH` | source_repo vs registry mismatch | high |
| `PROTOCOL_TEST_FAILED` | initialize or tools/list failed | medium |
| `PERMISSION_MISMATCH` | Declared permissions lower than detected | medium |
| `RISK_FLAG_*` | Risk signal detected (crypto, paid_api, etc.) | low |

## Permissions

```json
{
  "declared": {"network": "unrestricted", "filesystem": "none", "code_execution": "shell"},
  "detected": {"network": "likely", "filesystem": "none", "code_execution": "likely"},
  "mismatches": []
}
```

## Other Fields

| Field | Type | Description |
|---|---|---|
| `requirements` | object | From manifest: node/python version, host_apps |
| `tools_snapshot` | array | Tools discovered via protocol test (name, description, input_schema_keys) |
| `risk_flags` | string[] | Detected risk signals |
| `warnings` | string[] | Non-blocking warnings |
| `errors` | string[] | Blocking errors |
