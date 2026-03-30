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

## Three Surfaces

```
CLI           → for humans (search, install, publish)
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

Top-level function for running tools with trust-aware execution mode.

```python
from agentnode_sdk import run_tool

result = run_tool("pdf-reader-pack", mode="auto", file_path="report.pdf")
# result.success, result.result, result.error, result.mode_used, result.duration_ms
```

## License

MIT
