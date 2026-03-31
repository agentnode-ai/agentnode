"""Import conversion service — dispatches to platform converters and scaffolds ANP packages."""
from __future__ import annotations

import ast
import logging
import time

from app.import_.schemas import (
    ConvertRequest,
    ConvertResponse,
    DetectedTool,
    WarningItem,
)
from app.import_.converters.base import (
    build_available_names,
    classify_imports,
    collect_body_names,
    collect_business_imports,
    compute_confidence,
    detect_env_var_usage,
    detect_hardcoded_credentials,
    detect_structured_tool_pattern,
    detect_try_except_imports,
    detect_unresolved_symbols,
    generate_manifest_dict,
    generate_package_files,
    generate_tool_py,
    parse_source,
    slugify,
    yaml_dump,
    ConversionConfidence,
)
from app.import_.converters import langchain as langchain_converter
from app.import_.converters import crewai as crewai_converter
from app.import_.converters import mcp as mcp_converter
from app.import_.converters import openai_funcs as openai_converter

logger = logging.getLogger(__name__)

# ── Warning classification ───────────────────────────────────────────
# Patterns that determine warning category.
# "blocking" = draft_ready should be false, must fix before publish
# "review"   = manual check needed, but can publish
# "info"     = context only, no action required

_BLOCKING_PATTERNS = [
    "not draft-ready",
    "not supported",
    "syntax error",
    "no @tool decorator",
    "no tool pattern",
    "async",
    "relative import",
    "hardcoded credential",
]

_REVIEW_PATTERNS = [
    "is called but not defined",
    "could not be identified",
    "type hint",
    "environment variable",
    "try/except",
    "wrapped",
    "args_schema",
]

# Everything else → "info"


def _classify_warning(message: str) -> str:
    """Classify a warning message as blocking / review / info."""
    lower = message.lower()
    for pat in _BLOCKING_PATTERNS:
        if pat in lower:
            return "blocking"
    for pat in _REVIEW_PATTERNS:
        if pat in lower:
            return "review"
    return "info"


def _emit_outcome(**kwargs: object) -> None:
    """Emit a single unified telemetry event for every conversion attempt."""
    logger.info("import_convert_outcome", extra=kwargs)


