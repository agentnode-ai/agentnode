# agentnode-sdk

Python SDK for [AgentNode](https://agentnode.net) — the open upgrade and discovery infrastructure for AI agents.

## Installation

```bash
pip install agentnode-sdk
```

## Quick Start

```python
from agentnode_sdk import AgentNodeClient

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
for pkg in resolved.results:
    print(f"{pkg.slug} v{pkg.version} (score: {pkg.score})")

# Get package details
detail = client.get_package("pdf-reader-pack")
print(f"{detail.name} — downloads: {detail.download_count}")

# Get install metadata
meta = client.get_install_metadata("pdf-reader-pack")
print(f"Runtime: {meta.runtime}, Entrypoint: {meta.entrypoint}")
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
