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

##### `install(package_slug, version, source, event_type) -> dict`

Create an installation record and get artifact URL.

```python
result = an.install("pdf-reader-pack")
```

##### `recommend(missing_capabilities, framework, runtime) -> dict`

Get package recommendations for missing capabilities.

```python
result = an.recommend(["pdf_extraction", "web_search"], framework="langchain")
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

##### `install(slug, version, ...) -> InstallResult`

Download, verify, and pip-install a package locally. Also records the install event on the server.

```python
result = client.install("pdf-reader-pack")
print(result.installed, result.hash_verified)
```

##### `can_install(slug, version, ...) -> CanInstallResult`

Pre-flight check — evaluates trust level, permissions, and deprecation status without performing any installation.

```python
check = client.can_install("pdf-reader-pack", require_verified=True)
if check.allowed:
    client.install("pdf-reader-pack")
```

##### `resolve_and_install(capabilities, framework, ...) -> InstallResult`

Resolve capability gaps and install the best match.

```python
result = client.resolve_and_install(["pdf_extraction", "web_search"])
print(result.slug, result.installed)  # "pdf-reader-pack", True
```

##### `detect_and_install(error, *, auto_upgrade_policy, ...) -> DetectAndInstallResult`

Detect a missing capability from a runtime exception and optionally install the best match.

```python
try:
    process_pdf("report.pdf")
except Exception as e:
    result = client.detect_and_install(e, auto_upgrade_policy="safe")
    print(result.detected, result.capability, result.installed)
```

##### `smart_run(fn, *, auto_upgrade_policy, ...) -> SmartRunResult`

Wrap a callable with automatic capability gap detection, installation, and retry.

```python
result = client.smart_run(
    lambda: process_pdf("report.pdf"),
    auto_upgrade_policy="safe",
)
print(result.success)         # True
print(result.installed_slug)  # "pdf-reader-pack" (or None if no install needed)
print(result.duration_ms)     # Total wall time including detection + install + retry
```

**Parameters:**
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `fn` | Callable | required | The function to execute |
| `auto_upgrade_policy` | str | `"safe"` | `"off"`, `"safe"`, or `"strict"` |

**Policies:**
- `"off"` — never auto-install; raise on missing capability
- `"safe"` — install only verified+ packages automatically
- `"strict"` — install only trusted+ packages automatically

---

### `AsyncAgentNode` (async dict-based)

Async version of `AgentNode`. Same API, but all methods are `async`.

```python
from agentnode_sdk import AsyncAgentNode

async with AsyncAgentNode(api_key="ank_...") as an:
    results = await an.search("pdf extraction")
```

---

## AgentNodeRuntime

Zero-config LLM agent integration. Provides tool definitions, system prompts, and an auto-loop engine for any LLM provider.

```python
from agentnode_sdk import AgentNodeRuntime

runtime = AgentNodeRuntime()
```

**Constructor:**
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `client` | AgentNodeClient \| None | None | Optional pre-configured client |
| `api_key` | str \| None | None | API key (creates client internally) |
| `minimum_trust_level` | str | `"verified"` | Minimum trust for installs |

### Tool Bundle

```python
bundle = runtime.tool_bundle()
# {"tools": [...], "system_prompt": "..."}
```

### Provider-Specific Tool Formats

```python
runtime.as_openai_tools()     # OpenAI function-calling format
runtime.as_anthropic_tools()  # Anthropic format
runtime.as_gemini_tools()     # Gemini format
runtime.as_generic_tools()    # Generic format
```

### Auto-Loop (`run`)

Runs a full tool-calling loop with automatic dispatch:

```python
from openai import OpenAI

result = runtime.run(
    provider="openai",        # "openai", "anthropic", or "gemini"
    client=OpenAI(),
    model="gpt-4o",
    messages=[{"role": "user", "content": "Find PDF tools on AgentNode"}],
)
print(result.content)
```

Supported providers: `"openai"` (including OpenRouter), `"anthropic"`, `"gemini"`.

### Manual Dispatch (`handle`)

For custom tool-calling loops:

```python
result = runtime.handle("agentnode_search", {"query": "pdf extraction"})
# {"success": true, "result": {"total": 5, "results": [...]}}
```

### Meta-Tools

The runtime registers 5 meta-tools automatically:

| Tool | Description |
|------|-------------|
| `agentnode_capabilities` | List installed packages (local, no API call) |
| `agentnode_search` | Search the registry (max 5 results) |
| `agentnode_install` | Install a package by slug |
| `agentnode_run` | Execute an installed tool |
| `agentnode_acquire` | Search + install in one step |

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
| `run_id` | str \| None |

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

---

## Agent Run Observability

Every agent execution produces a structured run log for debugging and audit.

### Run Logs

```python
from agentnode_sdk.run_log import RunLog, read_run, list_runs

