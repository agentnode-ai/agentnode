"""Shared AST utilities for import converters.

All code analysis is done via ast.parse — no eval/exec ever.
"""
from __future__ import annotations

import ast
import builtins
import re
import sys
import textwrap
from dataclasses import dataclass, field
from enum import Enum

from app.import_.schemas import (
    ConversionConfidence,
    DetectedTool,
    ToolParam,
)

# ── Framework import sets ────────────────────────────────────────────
LANGCHAIN_MODULES = {"langchain", "langchain_core", "langchain_community"}
CREWAI_MODULES = {"crewai", "crewai_tools"}
ALL_FRAMEWORK_MODULES = LANGCHAIN_MODULES | CREWAI_MODULES

# ── Known third-party packages (conservative whitelist) ──────────────
KNOWN_THIRD_PARTY = {
    "requests", "httpx", "aiohttp", "urllib3",
    "pandas", "numpy", "scipy", "sklearn", "scikit_learn",
    "boto3", "botocore", "google", "azure",
    "openai", "anthropic", "tiktoken", "transformers",
    "langchain_openai", "langchain_community", "langchain_core",
    "langchain_text_splitters", "langchain_anthropic",
    "pydantic", "fastapi", "flask", "django",
    "beautifulsoup4", "bs4", "lxml", "html5lib",
    "selenium", "playwright",
    "pillow", "PIL",
    "PyPDF2", "pypdf", "pdfplumber", "fitz", "pymupdf",
    "sqlalchemy", "psycopg2", "pymongo", "redis",
    "celery", "dramatiq",
    "yaml", "pyyaml", "toml", "tomli",
    "jinja2", "mako",
    "paramiko", "fabric",
    "docker", "kubernetes",
    "stripe", "twilio", "sendgrid",
    "slack_sdk", "discord",
    "dotenv", "python_dotenv",
    "loguru", "structlog",
    "rich", "click", "typer",
    "pytest", "unittest",
    "dateutil", "arrow", "pendulum",
    "tqdm", "tenacity", "retry",
    "jsonschema", "marshmallow",
    "cryptography", "jwt", "pyjwt",
    "cv2", "opencv",
    "spacy", "nltk", "gensim",
    "matplotlib", "seaborn", "plotly",
    "openpyxl", "xlrd", "xlsxwriter",
    "chardet", "charset_normalizer",
    "feedparser", "newspaper",
}

# ── Capability map (reused from builder/service.py) ──────────────────
_CAPABILITY_MAP: list[tuple[list[str], str]] = [
    (["pdf", "portable document"], "pdf_extraction"),
    (["document", "docx", "doc parsing"], "document_parsing"),
    (["summarize", "summary", "summarise", "tldr", "digest"], "document_summary"),
    (["citation", "reference", "bibliography"], "citation_extraction"),
    (["web search", "google", "bing", "search the web", "internet search",
      "weather", "forecast", "stock", "ticker", "quote", "finance", "market",
      "geocod", "ip info", "ip address", "geolocation"], "web_search"),
    (["webpage", "website", "html", "scrape", "crawl", "fetch url", "extract from url"], "webpage_extraction"),
    (["browser", "navigate", "click", "playwright", "selenium"], "browser_navigation"),
    (["link", "href", "discover url"], "link_discovery"),
    (["csv", "comma separated"], "csv_analysis"),
    (["spreadsheet", "excel", "xlsx", "xls"], "spreadsheet_parsing"),
    (["clean data", "normalize data", "data cleaning"], "data_cleaning"),
    (["statistic", "average", "median", "standard deviation", "analytics"], "statistics_analysis"),
    (["chart", "graph", "plot", "visuali"], "chart_generation"),
    (["json", "json processing", "parse json", "transform json"], "json_processing"),
    (["sql", "sql query", "db query", "run query", "execute query"], "sql_generation"),
    (["log", "log file", "log analysis"], "log_analysis"),
    (["vector", "vector store", "vector memory"], "vector_memory"),
    (["knowledge", "knowledge base", "rag", "retrieval"], "knowledge_retrieval"),
    (["semantic search", "meaning search", "similarity search"], "semantic_search"),
    (["embedding", "embed", "vectorize"], "embedding_generation"),
    (["index document", "document index", "indexing"], "document_indexing"),
    (["conversation memory", "chat history", "recall conversation"], "conversation_memory"),
    (["email draft", "write email", "compose email", "email"], "email_drafting"),
    (["email summary", "summarize email"], "email_summary"),
    (["meeting", "meeting notes", "meeting summary"], "meeting_summary"),
    (["schedule", "calendar", "appointment", "booking"], "scheduling"),
    (["task", "todo", "to-do", "task manage"], "task_management"),
    (["translat", "language", "i18n", "locali"], "translation"),
    (["tone", "rewrite", "rephrase", "style"], "tone_adjustment"),
    (["code", "source code", "lint", "refactor", "analyse code", "analyze code",
      "calculate", "expression", "arithmetic", "math"], "code_analysis"),
]

