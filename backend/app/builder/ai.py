"""AI-powered capability generator using Claude Sonnet.
Generates real, working Python code from natural-language descriptions."""

from __future__ import annotations

import json
import logging
import re

import anthropic

from app.builder.schemas import BuilderGenerateResponse, BuilderMetadata, CodeFile
from app.config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are the AgentNode Builder — an expert at creating Python tool packages in ANP v0.2 format.

Given a user's description, you generate a COMPLETE, WORKING Python package with:
1. A manifest (JSON) following ANP v0.2
2. Real, functional Python code (not scaffolds, not placeholders)
3. A pyproject.toml
4. A basic test file

RULES:
- The tool function MUST contain real implementation logic, not NotImplementedError
- Use only Python stdlib + common packages (requests, beautifulsoup4, etc.)
- List actual dependencies in pyproject.toml and manifest
- capability_id MUST be from this list:
  pdf_extraction, document_parsing, document_summary, citation_extraction,
  web_search, webpage_extraction, browser_navigation, link_discovery,
  csv_analysis, spreadsheet_parsing, data_cleaning, statistics_analysis,
  chart_generation, json_processing, sql_generation, log_analysis,
  vector_memory, knowledge_retrieval, semantic_search, embedding_generation,
  document_indexing, conversation_memory, email_drafting, email_summary,
  meeting_summary, scheduling, task_management, translation, tone_adjustment,
  code_analysis
- package_id must be lowercase kebab-case ending with -pack (e.g. email-extractor-pack)
- tool function name must be snake_case
- entrypoint format: module_name.tool:function_name
- module_name = package_id with hyphens replaced by underscores
- permissions.network.level = "unrestricted" if the tool makes HTTP requests, "none" otherwise
- permissions.filesystem.level = "workspace_read" if the tool reads files, "none" otherwise
- compatibility.frameworks MUST be ["generic"]
- manifest_version MUST be "0.2"

ADDITIONAL FIELDS TO GENERATE:
- "readme_code": A full Markdown README with: Quick Start, Usage examples, API Reference, License section
- "use_cases": Array of 3-5 strings, each "verb + concrete object" (e.g. "Extract tables from PDF files")
- "examples": Array of structured code examples: [{"title": "...", "language": "python", "code": "..."}]

The manifest should also include:
- "use_cases": same as above
- "examples": same as above
- "env_requirements": array of {"name": "ENV_VAR", "required": true/false, "description": "..."} if any API keys needed

RESPONSE FORMAT — respond with ONLY a JSON object, no markdown, no explanation:
{
  "package_id": "...",
  "package_name": "...",
  "module_name": "...",
  "tool_name": "...",
  "capability_ids": ["..."],
  "manifest": { ... full ANP v0.2 manifest ... },
  "tool_code": "... full Python code for tool.py ...",
  "init_code": "... __init__.py content ...",
  "pyproject": "... pyproject.toml content ...",
  "test_code": "... test file content ...",
  "readme_code": "... full Markdown README ...",
  "use_cases": ["...", "..."],
  "examples": [{"title": "...", "language": "python", "code": "..."}],
  "dependencies": ["requests", ...],
  "warnings": []
}"""

USER_TEMPLATE = """\
Create a working ANP v0.2 package for:

{description}

The tool must contain REAL implementation code that actually works when called. \
Include proper error handling, type hints, and docstrings. \
The publisher field should be empty string "" (it gets filled at publish time)."""


def _extract_json(text: str) -> dict:
    """Extract JSON from Claude's response, handling possible markdown wrapping."""
    text = text.strip()
    # Remove markdown code fences if present
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    return json.loads(text)


