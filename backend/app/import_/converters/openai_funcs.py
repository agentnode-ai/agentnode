"""OpenAI Function Calling / GPT Actions converter.

Converts JSON-based OpenAI function definitions into ANP packages.
Unlike LangChain/CrewAI/MCP converters, this operates on JSON, not Python AST.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Callable

from app.builder.schemas import CodeFile
from app.import_.converters.base import (
    ExtractedTool,
    ImportClassification,
    detect_capability_id_for_tool,
    detect_capability_ids,
    generate_manifest_dict,
    generate_package_files,
    infer_permissions,
    slugify,
    to_snake,
    yaml_dump,
)
from app.import_.schemas import (
    ConversionConfidence,
    ConvertRequest,
    ConvertResponse,
    DetectedTool,
    ToolParam,
    WarningItem,
)

logger = logging.getLogger(__name__)

# ── JSON Schema → Python type mapping ────────────────────────────────

_JSON_TYPE_TO_PYTHON: dict[str, str] = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "array": "list",
    "object": "dict",
    "null": "None",
}

_PYTHON_TYPE_TO_DEFAULT: dict[str, str] = {
    "str": '""',
    "int": "0",
    "float": "0.0",
    "bool": "False",
    "list": "[]",
    "dict": "{}",
}


# ── JSON format detection & normalization ────────────────────────────


def _normalize_functions(raw: Any) -> list[dict]:
    """Normalize all known OpenAI JSON format variants into a flat list of
    {name, description, parameters} dicts.

    Supported formats:
    1. Array of functions: [{name, description, parameters}, ...]
    2. Wrapper with "tools": {"tools": [{type: "function", function: {...}}, ...]}
    3. Wrapper with "functions": {"functions": [{name, ...}, ...]}
    4. Single Chat Completions tool: {type: "function", function: {name, ...}}
    5. Single Responses API: {type: "function", name: ..., parameters: ...}
    6. Single bare function: {name: ..., description: ..., parameters: ...}
    """
    if isinstance(raw, list):
        # Array — each element could be a tool wrapper or a bare function
        return [_unwrap_single(item) for item in raw]

    if isinstance(raw, dict):
        # Check for wrapper objects
        if "tools" in raw and isinstance(raw["tools"], list):
            return [_unwrap_single(item) for item in raw["tools"]]
        if "functions" in raw and isinstance(raw["functions"], list):
            return [_unwrap_single(item) for item in raw["functions"]]
        # Single item
        return [_unwrap_single(raw)]

    return []


def _unwrap_single(item: Any) -> dict:
    """Unwrap a single function definition from various formats."""
    if not isinstance(item, dict):
        return {}

    # Chat Completions format: {type: "function", function: {name, ...}}
    if item.get("type") == "function" and "function" in item:
        func = item["function"]
        if isinstance(func, dict):
            return {
                "name": func.get("name", ""),
                "description": func.get("description", ""),
                "parameters": func.get("parameters", {}),
            }

    # Responses API format: {type: "function", name: ..., parameters: ...}
    if item.get("type") == "function" and "name" in item:
        return {
            "name": item.get("name", ""),
            "description": item.get("description", ""),
            "parameters": item.get("parameters", {}),
        }

    # Bare function: {name, description, parameters}
    if "name" in item:
        return {
            "name": item.get("name", ""),
            "description": item.get("description", ""),
            "parameters": item.get("parameters", {}),
        }

    return {}


# ── Parameter extraction from JSON Schema ────────────────────────────


def _extract_params_from_schema(parameters: dict) -> list[ToolParam]:
    """Extract ToolParam list from an OpenAI parameters JSON Schema object."""
    if not isinstance(parameters, dict):
        return []

    properties = parameters.get("properties", {})
    required_set = set(parameters.get("required", []))

    params = []
    for param_name, schema in properties.items():
        if not isinstance(schema, dict):
            continue

        json_type = schema.get("type", "string")
        python_type = _JSON_TYPE_TO_PYTHON.get(json_type, "Any")
        description = schema.get("description", "")

        # Handle enum values in description
        if "enum" in schema and isinstance(schema["enum"], list):
            enum_str = ", ".join(repr(v) for v in schema["enum"])
            if description:
                description += f" (one of: {enum_str})"
            else:
                description = f"One of: {enum_str}"

        is_required = param_name in required_set
        default_repr = None if is_required else _PYTHON_TYPE_TO_DEFAULT.get(python_type, "None")

        params.append(ToolParam(
            name=param_name,
            type_hint=python_type,
            required=is_required,
            default_repr=default_repr,
            annotation_missing=False,
            description=description,
        ))

    return params


# ── Code generation ──────────────────────────────────────────────────


def _generate_stub_body(func_name: str, params: list[ToolParam]) -> str:
    """Generate a stub function body with NotImplementedError."""
    lines = [
        f'    # TODO: Implement the logic for {func_name}',
        f'    raise NotImplementedError(',
        f'        "Function {func_name} is a stub generated from an OpenAI function schema. "',
        f'        "Replace this with your actual implementation."',
        f'    )',
    ]
    return "\n".join(lines) + "\n"


def _generate_tool_py(tools: list[ExtractedTool]) -> str:
    """Generate tool.py with stub implementations."""
    parts: list[str] = [
        '"""ANP tool module — generated from OpenAI function definitions.',
        '',
        'Each function below is a stub that needs implementation.',
        'Replace the NotImplementedError with your actual logic.',
        '"""',
        '',
    ]

    for tool in tools:
        # Build parameter signature
        sig_parts = []
        for p in tool.params:
            if p.type_hint and p.type_hint != "Any":
                if p.default_repr is not None:
                    sig_parts.append(f"{p.name}: {p.type_hint} = {p.default_repr}")
                else:
                    sig_parts.append(f"{p.name}: {p.type_hint}")
            else:
                if p.default_repr is not None:
                    sig_parts.append(f"{p.name}={p.default_repr}")
                else:
                    sig_parts.append(p.name)

        sig = ", ".join(sig_parts)
        parts.append(f"def {tool.name}({sig}) -> dict:")
        if tool.description:
            parts.append(f'    """{tool.description}"""')
        parts.append(tool.body_source.rstrip())
        parts.append("")
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