def convert(request: ConvertRequest) -> ConvertResponse:
    """Convert framework-specific tool code into an ANP package."""
    t0 = time.monotonic()

    # OpenAI is JSON, not Python — entirely different pipeline (no AST)
    if request.platform == "openai":
        return openai_converter.convert(request, t0, _classify_warning, _emit_outcome, _error_response)

    # 1. Parse
    try:
        tree = parse_source(request.content)
    except ValueError as e:
        _emit_outcome(
            platform=request.platform,
            content_length=len(request.content),
            duration_ms=int((time.monotonic() - t0) * 1000),
            success=False,
            parse_error=True,
            error_type="SyntaxError",
        )
        return _error_response(str(e))

    source_lines = request.content.splitlines()

    # 2. Classify imports
    imports = classify_imports(tree)

    # 3. Extract per platform
    if request.platform == "langchain":
        result = langchain_converter.extract(request.content, tree)
    elif request.platform == "crewai":
        result = crewai_converter.extract(request.content, tree)
    elif request.platform == "mcp":
        result = mcp_converter.extract(request.content, tree)
    else:
        return _error_response(f"Unsupported platform: {request.platform}")

    # 4. StructuredTool.from_function() detection (before no-tools check)
    if detect_structured_tool_pattern(tree):
        result.warnings.append(
            "Pattern `StructuredTool.from_function()` is not supported. "
            "Please convert to a plain `@tool` function manually."
        )

    # 4a. No tools found?
    if not result.tools:
        hint = {
            "langchain": "No @tool decorator or BaseTool class found.",
            "crewai": "No @tool decorator or BaseTool class found.",
            "mcp": "No @mcp.tool() decorator found. Make sure you use FastMCP.",
        }.get(request.platform, "No recognized tool pattern found.")
        all_warnings = result.warnings + [
            f"{hint} Check that your code contains a recognized tool pattern."
        ]
        _emit_outcome(
            platform=request.platform,
            content_length=len(request.content),
            duration_ms=int((time.monotonic() - t0) * 1000),
            success=True,
            no_tools_detected=True,
            confidence="low",
            draft_ready=False,
        )
        return ConvertResponse(
            code_files=[],
            manifest_yaml="",
            manifest_json={},
            confidence=ConversionConfidence(
                level="low", reasons=["No tool pattern detected"]
            ),
            draft_ready=False,
            requires_manual_review=True,
            warnings=all_warnings,
            grouped_warnings=[
                WarningItem(message=w, category=_classify_warning(w))
                for w in all_warnings
            ],
            changes=[],
            detected_dependencies=imports.third_party_names,
            unknown_imports=imports.unknown_names,
            detected_tools=[],
            package_id="",
        )

    # 4c. Environment variable detection
    env_vars = detect_env_var_usage(tree)
    if env_vars:
        var_list = ", ".join(f"`{v}`" for v in env_vars if v != "<dynamic>")
        if var_list:
            result.warnings.append(
                f"This tool uses environment variables ({var_list}). "
                "Ensure they are configured in the AgentNode runtime."
            )
        else:
            result.warnings.append(
                "This tool uses environment variables (dynamic keys). "
                "Ensure they are configured in the AgentNode runtime."
            )

    # 4d. Hardcoded credentials detection
    cred_vars = detect_hardcoded_credentials(tree)
    for var in cred_vars:
        result.warnings.append(
            f"Hardcoded credential detected in `{var}`. "
            "Remove secrets before publishing. **Not draft-ready.**"
        )

    # 4e. Try/except optional import detection
    try_imports = detect_try_except_imports(tree)
    for dep in try_imports:
        if dep not in imports.framework:
            result.warnings.append(
                f"Dependency `{dep}` is imported inside a try/except block (optional). "
                "Ensure it is listed in pyproject.toml for reliable execution."
            )

    # 5. Record framework import removal
    if imports.framework:
        removed = ", ".join(f"`{f}`" for f in imports.framework)
        result.changes.append(f"Framework imports removed: {removed}")

    # 6. Unresolved symbol detection
    all_params = []
    for tool in result.tools:
        all_params.extend(p.name for p in tool.params)

    available = build_available_names(tree, imports, all_params, result.helper_names)

    for tool in result.tools:
        unresolved = detect_unresolved_symbols(tool.body_source, available)
        for sym in unresolved:
            result.warnings.append(
                f"Function `{sym}` is called but not defined. "
                "Add it to the code or replace it."
            )

    # 7. Unknown import warnings
    tool_body_names = collect_body_names(result.tools)

    # Build mapping: unknown module -> names imported from it
    unknown_imported_names: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            top = node.module.split(".")[0]
            if top in imports.unknown:
                for alias in node.names:
                    unknown_imported_names.setdefault(top, []).append(
                        alias.asname or alias.name
                    )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in imports.unknown:
                    unknown_imported_names.setdefault(top, []).append(
                        alias.asname or top
                    )

    for unk in imports.unknown:
        if unk.startswith("."):
            result.warnings.append(
                f"`from {unk} import ...` — relative imports indicate a multi-file project. "
                "This importer only supports single-file snippets. "
                "Include the code directly. **Not draft-ready.**"
            )
        else:
            # Check if the module name or any name imported from it is used in tool bodies
            imported_names = unknown_imported_names.get(unk, [])
            used_in_body = (
                unk in tool_body_names
                or any(n in tool_body_names for n in imported_names)
            )
            if used_in_body:
                result.warnings.append(
                    f"Import `{unk}` could not be identified and is used in the tool body. "
                    "Add the code or replace with a known package. **Not draft-ready.**"
                )
            else:
                result.warnings.append(
                    f"Import `{unk}` could not be identified but is not directly used in the tool. "
                    "It can probably be removed."
                )

    # 8. Scaffold ANP package
    slug = slugify(result.tools[0].name) + "-pack"
    if not slug or slug == "-pack":
        slug = "imported-tool-pack"
    module_name = slug.replace("-", "_")

    business_imports = collect_business_imports(tree, source_lines)
    tool_py = generate_tool_py(result.tools, result.helpers, business_imports)

    # 9. Validate generated code
    try:
        ast.parse(tool_py)
    except SyntaxError as e:
        result.warnings.append(
            f"Generated code has a syntax error at line {e.lineno}: {e.msg}"
        )

    # 10. Generate manifest (JSON first, YAML from it)
    manifest_json = generate_manifest_dict(slug, module_name, result.tools, imports)
    manifest_yaml = yaml_dump(manifest_json)

    # 11. Generate package files
    code_files = generate_package_files(
        slug, module_name, result.tools,
        imports.third_party, tool_py, manifest_yaml,
    )

    # 12. Confidence + draft_ready + requires_manual_review
    # Build set of unknown modules that are actively used in tool bodies
    unknown_names_active: set[str] = set()
    for unk in imports.unknown:
        if unk.startswith("."):
            continue
        imported_names = unknown_imported_names.get(unk, [])
        if unk in tool_body_names or any(n in tool_body_names for n in imported_names):
            unknown_names_active.add(unk)

    confidence, draft_ready, requires_review = compute_confidence(
        result, imports, tool_body_names, unknown_names_active
    )

    # 13. Standard changes
    manifest_version = manifest_json.get("manifest_version", "0.2")
    if len(result.tools) == 1:
        result.changes.append(
            f"ANP manifest (v{manifest_version}) generated with entrypoint `{module_name}.tool`"
        )
    else:
        entrypoints = ", ".join(f"`{module_name}.tool:{t.name}`" for t in result.tools)
        result.changes.append(
            f"ANP manifest (v{manifest_version}) generated with entrypoints: {entrypoints}"
        )

    if imports.third_party:
        deps = ", ".join(f"`{d}`" for d in imports.third_party)
        result.changes.append(f"pyproject.toml generated with dependencies: {deps}")
    else:
        result.changes.append("pyproject.toml generated (no external dependencies)")

    result.changes.append("Test scaffold generated: `tests/test_tool.py`")

    # Package ID warning
    result.warnings.append(
        f"Package ID `{slug}` was auto-suggested. Please review before publishing."
    )

    # 14. Build detected tools
    detected_tools = [
        DetectedTool(
            name=t.name,
            original_name=t.original_name,
            description=t.description,
            params=t.params,
            has_return_dict=t.has_return_dict,
        )
        for t in result.tools
    ]

    # 15. Classify warnings into groups
    grouped_warnings = [
        WarningItem(message=w, category=_classify_warning(w))
        for w in result.warnings
    ]

    # 16. Unified outcome telemetry
    duration_ms = int((time.monotonic() - t0) * 1000)
    blocking_count = sum(1 for w in grouped_warnings if w.category == "blocking")
    review_count = sum(1 for w in grouped_warnings if w.category == "review")
    info_count = sum(1 for w in grouped_warnings if w.category == "info")
    unresolved_count = sum(
        1 for w in result.warnings if "is called but not defined" in w
    )
    _emit_outcome(
        platform=request.platform,
        content_length=len(request.content),
        duration_ms=duration_ms,
        success=True,
        confidence=confidence.level,
        draft_ready=draft_ready,
        tools_detected=len(result.tools),
        warning_count=len(result.warnings),
        blocking_count=blocking_count,
        warning_categories={"blocking": blocking_count, "review": review_count, "info": info_count},
        unknown_imports_count=len(imports.unknown),
        unresolved_symbols_count=unresolved_count,
        manifest_version=manifest_version,
        package_id=slug,
    )

    return ConvertResponse(
        code_files=code_files,
        manifest_yaml=manifest_yaml,
        manifest_json=manifest_json,
        confidence=confidence,
        draft_ready=draft_ready,
        requires_manual_review=requires_review,
        warnings=result.warnings,
        grouped_warnings=grouped_warnings,
        changes=result.changes,
        detected_dependencies=imports.third_party_names,
        unknown_imports=imports.unknown_names,
        detected_tools=detected_tools,
        package_id=slug,
    )


def _error_response(message: str) -> ConvertResponse:
    """Build an error response."""
    return ConvertResponse(
        code_files=[],
        manifest_yaml="",
        manifest_json={},
        confidence=ConversionConfidence(level="low", reasons=[message]),
        draft_ready=False,
        requires_manual_review=True,
        warnings=[message],
        grouped_warnings=[WarningItem(message=message, category="blocking")],
        changes=[],
        detected_dependencies=[],
        unknown_imports=[],
        detected_tools=[],
        package_id="",
    )
