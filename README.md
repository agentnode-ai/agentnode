# AgentNode

**The open upgrade and discovery infrastructure for AI agents.**

AgentNode helps developers and AI hosts resolve capability gaps with trust-aware, framework-compatible upgrade recommendations.

```bash
$ agentnode search "pdf extraction"

  pdf-reader-pack  v1.0.0  ★ unverified
  Extract text, tables, and metadata from PDF files.
  Capabilities: pdf_extraction
  Frameworks: langchain, crewai, generic

$ agentnode install pdf-reader-pack

  Installed: pdf-reader-pack@1.0.0
  Entrypoint: from pdf_reader_pack import tool
```

## What AgentNode Does

- **Discover** capabilities through search and resolution
- **Resolve** capability gaps to ranked, compatible packages
- **Evaluate** security policies before installation
- **Install** packages with trust verification and lockfile tracking
- **Upgrade** to better versions while maintaining policy compliance

## Quick Start

### CLI

```bash
npm install -g agentnode

agentnode search "pdf extraction"
agentnode install pdf-reader-pack
```

### Python SDK

```bash
pip install agentnode-sdk
```

```python
from agentnode import AgentNode

an = AgentNode(api_key="ank_...")
results = an.search("pdf extraction")
result = an.resolve_upgrade(
    missing_capability="pdf_extraction",
    framework="langchain"
)
```

### LangChain Adapter

```bash
pip install agentnode-langchain
```

```python
from agentnode_langchain import load_tool

tool = load_tool("pdf-reader-pack", api_key="ank_...")
```

## Architecture

```
agentnode/
├── backend/           # FastAPI + PostgreSQL + Redis + Meilisearch
├── cli/               # TypeScript CLI (npm package)
├── sdk/               # Python SDK (PyPI package)
├── adapter-langchain/ # LangChain integration
├── web/               # Next.js frontend
├── starter-packs/     # 3 reference packages
│   ├── pdf-reader-pack/
│   ├── web-search-pack/
│   └── webpage-extractor-pack/
├── examples/          # Research agent demo
└── docs/              # Documentation
```

## Core Concepts

### ANP — Agent Node Package

The native package format for installable agent capabilities. Each package has an `agentnode.yaml` manifest declaring capabilities, permissions, compatibility, and trust metadata.

### Resolution Engine

Scores packages based on capability match (40%), framework compatibility (20%), runtime match (15%), trust level (15%), and permission safety (10%).

### Trust & Policy

Four trust levels: `unverified` → `verified` → `trusted` → `curated`. Policy checks evaluate trust, permissions, and approval requirements before installation.

### Capability Taxonomy

30 curated capability IDs across document processing, web & browsing, data analysis, memory & retrieval, communication, productivity, language, and development.

## Documentation

- [Getting Started](docs/getting-started.md)
- [CLI Reference](docs/cli-reference.md)
- [API Reference](docs/api-reference.md)
- [SDK Reference](docs/sdk-reference.md)
- [Publishing Guide](docs/publishing.md)
- [ANP Format](docs/anp-format.md)
- [Capability Taxonomy](docs/capability-taxonomy.md)

## Development

### Backend

```bash
cd backend
pip install -e ".[dev]"
pytest
```

### CLI

```bash
cd cli
npm install
npm run build
npm test
```

### Web

```bash
cd web
npm install
npm run dev
```

### Local Services

```bash
docker-compose up -d  # PostgreSQL, Redis, Meilisearch, MinIO
```

## Open Core

- **Open:** ANP format, CLI, SDKs, adapters, starter packs, capability taxonomy
- **Proprietary:** Registry backend, resolution engine, trust logic, ranking data

## License

See individual component directories for license information.

---

**AgentNode — where agents find upgrades.**
