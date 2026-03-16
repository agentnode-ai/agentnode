#!/usr/bin/env python3
"""Generate agentnode.yaml manifests for all starter-packs that don't have one.

Reads pyproject.toml + tool.py to extract metadata, then writes a valid
ANP manifest. Existing manifests are NOT overwritten.
"""
from __future__ import annotations

import ast
import inspect
import os
import re
import sys
import textwrap

STARTER_PACKS_DIR = os.path.join(os.path.dirname(__file__), "..", "starter-packs")

# Valid capability IDs from the taxonomy
CAPABILITY_MAP: dict[str, tuple[str, str]] = {
    # Pack slug keyword -> (capability_id, category)
    "pdf-extractor": ("pdf_extraction", "document-processing"),
    "pdf-reader": ("pdf_extraction", "document-processing"),
    "ocr-reader": ("document_parsing", "document-processing"),
    "document-summarizer": ("document_summary", "document-processing"),
    "document-redaction": ("document_parsing", "document-processing"),
    "citation-manager": ("citation_extraction", "document-processing"),
    "contract-review": ("document_parsing", "document-processing"),
    "word-document": ("document_parsing", "document-processing"),
    "powerpoint-generator": ("document_parsing", "document-processing"),
    "file-converter": ("document_parsing", "document-processing"),
    "markdown-notes": ("document_parsing", "document-processing"),
    "web-search": ("web_search", "web-and-browsing"),
    "webpage-extractor": ("webpage_extraction", "web-and-browsing"),
    "browser-automation": ("browser_navigation", "web-and-browsing"),
    "screenshot-capture": ("browser_navigation", "web-and-browsing"),
    "web-design": ("webpage_extraction", "web-and-browsing"),
    "news-aggregator": ("web_search", "web-and-browsing"),
    "arxiv-search": ("web_search", "web-and-browsing"),
    "seo-optimizer": ("webpage_extraction", "web-and-browsing"),
    "csv-analyzer": ("csv_analysis", "data-analysis"),
    "json-processor": ("json_processing", "data-analysis"),
    "regex-builder": ("data_cleaning", "data-analysis"),
    "sql-generator": ("sql_generation", "data-analysis"),
    "data-visualizer": ("chart_generation", "data-analysis"),
    "scientific-computing": ("statistics_analysis", "data-analysis"),
    "excel-processor": ("spreadsheet_parsing", "data-analysis"),
    "database-connector": ("sql_generation", "data-analysis"),
    "embedding-generator": ("embedding_generation", "memory-and-retrieval"),
    "semantic-search": ("semantic_search", "memory-and-retrieval"),
    "text-humanizer": ("tone_adjustment", "language"),
    "text-translator": ("translation", "language"),
    "copywriting": ("tone_adjustment", "language"),
    "prompt-engineer": ("tone_adjustment", "language"),
    "email-drafter": ("email_drafting", "communication"),
    "email-automation": ("email_drafting", "communication"),
    "slack-connector": ("email_summary", "communication"),
    "discord-connector": ("email_summary", "communication"),
    "telegram-connector": ("email_summary", "communication"),
    "whatsapp-connector": ("email_summary", "communication"),
    "notion-connector": ("task_management", "productivity"),
    "google-workspace": ("email_drafting", "communication"),
    "microsoft-365": ("email_drafting", "communication"),
    "scheduler": ("scheduling", "productivity"),
    "calendar-manager": ("scheduling", "productivity"),
    "task-manager": ("task_management", "productivity"),
    "project-board": ("task_management", "productivity"),
    "user-story-planner": ("task_management", "productivity"),
    "crm-connector": ("task_management", "productivity"),
    "social-media": ("email_drafting", "communication"),
    "youtube-analyzer": ("webpage_extraction", "web-and-browsing"),
    "code-executor": ("code_analysis", "development"),
    "code-linter": ("code_analysis", "development"),
    "code-refactor": ("code_analysis", "development"),
    "test-generator": ("code_analysis", "development"),
    "api-docs-generator": ("code_analysis", "development"),
    "secret-scanner": ("code_analysis", "development"),
    "security-audit": ("code_analysis", "development"),
    "ci-cd-runner": ("code_analysis", "development"),
    "github-integration": ("code_analysis", "development"),
    "gitlab-connector": ("code_analysis", "development"),
    "api-connector": ("web_search", "web-and-browsing"),
    "docker-manager": ("code_analysis", "development"),
    "kubernetes-manager": ("code_analysis", "development"),
    "aws-toolkit": ("code_analysis", "development"),
    "azure-toolkit": ("code_analysis", "development"),
    "cloud-deploy": ("code_analysis", "development"),
    "image-analyzer": ("document_parsing", "document-processing"),
    "ai-image-generator": ("chart_generation", "data-analysis"),
    "video-generator": ("chart_generation", "data-analysis"),
    "audio-processor": ("document_parsing", "document-processing"),
    "gif-creator": ("chart_generation", "data-analysis"),
    "speech-to-text": ("document_parsing", "document-processing"),
    "text-to-speech": ("tone_adjustment", "language"),
    "icon-generator": ("chart_generation", "data-analysis"),
    "home-automation": ("scheduling", "productivity"),
    "smart-lights": ("scheduling", "productivity"),
}


