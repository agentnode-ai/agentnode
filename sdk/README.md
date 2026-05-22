# agentnode-sdk

Python SDK for [AgentNode](https://agentnode.net) — the open upgrade and discovery infrastructure for AI agents.

## Installation

```bash
pip install agentnode-sdk
```

## Quick Start — LLM Agent Runtime

Connect any LLM agent to AgentNode in three lines. The Runtime provides tool definitions, system prompt, and a tool-loop engine. Tested across 22 models — works with OpenAI, Anthropic, Gemini, Mistral, DeepSeek, Qwen, Llama, and more.

```python
from agentnode_sdk import AgentNodeRuntime

runtime = AgentNodeRuntime()

# Get tools + system prompt for your provider
bundle = runtime.tool_bundle()
# → { "tools": [...], "system_prompt": "..." }
```

### OpenAI

```python
from openai import OpenAI
from agentnode_sdk import AgentNodeRuntime

runtime = AgentNodeRuntime()
client = OpenAI()

result = runtime.run(
    provider="openai",
    client=client,
    model="gpt-4o",
    messages=[{"role": "user", "content": "Count the words in 'Hello world'"}],
)
print(result.content)
```

### Anthropic

```python
from anthropic import Anthropic
from agentnode_sdk import AgentNodeRuntime

runtime = AgentNodeRuntime()
client = Anthropic()

result = runtime.run(
    provider="anthropic",
    client=client,
    model="claude-sonnet-4-6",
    messages=[{"role": "user", "content": "Search for PDF tools on AgentNode"}],
)
```

### Gemini

```python
from google import genai
from agentnode_sdk import AgentNodeRuntime

runtime = AgentNodeRuntime()
client = genai.Client()

result = runtime.run(
    provider="gemini",
    client=client,
    model="gemini-2.5-flash",
    messages=[{"role": "user", "content": "What AgentNode tools are available?"}],
)
```

### OpenRouter (Mistral, DeepSeek, Qwen, Llama, and more)

Use any OpenAI-compatible provider by passing a custom `base_url`:

```python
from openai import OpenAI
from agentnode_sdk import AgentNodeRuntime

runtime = AgentNodeRuntime()
client = OpenAI(
    api_key="sk-or-...",
    base_url="https://openrouter.ai/api/v1",
)

result = runtime.run(
    provider="openai",
    client=client,
    model="mistralai/mistral-large",  # or deepseek/deepseek-chat, qwen/qwen-plus, etc.
    messages=[{"role": "user", "content": "Find and install a PDF reader tool"}],
)
```

### Generic / Manual Tool Calling

For any provider that supports tool calling, use `handle()` to dispatch calls manually:

```python
runtime = AgentNodeRuntime()

# Get tool definitions in your provider's format
tools = runtime.as_openai_tools()   # OpenAI format
tools = runtime.as_anthropic_tools() # Anthropic format
tools = runtime.as_gemini_tools()    # Gemini format
tools = runtime.as_generic_tools()   # Generic format

# When the LLM makes a tool call, dispatch it:
result = runtime.handle("agentnode_search", {"query": "pdf extraction"})
# → {"success": true, "result": {"total": 5, "results": [...]}}
```

## CLI

The `agentnode` CLI is the human interface. Single commands, multi-step tasks, diagnostics.

```bash
# Search & install
agentnode search "pdf extraction"
agentnode install pdf-reader-pack

# Run a single task (natural language)
agentnode run "extract text from report.pdf"

# Multi-step: pipe output between capabilities automatically
agentnode run "extract text from report.pdf then translate to german"

# Preview without executing
agentnode run "search for AI news then summarize" --dry-run

# Show reasoning
agentnode run "search for AI news" --explain

# Diagnostics
agentnode doctor              # detect missing capabilities, suggest packages
agentnode doctor --fix         # auto-install suggestions
agentnode recommend            # prioritized recommendations based on installed setup
agentnode audit                # recent policy decisions (allow/deny/prompt)
agentnode inspect pdf-reader-pack  # security report: trust, permissions, audit history

# Credentials & config
agentnode auth set openai
agentnode auth status
agentnode config list
```

All commands support `--json` for structured output.

**Note:** Multi-step runs respect your config:
- `install_confirmation: prompt` will ask before installing missing packages
- Low-confidence steps require confirmation (or abort in non-interactive mode)

## Three Surfaces

```
CLI           → for humans (search, install, run, diagnose)
SDK / Client  → for programmatic access (search, resolve, install, run)
Runtime       → for LLM agents (tool registration, dispatch, auto-loop)
```

## API Reference

### `AgentNodeRuntime`

Zero-config LLM agent integration.

| Method | Description |
|--------|-------------|
| `tool_specs()` | Internal typed tool definitions (`list[ToolSpec]`) |
| `as_openai_tools()` | Tools in OpenAI function-calling format |
| `as_anthropic_tools()` | Tools in Anthropic format |
| `as_generic_tools()` | Tools in generic/baseline format |
| `system_prompt()` | AgentNode system prompt block (append to yours) |
| `tool_bundle()` | Combined `{"tools": [...], "system_prompt": "..."}` |
| `handle(tool_name, arguments)` | Dispatch a tool call. Returns dict. Never throws. |
| `run(provider, client, messages, model, ...)` | Auto-loop with tool dispatch. Never throws. |

**Constructor:**

```python
AgentNodeRuntime(
    client=None,                     # Optional AgentNodeClient
    api_key=None,                    # Optional API key
    minimum_trust_level="verified",  # "verified" | "trusted" | "curated"
)
```

**5 Meta-Tools** (automatically registered):

| Tool | Description |
|------|-------------|
| `agentnode_capabilities` | List installed packages (local, no API call) |
| `agentnode_search` | Search the registry (max 5 results) |
| `agentnode_install` | Install a package by slug |
| `agentnode_run` | Execute an installed tool |
| `agentnode_acquire` | Search + install in one step |

### `AgentNodeClient`

The programmatic client with typed return models.

| Method | Description |
|--------|-------------|
| `search(query, ...)` | Search packages by keyword or capability |
| `resolve(capabilities, ...)` | Resolve capability gaps to ranked packages |
| `install(slug, ...)` | Download, verify, and install locally |
| `resolve_and_install(capabilities, ...)` | Resolve + install in one call |
| `run_tool(slug, tool_name=, ...)` | Run a tool with trust-aware isolation |
| `smart_run(fn, ...)` | Wrap logic with auto-detect, install, retry |
| `detect_and_install(error, ...)` | Detect capability gap and install |

### `run_tool()` (standalone)

Top-level function for running tools with process isolation.

```python
from agentnode_sdk import run_tool

result = run_tool("pdf-reader-pack", mode="auto", file_path="report.pdf")
# result.success, result.result, result.error, result.mode_used, result.duration_ms
# result.policy  → {"action": "allow", "reason": "...", "source": "..."}
# result.to_dict()  → structured output for --json
```

**Isolation contract.** `mode="auto"` always resolves to `subprocess`,
regardless of the package's trust level. This makes the isolation
guarantee true by default. If you need in-process execution (for
example, to share module-level state with the tool), pass
`mode="direct"` explicitly — that is an opt-in performance trade-off,
not a default.

### `get_risk_profile()` (standalone)

Usage risk assessment for installed packages. Separate from verification —
risk answers "how risky is the usage?" not "does it work reliably?"

```python
from agentnode_sdk import get_risk_profile

profile = get_risk_profile("gmail-sender-pack")
# profile.risk_level   → "medium"
# profile.risk_score   → 45  (0-100, higher = riskier)
# profile.signals      → ["Uses external network access", "Requires credentials (oauth)"]
# profile.risk_flags   → ["external_write_capable"]
```

Returns `None` if the package is not installed.

### Guard: Pre-Execution Policy Gateway

Guard classifies every tool call by action type and applies configurable
policy before any code runs. 9 action types, 3 decisions (allow/prompt/deny),
per-tool overrides, strict mode.

```bash
# Check what Guard would decide for a tool
agentnode guard check file-manager/delete_file
agentnode guard check web-scraper/fetch_page --json

# Show current policy
agentnode guard status

# Change policy for an action type
agentnode guard set delete deny           # block all deletes
agentnode guard set write_local allow      # allow local writes without prompt

# Per-tool override (granular escape hatch)
agentnode guard set delete allow --tool file-manager/delete_file

# Reset everything
agentnode guard reset

# Strict mode (CI / production)
export AGENTNODE_GUARD_STRICT=true
```

**Default policy:** read/compute/write_local/network_egress → allow.
delete/write_external/execute/credential_use/unknown → prompt.

**Strict mode:** delete/write_external/execute/unknown → deny.
write_local → prompt. Per-tool overrides ignored.

**Critical risk** (unverified + high-risk + secrets in env) → hard deny,
no override possible.

**Remote/connector hardening:** Credentialed requests require HTTPS and
explicit domain binding. Empty `allowed_domains` is denied — no
open-proxy default. The remote runner additionally warns on
method/action-type mismatches, oversized payloads, and scope/method
inconsistencies (advisory only, never blocks).

See [THREAT_MODEL.md](THREAT_MODEL.md) for the full security model.

### Lockfile Integrity & Publisher Signatures

Two layers of supply-chain protection:

**Integrity** (v0.7.0) — every installed package entry is sealed with a
SHA-256 hash over security-critical fields. Post-install tampering is
detected before any code executes.

**Authenticity** (v0.8.0) — publishers sign packages with Ed25519 keys.
Install verifies the signature before writing the lockfile. Invalid
signatures block install. Missing signatures warn but don't block
(gradual adoption).

```bash
# Verify integrity + signatures (CI-friendly, exit code 1 on mismatch/invalid)
agentnode lock verify
agentnode lock verify --strict   # also fail on missing integrity
agentnode lock verify --json     # structured output with signature status

# Inspect a single package
agentnode inspect pdf-reader-pack
# → Integrity       verified
# → Signature       valid (key ed25519:a1b2c3d4e5f6)

# Seal entries after manual lockfile edits
agentnode lock seal
```

New installs are sealed and signature-verified automatically. In strict
mode (`AGENTNODE_GUARD_STRICT=true`), tampered entries are denied at
runtime. Invalid signatures always block install regardless of mode.

### Risk Policies

Configure how the SDK reacts to computed risk flags. Uses the same
`allow | log | prompt | deny` values as permission policies.

```bash
# Default: log (audit only, no blocking)
agentnode config get risk_policies.external_write_capable

# Require confirmation for packages that can send data externally
agentnode config set risk_policies.external_write_capable prompt

# Reset to audit-only
agentnode config set risk_policies.external_write_capable log
```

Risk policies only fire after the normal permission check passes.
Hard policies (trust, permissions) always have priority.

### Multi-step Planner

Decompose and execute multi-step tasks programmatically.

```python
from agentnode_sdk.planner import plan_task, plan_and_run

# Plan without executing
plan = plan_task("extract text from report.pdf then summarize")
# plan.steps[0].capability == "pdf_extraction"
# plan.steps[1].capability == "text_summarization"
# plan.steps[1].uses_previous == True

# Plan and execute (each step runs through run_tool with full policy/audit)
result = plan_and_run("extract text from report.pdf then summarize")
# result.success, result.steps, result.duration_ms
# result.to_dict()  → structured output
```

**Limitations:** Max 3 steps. Rule-based splitting on connectors (`then`,
`and then`, `→`) — no LLM decomposition.

## License

MIT