_NETWORK_KEYWORDS = [
    "webpage", "website", "url", "http", "fetch", "scrape", "crawl",
    "api", "request", "download", "search", "web",
]
_FILESYSTEM_KEYWORDS = [
    "pdf", "file", "csv", "document", "excel", "spreadsheet",
    "read file", "write file", "directory", "folder", "image", "log file",
]

# ── Python builtins set ──────────────────────────────────────────────
_BUILTINS = set(dir(builtins))


# ── Data classes ─────────────────────────────────────────────────────

@dataclass
class ImportClassification:
    framework: list[str] = field(default_factory=list)
    stdlib: list[str] = field(default_factory=list)
    third_party: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)

    @property
    def third_party_names(self) -> list[str]:
        return self.third_party

    @property
    def unknown_names(self) -> list[str]:
        return self.unknown


@dataclass
class ExtractedTool:
    name: str  # snake_case
    original_name: str
    description: str
    params: list[ToolParam]
    body_source: str
    is_async: bool = False
    has_return_dict: bool = True
    return_annotation: str | None = None
    return_kind: str | None = None  # ReturnKind.value for confidence scoring
    has_self_refs: bool = False


@dataclass
class ExtractResult:
    tools: list[ExtractedTool] = field(default_factory=list)
    helpers: list[str] = field(default_factory=list)  # source strings
    helper_names: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)


# ── Return Type Classification ────────────────────────────────────────

class ReturnKind(str, Enum):
    DICT = "dict_like"
    STR = "str_like"
    LIST = "list_like"
    TUPLE = "tuple_like"
    NONE = "none_like"
    UNION = "union_like"
    UNKNOWN = "unknown"


def classify_return_annotation(annotation: str | None) -> ReturnKind:
    """Classify a return type annotation string into a ReturnKind.

    Normalizes generics so that list, List, list[str], List[str] all → LIST.
    """
    if not annotation:
        return ReturnKind.UNKNOWN

    value = annotation.replace("typing.", "").replace(" ", "").lower()

    # dict-like
    if value == "dict" or value.startswith("dict[") or value.startswith("mapping["):
        return ReturnKind.DICT

    # str-like
    if value == "str":
        return ReturnKind.STR

    # list-like (list, List, list[str], List[str], Sequence[...])
    if value in ("list", "sequence") or value.startswith("list[") or value.startswith("sequence["):
        return ReturnKind.LIST

    # tuple-like
    if value == "tuple" or value.startswith("tuple["):
        return ReturnKind.TUPLE

    # none-like
    if value in ("none", "nonetype"):
        return ReturnKind.NONE

    # union / optional — check before UNKNOWN fallback
    if value.startswith("union[") or "|" in value or value.startswith("optional["):
        # Optional[dict] / Union[dict, None] → dict-like
        if "dict" in value and not any(t in value for t in ("str", "list", "tuple", "int", "float")):
            return ReturnKind.DICT
        return ReturnKind.UNION

    return ReturnKind.UNKNOWN


@dataclass
class ReturnPolicy:
    kind: ReturnKind
    should_wrap: bool
    warning_template: str | None  # Appended after "`func_name` "
    max_confidence: str | None     # "medium", "low", or None (no cap)
    draft_ready_allowed: bool


_RETURN_POLICIES: dict[ReturnKind, ReturnPolicy] = {
    ReturnKind.DICT: ReturnPolicy(ReturnKind.DICT, False, None, None, True),
    ReturnKind.STR: ReturnPolicy(
        ReturnKind.STR, True,
        "returns `str`. Wrapped in `{'result': ...}`. Please review.",
        "medium", True,
    ),
    ReturnKind.LIST: ReturnPolicy(
        ReturnKind.LIST, True,
        "returns `list`. Wrapped in `{'result': ...}`. Please review.",
        "medium", True,
    ),
    ReturnKind.TUPLE: ReturnPolicy(
        ReturnKind.TUPLE, False,
        "returns `tuple`. ANP expects `dict`. Please wrap manually.",
        "low", False,
    ),
    ReturnKind.NONE: ReturnPolicy(
        ReturnKind.NONE, False,
        "returns `None`. No meaningful return for ANP. Please review.",
        "medium", True,
    ),
    ReturnKind.UNION: ReturnPolicy(
        ReturnKind.UNION, False,
        "has mixed/ambiguous return types. ANP expects `dict`. Please review.",
        "low", False,
    ),
    ReturnKind.UNKNOWN: ReturnPolicy(
        ReturnKind.UNKNOWN, False, None, "medium", True,
    ),
}


def get_return_policy(kind: ReturnKind) -> ReturnPolicy:
    """Get the return type handling policy for a given ReturnKind."""
    return _RETURN_POLICIES[kind]


