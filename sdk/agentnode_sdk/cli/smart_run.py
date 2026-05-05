"""Smart task execution: natural language → capability → install → run."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ParsedTask:
    capability: str
    confidence: str  # high, medium, low
    input_args: dict
    source: str  # which rule matched


# Task patterns: (regex, capability, arg_extractor)
_TASK_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    # PDF
    (re.compile(r"(extract|read|parse|get text from).*\.pdf", re.I), "pdf_extraction", "high"),
    (re.compile(r"(summarize|summary of).*\.pdf", re.I), "pdf_extraction", "high"),
    (re.compile(r"pdf", re.I), "pdf_extraction", "medium"),

    # CSV / Data
    (re.compile(r"(analyze|read|parse|describe).*\.(csv|tsv)", re.I), "csv_analysis", "high"),
    (re.compile(r"(csv|spreadsheet|data.?set)", re.I), "csv_analysis", "medium"),

    # Web search
    (re.compile(r"(search|find|look up|google)\s+(for\s+)?", re.I), "web_search", "high"),
    (re.compile(r"(what is|who is|when did|where is|how to)", re.I), "web_search", "medium"),

    # Web scraping
    (re.compile(r"(scrape|extract|fetch|get).*(from|at)\s+https?://", re.I), "webpage_extraction", "high"),
    (re.compile(r"(extract|read).*web(page|site|page)", re.I), "webpage_extraction", "medium"),
    (re.compile(r"https?://\S+", re.I), "webpage_extraction", "medium"),

    # Translation
    (re.compile(r"translat", re.I), "text_translation", "high"),
    (re.compile(r"(in|to|into)\s+(german|french|spanish|english|chinese|japanese)", re.I), "text_translation", "medium"),

    # Summarization
    (re.compile(r"summarize|summary|tldr|tl;dr", re.I), "text_summarization", "high"),
    (re.compile(r"(shorten|condense|brief)", re.I), "text_summarization", "medium"),

    # Document parsing
    (re.compile(r"\.(docx|doc|pptx)\b", re.I), "document_parsing", "high"),

    # Charts / Visualization
    (re.compile(r"(chart|graph|plot|visualize|histogram|bar chart)", re.I), "chart_generation", "high"),

    # OCR
    (re.compile(r"(ocr|read.*image|text.*from.*image|scan)", re.I), "ocr_reading", "high"),

    # Code
    (re.compile(r"(generate|write|create).*code", re.I), "code_generation", "medium"),
    (re.compile(r"(review|analyze|lint).*code", re.I), "code_analysis", "medium"),

    # Email
    (re.compile(r"(send|draft|compose).*email", re.I), "email_sending", "high"),

    # Spreadsheet
    (re.compile(r"\.(xlsx|xls)\b", re.I), "spreadsheet_parsing", "high"),
]

# File extension → input key mapping
_FILE_EXT_INPUT = {
    ".pdf": "file_path",
    ".csv": "file_path",
    ".tsv": "file_path",
    ".xlsx": "file_path",
    ".xls": "file_path",
    ".docx": "file_path",
    ".pptx": "file_path",
    ".html": "file_path",
    ".txt": "file_path",
}

_FILE_RE = re.compile(r'["\']?([^\s"\']+\.\w{2,5})["\']?')
_URL_RE = re.compile(r'(https?://\S+)')
_QUERY_STRIP_RE = re.compile(
    r"^(search\s+(for\s+)?|find\s+|look\s+up\s+|what\s+is\s+|who\s+is\s+|"
    r"how\s+to\s+|summarize\s+|translate\s+|extract\s+(from\s+)?)",
    re.I,
)


def parse_task(task: str) -> ParsedTask | None:
    """Parse a natural language task into a capability + input args."""
    task = task.strip()
    if not task:
        return None

    # Try each pattern
    for pattern, capability, confidence in _TASK_PATTERNS:
        if pattern.search(task):
            input_args = _extract_args(task, capability)
            return ParsedTask(
                capability=capability,
                confidence=confidence,
                input_args=input_args,
                source="pattern",
            )

    return None


def get_alternatives(task: str) -> list[str]:
    """Get alternative capability interpretations for a task."""
    task_lower = task.lower()
    matches: list[str] = []
    seen: set[str] = set()

    for pattern, capability, _ in _TASK_PATTERNS:
        if pattern.search(task_lower) and capability not in seen:
            seen.add(capability)
            matches.append(capability)

    return matches


def _extract_args(task: str, capability: str) -> dict:
    """Extract input arguments from the task string."""
    args: dict = {}

    # Extract file paths
    file_match = _FILE_RE.search(task)
    if file_match:
        filepath = file_match.group(1)
        for ext, key in _FILE_EXT_INPUT.items():
            if filepath.lower().endswith(ext):
                args[key] = filepath
                break

    # Extract URLs
    url_match = _URL_RE.search(task)
    if url_match:
        args["url"] = url_match.group(1)

    # For search-type capabilities, extract the query
    if capability == "web_search":
        query = _QUERY_STRIP_RE.sub("", task).strip()
        if query:
            args["query"] = query

    # For translation, try to extract target language
    if capability == "text_translation":
        lang_match = re.search(
            r"(?:to|into)\s+(german|french|spanish|english|chinese|japanese|italian|portuguese|dutch|russian|korean|arabic)",
            task, re.I,
        )
        if lang_match:
            args["target_language"] = lang_match.group(1).lower()
        text_match = re.search(r"[\"'](.+?)[\"']", task)
        if text_match:
            args["text"] = text_match.group(1)

    # For summarization, extract text or file
    if capability == "text_summarization" and "file_path" not in args:
        text_match = re.search(r"[\"'](.+?)[\"']", task)
        if text_match:
            args["text"] = text_match.group(1)

    # Fallback: store the whole task as 'query' or 'text'
    if not args:
        if capability in ("web_search",):
            args["query"] = task
        else:
            args["text"] = task

    return args
