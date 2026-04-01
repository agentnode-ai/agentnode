"""Heuristic capability builder — generates ANP v0.2 manifests and code scaffolds
from natural-language descriptions without requiring an external AI API."""

from __future__ import annotations

import re

from app.builder.schemas import (
    BuilderGenerateResponse,
    BuilderMetadata,
    CodeFile,
)

# ---------------------------------------------------------------------------
# Capability keyword map  (keyword → capability_id)
# ---------------------------------------------------------------------------
_CAPABILITY_MAP: list[tuple[list[str], str]] = [
    (["pdf", "portable document"], "pdf_extraction"),
    (["document", "docx", "doc parsing"], "document_parsing"),
    (["summarize", "summary", "summarise", "tldr", "digest"], "document_summary"),
    (["citation", "reference", "bibliography"], "citation_extraction"),
    (["web search", "google", "bing", "search the web", "internet search"], "web_search"),
    (["webpage", "website", "html", "scrape", "crawl", "fetch url", "extract from url"], "webpage_extraction"),
    (["browser", "navigate", "click", "playwright", "selenium"], "browser_navigation"),
    (["link", "href", "discover url"], "link_discovery"),
    (["csv", "comma separated"], "csv_analysis"),
    (["spreadsheet", "excel", "xlsx", "xls"], "spreadsheet_parsing"),
    (["clean data", "normalize data", "data cleaning"], "data_cleaning"),
    (["statistic", "average", "median", "standard deviation", "analytics"], "statistics_analysis"),
    (["chart", "graph", "plot", "visuali"], "chart_generation"),
    (["json", "json processing", "parse json", "transform json"], "json_processing"),
    (["sql", "query", "database query"], "sql_generation"),
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
    (["code", "source code", "lint", "refactor", "analyse code", "analyze code"], "code_analysis"),
]

_NETWORK_KEYWORDS = [
    "webpage", "website", "url", "http", "fetch", "scrape", "crawl",
    "api", "request", "download", "search", "web",
]
_FILESYSTEM_KEYWORDS = [
    "pdf", "file", "csv", "document", "excel", "spreadsheet",
    "read file", "write file", "directory", "folder", "image", "log file",
]


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s-]+", "-", text)
    text = text.strip("-")
    # Cut at word boundary, max 40 chars
    if len(text) > 40:
        text = text[:40].rsplit("-", 1)[0]
    return text


def _to_snake(text: str) -> str:
    import keyword
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", "_", text)
    result = text.strip("_")[:60]
    if result and result[0].isdigit():
        result = f"_{result}"
    if keyword.iskeyword(result):
        result = f"{result}_tool"
    return result