def apply_return_policy(
    func_name: str,
    annotation: str | None,
    body: str,
    result: ExtractResult,
) -> tuple[str, bool, ReturnKind]:
    """Classify return type, apply wrapping and warnings.

    Returns (possibly_wrapped_body, has_return_dict, return_kind).
    """
    kind = classify_return_annotation(annotation)
    policy = get_return_policy(kind)

    # Wrapping
    if policy.should_wrap:
        body = wrap_return_value(body)
        result.changes.append(f"Return type of `{func_name}` wrapped from `{annotation}` to `dict`")

    # Warning from policy template
    if policy.warning_template:
        result.warnings.append(f"`{func_name}` {policy.warning_template}")
    elif kind == ReturnKind.UNKNOWN:
        # Specific sub-case warnings for UNKNOWN
        if annotation and annotation in ("int", "float", "bool"):
            result.warnings.append(
                f"`{func_name}` returns `{annotation}`. ANP expects `dict`. Please wrap manually."
            )
        elif annotation and annotation == "Any":
            result.warnings.append(
                f"Return type of `{func_name}` is `Any`. "
                "Not clearly ANP-compatible. Please review and wrap in `dict` if needed."
            )
        elif not annotation:
            result.warnings.append(
                f"Return type of `{func_name}` is not annotated. "
                "Not clearly ANP-compatible. Please review and wrap in `dict` if needed."
            )
        else:
            result.warnings.append(
                f"Return type of `{func_name}` is `{annotation}`. "
                "Not clearly ANP-compatible. Please review and wrap in `dict` if needed."
            )

    has_return_dict = kind == ReturnKind.DICT or policy.should_wrap
    return body, has_return_dict, kind


# ── AST Parsing ──────────────────────────────────────────────────────

def parse_source(code: str) -> ast.Module:
    """Parse Python source with informative error on failure."""
    try:
        return ast.parse(code)
    except SyntaxError as e:
        raise ValueError(
            f"Syntax error at line {e.lineno}: {e.msg}"
        ) from e


# ── Import Classification ────────────────────────────────────────────

def classify_imports(tree: ast.Module) -> ImportClassification:
    """Classify all imports in the AST into framework/stdlib/third_party/unknown."""
    result = ImportClassification()
    stdlib_names = getattr(sys, "stdlib_module_names", set())

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                _classify_module(top, result, stdlib_names)
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                # Relative import
                result.unknown.append(f".{node.module or ''}")
                continue
            if node.module:
                top = node.module.split(".")[0]
                _classify_module(top, result, stdlib_names)

    # Deduplicate
    result.framework = list(dict.fromkeys(result.framework))
    result.stdlib = list(dict.fromkeys(result.stdlib))
    result.third_party = list(dict.fromkeys(result.third_party))
    result.unknown = list(dict.fromkeys(result.unknown))
    return result


def _classify_module(top: str, result: ImportClassification, stdlib_names: set[str]) -> None:
    if top in ALL_FRAMEWORK_MODULES:
        result.framework.append(top)
    elif top in stdlib_names:
        result.stdlib.append(top)
    elif top.lower().replace("-", "_") in {k.lower().replace("-", "_") for k in KNOWN_THIRD_PARTY}:
        result.third_party.append(top)
    else:
        result.unknown.append(top)


# ── Parameter Extraction ─────────────────────────────────────────────

def extract_params(func: ast.FunctionDef) -> tuple[list[ToolParam], list[str]]:
    """Extract parameters from a function definition.

    Returns (params, warnings).
    """
    params: list[ToolParam] = []
    warnings: list[str] = []
    args = func.args

    # Build defaults mapping (right-aligned)
    num_defaults = len(args.defaults)
    num_args = len(args.args)

    for i, arg in enumerate(args.args):
        if arg.arg == "self":
            continue

        # Type hint
        annotation_missing = False
        if arg.annotation:
            type_hint = _annotation_to_str(arg.annotation)
        else:
            type_hint = "Any"
            annotation_missing = True
            warnings.append(f"Parameter `{arg.arg}` has no type hint. `Any` was assumed.")

        # Default value
        default_idx = i - (num_args - num_defaults)
        has_default = default_idx >= 0
        default_repr = None
        if has_default:
            default_repr = _node_to_repr(args.defaults[default_idx])

        params.append(ToolParam(
            name=arg.arg,
            type_hint=type_hint,
            required=not has_default,
            default_repr=default_repr,
            annotation_missing=annotation_missing,
            description="",
        ))

    return params, warnings


