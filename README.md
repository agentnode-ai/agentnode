# AgentNode

**The open package registry for AI agent capabilities.**

AgentNode helps developers and AI agents discover, resolve, and install trust-verified capability upgrades. One registry for every framework.

[![License: MIT](https://img.shields.io/badge/CLI%20%2F%20SDK-MIT-green)](LICENSE-MIT)
[![License: BSL 1.1](https://img.shields.io/badge/Backend-BSL%201.1-blue)](LICENSE-BSL)
[![Website](https://img.shields.io/badge/web-agentnode.net-purple)](https://agentnode.net)

```bash
$ agentnode search "pdf extraction"

  pdf-reader-pack  v1.2.0  ★ trusted
  Extract text, tables, and metadata from PDF documents.
  Capabilities: pdf_extraction, document_summary
  Frameworks: langchain, crewai, generic

$ agentnode install csv-analyzer-pack

  Installed: csv-analyzer-pack@1.1.0
  Tools: describe, head, columns, filter_rows
```

## What AgentNode Does

- **Discover** — Search 76+ starter packs across 97 capability IDs
- **Resolve** — Match capability gaps to ranked, compatible packages
- **Verify** — Ed25519 signatures, security scanning, typosquatting detection
- **Install** — Trust-verified packages with lockfile tracking
- **Integrate** — Works with LangChain, CrewAI, AutoGPT, or any Python framework

## Quick Start

### CLI

```bash
npm install -g agentnode-cli

agentnode login
agentnode search "pdf extraction"
agentnode install pdf-reader-pack
agentnode validate .
agentnode publish .
```

### Python SDK

```bash
pip install agentnode-sdk
```

```python
from agentnode_sdk import AgentNodeClient
from agentnode_sdk.installer import load_tool

client = AgentNodeClient(api_key="ank_...")
results = client.search("pdf extraction")

# v0.2: Load specific tools from multi-tool packs
describe = load_tool("csv-analyzer-pack", tool_name="describe")
result = describe({"file_path": "data.csv"})
```

### MCP Integration

Use AgentNode packs as tools in Claude Code, Cursor, and other MCP clients:

```bash
pip install agentnode-mcp

# Expose a single pack as MCP tools
agentnode-mcp --pack pdf-reader-pack

# Expose the full platform (search, resolve, explain)
agentnode-mcp-platform --api-url https://api.agentnode.net
```

### GitHub Action

Automate pack publishing from CI/CD:

```yaml
- uses: agentnode/publish@v1
  with:
    api-key: ${{ secrets.AGENTNODE_API_KEY }}
```

## Architecture

```
agentnode/
├── backend/           # FastAPI + PostgreSQL + Redis + Meilisearch
├── cli/               # TypeScript CLI (npm)
├── sdk/               # Python SDK (PyPI)
├── adapter-mcp/       # MCP server integration
├── web/               # Next.js frontend (agentnode.net)
├── action/            # GitHub Action for CI/CD publishing
├── starter-packs/     # 76+ reference packages with tests
├── docs/              # Documentation
└── scripts/           # Tooling and seed scripts
```

## Core Concepts

### ANP Format

Every package has an `agentnode.yaml` manifest declaring identity, capabilities, permissions, framework compatibility, and trust metadata. ANP v0.2 supports multi-tool packs where each tool has its own entrypoint (`module.path:function`) and typed input/output schemas.

### Resolution Engine

Scores packages based on capability match (40%), framework compatibility (20%), runtime match (15%), trust level (15%), and permission safety (10%). Supports fuzzy matching and policy-aware filtering.

### Trust Layer

- **4 trust levels:** `unverified` > `verified` > `trusted` > `curated`
- **Ed25519 signatures** for provenance verification
- **Bandit security scanning** for vulnerability detection
- **Typosquatting detection** to prevent name confusion attacks
- **Publisher quarantine** for flagged accounts

### Capability Taxonomy

97 standardized capability IDs across 14 categories: Document Processing, Web & Browsing, Communication, Data Analysis, Developer Tools, Cloud & DevOps, AI & ML, Media, IoT, Security, Productivity, Design, Finance, and Science.

## Documentation

- [Getting Started](docs/getting-started.md)
- [CLI Reference](docs/cli-reference.md)
- [API Reference](docs/api-reference.md)
- [SDK Reference](docs/sdk-reference.md)
- [Publishing Guide](docs/publishing.md)
- [ANP Format](docs/anp-format.md)
- [Capability Taxonomy](docs/capability-taxonomy.md)
- [Deployment](docs/deployment.md)

## Development

### Prerequisites

- Python 3.12+
- Node.js 20+
- Docker (for local services)

### Local Setup

```bash
# Start infrastructure
docker-compose up -d  # PostgreSQL, Redis, Meilisearch, MinIO

# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload --port 8001

# Web
cd web
npm install
npm run dev

# CLI
cd cli
npm install
npm run build
```

## License

AgentNode uses a dual-license model:

| Component | License | Details |
|---|---|---|
| `cli/`, `sdk/`, `adapter-mcp/`, `starter-packs/`, `action/` | [MIT](LICENSE-MIT) | Free to use, modify, redistribute |
| `backend/` | [BSL 1.1](LICENSE-BSL) | Free to use except hosting a competing registry. Converts to Apache 2.0 on 2030-03-16 |

See [LICENSE](LICENSE) for the full overview or visit [agentnode.net/license](https://agentnode.net/license).

---

**AgentNode — where agents find upgrades.**
