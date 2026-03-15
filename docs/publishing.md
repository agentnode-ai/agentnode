# Publishing Packages

This guide walks you through creating and publishing an ANP (AgentNode Package) to the registry.

## Prerequisites

1. An AgentNode account with a publisher profile
2. 2FA enabled on your account
3. The AgentNode CLI installed (`npm install -g agentnode`)

## Step 1: Create Your Package

A minimal package structure:

```
my-tool-pack/
├── agentnode.yaml          # ANP manifest (required)
├── src/
│   └── my_tool_pack/
│       ├── __init__.py
│       └── tool.py         # Must export a run() function
├── pyproject.toml          # Python package metadata (required)
└── tests/
    └── test_tool.py
```

### The Manifest (`agentnode.yaml`)

```yaml
manifest_version: "0.1"
package_id: "my-tool-pack"
package_type: "toolpack"
name: "My Tool Pack"
publisher: "your-publisher-slug"
version: "1.0.0"
summary: "Short description of what this pack does."  # max 200 chars
description: |
  Longer description with markdown support.

runtime: "python"
install_mode: "package"
hosting_type: "agentnode_hosted"
entrypoint: "my_tool_pack.tool"

capabilities:
  tools:
    - name: "my_tool_function"
      capability_id: "pdf_extraction"     # Must exist in capability taxonomy
      description: "What this tool does"
      input_schema:
        type: "object"
        properties:
          input_param:
            type: "string"
            description: "Description of the parameter"
        required: ["input_param"]
      output_schema:
        type: "object"
        properties:
          result:
            type: "string"
  resources: []
  prompts: []

tags: ["my-tag", "another-tag"]
categories: ["document-processing"]

compatibility:
  frameworks: ["langchain", "crewai", "generic"]
  python: ">=3.10"

dependencies: []

permissions:
  network:
    level: "none"
    allowed_domains: []
  filesystem:
    level: "temp"
  code_execution:
    level: "none"
  data_access:
    level: "input_only"
  user_approval:
    required: "never"
  external_integrations: []

upgrade_roles: []
recommended_for: []
replaces: []
install_strategy: "local"
fallback_behavior: "skip"
policy_requirements:
  min_trust_level: "unverified"
  requires_approval: false

security:
  signature: ""
  provenance:
    source_repo: ""
    commit: ""
    build_system: "manual"

support:
  homepage: ""
  issues: ""

deprecation_policy: "6-months-notice"
```

### The Tool Code (`tool.py`)

Your tool must expose a `run()` function:

```python
def run(input_param: str, **kwargs) -> dict:
    """Your tool logic here."""
    result = do_something(input_param)
    return {"result": result}
```

### Python Package (`pyproject.toml`)

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "my-tool-pack"
version = "1.0.0"           # Must match agentnode.yaml version
requires-python = ">=3.10"
dependencies = [
    "your-dependency>=1.0",
]

[tool.setuptools.packages.find]
where = ["src"]
```

> **Important:** `project.version` in `pyproject.toml` must match `version` in `agentnode.yaml`.

## Step 2: Validate

```bash
agentnode validate ./my-tool-pack
```

This checks:
- Manifest structure and required fields
- Capability IDs exist in the taxonomy
- Version format (semver)
- Permission values are valid enums
- Input/output schemas are valid JSON Schema

Fix any errors before proceeding.

## Step 3: Build the Artifact

Create a `.tar.gz` archive of your package:

```bash
cd my-tool-pack
tar -czf ../my-tool-pack-1.0.0.tar.gz .
```

The archive must contain `pyproject.toml` at the root.

## Step 4: Publish

```bash
agentnode publish ./my-tool-pack
```

This will:
1. Validate the manifest
2. Upload the artifact to the AgentNode registry
3. Run security scans (async)
4. Index the package for search

### Quarantine

New publishers (< 3 cleared packages) have their packages auto-quarantined. Quarantined packages:
- Are not visible in search
- Cannot be installed by others
- Need manual clearance by AgentNode admins

After 3 packages are cleared, future publishes skip quarantine.

## Step 5: Verify

```bash
agentnode info my-tool-pack
```

Or visit `https://agentnode.net/packages/my-tool-pack`.

## Updating a Package

Publish a new version by updating `version` in both `agentnode.yaml` and `pyproject.toml`, then:

```bash
agentnode publish ./my-tool-pack
```

Each `(package_id, version)` combination must be unique.

## Yanking a Version

If a version has issues, you can yank it (hide from install):

```bash
curl -X POST https://agentnode.net/v1/packages/my-tool-pack/versions/1.0.0/yank \
  -H "Authorization: Bearer YOUR_TOKEN"
```

Yanked versions cannot be installed but remain in version history for owners.

## Deprecating a Package

If the entire package is outdated:

```bash
curl -X POST https://agentnode.net/v1/packages/my-tool-pack/deprecate \
  -H "Authorization: Bearer YOUR_TOKEN"
```

Deprecated packages can still be installed but show a warning.

## Permissions Guide

Be honest about what your package needs:

| Permission | Levels | Description |
|------------|--------|-------------|
| `network` | `none`, `restricted`, `unrestricted` | Network access |
| `filesystem` | `none`, `temp`, `workspace_read`, `workspace_write`, `any` | File system access |
| `code_execution` | `none`, `limited_subprocess`, `shell` | Code execution |
| `data_access` | `input_only`, `connected_accounts`, `persistent` | Data access scope |
| `user_approval` | `never`, `once`, `high_risk_only`, `always` | When to prompt user |

Requesting fewer permissions improves your package's resolution score and policy compatibility.

## Trust Levels

| Level | Criteria |
|-------|----------|
| `unverified` | Default for new publishers |
| `verified` | 2FA enabled + 1 published package |
| `trusted` | 3+ packages, 50+ downloads, 0 open critical findings, 30+ days active |
| `curated` | Manual admin promotion |