def _annotation_to_str(node: ast.expr) -> str:
    """Convert an annotation AST node to a string."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Constant):
        return str(node.value)
    if isinstance(node, ast.Attribute):
        return ast.unparse(node)
    if isinstance(node, ast.Subscript):
        return ast.unparse(node)
    return ast.unparse(node)


def _node_to_repr(node: ast.expr) -> str:
    """Convert a default value AST node to its string representation."""
    if isinstance(node, ast.Constant):
        return repr(node.value)
    return ast.unparse(node)


# ── Function Source Extraction ───────────────────────────────────────

def extract_function_body(func: ast.FunctionDef, source_lines: list[str]) -> str:
    """Extract the body source of a function (excluding the def line + docstring)."""
    body = func.body
    if not body:
        return "    pass\n"

    # Skip docstring if present
    start_idx = 0
    if (isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        start_idx = 1

    if start_idx >= len(body):
        return "    pass\n"

    first = body[start_idx]
    last = body[-1]
    start_line = first.lineno - 1
    end_line = last.end_lineno  # type: ignore[attr-defined]

    lines = source_lines[start_line:end_line]
    if not lines:
        return "    pass\n"

    return textwrap.dedent("\n".join(lines)) + "\n"


def extract_full_node_source(node: ast.AST, source_lines: list[str]) -> str:
    """Extract the full source of any AST node."""
    start = node.lineno - 1  # type: ignore[attr-defined]
    end = node.end_lineno  # type: ignore[attr-defined]
    lines = source_lines[start:end]
    return textwrap.dedent("\n".join(lines))


# ── Unresolved Symbol Detection ─────────────────────────────────────

def detect_unresolved_symbols(body_source: str, available_names: set[str]) -> list[str]:
    """Find names used in body_source that aren't in available_names."""
    try:
        tree = ast.parse(body_source)
    except SyntaxError:
        return []

    used_names: set[str] = set()
    defined_names: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load):
                used_names.add(node.id)
            elif isinstance(node.ctx, (ast.Store, ast.Del)):
                defined_names.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defined_names.add(node.name)
            for arg in node.args.args:
                defined_names.add(arg.arg)
        elif isinstance(node, ast.Lambda):
            for arg in node.args.args:
                defined_names.add(arg.arg)
        elif isinstance(node, ast.ExceptHandler):
            if node.name:
                defined_names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            defined_names.add(node.name)
        # For/With/Comprehension targets
        elif isinstance(node, ast.For):
            _collect_targets(node.target, defined_names)
        elif isinstance(node, ast.comprehension):
            _collect_targets(node.target, defined_names)

    unresolved = used_names - defined_names - available_names - _BUILTINS
    return sorted(unresolved)


def _collect_targets(node: ast.AST, names: set[str]) -> None:
    """Collect assignment target names from an AST node."""
    if isinstance(node, ast.Name):
        names.add(node.id)
    elif isinstance(node, ast.Tuple) or isinstance(node, ast.List):
        for elt in node.elts:
            _collect_targets(elt, names)
    elif isinstance(node, ast.Starred):
        _collect_targets(node.value, names)


# ── Helper Collection ────────────────────────────────────────────────

def collect_helpers(
    tree: ast.Module,
    source_lines: list[str],
    tool_func_names: set[str],
    framework_class_names: set[str],
) -> tuple[list[str], set[str]]:
    """Collect top-level helper functions/constants that are not tool functions.

    Returns (helper_sources, helper_names).
    """
    helpers: list[str] = []
    names: set[str] = set()

    def _collect_names_from_body(body: list[ast.stmt]) -> None:
        """Collect assignment names from nested blocks (try/except, if/else)."""
        for child in body:
            if isinstance(child, ast.Assign):
                for target in child.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
            elif isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                names.add(child.target.id)
            elif isinstance(child, ast.Try):
                _collect_names_from_body(child.body)
                for handler in child.handlers:
                    _collect_names_from_body(handler.body)
            elif isinstance(child, ast.If):
                _collect_names_from_body(child.body)
                _collect_names_from_body(child.orelse)

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name not in tool_func_names:
                helpers.append(extract_full_node_source(node, source_lines))
                names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            if node.name not in framework_class_names:
                helpers.append(extract_full_node_source(node, source_lines))
                names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
            helpers.append(extract_full_node_source(node, source_lines))
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                names.add(node.target.id)
            helpers.append(extract_full_node_source(node, source_lines))
        elif isinstance(node, ast.Try):
            # Collect names from try/except but emit the whole block as helper source
            _collect_names_from_body(node.body)
            for handler in node.handlers:
                _collect_names_from_body(handler.body)
            helpers.append(extract_full_node_source(node, source_lines))
        elif isinstance(node, ast.If):
            _collect_names_from_body(node.body)
            _collect_names_from_body(node.orelse)
            helpers.append(extract_full_node_source(node, source_lines))

    return helpers, names


# ── Import Statement Collection ──────────────────────────────────────

def collect_business_imports(tree: ast.Module, source_lines: list[str]) -> list[str]:
    """Collect non-framework import statements as source strings."""
    imports: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top not in ALL_FRAMEWORK_MODULES:
                    imports.append(extract_full_node_source(node, source_lines))
                    break
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                continue  # skip relative imports
            if node.module:
                top = node.module.split(".")[0]
                if top not in ALL_FRAMEWORK_MODULES:
                    imports.append(extract_full_node_source(node, source_lines))
    return list(dict.fromkeys(imports))


# ── Available Names ──────────────────────────────────────────────────

def build_available_names(
    tree: ast.Module,
    imports: ImportClassification,
    tool_params: list[str],
    helper_names: set[str],
) -> set[str]:
    """Build the set of names that are legitimately available in scope."""
    names: set[str] = set()

    # Python builtins
    names |= _BUILTINS

    # Imported names
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.names:
                for alias in node.names:
                    names.add(alias.asname or alias.name)

    # Function params
    names.update(tool_params)

    # Helpers
    names |= helper_names

    # Top-level definitions (including inside top-level try/except blocks)
    def _scan_body(body: list[ast.stmt]) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.add(node.name)
            elif isinstance(node, ast.ClassDef):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.add(node.target.id)
            elif isinstance(node, ast.Try):
                _scan_body(node.body)
                for handler in node.handlers:
                    _scan_body(handler.body)
                _scan_body(node.orelse)
                _scan_body(node.finalbody)
            elif isinstance(node, ast.If):
                _scan_body(node.body)
                _scan_body(node.orelse)

    _scan_body(tree.body)
    return names


