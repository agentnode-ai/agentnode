"""Canonical capability taxonomy with status, category, and metadata.

This is the authoring-side registry of all capability IDs the system knows
about.  Publishers reference these IDs in their ``agentnode.yaml`` manifests.

Status values:

- ``active`` — currently installable in the public catalog.
  Maintained manually until a registry-backed capability index exists.
- ``planned`` — recognized capability with no installable packages yet.
  Will not surface in CLI recommendations or doctor output.

The *runtime* graph (``capability_graph.py``) handles relationships and
scoring.  This module handles *identity*: what capabilities exist, which
ones are installable, and human-readable metadata for documentation and
publisher tooling.
"""
from __future__ import annotations


CAPABILITY_TAXONOMY: dict[str, dict] = {
    # --- Search & Web ---
    "web_search": {
        "label": "Web search",
        "category": "research",
        "status": "active",
        "description": "Search the web and return structured results",
    },
    "webpage_extraction": {
        "label": "Webpage extraction",
        "category": "research",
        "status": "active",
        "description": "Extract content from web pages",
    },
    "browser_navigation": {
        "label": "Browser navigation",
        "category": "research",
        "status": "active",
        "description": "Programmatic browser control and navigation",
    },

    # --- Document Processing ---
    "pdf_extraction": {
        "label": "PDF extraction",
        "category": "documents",
        "status": "active",
        "description": "Extract text and structure from PDF files",
    },
    "document_parsing": {
        "label": "Document parsing",
        "category": "documents",
        "status": "active",
        "description": "Parse structured documents (DOCX, HTML, etc.)",
    },
    "ocr_reading": {
        "label": "OCR reading",
        "category": "documents",
        "status": "active",
        "description": "Extract text from images via optical character recognition",
    },

    # --- Text Processing ---
    "text_summarization": {
        "label": "Text summarization",
        "category": "text",
        "status": "active",
        "description": "Summarize long text into concise output",
    },
    "text_translation": {
        "label": "Text translation",
        "category": "text",
        "status": "active",
        "description": "Translate text between languages",
    },
    "language_detection": {
        "label": "Language detection",
        "category": "text",
        "status": "active",
        "description": "Detect the language of a text input",
    },

    # --- Data & Analytics ---
    "csv_analysis": {
        "label": "CSV analysis",
        "category": "data",
        "status": "active",
        "description": "Analyze and query CSV/tabular data",
    },
    "spreadsheet_parsing": {
        "label": "Spreadsheet parsing",
        "category": "data",
        "status": "active",
        "description": "Parse Excel and spreadsheet files",
    },
    "chart_generation": {
        "label": "Chart generation",
        "category": "data",
        "status": "active",
        "description": "Generate charts and graphs from data",
    },
    "data_visualization": {
        "label": "Data visualization",
        "category": "data",
        "status": "active",
        "description": "Create visual representations of datasets",
    },
    "sql_generation": {
        "label": "SQL generation",
        "category": "data",
        "status": "active",
        "description": "Generate SQL queries from natural language",
    },
    "database_connector": {
        "label": "Database connector",
        "category": "data",
        "status": "active",
        "description": "Connect to and query databases",
    },

    # --- AI / Embeddings ---
    "embedding_generation": {
        "label": "Embedding generation",
        "category": "ai",
        "status": "active",
        "description": "Generate vector embeddings for text",
    },
    "vector_memory": {
        "label": "Vector memory",
        "category": "ai",
        "status": "active",
        "description": "Store and retrieve vectors for semantic search",
    },
    "knowledge_graph": {
        "label": "Knowledge graph",
        "category": "ai",
        "status": "active",
        "description": "Build and query knowledge graphs",
    },

    # --- Code ---
    "code_analysis": {
        "label": "Code analysis",
        "category": "code",
        "status": "active",
        "description": "Analyze source code for quality and patterns",
    },
    "code_generation": {
        "label": "Code generation",
        "category": "code",
        "status": "active",
        "description": "Generate source code from specifications",
    },
    "test_generation": {
        "label": "Test generation",
        "category": "code",
        "status": "active",
        "description": "Generate test cases and test suites",
    },

    # --- Communication ---
    "email_sending": {
        "label": "Email sending",
        "category": "communication",
        "status": "active",
        "description": "Send emails programmatically",
    },
    "email_reading": {
        "label": "Email reading",
        "category": "communication",
        "status": "active",
        "description": "Read and parse email messages",
    },

    # --- Media ---
    "screenshot_capture": {
        "label": "Screenshot capture",
        "category": "media",
        "status": "active",
        "description": "Capture screenshots of screens or windows",
    },
    "image_generation": {
        "label": "Image generation",
        "category": "media",
        "status": "active",
        "description": "Generate images from text prompts",
    },
    "image_editing": {
        "label": "Image editing",
        "category": "media",
        "status": "active",
        "description": "Edit and transform images",
    },

    # --- Planned (no installable packages yet) ---
    "meeting_summary": {
        "label": "Meeting summary",
        "category": "productivity",
        "status": "planned",
        "description": "Summarize meeting transcripts and recordings",
    },
    "audio_transcription": {
        "label": "Audio transcription",
        "category": "media",
        "status": "planned",
        "description": "Transcribe audio files to text",
    },
    "calendar_management": {
        "label": "Calendar management",
        "category": "productivity",
        "status": "planned",
        "description": "Create, read, and manage calendar events",
    },
    "task_management": {
        "label": "Task management",
        "category": "productivity",
        "status": "planned",
        "description": "Create and manage tasks and to-do lists",
    },
    "video_analysis": {
        "label": "Video analysis",
        "category": "media",
        "status": "planned",
        "description": "Analyze and extract information from video content",
    },
}


def list_capabilities(*, include_planned: bool = False) -> list[dict]:
    """List capabilities with their metadata.

    By default only returns active (installable) capabilities.
    Pass ``include_planned=True`` to include planned capabilities.
    """
    result = []
    for cap_id, meta in CAPABILITY_TAXONOMY.items():
        if not include_planned and meta["status"] != "active":
            continue
        result.append({"id": cap_id, **meta})
    return result


def is_runtime_capability(cap: str) -> bool:
    """True if capability is installable/active at runtime."""
    meta = CAPABILITY_TAXONOMY.get(cap)
    return meta is not None and meta["status"] == "active"


def is_known_capability(cap: str) -> bool:
    """True if capability exists in the taxonomy (active or planned)."""
    return cap in CAPABILITY_TAXONOMY
