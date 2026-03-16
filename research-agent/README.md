# AgentNode Reference Research Agent

A reference implementation that demonstrates the core AgentNode flow:
**Capability Resolution -> Installation -> Integration**.

This agent uses the 3 MVP starter packs to perform a web research task:

| Pack | Capability | What it does |
|------|-----------|--------------|
| `web-search-pack` | `web_search` | Searches the web via DuckDuckGo |
| `webpage-extractor-pack` | `webpage_extraction` | Extracts clean text from URLs |
| `pdf-reader-pack` | `pdf_extraction` | Extracts text and tables from PDFs |

## How it works

The agent runs three phases:

1. **Capability Resolution** -- Uses the `agentnode-sdk` to discover which packs
   satisfy the required capabilities (`web_search`, `webpage_extraction`,
   `pdf_extraction`). If the AgentNode API is not reachable, falls back to
   using the local starter packs directly.

2. **Research Pipeline** -- Orchestrates the three packs in sequence:
   - Search the web for the given topic
   - Extract full-text content from the top search results
   - (Optional) Extract text from a local PDF file

3. **Summary** -- Compiles all collected data into a structured research summary.

## Setup

From the project root (`agentnode/`):

```bash
# Create a virtual environment
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
# .venv\Scripts\activate    # Windows

# Install the SDK and all three starter packs as editable packages
pip install -e sdk/
pip install -e starter-packs/web-search-pack/
pip install -e starter-packs/webpage-extractor-pack/
pip install -e starter-packs/pdf-reader-pack/
```

## Usage

```bash
# Basic research -- search and extract
python research-agent/research_agent.py "artificial intelligence safety"

# Include a PDF in the research
python research-agent/research_agent.py "climate change" --pdf path/to/report.pdf

# Control how many results to search and extract
python research-agent/research_agent.py "quantum computing" --max-results 10 --extract-top 3

# Save results to JSON
python research-agent/research_agent.py "machine learning" --output results.json

# Use the AgentNode API for capability resolution
export AGENTNODE_API_KEY="your-api-key"
python research-agent/research_agent.py "robotics"
```

### CLI Options

| Option | Default | Description |
|--------|---------|-------------|
| `topic` | (required) | The research topic to investigate |
| `--pdf FILE` | none | Path to a PDF file to include |
| `--pdf-pages RANGE` | `all` | PDF page range, e.g. `1-5` |
| `--max-results N` | `5` | Number of web search results |
| `--extract-top N` | `2` | How many top URLs to extract full content from |
| `--api-key KEY` | `$AGENTNODE_API_KEY` | AgentNode API key for capability resolution |
| `--output FILE` | none | Write structured results to a JSON file |

## What this demonstrates

This agent is intentionally simple. It shows developers how to:

- **Discover capabilities** via the AgentNode SDK (`client.resolve()`)
- **Inspect install metadata** (`client.get_install_metadata()`)
- **Use tool packs** by importing and calling their `run()` functions
- **Compose a pipeline** that chains multiple packs together
- **Handle graceful degradation** when packs or the API are unavailable

It is a starting point for building more sophisticated agents that leverage
the AgentNode ecosystem for dynamic capability discovery and integration.
