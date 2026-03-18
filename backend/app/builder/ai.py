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
    manifest = data["manifest"]

    # Build manifest YAML from the JSON for display
    manifest_yaml = _json_to_yaml(manifest)

    code_files = [
        CodeFile(path=f"src/{module_name}/tool.py", content=data["tool_code"]),
        CodeFile(path=f"src/{module_name}/__init__.py", content=data.get("init_code", f'"""AgentNode package: {data["package_name"]}"""\n')),
        CodeFile(path="pyproject.toml", content=data["pyproject"]),
        CodeFile(path=f"tests/test_{tool_name}.py", content=data.get("test_code", "")),
    ]

    # Extract capability IDs — handle both dict and list formats from AI
    cap_ids = data.get("capability_ids", [])
    if not cap_ids:
        capabilities = manifest.get("capabilities") if isinstance(manifest, dict) else None
        if isinstance(capabilities, dict):
            tools = capabilities.get("tools", [])
            if isinstance(tools, list):
                cap_ids = [t["capability_id"] for t in tools if isinstance(t, dict) and "capability_id" in t]

    # Count tools robustly
    tool_count = 1
    if isinstance(manifest, dict):
        capabilities = manifest.get("capabilities")
        if isinstance(capabilities, dict):
            tools = capabilities.get("tools", [])
            if isinstance(tools, list):
                tool_count = len(tools) or 1

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