# ── Code Generation ──────────────────────────────────────────────────

def generate_tool_py(
    tools: list[ExtractedTool],
    helpers: list[str],
    business_imports: list[str],
) -> str:
    """Generate the tool.py source file."""
    parts: list[str] = []

    # Business imports
    if business_imports:
        for imp in business_imports:
            parts.append(imp.rstrip())
        parts.append("")

    # Helpers
    if helpers:
        for h in helpers:
            parts.append(h.rstrip())
            parts.append("")

    # Tool functions
    for tool in tools:
        sig_params = _build_param_sig(tool.params)
        parts.append(f"def {tool.name}({sig_params}) -> dict:")
        if tool.description:
            parts.append(f'    """{tool.description}"""')

        body = tool.body_source
        # Ensure proper indentation
        body_lines = body.splitlines()
        indented_lines: list[str] = []
        for line in body_lines:
            if line.strip():
                indented_lines.append(f"    {line}")
            else:
                indented_lines.append("")
        parts.append("\n".join(indented_lines))
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


def _build_param_sig(params: list[ToolParam]) -> str:
    """Build parameter signature string."""
    parts: list[str] = []
    for p in params:
        hint = p.type_hint if p.type_hint != "Any" else ""
        if hint:
            if p.default_repr is not None:
                parts.append(f"{p.name}: {hint} = {p.default_repr}")
            else:
                parts.append(f"{p.name}: {hint}")
        else:
            if p.default_repr is not None:
                parts.append(f"{p.name}={p.default_repr}")
            else:
                parts.append(p.name)
    return ", ".join(parts)


# ── Capability Detection ─────────────────────────────────────────────

def detect_capability_ids(tools: list[ExtractedTool]) -> list[str]:
    """Detect capability IDs from tool names and descriptions."""
    text = " ".join(f"{t.name} {t.description}" for t in tools).lower()
    found: list[str] = []
    for keywords, cap_id in _CAPABILITY_MAP:
        for kw in keywords:
            if kw in text:
                if cap_id not in found:
                    found.append(cap_id)
                break
    return found or ["code_analysis"]


# ── Permission Inference ─────────────────────────────────────────────

def infer_permissions(tools: list[ExtractedTool]) -> dict:
    """Infer permissions from tool names, descriptions and body code."""
    text = " ".join(
        f"{t.name} {t.description} {t.body_source}" for t in tools
    ).lower()

    network = "none"
    filesystem = "none"
    for kw in _NETWORK_KEYWORDS:
        if kw in text:
            network = "unrestricted"
            break
    for kw in _FILESYSTEM_KEYWORDS:
        if kw in text:
            filesystem = "workspace_read"
            break

    return {
        "network": network,
        "filesystem": filesystem,
        "code_execution": "none",
        "data_access": "input_only",
        "user_approval": "never",
    }


# ── Manifest Version ─────────────────────────────────────────────────

def choose_manifest_version(tools: list[ExtractedTool]) -> str:
    """Choose manifest version based on tool count."""
    return "0.1" if len(tools) == 1 else "0.2"


# ── Manifest Generation ─────────────────────────────────────────────

def generate_manifest_dict(
    package_id: str,
    module_name: str,
    tools: list[ExtractedTool],
    imports: ImportClassification,
) -> dict:
    """Generate manifest_json dict (source of truth)."""
    manifest_version = choose_manifest_version(tools)
    cap_ids = detect_capability_ids(tools)
    permissions = infer_permissions(tools)

    package_name = " ".join(
        w.capitalize() for w in package_id.replace("-", " ").split()
    )

    manifest_tools = []
    for tool in tools:
        # Build input schema from params
        properties = {}
        required = []
        for p in tool.params:
            prop: dict = {"type": _type_hint_to_json_type(p.type_hint)}
            if p.description:
                prop["description"] = p.description
            properties[p.name] = prop
            if p.required:
                required.append(p.name)

        input_schema = {"type": "object", "properties": properties}
        if required:
            input_schema["required"] = required

        tool_entry: dict = {
            "name": tool.name,
            "capability_id": cap_ids[0] if cap_ids else "code_analysis",
            "description": tool.description[:200] if tool.description else "",
        }

        if manifest_version == "0.2":
            tool_entry["entrypoint"] = f"{module_name}.tool:{tool.name}"
        tool_entry["input_schema"] = input_schema
        tool_entry["output_schema"] = {
            "type": "object",
            "properties": {"result": {"type": "string"}},
        }

        manifest_tools.append(tool_entry)

    entrypoint = f"{module_name}.tool"

    manifest: dict = {
        "manifest_version": manifest_version,
        "package_id": package_id,
        "package_type": "toolpack",
        "name": package_name,
        "summary": tools[0].description[:120] if tools and tools[0].description else f"Converted {package_name}",
        "description": tools[0].description[:500] if tools and tools[0].description else "",
        "version": "0.1.0",
        "runtime": "python",
        "install_mode": "package",
        "hosting_type": "agentnode_hosted",
        "entrypoint": entrypoint,
        "capabilities": {
            "tools": manifest_tools,
            "resources": [],
            "prompts": [],
        },
        "compatibility": {
            "frameworks": ["generic"],
            "python": ">=3.10",
            "dependencies": imports.third_party,
        },
        "permissions": {
            "network": {"level": permissions["network"]},
            "filesystem": {"level": permissions["filesystem"]},
            "code_execution": {"level": permissions["code_execution"]},
            "data_access": {"level": permissions["data_access"]},
            "user_approval": {"required": permissions["user_approval"]},
        },
        "tags": cap_ids[:3],
        "categories": [
            cap_ids[0].rsplit("_", 1)[0] if cap_ids and "_" in cap_ids[0] else "general"
        ],
    }

    return manifest