def read_pyproject(pack_dir: str) -> dict:
    """Parse pyproject.toml manually (no toml lib needed for simple cases)."""
    path = os.path.join(pack_dir, "pyproject.toml")
    if not os.path.exists(path):
        return {}

    with open(path, encoding="utf-8") as f:
        content = f.read()

    result: dict = {}

    # name
    m = re.search(r'^name\s*=\s*"([^"]+)"', content, re.MULTILINE)
    if m:
        result["name"] = m.group(1)

    # version
    m = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
    if m:
        result["version"] = m.group(1)

    # description
    m = re.search(r'^description\s*=\s*"([^"]+)"', content, re.MULTILINE)
    if m:
        result["description"] = m.group(1)

    # dependencies - handle multi-line array
    m = re.search(r'^dependencies\s*=\s*\[(.*?)\]', content, re.MULTILINE | re.DOTALL)
    if m:
        deps_raw = m.group(1)
        deps = re.findall(r'"([^"]+)"', deps_raw)
        result["dependencies"] = deps
    else:
        result["dependencies"] = []

    return result


def extract_run_info(pack_dir: str, pack_slug: str) -> dict:
    """Extract run() function info from tool.py using AST."""
    module_name = pack_slug.replace("-pack", "").replace("-", "_") + "_pack"
    tool_path = os.path.join(pack_dir, "src", module_name, "tool.py")

    if not os.path.exists(tool_path):
        return {"params": {}, "docstring": "", "name": "run"}

    with open(tool_path, encoding="utf-8") as f:
        source = f.read()

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {"params": {}, "docstring": "", "name": "run"}

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run":
            # Get docstring
            docstring = ast.get_docstring(node) or ""

            # Get parameters
            params = {}
            args = node.args
            defaults_offset = len(args.args) - len(args.defaults)

            for i, arg in enumerate(args.args):
                if arg.arg in ("self", "cls"):
                    continue
                param_info: dict = {"type": "string"}

                # Check annotation
                if arg.annotation:
                    ann_str = ast.unparse(arg.annotation)
                    if "int" in ann_str:
                        param_info["type"] = "integer"
                    elif "float" in ann_str:
                        param_info["type"] = "number"
                    elif "bool" in ann_str:
                        param_info["type"] = "boolean"
                    elif "list" in ann_str or "List" in ann_str:
                        param_info["type"] = "array"
                    elif "dict" in ann_str or "Dict" in ann_str:
                        param_info["type"] = "object"

                # Check if has default
                default_idx = i - defaults_offset
                if default_idx >= 0 and default_idx < len(args.defaults):
                    default_node = args.defaults[default_idx]
                    try:
                        param_info["default"] = ast.literal_eval(default_node)
                    except (ValueError, TypeError):
                        param_info["has_default"] = True
                else:
                    param_info["required"] = True

                params[arg.arg] = param_info

            return {"params": params, "docstring": docstring, "name": "run"}

    return {"params": {}, "docstring": "", "name": "run"}


