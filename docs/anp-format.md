# ANP — Agent Node Package Format

ANP (Agent Node Package) is the native package format for installable
capabilities in the AgentNode ecosystem. The current format is **v0.2**
(shipped 2026-Q1). Manifests using the older **v0.1** format are still
accepted unchanged for backwards compatibility.

See the full engineering spec at
[`ANP-v0.2-Engineering-Spec.md`](../ANP-v0.2-Engineering-Spec.md) for the
authoritative rules. This document is the high-level overview published
to developers.

## Overview

An ANP package consists of:

1. **`agentnode.yaml`** — The manifest describing the package
2. **Python source code** — The actual tool implementation
3. **`pyproject.toml`** — Standard Python package metadata

## Manifest Versions

| Version | Status | Notes |
|---------|--------|-------|
| `0.3`   | **Current** | Capability taxonomy (tools/resources/prompts), agent runtime, connector metadata, sequential orchestration, conditional steps, resource content delivery |
| `0.2`   | Supported | Multi-tool entrypoints, compact manifests with server-side defaults, richer metadata (`use_cases`, `examples`, `env_requirements`) |
| `0.1`   | Legacy (accepted) | Single-tool entrypoint, all fields explicit. New manifests should prefer v0.3. |

The backend validates both. For v0.2 manifests, the server applies
`normalize_manifest()` which fills MVP defaults (runtime, install_mode,
hosting_type, permissions, etc.) so publishers can ship compact
manifests and only override what they need.

## Required Fields (v0.2)

| Field | Type | Rules |
|-------|------|-------|
| `manifest_version` | string | Must be `"0.2"` (or `"0.1"` for legacy) |
| `package_id` | string | `[a-z0-9-]`, 3–60 chars, unique |
| `package_type` | enum | `toolpack`, `upgrade` (legacy: `agent` accepted, treated as `toolpack`) |
| `name` | string | 3–100 chars |
| `publisher` | string | Must match an existing publisher slug |
| `version` | string | Valid semver (e.g. `1.0.0`) |
| `summary` | string | 20–200 chars |
| `capabilities.tools` | array | At least 1 tool required |

### Defaulted Fields (v0.2 only)

For v0.2 manifests the server fills these defaults if omitted. You only
need to set them when overriding:

| Field | Default |
|-------|---------|
| `runtime` | `"python"` |
| `install_mode` | `"package"` |
| `hosting_type` | `"agentnode_hosted"` |
| `permissions.network` | `{ level: "none", allowed_domains: [] }` |
| `permissions.filesystem` | `{ level: "none" }` |
| `permissions.code_execution` | `{ level: "none" }` |
| `permissions.data_access` | `{ level: "input_only" }` |
| `permissions.user_approval` | `{ required: "never" }` |
| `compatibility.frameworks` | `["generic"]` |
| `dependencies` | `[]` |
| `tags` | `[]` |
| `categories` | `[]` |
| `env_requirements` | `[]` |
| `use_cases` | `[]` |
| `examples` | `[]` |

### Entrypoints (v0.2)

v0.2 unifies entrypoint handling. All entrypoints use the format
`module.path` or `module.path:function`:

```
module.path          = shorthand for module.path:run
module.path:function = explicit function reference
```

Rules:

- If the pack declares **exactly 1 tool**, a package-level `entrypoint`
  is sufficient. All calls route to `module.path:run`.
- If the pack declares **more than 1 tool**, **each tool MUST declare
  its own `entrypoint`** with the `module.path:function` form. The
  validator rejects multi-tool packs where any tool is missing its
  entrypoint (no silent fallback).
- Package-level `entrypoint` becomes optional when every tool defines
  its own.

For v0.1 manifests, only the package-level `entrypoint` is honoured,
and the format must be `module.path` (no `:function` suffix). Every
tool is invoked via `module.run()`.

## Capabilities

Each tool in `capabilities.tools` must have:

```yaml
capabilities:
  tools:
    - name: "function_name"           # Non-empty string
      capability_id: "pdf_extraction" # Must exist in capability taxonomy
      description: "What it does"
      entrypoint: "my_pack.tool:describe"   # v0.2 only, required for >1 tool
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

## Permissions

```yaml
permissions:
  network:
    level: "none"                     # none | restricted | unrestricted
    allowed_domains: []               # Only when level == "restricted"
  filesystem:
    level: "none"                     # none | temp | workspace_read | workspace_write | any
  code_execution:
    level: "none"                     # none | limited_subprocess | shell
  data_access:
    level: "input_only"              # input_only | connected_accounts | persistent
  user_approval:
    required: "never"                # never | once | high_risk_only | always
  external_integrations: []
```

## Compatibility

```yaml
compatibility:
  frameworks: ["generic"]             # generic | langchain | crewai | …
  python: ">=3.10"
