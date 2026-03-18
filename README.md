<p align="center">
  <img src="https://agentnode.net/logo.svg" width="60" alt="AgentNode" />
</p>

<h1 align="center">AgentNode</h1>

<p align="center">
  <strong>Build once. Run on any agent. Know it works.</strong>
</p>

<p align="center">
  Most AI tools don't work when you install them.<br/>
  AgentNode verifies every capability and makes it run across any agent framework.
</p>

<p align="center">
  <a href="https://agentnode.net">Website</a> ·
  <a href="https://agentnode.net/docs">Docs</a> ·
  <a href="https://agentnode.net/search">Browse Packages</a> ·
  <a href="https://agentnode.net/builder">Builder</a> ·
  <a href="https://agentnode.net/import">Import Tool</a>
</p>

<p align="center">
  <a href="LICENSE-MIT"><img src="https://img.shields.io/badge/CLI%20%2F%20SDK-MIT-green" alt="MIT" /></a>
  <a href="LICENSE-BSL"><img src="https://img.shields.io/badge/Backend-BSL%201.1-blue" alt="BSL" /></a>
  <a href="https://agentnode.net"><img src="https://img.shields.io/badge/web-agentnode.net-purple" alt="Website" /></a>
</p>

---

## The Problem

Every AI agent framework is rebuilding the same tools from scratch. PDF extraction, web search, email sending — written over and over, for each framework separately.

And most of them **don't even work**:

- **PyPI**: 500K+ packages. No way to know if one works with your agent. Many fail on install.
- **npm**: Thousands of abandoned packages. No agent compatibility metadata.
- **Building from scratch**: Weeks per capability. You own every bug and security hole.
- **Other marketplaces**: Framework lock-in. Proprietary formats. Limited discovery.

## The Fix

AgentNode is a **standard for portable, verified AI agent capabilities**.

Every capability is:

- **Verified to work** — 4-step automated pipeline tests install, import, execution, and unit tests on every publish
- **Built to run anywhere** — works with LangChain, CrewAI, AutoGPT, OpenAI, MCP, or vanilla Python
- **Secure by default** — Ed25519 signatures, Bandit scanning, typosquatting detection, permission manifests

```
$ agentnode search "pdf extraction"

  pdf-reader-pack  v1.2.0  ★ trusted  ✓ verified
  Extract text, tables, and metadata from PDF documents.
  Capabilities: pdf_extraction · Frameworks: langchain, crewai, generic
```

## Quick Start

```bash
pip install agentnode-sdk
```

```python
from agentnode_sdk import AgentNodeClient
from agentnode_sdk.installer import load_tool

client = AgentNodeClient()

# Find and install the best PDF tool
client.resolve_and_install(["pdf_extraction"])

# Load and use — typed input/output
extract = load_tool("pdf-reader-pack")
result = extract({"file_path": "report.pdf"})
print(result["pages"])
```

That's it. No framework glue. No dependency hell. No "does this even work?"

## Why AgentNode

### 1. Verified on Publish

Every package goes through a **4-step verification pipeline** before it reaches the registry:

| Step | What it checks |
|------|---------------|
| **Install** | Can it be pip-installed without errors? |
| **Import** | Does the module load? Are entrypoints callable? |
| **Smoke Test** | Does the tool execute without crashing? |
| **Unit Tests** | Do the author's tests pass? |

If install or import fails → **auto-quarantined**. Never reaches your agent.
If smoke or tests have issues → **transparent warning badges**. You decide.

**No other registry does this.** PyPI lists packages that fail to install. npm has abandoned packages with zero warning.

### 2. One Standard, Every Framework