def determine_permissions(pack_slug: str, deps: list[str]) -> dict:
    """Determine permission levels based on pack type and dependencies."""
    deps_lower = " ".join(d.lower() for d in deps)

    network = "none"
    filesystem = "none"
    code_exec = "none"
    data_access = "input_only"

    # Network access
    network_indicators = [
        "httpx", "requests", "aiohttp", "slack", "discord", "telegram",
        "github", "gitlab", "docker", "boto3", "azure", "google",
        "api", "connector", "webhook", "smtp", "imap",
    ]
    if any(ind in deps_lower or ind in pack_slug for ind in network_indicators):
        network = "unrestricted"

    # Filesystem
    fs_indicators = ["pdf", "word", "excel", "powerpoint", "file", "ocr", "image", "video", "audio"]
    if any(ind in pack_slug for ind in fs_indicators):
        filesystem = "workspace_read"

    # Generators write files
    write_indicators = ["generator", "creator", "writer"]
    if any(ind in pack_slug for ind in write_indicators):
        filesystem = "workspace_write"

    # Code execution
    exec_indicators = ["code-executor", "ci-cd", "docker", "kubernetes"]
    if any(ind in pack_slug for ind in exec_indicators):
        code_exec = "shell"
    elif "subprocess" in deps_lower or "code-linter" in pack_slug or "code-refactor" in pack_slug:
        code_exec = "limited_subprocess"

    # Browser
    if "browser" in pack_slug or "screenshot" in pack_slug or "playwright" in deps_lower:
        network = "unrestricted"
        filesystem = "temp"

    return {
        "network": network,
        "filesystem": filesystem,
        "code_execution": code_exec,
        "data_access": data_access,
    }


def yaml_str(s: str) -> str:
    """Quote a string for YAML if needed."""
    if not s:
        return '""'
    if any(c in s for c in ":{}\n\"'") or s.startswith(("- ", "# ")):
        escaped = s.replace('"', '\\"')
        return f'"{escaped}"'
    return f'"{s}"'