async def generate_with_ai(description: str) -> BuilderGenerateResponse:
    """Generate a complete ANP v0.2 package using Claude Sonnet."""
    client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    message = await client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=16384,
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": USER_TEMPLATE.format(description=description)},
        ],
    )

    raw = message.content[0].text
    logger.info("AI response length: %d chars, stop_reason: %s", len(raw), message.stop_reason)
    data = _extract_json(raw)

    package_id = data["package_id"]
    module_name = data["module_name"]
    tool_name = data["tool_name"]
    ai_manifest = data["manifest"]

    code_files = [
        CodeFile(path=f"src/{module_name}/tool.py", content=data["tool_code"]),
        CodeFile(path=f"src/{module_name}/__init__.py", content=data.get("init_code", f'"""AgentNode package: {data["package_name"]}"""\n')),
        CodeFile(path="pyproject.toml", content=data["pyproject"]),
        CodeFile(path=f"tests/test_{tool_name}.py", content=data.get("test_code", "")),
    ]

    # Add README if generated
    readme_code = data.get("readme_code")
    if readme_code:
        code_files.append(CodeFile(path="README.md", content=readme_code))

    # --- Normalize AI manifest into publish-compatible format ---
    manifest = _normalize_manifest(ai_manifest, data, package_id, module_name, tool_name)

    # Extract capability IDs from normalized manifest
    cap_ids: list[str] = []
    for t in manifest.get("capabilities", {}).get("tools", []):
        if isinstance(t, dict) and t.get("capability_id"):
            cap_ids.append(t["capability_id"])
    if not cap_ids:
        cap_ids = data.get("capability_ids", [])

    tool_count = len(manifest.get("capabilities", {}).get("tools", []))
    if tool_count == 0:
        tool_count = 1

    # Build manifest YAML from the normalized JSON for display
    manifest_yaml = _json_to_yaml(manifest)

    metadata = BuilderMetadata(
        package_id=package_id,
        package_name=data["package_name"],
        tool_count=tool_count,
        detected_capability_ids=cap_ids,
        detected_framework="generic",
        publish_ready=True,
        warnings=data.get("warnings", []),
    )

    return BuilderGenerateResponse(
        manifest_yaml=manifest_yaml,
        manifest_json=manifest,
        code_files=code_files,
        metadata=metadata,
    )


def _normalize_manifest(ai: dict, data: dict, package_id: str, module_name: str, tool_name: str) -> dict:
    """Normalize an AI-generated manifest into publish-compatible format.

    The AI generates inconsistent manifest formats. This function ensures the
    manifest always has the fields the Publish UI expects:
    - name (not display_name or package_name)
    - summary
    - capabilities.tools[] with name, description, capability_id, entrypoint
    - permissions with nested level keys
    """
    # --- Basic fields ---
    name = ai.get("name") or ai.get("display_name") or ai.get("package_name") or data.get("package_name", "")
    summary = ai.get("summary") or ai.get("description") or ""
    description = ai.get("description") or summary
    version = ai.get("version") or "1.0.0"

    # --- Tools ---
    tools: list[dict] = []
    ai_caps = ai.get("capabilities")
    ai_tools = ai.get("tools")

    # Case 1: capabilities.tools[] (correct format)
    if isinstance(ai_caps, dict) and isinstance(ai_caps.get("tools"), list):
        for t in ai_caps["tools"]:
            if isinstance(t, dict):
                tools.append(_normalize_tool(t, module_name))
    # Case 2: tools[] at top level (some AI outputs)
    elif isinstance(ai_tools, list) and ai_tools and isinstance(ai_tools[0], dict):
        for t in ai_tools:
            tools.append(_normalize_tool(t, module_name))

    # Case 3: no tools array — build from top-level entrypoint + capability_ids
    if not tools:
        cap_ids = data.get("capability_ids", [])
        if isinstance(ai_caps, list):
            cap_ids = ai_caps  # capabilities: ["web_search", ...] format
        entrypoint = ai.get("entrypoint") or f"{module_name}.tool:{tool_name}"
        tools.append({
            "name": tool_name,
            "description": description,
            "capability_id": cap_ids[0] if cap_ids else "",
            "entrypoint": entrypoint,
        })

    # --- Permissions ---
    perms = ai.get("permissions", {})
    normalized_perms = {}
    for key in ("network", "filesystem", "code_execution", "data_access", "user_approval"):
        val = perms.get(key, {})
        if isinstance(val, dict) and "level" in val:
            normalized_perms[key] = val
        elif isinstance(val, str):
            normalized_perms[key] = {"level": val}
        else:
            normalized_perms[key] = {"level": "none"}

    # --- Frameworks ---
    compat = ai.get("compatibility", {})
    if isinstance(compat, dict) and isinstance(compat.get("frameworks"), list):
        frameworks = compat["frameworks"]
    else:
        frameworks = ["generic"]

    result = {
        "manifest_version": "0.2",
        "package_id": package_id,
        "package_type": ai.get("package_type", "toolpack"),
        "name": name,
        "publisher": ai.get("publisher", ""),
        "version": version,
        "summary": summary,
        "description": description,
        "runtime": "python",
        "install_mode": "package",
        "hosting_type": "agentnode_hosted",
        "capabilities": {"tools": tools},
        "compatibility": {"frameworks": frameworks},
        "permissions": normalized_perms,
    }

    # Enrichment fields
    if ai.get("use_cases"):
        result["use_cases"] = ai["use_cases"]
    if ai.get("examples"):
        result["examples"] = ai["examples"]
    if ai.get("env_requirements"):
        result["env_requirements"] = ai["env_requirements"]

    return result


