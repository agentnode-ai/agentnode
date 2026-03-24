# agentnode-sdk

Python SDK for [AgentNode](https://agentnode.net) — the open upgrade and discovery infrastructure for AI agents.

## Installation

```bash
pip install agentnode-sdk
```

## Quick Start

```python
from agentnode_sdk import AgentNodeClient, run_tool

client = AgentNodeClient(api_key="ank_...")

# Search for packages
results = client.search("pdf extraction")
for hit in results.hits:
    print(f"{hit.slug} — {hit.summary}")

# Resolve capability gaps
resolved = client.resolve(
    capabilities=["pdf_extraction"],
    framework="langchain",
)

# v0.3: Run tools with trust-aware isolation
# trusted/curated → direct (in-process), verified/unverified → subprocess
result = run_tool("pdf-reader-pack", file_path="report.pdf")
print(result.result)  # tool output
print(result.mode_used)  # "direct" or "subprocess"

# Multi-tool packs — specify tool_name
result = run_tool("csv-analyzer-pack", tool_name="describe", file_path="data.csv")
```

## API Reference

### `AgentNodeClient`

The main client with typed return models.

| Method | Description |
|--------|-------------|
| `search(query, ...)` | Search packages by keyword or capability |
| `resolve(capabilities, ...)` | Resolve capability gaps to ranked packages |
| `get_package(slug)` | Get package details |
| `get_install_metadata(slug)` | Get install info (artifact, permissions, deps) |
| `download(slug)` | Track download and get artifact URL |
| `run_tool(slug, tool_name=, mode=, timeout=, **kwargs)` | Run a tool with optional subprocess isolation (v0.3) |
| `load_tool(slug, tool_name=)` | Load a tool function from an installed pack (v0.2) |

### `run_tool()` (standalone)

Top-level function for running tools with trust-aware execution mode.

```python
from agentnode_sdk import run_tool

result = run_tool("pdf-reader-pack", mode="auto", file_path="report.pdf")
# result.success, result.result, result.error, result.mode_used, result.duration_ms
```

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `slug` | str | required | Package slug |
| `tool_name` | str \| None | None | Tool name for multi-tool packs |
| `mode` | str | `"auto"` | `"direct"`, `"subprocess"`, or `"auto"` |
| `timeout` | float | 30.0 | Subprocess timeout in seconds |
| `**kwargs` | Any | — | Arguments forwarded to the tool function |

### `AgentNode`

Lightweight client returning raw dicts.

| Method | Description |
|--------|-------------|
| `search(query, ...)` | Search packages |
| `resolve_upgrade(missing_capability, ...)` | Resolve a single capability gap |
| `check_policy(package_slug, ...)` | Evaluate security policy |
| `get_install_metadata(slug)` | Get install metadata |
| `install(slug)` | Create installation record |
| `recommend(missing_capabilities)` | Get recommendations |
| `validate(manifest)` | Validate a package manifest |

## License

MIT