The [ANP format](https://agentnode.net/docs) (AgentNode Package) is an open standard for agent capabilities:

```yaml
manifest_version: "0.2"
package_id: "csv-analyzer-pack"

capabilities:
  tools:
    - name: "describe_csv"
      entrypoint: "csv_analyzer_pack.tool:describe"
      input_schema: { file_path: { type: "string" } }

    - name: "filter_csv"
      entrypoint: "csv_analyzer_pack.tool:filter_rows"
      input_schema: { file_path: { type: "string" }, query: { type: "string" } }

runtime:
  language: python
  min_version: "3.10"

frameworks: [langchain, crewai, autogpt, generic]
```

One pack. Multiple tools. Typed schemas. Works with any framework.

### 3. Agents Get Better Over Time

Agents shouldn't start from scratch every time. With AgentNode, they **resolve their own capability gaps**:

```python
# Your agent identifies what it can't do
# and autonomously finds, verifies, and installs the right tool
client.resolve_and_install(["pdf_extraction", "web_search"])

# Load specific tools from multi-tool packs
extract = load_tool("pdf-reader-pack")
search = load_tool("web-search-pack", tool_name="search")
```

The more capabilities in the registry, the more powerful every agent becomes.

### 4. Build or Import in Minutes

**Don't have a package?** Two paths to publishing:

- **[Builder](https://agentnode.net/builder)** — Describe what your tool does in plain language → get a complete ANP package with code, manifest, and schemas
- **[Import](https://agentnode.net/import)** — Paste existing LangChain / MCP / OpenAI / CrewAI code → get an ANP package back. Zero rewrite.

## Install

```bash
# Python SDK — for agents & apps
pip install agentnode-sdk

# CLI — search, install, publish
npm install -g agentnode-cli

# Framework adapters
pip install agentnode-langchain
pip install agentnode-mcp
```

## MCP Integration

Use AgentNode packs as tools in **Claude Code**, **Cursor**, and any MCP-compatible client:

```bash
# Expose a pack as MCP tools
agentnode-mcp --pack pdf-reader-pack

# Expose the full platform (search, resolve, explain)
agentnode-mcp-platform --api-url https://api.agentnode.net
```

## GitHub Action

Automate publishing from CI/CD:

```yaml
- uses: agentnode/publish@v1
  with:
    api-key: ${{ secrets.AGENTNODE_API_KEY }}
```

## Trust & Security

| Layer | What it does |
|-------|-------------|
| **4 trust levels** | Unverified → Verified → Trusted → Curated |
| **4-step verification** | Install, import, smoke test, unit tests on every publish |
| **Ed25519 signatures** | Cryptographic provenance verification |
| **Bandit scanning** | Static analysis for security vulnerabilities |
| **Typosquatting detection** | Prevents name confusion attacks |
| **Permission manifests** | See network, filesystem, code execution access before install |
| **Auto-quarantine** | Failed packages never reach the registry |

## Architecture

```
agentnode/
├── backend/           # FastAPI + PostgreSQL + Redis + Meilisearch
├── cli/               # TypeScript CLI (npm)
├── sdk/               # Python SDK (PyPI)
├── adapter-mcp/       # MCP server integration
├── web/               # Next.js frontend (agentnode.net)
├── action/            # GitHub Action for CI/CD publishing
├── starter-packs/     # 76+ verified reference packages
├── docs/              # Documentation
└── scripts/           # Tooling and seed scripts
```

## Documentation

- [Getting Started](docs/getting-started.md)
- [ANP Format Spec](docs/anp-format.md)
- [Publishing Guide](docs/publishing.md)
- [API Reference](docs/api-reference.md)
- [SDK Reference](docs/sdk-reference.md)
- [CLI Reference](docs/cli-reference.md)
- [Capability Taxonomy](docs/capability-taxonomy.md)

## Contributing

AgentNode is open source. The SDK, CLI, adapters, and all starter packs are MIT-licensed.

```bash
# Local development
docker-compose up -d
cd backend && pip install -e ".[dev]" && alembic upgrade head && uvicorn app.main:app --reload
cd web && npm install && npm run dev
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

## License

| Component | License |
|---|---|
| `cli/`, `sdk/`, `adapter-mcp/`, `starter-packs/`, `action/` | [MIT](LICENSE-MIT) |
| `backend/` | [BSL 1.1](LICENSE-BSL) → Apache 2.0 on 2030-03-16 |

---

<p align="center">
  <strong>Build once. Run on any agent. Know it works.</strong><br/>
  <a href="https://agentnode.net">agentnode.net</a>
</p>
