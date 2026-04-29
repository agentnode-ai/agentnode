"""Smoke test preflight analysis and reason-based classification.

Phase 1 of the evidence-based probe-run architecture.

Design principle:
    "Verify what you can prove. Classify what you can't. Never guess silently."
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ──────────────────────────────────────────────────────────────
# Reason taxonomy — stable names, not to be renamed after Phase 1
#
# Every smoke outcome gets exactly one reason.
# Each reason maps to: passed / inconclusive / failed.
# ──────────────────────────────────────────────────────────────

REASON_VERDICTS: dict[str, tuple[str, str]] = {
    # reason                              → (status, human label)

    # ── passed ──
    "ok":                                   ("passed",       "Function returned a result"),
    "acceptable_external_dependency":       ("passed",       "Function reached external dependency boundary"),

    # ── inconclusive ──
    "credential_boundary_reached":          ("inconclusive", "Tool correctly reports missing credentials — functional boundary reached"),
    "invalid_test_input":                   ("inconclusive", "Generated test input was rejected by function"),
    "unsupported_operation_space":          ("inconclusive", "Function requires operation/action values not determinable from schema"),
    "needs_credentials":                    ("inconclusive", "Requires API keys/tokens not available in verification"),
    "external_network_blocked":             ("inconclusive", "Requires network access (blocked during verification)"),
    "schema_signature_mismatch":            ("inconclusive", "Schema and function signature disagree on types"),
    "missing_system_dependency":            ("inconclusive", "Requires system-level dependency not available in sandbox"),
    "not_implemented":                      ("inconclusive", "Package raises NotImplementedError (stub/placeholder)"),
    "needs_binary_input":                   ("inconclusive", "Requires binary file formats that cannot be text-stubbed"),
    "heavy_import_timeout":                 ("inconclusive", "Heavy ML library import exceeded time budget"),
    "unknown_smoke_condition":              ("inconclusive", "Ambiguous error — could be broken code or missing data"),

    # ── failed ──
    "fatal_runtime_error":                  ("failed",       "Fundamental runtime error (SyntaxError, NameError)"),
    "fatal_import_during_smoke":            ("failed",       "Module failed to import during smoke test"),
    "fatal_type_error":                     ("failed",       "Function contract is broken (TypeError with schema present)"),
    "fatal_timeout":                        ("failed",       "Execution timed out without known network dependency"),
}

FATAL_REASONS = frozenset({
    "fatal_runtime_error",
    "fatal_import_during_smoke",
    "fatal_type_error",
    "fatal_timeout",
})


# ──────────────────────────────────────────────────────────────
# Heavy ML imports that can exceed smoke timeout during import
# ──────────────────────────────────────────────────────────────

KNOWN_HEAVY_IMPORTS = frozenset({
    "torch", "tensorflow", "transformers", "sentence_transformers", "spacy",
})


# ──────────────────────────────────────────────────────────────
# Error message patterns
# ──────────────────────────────────────────────────────────────

CREDENTIAL_PATTERNS = (
    r"api[_\- ]?key",
    r"token[_ ](?:is )?required",
    r"missing.*(?:key|token|credential)",
    r"unauthorized",
    r"authentication[_ ]required",
    r"no[_ ]credentials",
    r"credentials[_ ]required",
    r"secret.*required",
    r"password[_ ]required",
)

OPERATION_PATTERNS = (
    r"unknown operation",
    r"invalid operation",
    r"unsupported operation",
    r"unknown action",
    r"invalid action",
    r"unsupported provider",
    r"unknown provider",
    r"invalid provider",
    r"unsupported platform",
    r"unsupported method",
    r"unsupported type",
    r"unsupported mode",
    r"choose from",
    r"must be one of",
    r"not a valid (?!pdf|image|png|jpg|jpeg|gif|bmp|tiff|wav|mp3|mp4)",
    r"is not supported\.?\s*supported:",
    r"conversion .+ is not supported",
)

CONTRACT_BREAK_PATTERNS = (
    r"unexpected keyword argument",
    r"got an unexpected",
    r"takes \d+ positional argument",
    r"positional arguments but",
    r"got multiple values for argument",
    r"missing \d+ required",
)

INPUT_REJECTION_PATTERNS = (
    r"invalid value",
    r"expected",
    r"field required",
    r"must be",
    r"key.*not found",
    r"missing key",
)

# ── Phase 3A: New pattern sets for better classification ──

SYSTEM_DEPENDENCY_PATTERNS = (
    r"executable doesn't exist",
    r"executable.+not found",
    r"browser(?:type)?\.launch",
    r"playwright.*install",
    r"(?:chromium|chrome|firefox) is not installed",
    r"ffmpeg.*not found",
    r"tesseract.*not (?:found|installed)",
    r"tesseract is not installed",
    r"wkhtmlto.*not found",
    r"libreoffice.*not found",
    r"poppler.*not (?:found|installed)",
    r"(?:gs|ghostscript).*not found",
)

NOT_IMPLEMENTED_PATTERNS = (
    r"replace this",
    r"not yet implemented",
    r"todo:?\s*implement",
)

BINARY_INPUT_PATTERNS = (
    r"(?:invalid|not a valid|cannot (?:read|open|parse|identify)).*(?:pdf|image|png|jpg|jpeg|gif|bmp|tiff|wav|mp3|mp4)",
    r"(?:pdf|image) file (?:is )?(?:required|expected|needed)",
    r"(?:magic number|file header|file signature).*(?:invalid|wrong|unexpected)",
    r"is this really a pdf",
    r"no /root object",
    r"unidentified.*image",
    r"cannot identify image file",
    r"not a zip file",  # invalid DOCX/XLSX/PPTX
)

DATABASE_CONNECTION_PATTERNS = (
    r"could not (?:parse|translate|connect).*(?:url|dsn|connection)",
    r"(?:invalid|malformed).*(?:connection string|dsn|database url)",
    r"(?:operational|interface|database)error.*(?:connect|connection)",
)


# ──────────────────────────────────────────────────────────────
# SmokeContext — strictly small, preflight only
# ──────────────────────────────────────────────────────────────

@dataclass
class SmokeContext:
    """Preflight analysis for a single tool. No code introspection, no README."""
    tool_name: str
    has_required_env_requirements: bool = False
    missing_required_env_vars: list[str] = field(default_factory=list)
    declares_network_access: bool = False
    input_has_enum_hints: bool = False       # Phase-2 prep, not yet active for classification
    has_examples: bool = False
    input_schema_present: bool = False
    python_dependencies: frozenset[str] = field(default_factory=frozenset)


def build_smoke_context(tool: dict) -> SmokeContext:
    """Build preflight context from a normalized tool dict.

    Expects tool to have: name, entrypoint, input_schema,
    env_requirements, network_level, examples.
    """
    ctx = SmokeContext(tool_name=tool.get("name", "unknown"))

    # ── Credential check ──
    env_reqs = tool.get("env_requirements") or []
    if isinstance(env_reqs, list):
        missing = []
        for req in env_reqs:
            if isinstance(req, dict) and req.get("required", True):
                name = req.get("name", "")
                if name:
                    missing.append(name)
        if missing:
            ctx.has_required_env_requirements = True
            ctx.missing_required_env_vars = missing

    # ── Network dependency ──
    # "restricted" counts as declares_network_access=False
    # (restricted means: should not need network)
    network_level = tool.get("network_level") or "none"
    if network_level not in ("none", "", "restricted"):
        ctx.declares_network_access = True

    # ── Schema analysis ──
    schema = tool.get("input_schema")
    if schema and isinstance(schema, dict):
        ctx.input_schema_present = True
        props = schema.get("properties", {})
        if isinstance(props, dict):
            for prop_def in props.values():
                if isinstance(prop_def, dict) and "enum" in prop_def:
                    ctx.input_has_enum_hints = True
                    break

    # ── Examples ──
    if tool.get("examples"):
        ctx.has_examples = True

    # ── Python dependencies (for heavy-import detection) ──
    py_deps = tool.get("python_dependencies")
    if py_deps and isinstance(py_deps, (list, set, frozenset)):
        normalized = frozenset(
            d.replace("-", "_").lower().split("[")[0].split(">")[0].split("<")[0].split("=")[0].split("!")[0].split("~")[0].strip()
            for d in py_deps if isinstance(d, str)
        )
        ctx.python_dependencies = normalized

    return ctx


# ──────────────────────────────────────────────────────────────
# Classification — always context-sensitive
# ──────────────────────────────────────────────────────────────

def _matches_any(patterns: tuple[str, ...], text: str) -> bool:
    """Check if any pattern matches in the text."""
    for pattern in patterns:
        if re.search(pattern, text):
            return True
    return False


def _is_contract_break(msg_lower: str) -> bool:
    """Check if message indicates a function signature contract break."""
    return _matches_any(CONTRACT_BREAK_PATTERNS, msg_lower)


def classify_smoke_error(
    exception_type: str,
    exception_msg: str,
    ctx: SmokeContext,
) -> str:
    """Classify a smoke test exception into a reason.

    Priority order (descending):
    1.  Credential keywords → needs_credentials
    2.  Operation keywords → unsupported_operation_space
    3.  NotImplementedError → not_implemented
    4.  Network exceptions (ConnectionError/TimeoutError) → network-dependent
    5.  Import failures → fatal_import_during_smoke
    6.  Fatal runtime errors (SyntaxError/NameError) → fatal_runtime_error
    7.  System dependency detection → missing_system_dependency
    8.  AttributeError → schema_signature_mismatch or unknown
    9.  TypeError → context-sensitive (schema-aware)
    10. Binary input detection → needs_binary_input
    11. FileNotFoundError/FileExistsError/IsADirectoryError → check system deps or invalid_test_input
    12. ValueError/KeyError/IndexError → input-rejection or unknown
    13. Default → unknown_smoke_condition
    """
    msg_lower = exception_msg.lower()

    # ── 1. Credential-related (highest priority) ──
    # Phase 7A: Use credential boundary detection with confidence
    cred_reason, cred_confidence = classify_credential_boundary(
        exception_type, msg_lower, ctx,
    )
    if cred_reason:
        return cred_reason

    # ── 2. Operation/enum validation errors ──
    if _matches_any(OPERATION_PATTERNS, msg_lower):
        return "unsupported_operation_space"

    # ── 3. NotImplementedError → not_implemented (Phase 3A) ──
    if exception_type == "NotImplementedError":
        return "not_implemented"

    # ── 4. Network exceptions ──
    if exception_type in ("ConnectionError", "TimeoutError", "ConnectError",
                          "ConnectTimeout", "ReadTimeout", "HTTPStatusError"):
        if ctx.declares_network_access:
            return "acceptable_external_dependency"
        # Check if it's credential-related (e.g. 401/403)
        if "401" in exception_msg or "403" in exception_msg:
            return "credential_boundary_reached"
        return "external_network_blocked"

    # ── 5. Import failures ──
    if exception_type in ("ImportError", "ModuleNotFoundError"):
        return "fatal_import_during_smoke"

    # ── 6. Fatal runtime errors ──
    if exception_type in ("SyntaxError", "NameError"):
        return "fatal_runtime_error"

    # ── 7. System dependency detection (Phase 3A) ──
    if exception_type in ("TesseractNotFoundError", "TesseractError"):
        return "missing_system_dependency"
    if _matches_any(SYSTEM_DEPENDENCY_PATTERNS, msg_lower):
        return "missing_system_dependency"

    # ── 8. AttributeError ──
    if exception_type == "AttributeError":
        if "object has no attribute" in msg_lower:
            return "schema_signature_mismatch"
        return "unknown_smoke_condition"

    # ── 9. TypeError (context-sensitive) ──
    if exception_type == "TypeError":
        if _is_contract_break(msg_lower):
            if ctx.input_schema_present:
                return "fatal_type_error"
            return "schema_signature_mismatch"
        return "unknown_smoke_condition"

    # ── 10. Binary input detection (Phase 3A) ──
    if _matches_any(BINARY_INPUT_PATTERNS, msg_lower):
        return "needs_binary_input"

    # ── 11. File-system errors ──
    if exception_type in ("FileNotFoundError", "FileExistsError", "IsADirectoryError"):
        # Check if it's actually a system dependency (e.g. "ffmpeg not found")
        if _matches_any(SYSTEM_DEPENDENCY_PATTERNS, msg_lower):
            return "missing_system_dependency"
        return "invalid_test_input"

    # ── 11b. Database connection errors ──
    if exception_type == "ArgumentError" or _matches_any(DATABASE_CONNECTION_PATTERNS, msg_lower):
        return "credential_boundary_reached"

    # ── 12. ValueError, KeyError, IndexError ──
    if exception_type in ("ValueError", "KeyError", "IndexError"):
        # Signature-level errors (e.g. ValueError wrapping missing-arg message)
        if re.search(r"missing \d+ required positional", msg_lower):
            if ctx.input_schema_present:
                return "fatal_type_error"
            return "schema_signature_mismatch"
        # Binary input detection in ValueError
        if _matches_any(BINARY_INPUT_PATTERNS, msg_lower):
            return "needs_binary_input"
        # Input rejection
        if _matches_any(INPUT_REJECTION_PATTERNS, msg_lower):
            return "invalid_test_input"
        return "unknown_smoke_condition"

    # ── 12b. Binary-format specific exceptions ──
    if exception_type in ("PdfminerException", "PdfReadError", "PdfStreamError",
                          "UnidentifiedImageError", "DecompressionBombError"):
        return "needs_binary_input"

    # ── 13. Default ──
    return "unknown_smoke_condition"


def classify_credential_boundary(
    exception_type: str, msg_lower: str, ctx: SmokeContext,
) -> tuple[str | None, str | None]:
    """Detect if an error represents a credential boundary.

    A tool that correctly reports "API key required" = functional up to auth check.

    Returns (reason, confidence) or (None, None) if not a credential boundary.
    """
    # HIGH confidence: Typed auth exceptions
    auth_exception_types = (
        "AuthenticationError", "AuthError", "APIKeyError",
        "AuthorizationError", "CredentialError", "AuthFailure",
        "InvalidAPIKey", "MissingAPIKey",
    )
    if exception_type in auth_exception_types:
        return "credential_boundary_reached", "high"

    # HIGH confidence: Known credential pattern + declared env requirements
    if _matches_any(CREDENTIAL_PATTERNS, msg_lower) and ctx.has_required_env_requirements:
        return "credential_boundary_reached", "high"

    # MEDIUM confidence: Credential pattern without env declaration
    if _matches_any(CREDENTIAL_PATTERNS, msg_lower):
        return "credential_boundary_reached", "medium"

    # MEDIUM confidence: HTTP 401/403
    if "401" in msg_lower or "403" in msg_lower:
        return "credential_boundary_reached", "medium"

    return None, None


def classify_timeout(ctx: SmokeContext) -> str:
    """Classify a subprocess timeout.

    This is for subprocess-level timeouts (sandbox.run_python_code returns
    "timed out"), NOT for TimeoutError exceptions raised inside the tool code.
    """
    if ctx.declares_network_access:
        return "external_network_blocked"
    if ctx.python_dependencies and ctx.python_dependencies & KNOWN_HEAVY_IMPORTS:
        return "heavy_import_timeout"
    return "fatal_timeout"
