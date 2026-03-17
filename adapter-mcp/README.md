# agentnode-mcp

MCP server adapter for [AgentNode](https://agentnode.net) — expose AgentNode packs as MCP tools for **OpenClaw**, **Claude Code**, **Cursor**, and any MCP-compatible client.

## Installation

```bash
pip install agentnode-mcp
```

## Quick Start

```bash
# Install some packs first
agentnode install regex-builder-pack
agentnode install document-redaction-pack

# Start MCP server with specific packs
agentnode-mcp --packs regex-builder-pack,document-redaction-pack

# Or load all installed packs
agentnode-mcp --all
```

## Use with OpenClaw

Add to your OpenClaw config:

```json
{
  "skills": [
    {
      "type": "mcp",
      "command": "agentnode-mcp",
      "args": ["--packs", "regex-builder-pack,document-redaction-pack,json-processor-pack"]
    }
  ]
}
```

## Use with Claude Code

Add to `~/.claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "agentnode": {
      "command": "agentnode-mcp",
      "args": ["--packs", "regex-builder-pack,document-redaction-pack"]
    }
  }
}
```

## Use with Cursor

Add to `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "agentnode": {
      "command": "agentnode-mcp",
      "args": ["--all"]
    }
  }
}
```

## How It Works

1. You install AgentNode packs via `agentnode install <pack>`
2. `agentnode-mcp` reads the lockfile and exposes each tool as an MCP tool
3. v0.2 multi-tool packs expose individual tools (e.g., `csv-analyzer-pack/describe`, `csv-analyzer-pack/filter`)
4. v0.1 packs expose a single tool using the pack's `run()` function
5. Any MCP client can discover and call these tools

## License

MIT