def _escape_yaml(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _extract_core_action(description: str) -> str:
    desc_lower = description.lower()
    m = re.search(
        r"(?:that|which|to)\s+(\w+?)s?\s+(.+?)(?:\s+from|\s+in|\s+on|\s+into|\s+to|\s+using|$)",
        desc_lower,
    )
    if m:
        verb = m.group(1).rstrip("e")
        obj = m.group(2).strip()
        obj = re.sub(r"\s+(from|in|on|into|to|using|with|for).*$", "", obj)
        result = f"{verb} {obj}".strip()
        # If still too long, take only the first 3 meaningful words
        if len(result) > 30:
            words = result.split()[:3]
            result = " ".join(words)
        return result

    words = re.findall(r"[a-z]+", desc_lower)
    skip = {"a", "an", "the", "that", "which", "tool", "to", "for", "and", "or", "is", "it"}
    meaningful = [w for w in words if w not in skip][:3]
    return " ".join(meaningful)


# ---------------------------------------------------------------------------
# Inference helpers
# ---------------------------------------------------------------------------

def _detect_capability_ids(description: str) -> list[str]:
    desc_lower = description.lower()
    found: list[str] = []
    for keywords, cap_id in _CAPABILITY_MAP:
        for kw in keywords:
            if kw in desc_lower:
                if cap_id not in found:
                    found.append(cap_id)
                break
        if len(found) >= 2:
            break
    return found or ["code_analysis"]


def _infer_permissions(description: str) -> dict:
    desc_lower = description.lower()
    network = "none"
    filesystem = "none"
    for kw in _NETWORK_KEYWORDS:
        if kw in desc_lower:
            network = "unrestricted"
            break
    for kw in _FILESYSTEM_KEYWORDS:
        if kw in desc_lower:
            filesystem = "workspace_read"
            break
    return {
        "network": network,
        "filesystem": filesystem,
        "code_execution": "none",
        "data_access": "input_only",
        "user_approval": "never",
    }


def _generate_input_schema(description: str) -> tuple[str, str, str]:
    """Returns (param_name, param_type_str, param_description)."""
    desc_lower = description.lower()
    if any(kw in desc_lower for kw in ["url", "webpage", "website", "link", "http"]):
        return "url", "string", "The URL to process"
    if any(kw in desc_lower for kw in ["pdf", "csv", "file", "document", "spreadsheet", "excel", "image"]):
        return "file_path", "string", "Path to the input file"
    if any(kw in desc_lower for kw in ["search", "query", "find", "look up", "lookup"]):
        return "query", "string", "The search query"
    return "text", "string", "The input text to process"


def _generate_output_key(description: str) -> str:
    desc_lower = description.lower()
    if any(kw in desc_lower for kw in ["extract", "find", "list", "discover", "collect"]):
        return "results"
    if any(kw in desc_lower for kw in ["summarize", "summary", "translate", "convert", "generate", "draft", "rewrite"]):
        return "output"
    return "result"


def _output_is_array(description: str) -> bool:
    desc_lower = description.lower()
    return any(kw in desc_lower for kw in ["extract", "find", "list", "discover", "collect"])


# ---------------------------------------------------------------------------
# YAML builder (produces clean, correctly-indented YAML)
# ---------------------------------------------------------------------------

def _build_manifest_yaml(
    package_slug: str,
    package_name: str,
    module_name: str,
    tool_name: str,
    description: str,
    cap_ids: list[str],
    permissions: dict,
    param_name: str,
    param_desc: str,
    output_key: str,
    output_array: bool,
) -> str:
    cat = cap_ids[0].rsplit("_", 1)[0] if "_" in cap_ids[0] else "general"
    tags = ", ".join(f'"{c}"' for c in cap_ids[:1])

    # Build output_schema block (8-space base indent to match input_schema)
    if output_array:
        output_block = (
            f"        type: object\n"
            f"        properties:\n"
            f"          {output_key}:\n"
            f"            type: array\n"
            f"            items:\n"
            f"              type: string"
        )
    else:
        output_block = (
            f"        type: object\n"
            f"        properties:\n"
            f"          {output_key}:\n"
            f"            type: string"
        )

    return (
        f'manifest_version: "0.2"\n'
        f'package_id: "{package_slug}"\n'
        f'package_type: "toolpack"\n'
        f'name: "{package_name}"\n'
        f'summary: "{_escape_yaml(description[:120])}"\n'
        f'description: "{_escape_yaml(description)}"\n'
        f'version: "0.1.0"\n'
        f'runtime: "python"\n'
        f'install_mode: "package"\n'
        f'hosting_type: "agentnode_hosted"\n'
        f'entrypoint: "{module_name}.tool"\n'
        f"\n"
        f"capabilities:\n"
        f"  tools:\n"
        f'    - name: "{tool_name}"\n'
        f'      capability_id: "{cap_ids[0]}"\n'
        f'      description: "{_escape_yaml(description[:200])}"\n'
        f'      entrypoint: "{module_name}.tool:{tool_name}"\n'
        f"      input_schema:\n"
        f"        type: object\n"
        f"        properties:\n"
        f"          {param_name}:\n"
        f"            type: string\n"
        f'            description: "{param_desc}"\n'
        f"        required:\n"
        f'          - "{param_name}"\n'
        f"      output_schema:\n"
        f"{output_block}\n"
        f"  resources: []\n"
        f"  prompts: []\n"
        f"\n"
        f"compatibility:\n"
        f'  frameworks: ["generic"]\n'
        f'  python: ">=3.10"\n'
        f"  dependencies: []\n"
        f"\n"
        f"permissions:\n"
        f"  network:\n"
        f'    level: "{permissions["network"]}"\n'
        f"  filesystem:\n"
        f'    level: "{permissions["filesystem"]}"\n'
        f"  code_execution:\n"
        f'    level: "{permissions["code_execution"]}"\n'
        f"  data_access:\n"
        f'    level: "{permissions["data_access"]}"\n'
        f"  user_approval:\n"
        f'    required: "{permissions["user_approval"]}"\n'
        f"\n"
        f"tags: [{tags}]\n"
        f'categories: ["{cat}"]'
    )


# ---------------------------------------------------------------------------
# Code scaffold builder
# ---------------------------------------------------------------------------

def _build_code_scaffold(
    tool_name: str,
    module_name: str,
    description: str,
    param_name: str,
    param_desc: str,
    output_key: str,
) -> str:
    return (
        f'"""\n'
        f"{module_name} — AgentNode capability (ANP v0.2)\n"
        f"Auto-generated scaffold. Edit and extend as needed.\n"
        f'"""\n'
        f"\n"
        f"\n"
        f"def {tool_name}({param_name}: str) -> dict:\n"
        f'    """\n'
        f"    {description}\n"
        f"\n"
        f"    Args:\n"
        f"        {param_name}: {param_desc}\n"
        f"\n"
        f"    Returns:\n"
        f'        dict with key "{output_key}"\n'
        f'    """\n'
        f"    # TODO: Implement your logic here\n"
        f'    raise NotImplementedError("Replace this with your implementation")\n'
        f"\n"
        f"    # Example return:\n"
        f'    # return {{"{output_key}": ...}}\n'
    )


# ---------------------------------------------------------------------------
# Main generation function
# ---------------------------------------------------------------------------

def generate_capability(description: str) -> BuilderGenerateResponse:
    """Generate an ANP v0.2 manifest + code scaffold from a description."""

    core_action = _extract_core_action(description)
    tool_name = _to_snake(core_action) or "process_input"
    package_slug = _slugify(core_action) + "-pack" if core_action else "my-tool-pack"
    if not re.match(r"^[a-z][a-z0-9-]*-pack$", package_slug):
        package_slug = "custom-tool-pack"
    package_name = " ".join(w.capitalize() for w in package_slug.replace("-", " ").split())
    module_name = package_slug.replace("-", "_")

    cap_ids = _detect_capability_ids(description)
    permissions = _infer_permissions(description)
    param_name, _, param_desc = _generate_input_schema(description)
    output_key = _generate_output_key(description)
    output_array = _output_is_array(description)

    manifest_yaml = _build_manifest_yaml(
        package_slug=package_slug,
        package_name=package_name,
        module_name=module_name,
        tool_name=tool_name,
        description=description,
        cap_ids=cap_ids,
        permissions=permissions,
        param_name=param_name,
        param_desc=param_desc,
        output_key=output_key,
        output_array=output_array,
    )

    # Build JSON manifest for the publish flow
    if output_array:
        output_schema = {
            "type": "object",
            "properties": {output_key: {"type": "array", "items": {"type": "string"}}},
        }
    else:
        output_schema = {
            "type": "object",
            "properties": {output_key: {"type": "string"}},
        }

    manifest_json = {
        "manifest_version": "0.2",
        "package_id": package_slug,
        "package_type": "toolpack",
        "name": package_name,
        "summary": description[:120],
        "description": description,
        "version": "0.1.0",
        "runtime": "python",
        "install_mode": "package",
        "hosting_type": "agentnode_hosted",
        "entrypoint": f"{module_name}.tool",
        "capabilities": {
            "tools": [
                {
                    "name": tool_name,
                    "capability_id": cap_ids[0],
                    "description": description[:200],
                    "entrypoint": f"{module_name}.tool:{tool_name}",
                    "input_schema": {
                        "type": "object",
                        "properties": {param_name: {"type": "string", "description": param_desc}},
                        "required": [param_name],
                    },
                    "output_schema": output_schema,
                }
            ],
            "resources": [],
            "prompts": [],
        },
        "compatibility": {
            "frameworks": ["generic"],
            "python": ">=3.10",
            "dependencies": [],
        },
        "permissions": {
            "network": {"level": permissions["network"]},
            "filesystem": {"level": permissions["filesystem"]},
            "code_execution": {"level": permissions["code_execution"]},
            "data_access": {"level": permissions["data_access"]},
            "user_approval": {"required": permissions["user_approval"]},
        },
        "tags": cap_ids[:1],  # Only the primary capability as tag
        "categories": [cap_ids[0].rsplit("_", 1)[0] if "_" in cap_ids[0] else "general"],
    }

    code = _build_code_scaffold(
        tool_name=tool_name,
        module_name=module_name,
        description=description,
        param_name=param_name,
        param_desc=param_desc,
        output_key=output_key,
    )

    code_files = [
        CodeFile(path=f"src/{module_name}/tool.py", content=code),
        CodeFile(path=f"src/{module_name}/__init__.py", content=f'"""AgentNode package: {package_name}"""\n'),
        CodeFile(
            path="pyproject.toml",
            content=(
                "[build-system]\n"
                'requires = ["setuptools>=68.0"]\n'
                'build-backend = "setuptools.build_meta"\n'
                "\n"
                "[project]\n"
                f'name = "{package_slug}"\n'
                'version = "0.1.0"\n'
                f'description = "{_escape_yaml(description[:120])}"\n'
                'requires-python = ">=3.10"\n'
                "dependencies = []\n"
                "\n"
                "[tool.setuptools.packages.find]\n"
                'where = ["src"]\n'
            ),
        ),
    ]

    warnings: list[str] = []
    if tool_name == "process_input":
        warnings.append("Could not infer a specific tool name — consider renaming.")
    if cap_ids == ["code_analysis"]:
        warnings.append("No specific capability matched — defaulted to code_analysis.")

    metadata = BuilderMetadata(
        package_id=package_slug,
        package_name=package_name,
        tool_count=1,
        detected_capability_ids=cap_ids,
        detected_framework="generic",
        publish_ready=False,
        warnings=warnings + ["This is a scaffold — implement the tool logic before publishing."],
    )

    return BuilderGenerateResponse(
        manifest_yaml=manifest_yaml,
        manifest_json=manifest_json,
        code_files=code_files,
        metadata=metadata,
    )
