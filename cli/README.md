# agentnode-cli

CLI for [AgentNode](https://agentnode.net) — discover, resolve, and install AI agent capabilities.

## Installation

```bash
npm install -g agentnode-cli
```

## Usage

```bash
# Search for capabilities
agentnode search "pdf extraction"

# Get package details
agentnode info pdf-reader-pack

# Resolve capability gaps
agentnode resolve --capabilities pdf_extraction --framework langchain

# Install a package
agentnode install pdf-reader-pack

# Check security policy
agentnode policy-check pdf-reader-pack

# Validate a package manifest
agentnode validate agentnode.yaml
```

## Commands

| Command | Description |
|---------|-------------|
| `search <query>` | Search for packages by keyword or capability |
| `info <slug>` | Show package details |
| `resolve` | Find best packages for capability gaps |
| `install <slug>` | Install a package |
| `list` | List installed packages |
| `update` | Update installed packages |
| `rollback <slug>` | Roll back to a previous version |
| `policy-check <slug>` | Evaluate package against security policy |
| `validate <file>` | Validate an agentnode.yaml manifest |
| `publish` | Publish a package to the registry |
| `import` | Import a tool from another platform (langchain, mcp, …) |
| `audit` | Audit installed packages for issues |
| `doctor` | Diagnose environment problems |
| `report <slug>` | Report a package for review |
| `recommend` | Get capability recommendations for your project |
| `resolve-upgrade` | Find a higher-trust replacement for an installed pack |
| `explain <slug>` | Explain why a package was selected |
| `api-keys` | Manage API keys (list, create, revoke) |
| `login` | Authenticate with the registry |

## ANP v0.2 Support

The CLI fully supports ANP v0.2 multi-tool packs. When installing a v0.2 pack, the lockfile records individual tool entrypoints:

```bash
$ agentnode install csv-analyzer-pack

Installing csv-analyzer-pack@1.1.0...
  Tools: describe, head, columns, filter_rows
  Lockfile updated with per-tool entrypoints
```

## Configuration

The CLI stores configuration in `~/.agentnode/config.json`:

```json
{
  "api_key": "ank_...",
  "base_url": "https://api.agentnode.net/v1"
}
```

## License

MIT
