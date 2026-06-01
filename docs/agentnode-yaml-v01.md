# agentnode.yaml v0.1 — MCP Server Manifest

## Purpose

A machine-readable file that MCP maintainers place in their repository root.
It declares what the MCP does, needs, and is allowed to do.
AgentNode reads this file, verifies the claims, and generates a Verification Report.

Principle: **Maintainer declares. AgentNode verifies.**

## Schema

```yaml
# Required
agentnode: "0.1"                    # Manifest version
name: "My MCP Server"              # Human-readable name
summary: "One sentence."           # Max 200 chars

package:
  registry: npm                    # npm | pypi
  name: "@org/my-mcp"             # Exact registry name
  version: "1.2.3"                # Pinned version

transport: stdio                   # stdio | sse

command:                           # Start command (array)
  - npx
  - -y
  - "@org/my-mcp@1.2.3"

# Required — declare honestly, AgentNode verifies
permissions:
  network: none                    # none | restricted | unrestricted
  filesystem: none                 # none | read_only | workspace_write | any
  code_execution: none             # none | limited_subprocess | shell

env_keys: []                       # List of required environment variables

# Optional
description: "Longer description." # Multi-line allowed
license: MIT                       # SPDX identifier (if not in package.json)
source_repo: "https://github.com/org/repo"

requirements:                      # Runtime prerequisites
  node: ">=18"                     # or python: ">=3.10"
  host_apps: []                    # ["firefox", "blender", "davinci-resolve"]

tags:                              # Discovery tags
  - browser
  - automation

categories:                        # AgentNode categories
  - web-automation

homepage: "https://example.com"
docs: "https://example.com/docs"
```

## Field Reference

### Required Fields

| Field | Type | Description |
|---|---|---|
| `agentnode` | string | Manifest version. Always `"0.1"`. |
| `name` | string | Human-readable package name. |
| `summary` | string | One-sentence description, max 200 chars. |
| `package.registry` | enum | `npm` or `pypi`. |
| `package.name` | string | Exact package name on the registry. |
| `package.version` | string | Exact pinned version. |
| `transport` | enum | `stdio` or `sse`. |
| `command` | string[] | Start command as array. Must include pinned version. |
| `permissions.network` | enum | `none`, `restricted`, `unrestricted`. |
| `permissions.filesystem` | enum | `none`, `read_only`, `workspace_write`, `any`. |
| `permissions.code_execution` | enum | `none`, `limited_subprocess`, `shell`. |
| `env_keys` | string[] | Required environment variables. Empty array if none. |

### Optional Fields

| Field | Type | Description |
|---|---|---|
| `description` | string | Longer description. |
| `license` | string | SPDX identifier. Fallback: read from package.json. |
| `source_repo` | string | GitHub/GitLab URL. Fallback: read from registry. |
| `requirements.node` | string | Node.js version constraint. |
| `requirements.python` | string | Python version constraint. |
| `requirements.host_apps` | string[] | Required local applications. |
| `tags` | string[] | Discovery tags. |
| `categories` | string[] | AgentNode categories. |
| `homepage` | string | Project homepage URL. |
| `docs` | string | Documentation URL. |

## What AgentNode Derives (NOT in the YAML)

These fields are determined by AgentNode's verification pipeline.
Maintainers cannot self-declare them.

| Field | Source | Description |
|---|---|---|
| `owner_verified` | npm/PyPI registry repo URL vs source_repo | Ownership match |
| `protocol_verified` | initialize + tools/list test | MCP handshake works |
| `tools_snapshot` | tools/list response | Actual tools discovered |
| `npm_shasum` | npm registry | Tarball hash |
| `npm_integrity` | npm registry | SRI hash |
| `npm_maintainers` | npm registry | Package maintainers |
| `risk_flags` | README + tools + metadata analysis | Automated risk signals |
| `verification_status` | Pipeline result | listed/tested/reviewed/official |
| `dependency_audit` | npm audit / pip audit | Known vulnerabilities |

## What Maintainers Cannot Self-Declare

| Claim | Why not |
|---|---|
| trust_level | Determined by AgentNode's review process |
| verification_status | Result of verification, not an input |
| risk_flags | Derived from objective analysis |
| tools (authoritative) | AgentNode discovers via protocol test; YAML tools are hints only |
| "safe" / "secure" / "trusted" | Social properties, not technical claims |

## Permission Honesty Rule

AgentNode verifies declared permissions against actual tool capabilities:

- Tool has `url` parameter → expects `network: restricted` or `unrestricted`
- Tool has `path`/`file` parameter → expects `filesystem: read_only` or higher
- Tool has `code`/`command`/`script` parameter → expects `code_execution: limited_subprocess` or higher
- Tool named `*_execute`, `*_run`, `*_eval` → expects `code_execution`

