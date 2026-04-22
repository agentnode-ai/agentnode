# CLI Reference

## Installation

```bash
npm install -g agentnode-cli
```

## Configuration

Config file: `~/.agentnode/config.json`

```json
{
  "api_url": "https://api.agentnode.net",
  "api_key": "ank_...",
  "username": "your-username"
}
```

Environment variables (override config):
- `AGENTNODE_API_URL` — API base URL
- `AGENTNODE_API_KEY` — API key

## Commands

### `agentnode init [name]`

Scaffold a new AgentNode package project.

```bash
agentnode init my-tool
agentnode init my-agent --type agent
agentnode init my-tool --publisher acme --dir ./packages/my-tool
```

**Options:**
| Flag | Description |
|------|-------------|
| `-t, --type <type>` | Package type: `toolpack`, `agent`, or `upgrade` (default: `toolpack`) |
| `--publisher <slug>` | Publisher slug |
| `--dir <path>` | Output directory (defaults to `./<name>`) |

---

### `agentnode login`

Authenticate with the AgentNode registry.

```bash
agentnode login
agentnode login --api-key ank_...
```

Prompts for an API key (input is hidden). Verifies the key against the API and stores credentials in `~/.agentnode/config.json`.

**Options:**
| Flag | Description |
|------|-------------|
| `--api-key <key>` | API key (or set `AGENTNODE_API_KEY` env var) |

---

### `agentnode auth [provider]`

Manage local credentials for connector packages (no account needed).

```bash
agentnode auth github
agentnode auth slack
agentnode auth list
agentnode auth remove github
```

**Subcommands:**
| Subcommand | Description |
|------------|-------------|
| `<provider>` | Store a token for a provider (e.g. `github`, `slack`). Prompts for the token interactively. |
| `list` | List locally stored credentials |
| `remove <provider>` | Remove a locally stored credential |

**Options (for `auth <provider>`):**
| Flag | Description |
|------|-------------|
| `--no-validate` | Skip token validation |
| `--json` | Output JSON |

**Options (for `auth remove`):**
| Flag | Description |
|------|-------------|
| `--yes` | Skip confirmation prompt |
| `--json` | Output JSON |

Supported providers: `github`, `slack`.

---

### `agentnode search <query>`

Search for packages by keyword or capability.

```bash
agentnode search "pdf extraction"
agentnode search "web search" -f langchain
agentnode search "browser" -t agent -l 5
agentnode search "slack" -c slack_integration
```

**Options:**
| Flag | Description |
|------|-------------|
| `-f, --framework <name>` | Filter by framework (langchain, crewai, generic) |
| `-t, --type <type>` | Filter by package type (agent, toolpack, upgrade) |
| `-c, --capability <id>` | Filter by capability ID |
| `-l, --limit <n>` | Max results (default: 10) |

---

### `agentnode info <slug>`

Show detailed information about a package.

```bash
agentnode info pdf-reader-pack
```

Displays: capabilities, compatibility, permissions, trust level, install command.

---

### `agentnode install <slug>`

Install a package from the AgentNode registry.

```bash
agentnode install pdf-reader-pack
agentnode install pdf-reader-pack -v 1.0.0
agentnode install pdf-reader-pack --json
```

