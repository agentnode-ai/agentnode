# Python SDK Reference

## Installation

```bash
pip install agentnode-sdk
```

## Quick Start

```python
from agentnode_sdk import AgentNode

an = AgentNode(api_key="ank_your_key_here")

results = an.search("pdf extraction")
print(results)
```

## Client Classes

### `AgentNode` (dict-based)

Returns raw API response dicts. Recommended for simple use cases.

```python
from agentnode_sdk import AgentNode

an = AgentNode(api_key="ank_...", base_url="https://api.agentnode.net/v1")
```

**Parameters:**
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `api_key` | str | required | API key (`ank_...`) |
| `base_url` | str | `https://api.agentnode.net/v1` | API base URL |

#### Methods

##### `search(query, capability_id, framework, sort_by, page) -> dict`

```python
results = an.search("pdf extraction", framework="langchain")
```

##### `resolve_upgrade(missing_capability, framework, runtime, current_capabilities, policy) -> dict`

```python
result = an.resolve_upgrade(
    missing_capability="pdf_extraction",
    framework="langchain",
    runtime="python",
    policy={"min_trust": "verified", "allow_shell": False}
)
```

##### `check_policy(package_slug, framework, policy) -> dict`

```python
result = an.check_policy(
    "browser-pack",
    framework="langchain",
    policy={"min_trust": "trusted", "allow_shell": False, "allow_network": False}
)
# result["result"] is "allowed", "blocked", or "requires_approval"
```

##### `get_package(slug) -> dict`

```python
pkg = an.get_package("pdf-reader-pack")
```

##### `get_install_metadata(package_slug, version) -> dict`

Read-only. Does not create installation records.

```python
meta = an.get_install_metadata("pdf-reader-pack")
# meta["entrypoint"], meta["artifact"]["url"], etc.
```

##### `validate(manifest) -> dict`

```python
result = an.validate({"package_id": "my-pack", "version": "1.0.0", ...})
# result["valid"], result["errors"], result["warnings"]
```

---

### `AgentNodeClient` (typed)

Returns typed dataclass models. Recommended for larger applications.

```python
from agentnode_sdk import AgentNodeClient

client = AgentNodeClient(api_key="ank_...", base_url="https://api.agentnode.net/v1")
```

**Parameters:**
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `base_url` | str | `https://api.agentnode.net/v1` | API base URL |
| `api_key` | str | None | API key authentication |
| `token` | str | None | Bearer token authentication |
| `timeout` | float | 30.0 | Request timeout in seconds |

#### Methods

##### `search(...) -> SearchResult`

```python
result = client.search("pdf", framework="langchain", limit=10)
for hit in result.hits:
    print(f"{hit.slug} v{hit.latest_version} ({hit.trust_level})")
```

##### `resolve(capabilities, framework, runtime, limit) -> ResolveResult`

```python
result = client.resolve(["pdf_extraction", "web_search"], framework="langchain")
for pkg in result.results:
    print(f"{pkg.slug}: score={pkg.score}, trust={pkg.trust_level}")
```

##### `get_package(slug) -> PackageDetail`

```python
pkg = client.get_package("pdf-reader-pack")
print(pkg.summary, pkg.download_count, pkg.latest_version)
```

##### `get_install_metadata(slug, version) -> InstallMetadata`

```python
meta = client.get_install_metadata("pdf-reader-pack")
print(meta.entrypoint)  # "pdf_reader_pack.tool"
print(meta.artifact.hash_sha256)
for cap in meta.capabilities:
    print(f"  {cap.name} ({cap.capability_id})")
```

##### `download(slug, version) -> str | None`

Track download and get artifact URL.

```python
url = client.download("pdf-reader-pack")
```

---

## Tool Execution

### `run_tool(slug, tool_name, *, mode, timeout, lockfile_path, **kwargs) -> RunToolResult`

Run an installed tool with subprocess isolation by default.

```python
from agentnode_sdk import run_tool

# Default (auto): runs the tool in an isolated subprocess regardless of trust.
result = run_tool("pdf-reader-pack", file_path="report.pdf")

# Explicit subprocess with a custom timeout.
result = run_tool("untrusted-pack", mode="subprocess", timeout=15.0, data="input")

# Opt-in direct (in-process) execution — for tools that need to share
# in-process state with the host. This bypasses isolation.
result = run_tool("my-trusted-pack", mode="direct", query="test")
```

