# ANP — Agent Node Package Format

ANP (Agent Node Package) is the native package format for installable capabilities in the AgentNode ecosystem.

## Overview

An ANP package consists of:

1. **`agentnode.yaml`** — The manifest describing the package
2. **Python source code** — The actual tool implementation
3. **`pyproject.toml`** — Standard Python package metadata

## Manifest Schema (v0.1)

### Required Fields

| Field | Type | Rules |
|-------|------|-------|
| `manifest_version` | string | Must be `"0.1"` |
| `package_id` | string | `[a-z0-9-]`, 3-60 chars, unique |
| `package_type` | enum | `toolpack`, `agent`, `upgrade` |
| `name` | string | 1-100 chars |
| `publisher` | string | Must match existing publisher slug |
| `version` | string | Valid semver (e.g., `1.0.0`) |
| `summary` | string | 1-200 chars |
| `runtime` | enum | `python` (MVP only) |
| `install_mode` | enum | `package` (MVP only) |
| `hosting_type` | enum | `agentnode_hosted` (MVP only) |
| `entrypoint` | string | Python import path (e.g., `my_pack.tool`) |
| `capabilities.tools` | array | At least 1 tool required |
| `compatibility.frameworks` | array | At least 1 framework |
| `permissions` | object | All sub-fields required |

### Capabilities

Each tool in `capabilities.tools` must have:

```yaml
capabilities:
  tools:
    - name: "function_name"           # Non-empty string
      capability_id: "pdf_extraction" # Must exist in capability taxonomy
      description: "What it does"
      input_schema:                   # Valid JSON Schema
        type: "object"
        properties:
          param_name:
            type: "string"
            description: "..."
        required: ["param_name"]
      output_schema:                  # Valid JSON Schema
        type: "object"
        properties:
          result:
            type: "string"
  resources: []
  prompts: []
```

### Permissions

```yaml
permissions:
  network:
    level: "none"                     # none | restricted | unrestricted
    allowed_domains: []               # Only if restricted
  filesystem:
    level: "temp"                     # none | temp | workspace_read | workspace_write | any
  code_execution:
    level: "none"                     # none | limited_subprocess | shell
  data_access:
    level: "input_only"              # input_only | connected_accounts | persistent
  user_approval:
    required: "never"                # never | once | high_risk_only | always
  external_integrations: []
```

### Compatibility

```yaml
compatibility:
  frameworks: ["langchain", "crewai", "generic"]
  python: ">=3.10"
```

### Upgrade Metadata (Optional)

```yaml
upgrade_roles: ["document_processing"]
recommended_for:
  - agent_type: "legal-assistant"
    missing_capability: "pdf_extraction"
replaces: []
install_strategy: "local"            # local | remote
fallback_behavior: "skip"            # skip | error
policy_requirements:
  min_trust_level: "unverified"
  requires_approval: false
```

### Security (Optional)

```yaml
security:
  signature: ""                      # SHA256, filled at publish
  provenance:
    source_repo: "https://github.com/..."
    commit: ""
    build_system: "github-actions"   # github-actions | manual
```

## Compliance Levels

| Level | Fields Required |
|-------|----------------|
| Minimal | Identity, runtime, 1 capability, permissions |
| Standard | + compatibility, tags, description |
| Full | + provenance, upgrade metadata, security |

## Validation Rules

- `manifest_version` must be `"0.1"`
- `package_id` must match `[a-z0-9-]`, 3-60 chars
- `version` must be valid semver
- `summary` must be 1-200 chars
- `runtime` must be `"python"` (MVP)
- `install_mode` must be `"package"` (MVP)
- `hosting_type` must be `"agentnode_hosted"` (MVP)
- Each tool's `capability_id` must exist in the capability taxonomy
- Each tool's `input_schema` must be valid JSON Schema
- `compatibility.frameworks` must have at least 1 entry
- `pyproject.toml` version must match manifest version

## Slug vs. Entrypoint

These are separate fields:

```
slug:       "pdf-reader-pack"          (hyphens, used in URLs and CLI)
entrypoint: "pdf_reader_pack.tool"     (underscores, Python import path)
```

Never infer one from the other. The `entrypoint` field is the source of truth for imports.

## Full Example

See [starter-packs/pdf-reader-pack/agentnode.yaml](../starter-packs/pdf-reader-pack/agentnode.yaml) for a complete reference.
