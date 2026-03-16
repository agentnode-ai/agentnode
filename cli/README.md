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
| `audit` | Audit installed packages for issues |
| `doctor` | Diagnose environment problems |
| `login` | Authenticate with the registry |

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
