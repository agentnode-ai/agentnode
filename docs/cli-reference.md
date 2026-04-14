# CLI Reference

## Installation

```bash
npm install -g agentnode-cli
```

## Configuration

Config file: `~/.agentnode/config.json`

```json
{
  "api_url": "https://agentnode.net",
  "api_key": "ank_...",
  "username": "your-username"
}
```

Environment variables (override config):
- `AGENTNODE_API_URL` — API base URL
- `AGENTNODE_API_KEY` — API key

## Commands

### `agentnode login`

Authenticate with AgentNode.

```bash
agentnode login
```

Prompts for email and password. Stores credentials in `~/.agentnode/config.json`.

---

### `agentnode search <query>`

Search for packages by keyword or capability.

```bash
agentnode search "pdf extraction"
agentnode search "web search" --framework langchain
```

**Options:**
| Flag | Description |
|------|-------------|
| `--framework <name>` | Filter by framework (langchain, crewai, generic) |
| `--type <type>` | Filter by package type (toolpack, agent, upgrade) |
| `--limit <n>` | Max results (default: 20) |

---

### `agentnode info <slug>`

Show detailed information about a package.

```bash
agentnode info pdf-reader-pack
```

Displays: capabilities, compatibility, permissions, trust level, install command.

---

### `agentnode install <slug>`

Install a package into the local Python environment.

```bash
agentnode install pdf-reader-pack
agentnode install pdf-reader-pack --version 1.0.0
```

**Options:**
| Flag | Description |
|------|-------------|
| `--version <ver>` | Install a specific version |
| `--pkg-version <ver>` | Alias for `--version` (avoids conflicts with commander's built-in `--version`) |

**What happens:**
1. Fetches install metadata from the API
2. Downloads the artifact (.tar.gz)
3. Verifies SHA-256 hash
4. Runs `pip install` on the artifact
5. Updates `agentnode.lock`

---

### `agentnode update [slug]`

Update an installed package to the latest version.

```bash
agentnode update pdf-reader-pack
agentnode update --all
```

**Options:**
| Flag | Description |
|------|-------------|
| `--all` | Update all installed packages |

---

### `agentnode rollback <slug> [version]`

Roll back a package to a previous version.

```bash
agentnode rollback pdf-reader-pack
agentnode rollback pdf-reader-pack@1.0.0
```

---

### `agentnode list`

List all installed packages from `agentnode.lock`.

```bash
agentnode list
```

---

### `agentnode validate <path>`

Validate an ANP manifest before publishing.

```bash
agentnode validate ./my-pack
agentnode validate ./my-pack --no-network
```

Checks `agentnode.yaml` against all validation rules.

**Options:**
| Flag | Description |
|------|-------------|
| `--no-network` | Offline validation only (checks manifest shape without calling the API) |
| `--timeout <ms>` | Request timeout in milliseconds (default: 30000) |

---

### `agentnode publish <path>`

Publish a package to the AgentNode registry.

```bash
agentnode publish ./my-pack
```

Requires authentication and 2FA enabled.

---

### `agentnode audit <slug>`

Run a security audit on a package.

```bash
agentnode audit pdf-reader-pack
```

Shows trust level, security findings, permissions, and provenance info.

---

### `agentnode report <slug>`

Generate an installation/security report for a package.

```bash
agentnode report pdf-reader-pack
```

---

### `agentnode resolve <capabilities...>`

Resolve capabilities to matching packages.

```bash
agentnode resolve pdf_extraction web_search
```

---

### `agentnode resolve-upgrade`

Find upgrade packages for capabilities with policy filtering.

```bash
agentnode resolve-upgrade --missing pdf_extraction --framework langchain
agentnode resolve-upgrade --missing browser_automation --min-trust verified
```

**Options:**
| Flag | Description |
|------|-------------|
| `--missing <cap>` | The missing capability to resolve |
| `--framework <name>` | Target framework |
| `--min-trust <level>` | Minimum trust level (unverified, verified, trusted, curated) |
| `--no-shell` | Exclude packages requiring shell access |
| `--no-network` | Exclude packages requiring unrestricted network |

---

### `agentnode recommend`

Get package recommendations for missing capabilities.

```bash
agentnode recommend --missing pdf_extraction,web_search --framework langchain
```

**Options:**
| Flag | Description |
|------|-------------|
| `--missing <caps>` | Comma-separated list of missing capabilities |
| `--framework <name>` | Target framework |
| `--runtime <rt>` | Target runtime |

---

### `agentnode policy-check`

Check if a package meets your policy constraints before installing.

```bash
agentnode policy-check --package pdf-reader-pack --min-trust verified
agentnode policy-check --package browser-pack --no-shell --no-network
```

**Options:**
| Flag | Description |
|------|-------------|
| `--package <slug>` | Package to check |
| `--min-trust <level>` | Minimum trust level |
| `--no-shell` | Disallow shell execution |
| `--no-network` | Disallow unrestricted network |

---

### `agentnode import <path>`

Import a tool from another platform and convert it to an ANP package.

```bash
agentnode import tool.py --from langchain
agentnode import manifest.json --from mcp --out ./my-pack
agentnode import tool.py --from openai --force
```

**Options:**
| Flag | Description |
|------|-------------|
| `--from <platform>` | Source platform: `mcp`, `langchain`, `openai`, `crewai`, `clawhub`, `skillssh` |
| `--out <dir>` | Output directory (default: current directory) |
| `--force` | Overwrite existing files in the output directory |

---

### `agentnode doctor`

Diagnose environment problems (Python, pip, lockfile, config).

```bash
agentnode doctor
```

Checks: Python availability, pip version, config file validity, lockfile integrity, API reachability.

---

### `agentnode explain <slug>`

Explain why a package was selected by the resolution engine.

```bash
agentnode explain pdf-reader-pack
```

Shows: score breakdown, capability match, trust level, framework compatibility.

---

### `agentnode api-keys`

Manage API keys for programmatic access.

```bash
agentnode api-keys list
agentnode api-keys create <label>
agentnode api-keys set <key>
agentnode api-keys remove <key>
```

**Subcommands:**
| Subcommand | Description |
|------------|-------------|
| `create <label>` | Create a new API key (requires Bearer token from login) |
| `list` | List all API keys for your account |
| `set <key>` | Set the active API key in local config |
| `remove <key>` | Remove an API key from local config |

---

## Lockfile

`agentnode.lock` is automatically created and maintained in your project directory. It tracks installed packages, versions, and artifact hashes.

```json
{
  "packages": {
    "pdf-reader-pack": {
      "version": "1.0.0",
      "hash": "sha256:abc123...",
      "installed_at": "2026-03-14T12:00:00Z"
    }
  }
}
```