**Options:**
| Flag | Description |
|------|-------------|
| `-v, --version <ver>` | Install a specific version |
| `--pkg-version <ver>` | Alias for `--version` (avoids conflicts with commander's built-in `--version`) |
| `--verbose` | Show detailed output |
| `--json` | Output JSON |
| `--allow-unhashed` | Proceed even when the server did not return an artifact hash (unsafe — only for local/dev testing) |

**What happens:**
1. Fetches install metadata from the API
2. Downloads the artifact (.tar.gz)
3. Verifies SHA-256 hash (fails closed if hash missing, unless `--allow-unhashed`)
4. Runs `pip install` on the artifact
5. Updates `agentnode.lock`

---

### `agentnode update [slug]`

Update an installed package to the latest version.

```bash
agentnode update pdf-reader-pack
agentnode update --all
agentnode update pdf-reader-pack --json
```

**Options:**
| Flag | Description |
|------|-------------|
| `--all` | Update all installed packages |
| `--verbose` | Show detailed output |
| `--json` | Output JSON |

---

### `agentnode rollback <slug@version>`

Roll back a package to a specific version.

```bash
agentnode rollback pdf-reader-pack@0.9.0
```

The argument must be in `<slug>@<version>` format.

**Options:**
| Flag | Description |
|------|-------------|
| `--verbose` | Show detailed output |
| `--json` | Output JSON |

---

### `agentnode list`

List all installed packages from `agentnode.lock`.

```bash
agentnode list
agentnode list --json
```

**Options:**
| Flag | Description |
|------|-------------|
| `--json` | Output JSON |

---

### `agentnode validate [dir]`

Validate an ANP package manifest.

```bash
agentnode validate ./my-pack
agentnode validate ./my-pack --no-network
agentnode validate ./my-pack --json
```

Checks `agentnode.yaml` against all validation rules.

**Options:**
| Flag | Description |
|------|-------------|
| `--no-network` | Offline validation only (checks manifest shape without calling the API) |
| `--timeout <seconds>` | Network timeout in seconds (default: 30) |
| `--json` | Output JSON |

---

### `agentnode publish <path>`

Publish a package to the AgentNode registry.

```bash
agentnode publish ./my-pack
agentnode publish ./my-pack --token ank_...
agentnode publish ./my-pack --no-artifact
```

Requires authentication (via `--token`, stored API key, or `agentnode login`).

**Options:**
| Flag | Description |
|------|-------------|
| `--token <token>` | Authentication token |
| `--no-artifact` | Publish metadata only (skip artifact upload) |

---

### `agentnode audit`

View and manage the policy decision audit trail.

Reads `~/.agentnode/audit.jsonl` (append-only JSONL written by the SDK policy kernel) and presents it in human-readable or JSON format.

```bash
agentnode audit show
agentnode audit show --limit 50 --json
agentnode audit stats
agentnode audit stats --json
agentnode audit clear --yes
```

**Subcommands:**
| Subcommand | Description |
|------------|-------------|
| `show` | Display recent audit entries (tabular or JSON) |
| `stats` | Show summary statistics (allow/deny/prompt counts, top slugs) |
| `clear` | Delete the audit log file (requires `--yes` confirmation) |

**Show options:**
| Flag | Description |
|------|-------------|
| `-n, --limit <count>` | Number of entries to show (default: 20) |
| `--json` | Output raw JSON lines |

**Stats options:**
| Flag | Description |
|------|-------------|
| `--json` | Output JSON |

**Clear options:**
| Flag | Description |
|------|-------------|
| `--yes` | Skip confirmation prompt |
| `--json` | Output JSON |

---

### `agentnode report <slug>`

Report a package for policy violation or abuse.

```bash
agentnode report suspicious-pack
agentnode report suspicious-pack -r malware -d "Contains obfuscated code"
agentnode report suspicious-pack --json
```

**Options:**
| Flag | Description |
|------|-------------|
| `-r, --reason <reason>` | Reason: `malware`, `typosquatting`, `spam`, `misleading`, `policy_violation`, `other` |
| `-d, --description <text>` | Description of the issue |
| `--json` | Output JSON |

If `--reason` or `--description` are omitted, you will be prompted interactively.

---

### `agentnode resolve <capabilities...>`

Resolve capabilities to ranked packages.

```bash
agentnode resolve pdf_extraction web_search
agentnode resolve pdf_extraction -f langchain -l 10
```

**Options:**
| Flag | Description |
|------|-------------|
| `-f, --framework <name>` | Preferred framework |
| `-l, --limit <n>` | Max results (default: 5) |

---

### `agentnode resolve-upgrade`

Find upgrade packages for capabilities with policy filtering.

```bash
agentnode resolve-upgrade --missing pdf_extraction --framework langchain
agentnode resolve-upgrade --missing pdf_extraction browser_automation --min-trust verified
agentnode resolve-upgrade --missing browser_automation --runtime python --json
```

**Options:**
| Flag | Description |
|------|-------------|
| `--missing <capabilities...>` | Missing capability IDs (space-separated, variadic) |
| `--framework <name>` | Target framework |
| `--runtime <runtime>` | Target runtime |
| `--min-trust <level>` | Minimum trust level (unverified, verified, trusted, curated) |
| `--no-shell` | Exclude packages requiring shell access |
| `--json` | Output JSON |

---

### `agentnode recommend`

Get package recommendations for missing capabilities.

```bash
agentnode recommend --missing pdf_extraction web_search --framework langchain
agentnode recommend --missing browser_automation --runtime python --json
```

**Options:**
| Flag | Description |
|------|-------------|
| `--missing <capabilities...>` | Missing capability IDs (space-separated, variadic) |
| `--framework <name>` | Target framework |
| `--runtime <runtime>` | Target runtime |
| `--json` | Output JSON |

---

### `agentnode policy-check`

Check if a package meets your policy constraints before installing.

```bash
agentnode policy-check --package pdf-reader-pack --min-trust verified
agentnode policy-check --package browser-pack --no-shell --no-network --json
```

**Options:**
| Flag | Description |
|------|-------------|
| `--package <slug>` | Package to check |
| `--min-trust <level>` | Minimum trust level |
| `--no-shell` | Disallow shell execution |
| `--no-network` | Disallow unrestricted network |
| `--json` | Output JSON |

---

### `agentnode import <file>`

Import tools from other platforms and convert to ANP format.

```bash
agentnode import tool.py --from langchain
agentnode import manifest.json --from mcp -o ./my-pack
agentnode import tool.py --from openai --force
agentnode import tool.py --from crewai --publisher acme --slug my-tool-pack --dry-run
```

**Options:**
| Flag | Description |
|------|-------------|
| `--from <platform>` | Source platform: `mcp`, `langchain`, `openai`, `crewai`, `clawhub`, `skillssh` (required) |
| `-o, --output <dir>` | Output directory (default: `.`) |
| `--publisher <name>` | Publisher name (default: `my-publisher`) |
| `--slug <slug>` | Package slug (auto-generated from tool name if not provided) |
| `--dry-run` | Show what would be generated without writing files |
| `--json` | Output manifest as JSON instead of YAML |
| `--force` | Overwrite existing files in the output directory |

---

### `agentnode doctor`

Analyze installed packages for deprecation, security findings, risky permissions, and available updates.

```bash
agentnode doctor
agentnode doctor --json
```

Checks all packages in `agentnode.lock` against the registry and reports: deprecated packages, open security findings, shell/unrestricted-network permissions, and available version updates.

**Options:**
| Flag | Description |
|------|-------------|
| `--json` | Output JSON |

---

### `agentnode explain <slug>`

Explain what a package does, its capabilities, permissions, and use cases.

```bash
agentnode explain pdf-reader-pack
agentnode explain pdf-reader-pack --json
```

Shows: capabilities, compatibility, permissions, trust level, install command.

**Options:**
| Flag | Description |
|------|-------------|
| `--json` | Output JSON |

---

### `agentnode api-keys`

Manage API keys for programmatic access.

```bash
agentnode api-keys list
agentnode api-keys list --json
agentnode api-keys create my-ci-key
agentnode api-keys create my-ci-key --token ank_... --json
agentnode api-keys set ank_...
agentnode api-keys remove
```

**Subcommands:**
| Subcommand | Description |
|------------|-------------|
| `create <label>` | Create a new API key (requires Bearer token from login) |
| `list` | Show currently configured API key |
| `set <key>` | Store an API key in local config |
| `remove` | Remove stored API key from config (takes no argument) |

**Create options:**
| Flag | Description |
|------|-------------|
| `--token <token>` | Bearer token (or uses stored token) |
| `--json` | Output JSON |

**List options:**
| Flag | Description |
|------|-------------|
| `--json` | Output JSON |

---

### `agentnode runs`

Manage agent run logs stored locally at `~/.agentnode/runs/`.

```bash
agentnode runs list
agentnode runs list --limit 5 --json
agentnode runs show <run_id>
agentnode runs show <run_id> --json
agentnode runs clean --dry-run
agentnode runs clean --max-age 7 --max-count 100 --json
```

**Subcommands:**
| Subcommand | Description |
|------------|-------------|
| `list [--limit N]` | List recent runs (newest first, default: 20) |
| `show <run_id>` | Show events for a specific run (full or prefix ID) |
| `clean [--dry-run]` | Remove old run logs based on retention policy |

**List options:**
| Flag | Description |
|------|-------------|
| `--limit <n>` | Max runs to show (default: 20) |
| `--json` | Output JSON |

**Show options:**
| Flag | Description |
|------|-------------|
| `--json` | Output JSON |

**Clean options:**
| Flag | Description |
|------|-------------|
| `--dry-run` | Show what would be deleted without deleting |
| `--max-age <days>` | Max age in days (default: 30) |
| `--max-count <n>` | Max number of runs to keep (default: 500) |
| `--json` | Output JSON |

Runs are also automatically cleaned after each agent execution based on
`~/.agentnode/config.json`:

```json
{ "run_log": { "max_age_days": 30, "max_count": 500 } }
```

---

### `agentnode credentials`

Manage stored credentials for connector packages. Requires authentication.

```bash
agentnode credentials list
agentnode credentials test <credential_id>
agentnode credentials delete <credential_id>
```

**Subcommands:**
| Subcommand | Description |
|------------|-------------|
| `list` | List all stored credentials (provider, status, domains) |
| `test <id>` | Test a credential's connectivity via the backend health check |
| `delete <id>` | Revoke and delete a stored credential |

All subcommands support `--json` for machine-readable output.

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
