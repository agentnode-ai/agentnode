# agentnode-sdk

Python SDK for [AgentNode](https://agentnode.net) — the open upgrade and discovery infrastructure for AI agents.

## Installation

```bash
pip install agentnode-sdk
```

## Quick Start

```python
from agentnode_sdk import AgentNodeClient
from agentnode_sdk.installer import load_tool

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

# v0.2: Load specific tools from multi-tool packs
describe = load_tool("csv-analyzer-pack", tool_name="describe")
result = describe({"file_path": "data.csv"})

# Single-tool packs — no tool_name needed
extract = load_tool("pdf-reader-pack")
pdf = extract({"file_path": "report.pdf"})
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
| `load_tool(slug, tool_name=)` | Load a tool function from an installed pack (v0.2) |

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