def _type_hint_to_json_type(hint: str) -> str:
    """Map Python type hint to JSON Schema type."""
    mapping = {
        "str": "string",
        "int": "integer",
        "float": "number",
        "bool": "boolean",
        "list": "array",
        "dict": "object",
    }
    return mapping.get(hint, "string")


# ── YAML Rendering ───────────────────────────────────────────────────

def yaml_dump(manifest: dict) -> str:
    """Render manifest dict as YAML string."""
    try:
        import yaml
        return yaml.dump(manifest, default_flow_style=False, sort_keys=False, allow_unicode=True)
    except ImportError:
        # Minimal fallback without PyYAML
        return _minimal_yaml_dump(manifest)


def _minimal_yaml_dump(obj: object, indent: int = 0) -> str:
    """Minimal YAML serializer for when PyYAML isn't available."""
    prefix = "  " * indent
    lines: list[str] = []

    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(value, (dict, list)) and value:
                lines.append(f"{prefix}{key}:")
                lines.append(_minimal_yaml_dump(value, indent + 1))
            elif isinstance(value, list) and not value:
                lines.append(f"{prefix}{key}: []")
            else:
                lines.append(f"{prefix}{key}: {_yaml_scalar(value)}")
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict):
                first = True
                for key, value in item.items():
                    if first:
                        lines.append(f"{prefix}- {key}: {_yaml_scalar(value)}")
                        first = False
                    elif isinstance(value, (dict, list)) and value:
                        lines.append(f"{prefix}  {key}:")
                        lines.append(_minimal_yaml_dump(value, indent + 2))
                    else:
                        lines.append(f"{prefix}  {key}: {_yaml_scalar(value)}")
            else:
                lines.append(f"{prefix}- {_yaml_scalar(item)}")

    return "\n".join(lines)


def _yaml_scalar(value: object) -> str:
    if isinstance(value, str):
        if any(c in value for c in ":{}\n\"'"):
            return f'"{value}"'
        return f'"{value}"'
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return str(value)


# ── Package File Generation ──────────────────────────────────────────