def generate_manifest(pack_dir: str, pack_slug: str) -> str:
    """Generate agentnode.yaml content for a pack."""
    pyproject = read_pyproject(pack_dir)
    run_info = extract_run_info(pack_dir, pack_slug)

    name = pyproject.get("name", pack_slug)
    # Convert slug to human name
    human_name = " ".join(
        w.capitalize() for w in pack_slug.replace("-pack", "").split("-")
    ) + " Pack"

    version = pyproject.get("version", "1.0.0")
    description = pyproject.get("description", f"AgentNode pack: {pack_slug}")
    summary = description[:200] if len(description) <= 200 else description[:197] + "..."

    # Module/entrypoint
    module_name = pack_slug.replace("-pack", "").replace("-", "_") + "_pack"
    entrypoint = f"{module_name}.tool"

    # Capability
    cap_key = pack_slug.replace("-pack", "")
    cap_id, category = CAPABILITY_MAP.get(cap_key, ("code_analysis", "development"))

    # Tool name from capability
    tool_name = cap_id

    # Build input_schema from run() params
    properties = {}
    required = []
    for pname, pinfo in run_info["params"].items():
        prop: dict = {"type": pinfo.get("type", "string")}
        if "default" in pinfo and pinfo["default"] is not None:
            prop["default"] = pinfo["default"]
        properties[pname] = prop
        if pinfo.get("required"):
            required.append(pname)

    # Permissions
    deps = pyproject.get("dependencies", [])
    perms = determine_permissions(pack_slug, deps)

    # Tags
    tags = [w for w in pack_slug.replace("-pack", "").split("-")]

    # Docstring first line as tool description
    docstring = run_info.get("docstring", "")
    tool_desc = docstring.split("\n")[0].strip() if docstring else description

    # Build YAML
    lines = []
    lines.append(f'manifest_version: "0.1"')
    lines.append(f'package_id: "{pack_slug}"')
    lines.append(f'package_type: "toolpack"')
    lines.append(f'name: {yaml_str(human_name)}')
    lines.append(f'publisher: "agentnode"')
    lines.append(f'version: "{version}"')
    lines.append(f'summary: {yaml_str(summary)}')
    lines.append(f'description: {yaml_str(description)}')
    lines.append(f'runtime: "python"')
    lines.append(f'install_mode: "package"')
    lines.append(f'hosting_type: "agentnode_hosted"')
    lines.append(f'entrypoint: "{entrypoint}"')
    lines.append("")

    # Capabilities
    lines.append("capabilities:")
    lines.append("  tools:")
    lines.append(f'    - name: "{tool_name}"')
    lines.append(f'      capability_id: "{cap_id}"')
    lines.append(f'      description: {yaml_str(tool_desc)}')
    lines.append("      input_schema:")
    lines.append('        type: "object"')
    lines.append("        properties:")
    if properties:
        for pname, prop in properties.items():
            default_str = ""
            if "default" in prop:
                dval = prop["default"]
                if isinstance(dval, str):
                    default_str = f', default: "{dval}"'
                elif isinstance(dval, bool):
                    default_str = f", default: {'true' if dval else 'false'}"
                elif dval is None:
                    default_str = ""
                else:
                    default_str = f", default: {dval}"
            lines.append(f'          {pname}: {{type: "{prop["type"]}"{default_str}}}')
    else:
        lines.append('          input: {type: "string"}')
    if required:
        lines.append(f"        required: [{', '.join(repr(r) for r in required)}]")
    lines.append("  resources: []")
    lines.append("  prompts: []")
    lines.append("")

    # Tags and categories
    lines.append(f'tags: [{", ".join(yaml_str(t) for t in tags)}]')
    lines.append(f'categories: ["{category}"]')
    lines.append("")

    # Compatibility
    lines.append("compatibility:")
    lines.append('  frameworks: ["langchain", "crewai", "generic"]')
    lines.append('  python: ">=3.10"')
    if deps:
        lines.append("  dependencies:")
        for dep in deps:
            lines.append(f'    - "{dep}"')
    lines.append("")

    # Permissions
    lines.append("permissions:")
    lines.append(f'  network: {{level: "{perms["network"]}"}}')
    lines.append(f'  filesystem: {{level: "{perms["filesystem"]}"}}')
    lines.append(f'  code_execution: {{level: "{perms["code_execution"]}"}}')
    lines.append(f'  data_access: {{level: "{perms["data_access"]}"}}')
    lines.append('  user_approval: {required: "never"}')
    lines.append("")

    # Upgrade metadata
    lines.append(f'upgrade_roles: ["{cap_id}"]')
    lines.append("recommended_for:")
    lines.append(f'  - agent_type: "general-assistant"')
    lines.append(f'    missing_capability: "{cap_id}"')
    lines.append("")

    # Install & policy
    lines.append('install_strategy: "local"')
    lines.append('fallback_behavior: "skip"')
    lines.append("policy_requirements:")
    lines.append('  min_trust_level: "unverified"')
    lines.append("  requires_approval: false")
    lines.append("")

    # Security
    lines.append("security:")
    lines.append("  signature: null")
    lines.append("  provenance:")
    lines.append("    source_repo: null")
    lines.append("    commit: null")
    lines.append("    build_system: null")
    lines.append("")

    # Support
    lines.append("support:")
    lines.append(f'  homepage: "https://agentnode.net/packages/{pack_slug}"')
    lines.append(f'  issues: "https://github.com/agentnode-ai/agentnode/issues"')
    lines.append("")

    lines.append('deprecation_policy: "6-months-notice"')

    return "\n".join(lines) + "\n"


def main():
    packs_dir = os.path.abspath(STARTER_PACKS_DIR)
    if not os.path.isdir(packs_dir):
        print(f"Starter packs directory not found: {packs_dir}", file=sys.stderr)
        sys.exit(1)

    created = 0
    skipped = 0
    errors = 0

    for entry in sorted(os.listdir(packs_dir)):
        pack_dir = os.path.join(packs_dir, entry)
        if not os.path.isdir(pack_dir):
            continue
        if not entry.endswith("-pack"):
            continue

        manifest_path = os.path.join(pack_dir, "agentnode.yaml")
        if os.path.exists(manifest_path):
            print(f"  SKIP {entry} (manifest exists)")
            skipped += 1
            continue

        try:
            content = generate_manifest(pack_dir, entry)
            with open(manifest_path, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"  CREATE {entry}/agentnode.yaml")
            created += 1
        except Exception as e:
            print(f"  ERROR {entry}: {e}", file=sys.stderr)
            errors += 1

    print(f"\nDone: {created} created, {skipped} skipped, {errors} errors")


if __name__ == "__main__":
    main()