If declared permissions are lower than detected capabilities:

```
⚠ Permission mismatch:
  Declared: network: none
  Detected: Tool "fetch_url" has url parameter → network access likely
```

This is a warning, not a block. But it appears in the Verification Report.

## Examples

### Example 1: Simple MCP without API keys

```yaml
agentnode: "0.1"
name: "Met Museum MCP"
summary: "Search and browse The Metropolitan Museum of Art Collection."

package:
  registry: npm
  name: metmuseum-mcp
  version: "1.0.0"

transport: stdio
command: ["npx", "-y", "metmuseum-mcp@1.0.0"]

permissions:
  network: restricted      # Talks to Met Museum API only
  filesystem: none
  code_execution: none

env_keys: []

tags: [museum, art, culture]
categories: [data-management]
```

### Example 2: API MCP with env_keys

```yaml
agentnode: "0.1"
name: "Brave Search MCP"
summary: "Web and local search via the Brave Search API."

package:
  registry: npm
  name: "@modelcontextprotocol/server-brave-search"
  version: "2025.3.28"

transport: stdio
command: ["npx", "-y", "@modelcontextprotocol/server-brave-search@2025.3.28"]

permissions:
  network: restricted      # Brave Search API only
  filesystem: none
  code_execution: none

env_keys:
  - BRAVE_API_KEY

tags: [search, web]
categories: [search]
```

### Example 3: High-permission MCP (Playwright)

```yaml
agentnode: "0.1"
name: "Playwright MCP"
summary: "Browser automation via Playwright. Navigate, click, type, screenshot, execute JS."

package:
  registry: npm
  name: "@playwright/mcp"
  version: "0.0.75"

transport: stdio
command: ["npx", "-y", "@playwright/mcp@0.0.75", "--headless"]

permissions:
  network: unrestricted    # Browser can load any URL
  filesystem: none
  code_execution: shell    # browser_run_code_unsafe is RCE-equivalent

env_keys: []

requirements:
  node: ">=18"
  host_apps: []            # Chromium auto-downloads

license: Apache-2.0
source_repo: "https://github.com/microsoft/playwright-mcp"
tags: [browser, automation, testing, playwright]
categories: [web-automation]
```

### Example 4: Host-dependency MCP (Firefox DevTools)

```yaml
agentnode: "0.1"
name: "Firefox DevTools MCP"
summary: "Firefox browser automation and DevTools inspection."

package:
  registry: npm
  name: "@mozilla/firefox-devtools-mcp"
  version: "0.9.3"

transport: stdio
command: ["npx", "-y", "@mozilla/firefox-devtools-mcp@0.9.3", "--headless"]

permissions:
  network: unrestricted    # Browser can load any URL
  filesystem: none
  code_execution: none     # No arbitrary code execution tool

env_keys: []

requirements:
  node: ">=20.19"
  host_apps:
    - firefox              # Firefox 100+ must be installed

license: MIT OR Apache-2.0
source_repo: "https://github.com/mozilla/firefox-devtools-mcp"
tags: [firefox, browser, devtools, mozilla]
categories: [web-automation]
```

### Example 5: Python MCP with uvx

```yaml
agentnode: "0.1"
name: "Blender MCP"
summary: "Control Blender 3D via MCP for AI-driven 3D modeling."

package:
  registry: pypi
  name: blender-mcp
  version: "1.5.6"

transport: stdio
command: ["uvx", "blender-mcp@1.5.6"]

permissions:
  network: none
  filesystem: workspace_write  # Creates/modifies Blender files
  code_execution: limited_subprocess

env_keys: []

requirements:
  python: ">=3.10"
  host_apps:
    - blender              # Blender 3.0+ with addon loaded

tags: [blender, 3d, modeling, creative]
categories: [creative-tools]
```

## Verification Report (output)

When AgentNode processes an agentnode.yaml, it produces a report:

```yaml
verification:
  manifest_version: "0.1"
  checked_at: "2026-06-01T12:00:00Z"

  package_resolved: true
  package_shasum: "20f3a1eb..."
  package_integrity: "sha512-oBjz..."

  owner_verified: true
  owner_match: "microsoft/playwright-mcp"

  protocol_verified: true
  tools_discovered: 23
  tools_snapshot:
    - name: browser_navigate
      description: "Navigate to a URL"
    # ...

  permission_check:
    declared:
      network: unrestricted
      filesystem: none
      code_execution: shell
    detected:
      network: likely     # tools have url parameters
      filesystem: none
      code_execution: likely  # browser_run_code_unsafe, browser_evaluate
    mismatches: []        # empty = declarations match reality

  risk_flags: []
  dependency_audit:
    vulnerabilities: 0
    advisories: []

  status: tested          # listed | tested | reviewed | official
```
