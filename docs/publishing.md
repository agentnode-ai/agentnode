# Publishing Packages

This guide walks you through creating and publishing an ANP (AgentNode Package) to the registry.

## Prerequisites

1. An AgentNode account with a publisher profile
2. 2FA enabled on your account
3. The AgentNode CLI installed (`npm install -g agentnode-cli`)

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
manifest_version: "0.2"
package_id: "my-tool-pack"
package_type: "toolpack"
name: "My Tool Pack"
publisher: "your-publisher-slug"
version: "1.0.0"
summary: "Short description of what this pack does."  # 20-200 chars
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
  frameworks: ["generic"]
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

homepage_url: ""                       # Must be https:// or http://
docs_url: ""                           # Must be https:// or http://
source_url: ""                         # Must be https:// or http://
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

> **Required:** The artifact must include a `tests/` directory with at least
> one `test_*.py` file. The quality gate (`validate_artifact_quality()`) will
> reject the publish if no tests are found.

## Step 4: Publish

```bash
agentnode publish ./my-tool-pack
```

This will:
1. Validate the manifest (schema, field ranges, type combinations)
2. Run the quality gate (tests required in artifact)
3. Check for typosquatting against existing package slugs
4. Verify artifact signature (if signing key is registered)
5. Check ownership (existing packages must belong to you)
6. Check version uniqueness (duplicate `(package_id, version)` is rejected)
7. Upload the artifact to the AgentNode registry
8. Index the package for search (unless quarantined)

### Quarantine

First-time publishers (0 cleared packages) have their packages auto-quarantined.
Quarantined packages:
- Are not visible in search
- Cannot be installed by others
- Need manual clearance by AgentNode admins

After your first package is cleared, future publishes skip quarantine.
Publishers with `trusted` or `curated` trust level skip quarantine entirely
(including typosquatting quarantine).

## Step 5: Verify

```bash
agentnode info my-tool-pack
```

Or visit `https://agentnode.net/packages/my-tool-pack`.

## Publishing Agent Packages

Agent packages (`package_type: "agent"`) are autonomous agents that orchestrate tools to accomplish goals. They require an additional `agent:` section in the manifest.

### Quick Start

```bash
agentnode init --type agent my-agent
cd my-agent
# Edit agentnode.yaml and implement your agent
agentnode validate agentnode.yaml
agentnode publish
```

### Agent Manifest

The `agent:` section defines how the agent operates:

```yaml
package_type: "agent"

agent:
  entrypoint: "my_agent.agent:run"          # module.path:function format (required)
  goal: "What this agent does in one sentence"  # required
  isolation: "thread"                        # "thread" (default) or "process"
  tool_access:
    allowed_packages:                        # Packages this agent may use
      - "web-search-pack"
      - "document-summarizer-pack"
  limits:
    max_iterations: 10                       # 1-100
    max_tool_calls: 50                       # 1-500
    max_runtime_seconds: 300                 # 1-3600
  termination:
    stop_on_final_answer: true               # bool
    stop_on_consecutive_errors: 3            # int, 1-10
  state:
    persistence: "none"                      # "none" or "session"
```

> **Note:** `agent.max_tokens` and `agent.planning` are explicitly rejected
> by the validator and will produce errors if included.

### Connector Metadata (toolpack only)

Toolpacks that wrap external services can declare a `connector:` section:

```yaml
connector:
  provider: "slack"
  auth_type: "oauth2"                # "api_key" or "oauth2"
  scopes: ["chat:read", "chat:write"]
  health_check:
    endpoint: "/api/health"
    interval_seconds: 60
```

### Agent Entrypoint

Your agent must expose an async `run()` function:

```python
async def run(goal: str, context: dict | None = None) -> dict:
    """Agent entrypoint.

    Args:
        goal: The task/goal for the agent.
        context: Optional dict with prior state, tool references, etc.

    Returns:
        Dict with at least a 'result' key.
    """
    # Your agent logic here
    return {"result": "...", "steps_taken": 0}
```

### Validation

The validator checks:
- `agent.entrypoint` is in `module.path:function` format
- `agent.goal` is present
- `agent.tool_access.allowed_packages` entries are strings
- `agent.limits` values are within valid ranges
- The verification pipeline imports and tests the agent entrypoint

### Example

See [`research-agent-pack`](../starter-packs/research-agent-pack/) for a complete working example that orchestrates web search, PDF extraction, and summarization.

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
