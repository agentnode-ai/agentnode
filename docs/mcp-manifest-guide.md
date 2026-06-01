# MCP Manifest Guide

MCP servers use the standard `agentnode.yaml` manifest (v0.3) with `runtime: mcp`.

## Required Structure

```yaml
manifest_version: "0.3"
package_id: mcp-your-server
name: "Your MCP Server"
package_type: toolpack
runtime: mcp
install_mode: package
version: "0.1.0"
visibility: public
publisher: your-publisher-slug
summary: "One sentence describing what this MCP does."
description: "Longer description."

mcp_server:
  command: ["npx", "-y", "@your-org/your-mcp@1.0.0"]
  transport: stdio
  npm_package: "@your-org/your-mcp"
  source_repo: "https://github.com/your-org/your-repo"
  env_keys: []          # List required env vars, empty if none

capabilities:
  tools:
    - name: your_tool
      capability_id: general
      description: "What this tool does"
      input_schema: {type: object, properties: {}}
  resources: []
  prompts: []

permissions:
  network: {level: none}           # none | restricted | unrestricted
  filesystem: {level: none}        # none | read_only | workspace_write | any
  code_execution: {level: none}    # none | limited_subprocess | shell

tags: [mcp, mcp-server, your-tags]
categories: [your-category]
compatibility: {frameworks: [mcp]}
```

## Permission Levels

Declare honestly — AgentNode verifies claims against actual tool capabilities.

| Level | Network | Filesystem | Code Execution |
|---|---|---|---|
| `none` | No network access | No file access | No code execution |
| `restricted` | Specific API only | Read-only | Subprocess only |
| `unrestricted` | Any URL | Read + write | Shell access |

## Validation

```bash
agentnode mcp verify .          # Schema + package + owner (no code execution)
agentnode mcp verify . --test   # Also runs protocol test (starts the MCP)
agentnode mcp verify . --json   # Machine-readable report
```

## Key Fields in mcp_server

| Field | Required | Description |
|---|---|---|
| `command` | Yes | Start command as array, must include pinned version |
| `transport` | Yes | `stdio` or `sse` |
| `npm_package` | Yes* | npm package name (*or `pypi_package` for Python) |
| `source_repo` | Recommended | GitHub URL — verified against npm/PyPI registry |
| `env_keys` | Yes | Required environment variables (empty array if none) |
