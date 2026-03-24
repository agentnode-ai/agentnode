"""Capability gap detection for AgentNode SDK.

Three-layer detection with confidence levels:
- Layer 1 (high):   ImportError → module name mapping
- Layer 2 (medium): Error message keyword matching
- Layer 3 (low):    Context-based (file extension, URL patterns)
"""
from __future__ import annotations

import re

from agentnode_sdk.models import DetectedGap

# ---------------------------------------------------------------------------
# Layer 1: ImportError → module → capability (confidence=high)
# ---------------------------------------------------------------------------

_MODULE_MAP: dict[str, str] = {
    "pdfplumber": "pdf_extraction",
    "pypdf": "pdf_extraction",
    "fitz": "pdf_extraction",
    "pymupdf": "pdf_extraction",
    "bs4": "webpage_extraction",
    "beautifulsoup4": "webpage_extraction",
    "scrapy": "webpage_extraction",
    "requests_html": "webpage_extraction",
    "selenium": "browser_navigation",
    "playwright": "browser_navigation",
    "pandas": "csv_analysis",
    "openpyxl": "spreadsheet_parsing",
    "xlrd": "spreadsheet_parsing",
    "xlsxwriter": "spreadsheet_parsing",
    "matplotlib": "chart_generation",
    "plotly": "chart_generation",
    "googlesearch": "web_search",
    "docx": "document_parsing",
    "python_docx": "document_parsing",
    "sqlalchemy": "sql_generation",
    "sentence_transformers": "embedding_generation",
    "faiss": "vector_memory",
    "chromadb": "vector_memory",
    "langdetect": "translation",
}

_IMPORT_RE = re.compile(r"No module named ['\"]?(\w[\w.]*)")


def _extract_module_name(error: BaseException) -> str | None:
    """Extract top-level module name from an ImportError message."""
    msg = str(error)
    m = _IMPORT_RE.search(msg)
    if not m:
        return None
    # Take top-level: "bs4.element" → "bs4"
    return m.group(1).split(".")[0]


def _check_layer1(error: BaseException) -> DetectedGap | None:
    """Layer 1: ImportError → known module → capability."""
    if not isinstance(error, (ImportError, ModuleNotFoundError)):
        return None
    module = _extract_module_name(error)
    if module is None:
        return None
    capability = _MODULE_MAP.get(module)
    if capability is None:
        return None
    return DetectedGap(
        capability=capability,
        confidence="high",
        source="import_error",
    )


# ---------------------------------------------------------------------------
# Layer 2: Error message keywords (confidence=medium)
# ---------------------------------------------------------------------------

_ERROR_PATTERNS: list[tuple[list[str], str]] = [
    # PDF — module names and format signals
    (["pdfplumber", "pypdf", "fitz", "pymupdf", "pdf reader", "pdf parser"], "pdf_extraction"),
    # CSV / Data
    (["pandas", "csv reader", "csv parser"], "csv_analysis"),
    # Spreadsheet
    (["openpyxl", "xlrd", "xlsx reader", "xlsx parser"], "spreadsheet_parsing"),
    # Web scraping
    (["beautifulsoup", "bs4", "html parser", "scraper"], "webpage_extraction"),
    # Browser automation
    (["selenium", "playwright", "webdriver", "chromedriver"], "browser_navigation"),
    # Embeddings / Vectors
    (["sentence_transformers", "embedding model"], "embedding_generation"),
    (["faiss", "chromadb", "pinecone", "vector store"], "vector_memory"),
    # SQL
    (["sqlalchemy", "sql parser"], "sql_generation"),
    # Charts
    (["matplotlib", "plotly", "chart library"], "chart_generation"),
    # Documents
    (["python-docx", "docx parser"], "document_parsing"),
]


def _check_layer2(error: BaseException) -> DetectedGap | None:
    """Layer 2: Error message keyword scan."""
    msg = str(error).lower()
    if not msg:
        return None
    for keywords, capability in _ERROR_PATTERNS:
        for kw in keywords:
            if kw.lower() in msg:
                return DetectedGap(
                    capability=capability,
                    confidence="medium",
                    source="error_message",
                )
    return None


# ---------------------------------------------------------------------------
# Layer 3: Context-based detection (confidence=low)
# ---------------------------------------------------------------------------

_EXTENSION_MAP: dict[str, str] = {
    ".pdf": "pdf_extraction",
    ".csv": "csv_analysis",
    ".xlsx": "spreadsheet_parsing",
    ".xls": "spreadsheet_parsing",
    ".docx": "document_parsing",
    ".pptx": "document_parsing",
    ".html": "webpage_extraction",
    ".htm": "webpage_extraction",
}

_URL_PATTERNS: list[tuple[str, str]] = [
    ("http://", "webpage_extraction"),
    ("https://", "webpage_extraction"),
]


def _check_layer3(context: dict[str, str] | None) -> DetectedGap | None:
    """Layer 3: Context clues (file extensions, URLs)."""
    if not context:
        return None

    # Check file paths
    file_path = context.get("file", "") or context.get("path", "")
    if file_path:
        file_lower = file_path.lower()
        for ext, capability in _EXTENSION_MAP.items():
            if file_lower.endswith(ext):
                return DetectedGap(
                    capability=capability,
                    confidence="low",
                    source="context",
                )

    # Check URLs
    url = context.get("url", "")
    if url:
        url_lower = url.lower()
        for pattern, capability in _URL_PATTERNS:
            if url_lower.startswith(pattern):
                return DetectedGap(
                    capability=capability,
                    confidence="low",
                    source="context",
                )

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_gap(
    error: BaseException,
    context: dict[str, str] | None = None,
) -> DetectedGap | None:
    """Detect a capability gap from an error and optional context.

    Checks three layers in priority order:
    1. ImportError → module name (high confidence)
    2. Error message keywords (medium confidence)
    3. Context clues like file extensions (low confidence)

    Returns ``None`` if no gap is detected.
    """
    # Layer 1: ImportError (highest priority)
    result = _check_layer1(error)
    if result is not None:
        return result

    # Layer 2: Error message keywords
    result = _check_layer2(error)
    if result is not None:
        return result

    # Layer 3: Context-based
    return _check_layer3(context)