def _escape_toml(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def generate_package_files(
    package_id: str,
    module_name: str,
    tools: list[ExtractedTool],
    third_party_deps: list[str],
    tool_py: str,
    manifest_yaml: str,
) -> list:
    """Generate the 5 ANP package files."""
    from app.builder.schemas import CodeFile

    package_name = " ".join(
        w.capitalize() for w in package_id.replace("-", " ").split()
    )
    description = tools[0].description[:120] if tools and tools[0].description else f"Converted {package_name}"

    # 1. tool.py
    tool_file = CodeFile(
        path=f"src/{module_name}/tool.py",
        content=tool_py,
    )

    # 2. __init__.py
    init_file = CodeFile(
        path=f"src/{module_name}/__init__.py",
        content=f'"""AgentNode package: {package_name}"""\n',
    )

    # 3. pyproject.toml
    deps_str = ", ".join(f'"{d}"' for d in third_party_deps) if third_party_deps else ""
    pyproject = CodeFile(
        path="pyproject.toml",
        content=(
            "[build-system]\n"
            'requires = ["setuptools>=68.0"]\n'
            'build-backend = "setuptools.build_meta"\n'
            "\n"
            "[project]\n"
            f'name = "{package_id}"\n'
            'version = "0.1.0"\n'
            f'description = "{_escape_toml(description)}"\n'
            'requires-python = ">=3.10"\n'
            f"dependencies = [{deps_str}]\n"
            "\n"
            "[tool.setuptools.packages.find]\n"
            'where = ["src"]\n'
        ),
    )

    # 4. tests/test_tool.py
    test_imports = ", ".join(t.name for t in tools)
    test_cases = []
    for t in tools:
        test_cases.append(
            f"def test_{t.name}_exists():\n"
            f"    assert callable({t.name})\n"
        )
    test_file = CodeFile(
        path="tests/test_tool.py",
        content=(
            f"from {module_name}.tool import {test_imports}\n\n\n"
            + "\n\n".join(test_cases)
        ),
    )

    # 5. agentnode.yaml
    manifest_file = CodeFile(
        path="agentnode.yaml",
        content=manifest_yaml,
    )

    return [tool_file, init_file, pyproject, test_file, manifest_file]


# ── Confidence Scoring ───────────────────────────────────────────────

def compute_confidence(
    result: ExtractResult,
    imports: ImportClassification,
    tool_body_names: set[str] | None = None,
    unknown_names_active: set[str] | None = None,
) -> tuple[ConversionConfidence, bool, bool]:
    """Compute confidence, draft_ready, and requires_manual_review.

    tool_body_names: set of Name nodes used inside tool bodies (for unknown import check).
    unknown_names_active: set of unknown module names that are actively used in body
                          (pre-resolved by service including imported names).
    """
    reasons: list[str] = []
    has_hard_block = False

    # Check for async
    for tool in result.tools:
        if tool.is_async:
            reasons.append(f"async function `{tool.name}` detected")
            has_hard_block = True

    # Check for self references
    for tool in result.tools:
        if tool.has_self_refs:
            reasons.append(f"`self` references in `{tool.name}` body")
            has_hard_block = True

    # Check relative imports
    for imp in imports.unknown:
        if imp.startswith("."):
            reasons.append(f"Relative import `{imp}` detected")
            has_hard_block = True

    # Count unresolved symbols across all tools
    unresolved_warnings = [w for w in result.warnings if "is called but not defined" in w]
    unresolved_count = len(unresolved_warnings)

    # Check unknown imports used in body (pre-resolved by service)
    if unknown_names_active:
        for unk in unknown_names_active:
            reasons.append(f"Unknown import `{unk}` used in tool body")
            has_hard_block = True

    # No tools found
    if not result.tools:
        reasons.append("No tool pattern detected")
        has_hard_block = True

    # Check for hardcoded credentials
    cred_warnings = [w for w in result.warnings if "hardcoded credential" in w.lower()]
    if cred_warnings:
        reasons.append("Hardcoded credentials detected")
        has_hard_block = True

    # Syntax error in generated code (checked externally, but signal via warnings)
    syntax_warnings = [w for w in result.warnings if "Syntax error" in w or "syntax" in w.lower()]
    if syntax_warnings:
        reasons.append("Syntax error in generated code")
        has_hard_block = True

    # Determine level
    if has_hard_block or unresolved_count > 3:
        level = "low"
    elif unresolved_count > 0 or imports.unknown:
        level = "medium"
    else:
        level = "high"

    # Clamp confidence based on return type policies
    _conf_order = {"low": 0, "medium": 1, "high": 2}
    for tool in result.tools:
        if tool.return_kind:
            kind = ReturnKind(tool.return_kind)
            policy = get_return_policy(kind)
            if policy.max_confidence and _conf_order[level] > _conf_order[policy.max_confidence]:
                level = policy.max_confidence

    # Missing parameter type hints → cap at medium
    if level == "high":
        has_missing_hints = any("type hint" in w.lower() or "no type hint" in w.lower() for w in result.warnings)
        if has_missing_hints:
            level = "medium"

    if unresolved_count > 0 and unresolved_count <= 3:
        reasons.append(f"{unresolved_count} unresolved symbol(s)")

    # draft_ready
    draft_ready = level != "low"

    # requires_manual_review
    requires_manual_review = level != "high"

    if not reasons and level == "high":
        reasons.append("All tools extracted successfully")

    return ConversionConfidence(level=level, reasons=reasons), draft_ready, requires_manual_review


# ── Self-Reference Detection ─────────────────────────────────────────

def detect_self_references(body_source: str) -> list[str]:
    """Find self.xxx references in source code."""
    refs: list[str] = []
    try:
        tree = ast.parse(body_source)
    except SyntaxError:
        return refs

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == "self":
                refs.append(f"self.{node.attr}")
    return list(dict.fromkeys(refs))


# ── Hardcoded Credentials Detection ──────────────────────────────────

# Variable names that suggest credentials
_CREDENTIAL_NAME_PATTERNS = re.compile(
    r"(?:api[_-]?key|apikey|secret[_-]?key|auth[_-]?token|"
    r"access[_-]?(?:token|key)|password|passwd|private[_-]?key|"
    r"client[_-]?secret|bearer[_-]?token|refresh[_-]?token|signing[_-]?key)",
    re.IGNORECASE,
)

# String prefixes that look like real credentials
_CREDENTIAL_VALUE_PREFIXES = (
    "sk-", "pk-", "Bearer ", "token-", "ghp_", "gho_", "ghs_",
    "xoxb-", "xoxp-", "AKIA",  # Slack, AWS
)


def detect_hardcoded_credentials(tree: ast.Module) -> list[str]:
    """Detect assignments that look like hardcoded API keys or secrets.

    Returns list of variable names with suspected hardcoded credentials.
    Only flags when BOTH the variable name suggests a credential AND the value
    looks like a real secret (long string or known prefix). This avoids
    false positives on empty strings and short placeholders.
    """
    found: list[str] = []

    for node in ast.walk(tree):
        # name = "value"
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant):
                    _check_credential(target.id, node.value.value, found)
        # name: str = "value"
        elif isinstance(node, ast.AnnAssign):
            if (isinstance(node.target, ast.Name)
                    and node.value is not None
                    and isinstance(node.value, ast.Constant)):
                _check_credential(node.target.id, node.value.value, found)

    return list(dict.fromkeys(found))