# ── Main convert function ────────────────────────────────────────────


def convert(
    request: ConvertRequest,
    t0: float,
    classify_warning: Callable[[str], str],
    emit_outcome: Callable[..., None],
    error_response: Callable[[str], ConvertResponse],
) -> ConvertResponse:
    """Convert OpenAI function calling JSON into an ANP package.

    This is called directly from service.py, bypassing the AST pipeline.
    """
    # 1. Parse JSON
    content = request.content.strip()
    try:
        raw = json.loads(content)
    except json.JSONDecodeError as e:
        emit_outcome(
            platform="openai",
            content_length=len(request.content),
            duration_ms=int((time.monotonic() - t0) * 1000),
            success=False,
            parse_error=True,
            error_type="JSONDecodeError",
        )
        return error_response(f"Invalid JSON: {e.msg} at line {e.lineno}")

    # 2. Normalize into function list
    functions = _normalize_functions(raw)
    functions = [f for f in functions if f.get("name")]  # drop empties

    if not functions:
        emit_outcome(
            platform="openai",
            content_length=len(request.content),
            duration_ms=int((time.monotonic() - t0) * 1000),
            success=True,
            no_tools_detected=True,
            confidence="low",
            draft_ready=False,
        )
        return error_response(
            "No function definitions found in JSON. "
            "Expected OpenAI function calling format: "
            '{name: "...", description: "...", parameters: {...}}'
        )

    # 3. Extract tools
    tools: list[ExtractedTool] = []
    warnings: list[str] = []
    changes: list[str] = []

    for func_def in functions:
        name = func_def.get("name", "unknown")
        func_name = to_snake(name)
        description = func_def.get("description", "")
        parameters = func_def.get("parameters", {})

        params = _extract_params_from_schema(parameters)
        body = _generate_stub_body(func_name, params)

        tool = ExtractedTool(
            name=func_name,
            original_name=name,
            description=description[:200] if description else "",
            params=params,
            body_source=body,
            is_async=False,
            has_return_dict=True,
            return_annotation="dict",
            return_kind="dict_like",
        )
        tools.append(tool)
        changes.append(f"Function `{name}` extracted from OpenAI JSON schema")

    # Always warn that these are stubs
    warnings.append(
        "Code generated from OpenAI function schemas contains stub implementations only. "
        "You must replace each `NotImplementedError` with your actual logic before publishing."
    )

    # 4. Build slug and module name
    slug = slugify(tools[0].name) + "-pack"
    if not slug or slug == "-pack":
        slug = "imported-tool-pack"
    module_name = slug.replace("-", "_")

    # 5. Generate tool.py
    tool_py = _generate_tool_py(tools)

    # 6. Generate manifest
    # Build a minimal ImportClassification (no Python imports for JSON input)
    imports = ImportClassification()
    manifest_json = generate_manifest_dict(slug, module_name, tools, imports)

    # Preserve original JSON Schema in manifest tools (richer than what we generate)
    for i, func_def in enumerate(functions):
        if i < len(manifest_json.get("capabilities", {}).get("tools", [])):
            original_params = func_def.get("parameters", {})
            if original_params and isinstance(original_params, dict):
                manifest_json["capabilities"]["tools"][i]["input_schema"] = original_params

    manifest_yaml = yaml_dump(manifest_json)

    # 7. Generate package files
    code_files = generate_package_files(
        slug, module_name, tools, [], tool_py, manifest_yaml,
    )

    # 8. Confidence: medium (stubs are valid but not functional)
    confidence = ConversionConfidence(
        level="medium",
        reasons=["Generated from JSON schema — stub implementations require manual coding"],
    )
    draft_ready = False
    requires_review = True

    # 9. Standard changes
    manifest_version = manifest_json.get("manifest_version", "0.2")
    if len(tools) == 1:
        changes.append(
            f"ANP manifest (v{manifest_version}) generated with entrypoint `{module_name}.tool`"
        )
    else:
        entrypoints = ", ".join(f"`{module_name}.tool:{t.name}`" for t in tools)
        changes.append(
            f"ANP manifest (v{manifest_version}) generated with entrypoints: {entrypoints}"
        )
    changes.append("pyproject.toml generated (no external dependencies)")
    changes.append("Test scaffold generated: `tests/test_tool.py`")

    # Package ID warning
    warnings.append(
        f"Package ID `{slug}` was auto-suggested. Please review before publishing."
    )

    # 10. Build detected tools
    detected_tools = [
        DetectedTool(
            name=t.name,
            original_name=t.original_name,
            description=t.description,
            params=t.params,
            has_return_dict=t.has_return_dict,
        )
        for t in tools
    ]

    # 11. Classify warnings
    grouped_warnings = [
        WarningItem(message=w, category=classify_warning(w))
        for w in warnings
    ]

    # 12. Telemetry
    duration_ms = int((time.monotonic() - t0) * 1000)
    blocking_count = sum(1 for w in grouped_warnings if w.category == "blocking")
    review_count = sum(1 for w in grouped_warnings if w.category == "review")
    info_count = sum(1 for w in grouped_warnings if w.category == "info")
    emit_outcome(
        platform="openai",
        content_length=len(request.content),
        duration_ms=duration_ms,
        success=True,
        confidence=confidence.level,
        draft_ready=draft_ready,
        tools_detected=len(tools),
        warning_count=len(warnings),
        blocking_count=blocking_count,
        warning_categories={"blocking": blocking_count, "review": review_count, "info": info_count},
        unknown_imports_count=0,
        unresolved_symbols_count=0,
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
        warnings=warnings,
        grouped_warnings=grouped_warnings,
        changes=changes,
        detected_dependencies=[],
        unknown_imports=[],
        detected_tools=detected_tools,
        package_id=slug,
    )