def _normalize_tool(t: dict, module_name: str) -> dict:
    """Normalize a single tool entry from AI output."""
    name = t.get("name") or t.get("id") or t.get("display_name") or ""
    # capability_id: string or first from array
    cap_id = t.get("capability_id") or ""
    if not cap_id and isinstance(t.get("capability_ids"), list) and t["capability_ids"]:
        cap_id = t["capability_ids"][0]
    entrypoint = t.get("entrypoint") or ""
    if not entrypoint and name:
        entrypoint = f"{module_name}.tool:{name}"

    tool: dict = {
        "name": name,
        "description": t.get("description") or "",
        "capability_id": cap_id,
        "entrypoint": entrypoint,
    }
    if t.get("input_schema") or t.get("parameters"):
        tool["input_schema"] = t.get("input_schema") or t.get("parameters")
    if t.get("output_schema") or t.get("returns"):
        tool["output_schema"] = t.get("output_schema") or t.get("returns")
    return tool


def _json_to_yaml(obj: dict | list | str | int | float | bool | None, indent: int = 0) -> str:
    """Convert a JSON-like dict to YAML string. Simple recursive converter."""
    lines: list[str] = []
    prefix = " " * indent

    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(value, dict):
                lines.append(f"{prefix}{key}:")
                lines.append(_json_to_yaml(value, indent + 2))
            elif isinstance(value, list):
                if not value:
                    lines.append(f"{prefix}{key}: []")
                elif all(isinstance(v, (str, int, float, bool)) for v in value):
                    # Inline simple lists
                    formatted = ", ".join(
                        f'"{v}"' if isinstance(v, str) else str(v) for v in value
                    )
                    lines.append(f"{prefix}{key}: [{formatted}]")
                else:
                    lines.append(f"{prefix}{key}:")
                    for item in value:
                        if isinstance(item, dict):
                            first = True
                            for k2, v2 in item.items():
                                item_prefix = f"{prefix}  - " if first else f"{prefix}    "
                                first = False
                                if isinstance(v2, (dict, list)):
                                    lines.append(f"{item_prefix}{k2}:")
                                    lines.append(_json_to_yaml(v2, indent + 6))
                                else:
                                    val_str = f'"{v2}"' if isinstance(v2, str) else str(v2)
                                    lines.append(f"{item_prefix}{k2}: {val_str}")
                        else:
                            val_str = f'"{item}"' if isinstance(item, str) else str(item)
                            lines.append(f"{prefix}  - {val_str}")
            else:
                if isinstance(value, str):
                    lines.append(f'{prefix}{key}: "{value}"')
                elif isinstance(value, bool):
                    lines.append(f"{prefix}{key}: {'true' if value else 'false'}")
                elif value is None:
                    lines.append(f"{prefix}{key}: null")
                else:
                    lines.append(f"{prefix}{key}: {value}")
    return "\n".join(lines)