```

## Upgrade Metadata (Optional)

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

## Security (Optional)

```yaml
security:
  signature: ""                      # SHA256, filled at publish
  provenance:
    source_repo: "https://github.com/..."
    commit: ""
    build_system: "github-actions"   # github-actions | manual
```

Signature verification is **required** for packages outside the
`trusted`/`curated` tiers and **recommended** for trusted publishers.
See the v0.2 engineering spec for the exact signature rules.

## v0.2 Enrichment (Optional)

v0.2 adds optional discoverability and UX fields:

```yaml
env_requirements:
  - name: "OPENAI_API_KEY"
    purpose: "OpenAI GPT API access"
    required: true
use_cases:
  - "Extract structured data from academic PDFs"
  - "Summarise invoices into JSON line items"
examples:
  - title: "Extract text from a scanned PDF"
    code: |
      result = run_tool("pdf-reader-pack", file="scan.pdf")
      print(result["text"])
```

## Compliance Levels

| Level | Fields Required |
|-------|----------------|
| Minimal | Identity, 1 capability |
| Standard | + compatibility, tags, description |
| Full | + provenance, upgrade metadata, security, use_cases/examples |

## Validation Rules (summary)

- `manifest_version` must be `"0.1"` or `"0.2"`
- `package_id` must match `[a-z0-9-]`, 3–60 chars
- `version` must be valid semver
- `summary` must be 20–200 chars
- `runtime` must be `"python"` (MVP)
- `install_mode` must be `"package"` (MVP)
- `hosting_type` must be `"agentnode_hosted"` (MVP)
- Each tool's `capability_id` must exist in the capability taxonomy
- Each tool's `input_schema` / `output_schema` must be valid JSON Schema
- v0.2 multi-tool packs must declare a per-tool `entrypoint`
- `pyproject.toml` version must match manifest version

## Agent Configuration (v0.4)

Packages with `package_type: agent` can configure agent runtime behavior:

```yaml
agent:
  isolation: "thread"         # "thread" (default) or "process"
  entrypoint: "my_agent:run"  # Agent entrypoint function
  timeout: 60                 # Max execution time in seconds
  max_iterations: 10          # Max ReAct loop iterations
  allowed_tools:              # S4 allowlist
    - "pdf-reader-pack"
    - "web-search-pack"
```

### Agent Isolation

| Value | Behavior |
|-------|----------|
| `"thread"` | Daemon thread with timeout (default). Safe for all entrypoints. |
| `"process"` | `multiprocessing.Process` with terminate→kill escalation. Requires pickle-safe entrypoint. |

The default is `"thread"` because `AgentContext` contains closures that
cannot be trivially serialized across process boundaries. Opt into
`"process"` only when hard isolation is needed and the entrypoint supports it.

## Resource Content (v0.4)

Resources can include local content files in the package:

```yaml
capabilities:
  resources:
    - name: "api_spec"
      uri: "resource://my-pack/api_spec"
      description: "OpenAPI specification"
      content_path: "resources/api_spec.json"   # optional, relative to package root
```

**Rules for `content_path`:**
- Must be a relative path (no leading `/`)
- No directory traversal (`..` is rejected)
- File must exist in the package at publish time
- Max content size: 100KB

When installed, `resource://` URIs resolve to inline content from these files.
Without `content_path`, resources return metadata only.

## Conditional Orchestration Steps (v0.4)

Sequential orchestration steps can include a `when` condition:

```yaml
orchestration:
  mode: sequential
  steps:
    - name: "extract"
      tool: "pdf-reader-pack"
      input:
        file: "$input.file_path"

    - name: "translate"
      tool: "translation-pack"
      when: "$steps.extract.result is not null"
      input:
        text: "$steps.extract.result"
        target_lang: "$input.lang"
```

**Supported expressions:**
| Syntax | Meaning |
|--------|---------|
| `$ref == value` | Equality check |
| `$ref != value` | Inequality check |
| `$ref is null` | Reference is null or unresolvable |
| `$ref is not null` | Reference exists and is not null |

- Only single comparisons — no `and`/`or`
- Unresolvable `$ref` → step is skipped (not an error)
- Skipped steps are tracked in `step_details` with `skipped: true`
- Subsequent steps cannot reference results from skipped steps

## Slug vs. Entrypoint

These are separate fields:

```
slug:       "pdf-reader-pack"          (hyphens, used in URLs and CLI)
entrypoint: "pdf_reader_pack.tool"     (underscores, Python import path)
```

Never infer one from the other. The `entrypoint` field is the source of
truth for imports.

## Full Example

See [starter-packs/pdf-reader-pack/agentnode.yaml](../starter-packs/pdf-reader-pack/agentnode.yaml)
for a complete reference, and the
[v0.2 Engineering Spec](../ANP-v0.2-Engineering-Spec.md) for every rule.