def _check_credential(var_name: str, value: object, found: list[str]) -> None:
    """Check if a variable assignment looks like a hardcoded credential."""
    if not isinstance(value, str):
        return
    # Skip empty strings, placeholders, and very short values
    if len(value) < 8 or value in ("", "...", "xxx", "your-key-here", "CHANGE_ME"):
        return
    # Variable name must suggest a credential
    if not _CREDENTIAL_NAME_PATTERNS.search(var_name):
        return
    # Value must look like a real secret: long enough or has known prefix
    has_prefix = any(value.startswith(p) for p in _CREDENTIAL_VALUE_PREFIXES)
    if has_prefix or len(value) >= 16:
        found.append(var_name)


# ── Return Type Analysis ─────────────────────────────────────────────

def get_return_annotation(func: ast.FunctionDef) -> str | None:
    """Get return type annotation as string, or None."""
    if func.returns:
        return _annotation_to_str(func.returns)
    return None


def wrap_return_value(body_source: str) -> str:
    """Wrap return values in {'result': ...} for non-dict-returning functions."""
    try:
        tree = ast.parse(body_source)
    except SyntaxError:
        return body_source

    lines = body_source.splitlines()
    # Find return statements and wrap them
    replacements: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and node.value is not None:
            line_idx = node.lineno - 1
            if line_idx < len(lines):
                original_line = lines[line_idx]
                indent = original_line[: len(original_line) - len(original_line.lstrip())]
                value_src = ast.unparse(node.value)
                new_line = f'{indent}return {{"result": {value_src}}}'
                replacements.append((line_idx, original_line, new_line))

    for line_idx, old, new in reversed(replacements):
        lines[line_idx] = new

    return "\n".join(lines)


# ── Body Name Collection ─────────────────────────────────────────────

def collect_body_names(tools: list[ExtractedTool]) -> set[str]:
    """Collect all Name nodes used in tool bodies."""
    names: set[str] = set()
    for tool in tools:
        try:
            tree = ast.parse(tool.body_source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                names.add(node.id)
    return names


# ── Environment Variable Detection ────────────────────────────────────

def detect_env_var_usage(tree: ast.Module) -> list[str]:
    """Detect os.getenv() / os.environ usage → warns about runtime context dependency."""
    env_vars: list[str] = []
    for node in ast.walk(tree):
        # os.getenv("KEY") or os.environ["KEY"] or os.environ.get("KEY")
        if isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Attribute) and func.attr == "getenv"
                    and isinstance(func.value, ast.Name) and func.value.id == "os"):
                if node.args and isinstance(node.args[0], ast.Constant):
                    env_vars.append(str(node.args[0].value))
                else:
                    env_vars.append("<dynamic>")
            elif (isinstance(func, ast.Attribute) and func.attr == "get"
                    and isinstance(func.value, ast.Attribute)
                    and func.value.attr == "environ"
                    and isinstance(func.value.value, ast.Name)
                    and func.value.value.id == "os"):
                if node.args and isinstance(node.args[0], ast.Constant):
                    env_vars.append(str(node.args[0].value))
        elif isinstance(node, ast.Subscript):
            if (isinstance(node.value, ast.Attribute) and node.value.attr == "environ"
                    and isinstance(node.value.value, ast.Name) and node.value.value.id == "os"):
                if isinstance(node.slice, ast.Constant):
                    env_vars.append(str(node.slice.value))
                else:
                    env_vars.append("<dynamic>")
    return list(dict.fromkeys(env_vars))


# ── Try/Except Import Detection ──────────────────────────────────────

def detect_try_except_imports(tree: ast.Module) -> list[str]:
    """Detect imports inside try/except blocks → optional dependencies that may be missing."""
    optional_deps: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for child in ast.walk(node):
                if child is node:
                    continue
                if isinstance(child, ast.Import):
                    for alias in child.names:
                        optional_deps.append(alias.name.split(".")[0])
                elif isinstance(child, ast.ImportFrom) and child.module and child.level == 0:
                    optional_deps.append(child.module.split(".")[0])
    return list(dict.fromkeys(optional_deps))


# ── StructuredTool.from_function Detection ────────────────────────────

def detect_structured_tool_pattern(tree: ast.Module) -> bool:
    """Detect StructuredTool.from_function() usage — not supported in Sprint 1."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Attribute) and func.attr == "from_function"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "StructuredTool"):
                return True
    return False


# ── Slugify ──────────────────────────────────────────────────────────

def slugify(text: str) -> str:
    """Convert text to a URL-safe slug."""
    text = text.lower().strip()
    text = text.replace("_", "-")
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")[:60]


def to_snake(text: str) -> str:
    """Convert text to snake_case."""
    # Handle CamelCase
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", text)
    text = re.sub(r"([a-z\d])([A-Z])", r"\1_\2", text)
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]", "_", text)
    text = re.sub(r"_+", "_", text)
    return text.strip("_")[:60]
