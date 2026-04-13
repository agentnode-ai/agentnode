# Getting Started with AgentNode

AgentNode is the open upgrade and discovery infrastructure for AI agents. It helps developers find, evaluate, and install capabilities their agents are missing.

## Quick Start

### 1. Install the CLI

```bash
npm install -g agentnode-cli
```

### 2. Search for a capability

```bash
agentnode search "pdf extraction"
```

Output:

```
  pdf-reader-pack  v1.0.0  ★ unverified
  Extract text, tables, and metadata from PDF files.
  Capabilities: pdf_extraction
  Frameworks: langchain, crewai, generic
```

### 3. Get package details

```bash
agentnode info pdf-reader-pack
```

### 4. Install a package

```bash
agentnode install pdf-reader-pack
```

This will:
- Check version, trust, and compatibility
- Download the artifact from the AgentNode registry
- Install it into your local Python environment
- Write an entry to `agentnode.lock`

### 5. Run the package

```python
from agentnode_sdk import run_tool

# Auto-mode (default) always runs the tool in an isolated subprocess,
# matching the documented isolation guarantee. Pass mode="direct" only if
# you need in-process execution for a trusted tool (performance opt-in).
result = run_tool("pdf-reader-pack", file_path="report.pdf")
print(result.result["text"])
```

## Using the Python SDK

Install the SDK for programmatic access:

```bash
pip install agentnode-sdk
```

```python
from agentnode_sdk import AgentNodeClient, run_tool

# API key from arg, env var AGENTNODE_API_KEY, or ~/.agentnode/config.json
client = AgentNodeClient(api_key="ank_your_key_here")

# Search
results = client.search("pdf extraction")

# Resolve a capability gap
result = client.resolve_upgrade(
    missing_capability="pdf_extraction",
    framework="langchain",
    runtime="python",
    policy={"min_trust": "verified", "allow_shell": False}
)

# Run an installed tool. mode="auto" (default) always runs in a
# subprocess sandbox; mode="direct" opts into in-process execution for
# trusted tools when you need to share state or avoid subprocess overhead.
result = run_tool("pdf-reader-pack", file_path="report.pdf")
print(result.result)     # tool output
print(result.mode_used)  # "subprocess" (default) or "direct" (opt-in)
```

### For LLM Agents (Runtime)

Agents can detect and install missing capabilities at runtime:

```python
from agentnode_sdk import AgentNodeClient

client = AgentNodeClient()  # uses AGENTNODE_API_KEY env var

# Auto-detect and install a missing capability
result = client.detect_and_install(["pdf_extraction"])

# Or use smart_run to auto-resolve + execute in one call
result = client.smart_run("pdf_extraction", file_path="report.pdf")
```

## Using the LangChain Adapter

```bash
pip install agentnode-langchain
```

```python
from agentnode_langchain import load_tool

# Load an installed pack as a LangChain tool
tool = load_tool("pdf-reader-pack", api_key="ank_your_key_here")
agent_tools = [tool]
```

## Authentication

### Create an account

```bash
# Via CLI
agentnode login
```

Or register at [agentnode.net/auth/register](https://agentnode.net/auth/register).

### API Keys

After logging in, create an API key for SDK/programmatic use:

```bash
curl -X POST https://agentnode.net/v1/auth/api-keys \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"label": "my-project"}'
```

The key (format: `ank_...`) is shown only once. Store it securely.

Use it in the SDK:

```python
client = AgentNodeClient(api_key="ank_your_key_here")
```

Or in the CLI config (`~/.agentnode/config.json`):

```json
{
  "api_url": "https://agentnode.net",
  "api_key": "ank_your_key_here"
}
```

Or as an environment variable:

```bash
export AGENTNODE_API_KEY=ank_your_key_here
```

## Next Steps

- [Publishing Packages](publishing.md) — Create and publish your own packs
- [ANP Format](anp-format.md) — Learn the Agent Node Package manifest format
- [CLI Reference](cli-reference.md) — All CLI commands
- [API Reference](api-reference.md) — REST API documentation
- [SDK Reference](sdk-reference.md) — Python SDK documentation
- [Capability Taxonomy](capability-taxonomy.md) — Available capability IDs
