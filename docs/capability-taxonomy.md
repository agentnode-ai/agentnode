# Capability Taxonomy v0.1

AgentNode uses a flat, curated list of capability IDs. Packages declare which capabilities they provide via `capability_id` in their manifest.

## Rules

- Capabilities are official IDs, not free-form strings
- New IDs are added only on proposal and review
- Free-form `tags` are allowed on packages but are secondary to capability IDs
- Resolution and ranking are based on official capability IDs

## Capability IDs

### Document Processing

| ID | Display Name | Description |
|----|--------------|-------------|
| `pdf_extraction` | PDF Extraction | Extract text, tables, and metadata from PDF files |
| `document_parsing` | Document Parsing | Parse structured content from documents |
| `document_summary` | Document Summary | Summarize document content |
| `citation_extraction` | Citation Extraction | Extract citations and references |

### Web & Browsing

| ID | Display Name | Description |
|----|--------------|-------------|
| `web_search` | Web Search | Search the web for information |
| `webpage_extraction` | Webpage Extraction | Extract content from web pages |
| `browser_navigation` | Browser Navigation | Navigate and interact with web pages |
| `link_discovery` | Link Discovery | Discover and follow links |

### Data Analysis

| ID | Display Name | Description |
|----|--------------|-------------|
| `csv_analysis` | CSV Analysis | Analyze CSV data |
| `spreadsheet_parsing` | Spreadsheet Parsing | Parse spreadsheet files |
| `data_cleaning` | Data Cleaning | Clean and normalize data |
| `statistics_analysis` | Statistics Analysis | Statistical analysis of data |
| `chart_generation` | Chart Generation | Generate charts and visualizations |
| `json_processing` | JSON Processing | Process and transform JSON data |
| `sql_generation` | SQL Generation | Generate SQL queries |
| `log_analysis` | Log Analysis | Analyze log files |

### Memory & Retrieval

| ID | Display Name | Description |
|----|--------------|-------------|
| `vector_memory` | Vector Memory | Vector-based memory storage and retrieval |
| `knowledge_retrieval` | Knowledge Retrieval | Retrieve knowledge from indexed sources |
| `semantic_search` | Semantic Search | Meaning-based search over content |
| `embedding_generation` | Embedding Generation | Generate vector embeddings |
| `document_indexing` | Document Indexing | Index documents for retrieval |
| `conversation_memory` | Conversation Memory | Store and recall conversation context |

### Communication

| ID | Display Name | Description |
|----|--------------|-------------|
| `email_drafting` | Email Drafting | Draft email messages |
| `email_summary` | Email Summary | Summarize email content |
| `meeting_summary` | Meeting Summary | Summarize meetings |

### Productivity

| ID | Display Name | Description |
|----|--------------|-------------|
| `scheduling` | Scheduling | Calendar and scheduling operations |
| `task_management` | Task Management | Manage tasks and to-dos |

### Language

| ID | Display Name | Description |
|----|--------------|-------------|
| `translation` | Translation | Translate between languages |
| `tone_adjustment` | Tone Adjustment | Adjust text tone and style |

### Development

| ID | Display Name | Description |
|----|--------------|-------------|
| `code_analysis` | Code Analysis | Analyze source code |

## Using Capability IDs

### In Manifests

```yaml
capabilities:
  tools:
    - name: "extract_pdf_text"
      capability_id: "pdf_extraction"
```

### In Resolution

```bash
agentnode resolve-upgrade --missing pdf_extraction --framework langchain
```

### In the SDK

```python
result = an.resolve_upgrade(
    missing_capability="pdf_extraction",
    framework="langchain"
)
```

## Proposing New Capabilities

New capability IDs can be proposed via GitHub issues. Requirements:
- Clear, distinct use case not covered by existing IDs
- At least one package that would use it
- Snake_case format matching existing conventions
