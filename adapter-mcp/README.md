# agentnode-mcp

> **Deprecated — use `agentnode serve` instead.** This package stays available and unchanged, but it is no
> longer the recommended way to use AgentNode over MCP. It imports a pack's Python code into its own process
> and calls it directly, outside the sandbox, guard and host-trust policy. `agentnode serve` (in
> `agentnode-sdk`) offers the same tools and runs every pack tool through the same checks as `agentnode run`.
>
> | Before | Now |
> |---|---|
> | `agentnode-mcp --packs a,b` | `agentnode serve --packs a,b` |
> | `agentnode-mcp --all` | `agentnode serve` |
> | `agentnode-mcp-platform [--api-url URL] [--api-key KEY]` | `agentnode serve` (the four platform tools are included; set `AGENTNODE_API_URL` / `AGENTNODE_API_KEY`) |
> | client config `"command": "agentnode-mcp", "args": ["--all"]` | `"command": "agentnode", "args": ["serve"]` |
>
> Tool names stay the same (`<slug>` or `<slug>/<tool>`, `agentnode_search`, `agentnode_resolve`,
> `agentnode_explain`, `agentnode_capabilities`). What changes on purpose: a pack that is not curated runs
> only in the container sandbox (or is refused when none is available), a tool that needs a confirmation is
> refused instead of run, and a failed call is reported as an MCP tool error (`isError`).

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