**Parameters:**
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `slug` | str | required | Package slug (e.g. `"pdf-reader-pack"`) |
| `tool_name` | str \| None | None | Tool name for multi-tool packs |
| `mode` | str | `"auto"` | `"direct"`, `"subprocess"`, or `"auto"` |
| `timeout` | float | 30.0 | Maximum seconds for subprocess execution |
| `lockfile_path` | Path \| None | None | Override path to `agentnode.lock` |
| `**kwargs` | Any | — | Arguments forwarded to the tool function |

**Auto-mode routing (SDK 0.4.1+):**
`mode="auto"` always resolves to `subprocess`, independent of trust
level. This makes the documented isolation guarantee true by default.
Callers that want in-process execution (to share module-level state
with the tool) must pass `mode="direct"` explicitly.

**Subprocess safety features:**
- Environment variable filtering — API keys and tokens are stripped
- Working directory isolation — tool runs in a temporary directory
- Timeout enforcement — hung tools are killed after the configured limit
- stdout isolation — tool print() calls don't corrupt the result
- Safe serialization — non-JSON outputs return a fallback representation

### `load_tool(slug, tool_name) -> Callable`

Load a tool function from an installed pack. Returns a raw Python callable. Use `run_tool()` for trust-aware execution with isolation.

```python
from agentnode_sdk import load_tool

extract = load_tool("pdf-reader-pack")
result = extract(file_path="report.pdf")
```

---

## Data Models

All models are Python dataclasses in `agentnode_sdk.models`.

### `SearchResult`
| Field | Type |
|-------|------|
| `query` | str |
| `hits` | list[SearchHit] |
| `total` | int |

### `SearchHit`
| Field | Type |
|-------|------|
| `slug` | str |
| `name` | str |
| `package_type` | str |
| `summary` | str |
| `publisher_slug` | str |
| `trust_level` | str |
| `latest_version` | str \| None |
| `runtime` | str \| None |
| `capability_ids` | list[str] |
| `download_count` | int |

### `ResolveResult`
| Field | Type |
|-------|------|
| `results` | list[ResolvedPackage] |
| `total` | int |

### `ResolvedPackage`
| Field | Type |
|-------|------|
| `slug` | str |
| `name` | str |
| `version` | str |
| `score` | float |
| `breakdown` | ScoreBreakdown |
| `trust_level` | str |
| `matched_capabilities` | list[str] |

### `ScoreBreakdown`
| Field | Type |
|-------|------|
| `capability` | float |
| `framework` | float |
| `runtime` | float |
| `trust` | float |
| `permissions` | float |

### `PackageDetail`
| Field | Type |
|-------|------|
| `slug` | str |
| `name` | str |
| `package_type` | str |
| `summary` | str |
| `description` | str \| None |
| `download_count` | int |
| `is_deprecated` | bool |
| `latest_version` | str \| None |

### `InstallMetadata`
| Field | Type |
|-------|------|
| `slug` | str |
| `version` | str |
| `package_type` | str |
| `install_mode` | str |
| `hosting_type` | str |
| `runtime` | str |
| `entrypoint` | str \| None |
| `artifact` | ArtifactInfo \| None |
| `capabilities` | list[CapabilityInfo] |
| `dependencies` | list[DependencyInfo] |
| `permissions` | PermissionsInfo \| None |

### `RunToolResult`
| Field | Type |
|-------|------|
| `success` | bool |
| `result` | Any |
| `error` | str \| None |
| `mode_used` | str |
| `duration_ms` | float |
| `timed_out` | bool |

### `ArtifactInfo`
| Field | Type |
|-------|------|
| `url` | str \| None |
| `hash_sha256` | str \| None |
| `size_bytes` | int \| None |

### `PermissionsInfo`
| Field | Type |
|-------|------|
| `network_level` | str |
| `filesystem_level` | str |
| `code_execution_level` | str |
| `data_access_level` | str |
| `user_approval_level` | str |

---

## Exceptions

All exceptions inherit from `AgentNodeError`.

```python
from agentnode_sdk import AgentNodeError, NotFoundError, AuthError, ValidationError

try:
    pkg = an.get_package("nonexistent")
except NotFoundError as e:
    print(e.code, e.message)  # "PACKAGE_NOT_FOUND", "..."
except AuthError as e:
    print("Auth failed:", e.message)
except AgentNodeError as e:
    print("API error:", e.code, e.message)
```

| Exception | HTTP Status | Description |
|-----------|-------------|-------------|
| `AgentNodeError` | any | Base error class |
| `NotFoundError` | 404 | Resource not found |
| `AuthError` | 401, 403 | Authentication/authorization failure |
| `ValidationError` | 422 | Invalid input or manifest |

---

## Context Manager

Both clients support `with` statements:

```python
with AgentNode(api_key="ank_...") as an:
    results = an.search("pdf")
# connection is automatically closed
```