# Run logs are created automatically during run_agent()
# Stored at ~/.agentnode/runs/{run_id}.jsonl

# Read all events for a specific run
events = read_run("550e8400-e29b-41d4-a716-446655440000")
for event in events:
    print(event["event"], event["ts"], event.get("tool_name"))

# List recent runs
run_ids = list_runs(limit=10)
```

**Event types:**
| Event | Fields |
|-------|--------|
| `run_start` | `slug`, `run_id` |
| `tool_call` | `slug`, `tool_name`, `run_id` |
| `tool_result` | `slug`, `tool_name`, `duration_ms`, `success`, `error?` |
| `iteration` | `iteration`, `run_id` |
| `step_start` | `step_name`, `slug` |
| `step_result` | `step_name`, `success`, `duration_ms` |
| `run_end` | `slug`, `success`, `duration_ms` |
| `truncated` | Emitted when max entries (1000) reached |

Run logs contain only metadata — no tool inputs, outputs, or secrets.

### Run-Log Retention

```python
from agentnode_sdk.run_log import cleanup_old_runs

# Manual cleanup: delete runs older than 7 days, keep at most 100
deleted = cleanup_old_runs(max_age_days=7, max_count=100)
print(f"Deleted {deleted} old run logs")

# Using config defaults (reads from ~/.agentnode/config.json)
deleted = cleanup_old_runs()
```

**Configuration** (`~/.agentnode/config.json`):
```json
{
  "run_log": {
    "max_age_days": 30,
    "max_count": 500
  }
}
```

Cleanup runs automatically after every `run_agent()` call. It is non-blocking —
failures are logged at debug level and never crash the agent.

---

## Agent Isolation

Agent execution can run in a thread (default) or a separate process.

```yaml
# In agentnode.yaml manifest
agent:
  isolation: "thread"    # default — daemon thread with timeout
  # or
  isolation: "process"   # multiprocessing.Process with terminate→kill
```

**Thread (default):** Daemon thread with timeout. Sufficient for most use cases. The default because `AgentContext` contains closures that are not trivially picklable.

**Process (opt-in):** Separate `multiprocessing.Process` with escalating termination (SIGTERM → grace period → SIGKILL). Use when the entrypoint is pickle-safe and hard isolation is required.

---

## Credential Resolution

The SDK resolves credentials through a configurable chain.

```python
from agentnode_sdk.credential_resolver import resolve_handle

# Resolution order (mode="auto"): env var → API → None
handle = resolve_handle(provider="github", auth_type="oauth2")
```

**Configuration** (`~/.agentnode/config.json`):
```json
{
  "credentials": {
    "resolve_mode": "auto"
  }
}
```

| Mode | Behavior |
|------|----------|
| `"env"` | Only check `AGENTNODE_CRED_{PROVIDER}` env vars |
| `"api"` | Only call the backend resolve endpoint |
| `"auto"` | Try env var first, then API if available (default) |

When resolving via API, the SDK receives a `ProxyCredentialHandle` that routes `authorized_request()` calls through the backend's proxy endpoint. Secrets never leave the server.

---

## Resource Content Provider

Read content from resource URIs.

```python
from agentnode_sdk.resource_provider import read_content

# Local package resource → inline content
rc = read_content("resource://my-pack/api_spec", installed_packages_dir="~/.agentnode/packages")
print(rc.type)       # "inline"
print(rc.content)    # '{"openapi": "3.0.0"}'
print(rc.mime_type)  # "application/json"

# HTTPS resource → URI reference (no fetch)
rc = read_content("https://example.com/schema.json")
print(rc.type)  # "uri_reference"
print(rc.uri)   # "https://example.com/schema.json"
```

**Behavior by scheme:**
| URI scheme | Result type | Notes |
|------------|-------------|-------|
| `resource://` | `inline` | Reads from `{package}/resources/{name}.{ext}` |
| `resource://` (no file) | `metadata_only` | Fallback when file not found |
| `https://` | `uri_reference` | No implicit fetching |

Content limit: 100KB. Files larger than this return `metadata_only` with a description.

Supported extensions: `.json`, `.txt`, `.md`, `.yaml`, `.yml`, `.csv`, `.xml`, or exact name match.

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
| `RateLimitError` | 429 | Too many requests |

---

## Context Manager

Both clients support `with` statements:

```python
with AgentNode(api_key="ank_...") as an:
    results = an.search("pdf")
# connection is automatically closed
```
