"""Package scaffolding for `agentnode init`."""
from __future__ import annotations

import re
from pathlib import Path

from agentnode_sdk.cli.templates import TEMPLATES

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")


def scaffold_package(
    template_key: str,
    target_dir: Path,
    *,
    package_id: str,
    name: str,
    publisher: str = "your-publisher-slug",
    summary: str = "",
    description: str = "",
    tool_name: str = "",
    tool_description: str = "",
    capability_id: str = "",
    agent_goal: str = "",
) -> list[str]:
    """Generate package files from template. Returns list of created file paths."""
    template = TEMPLATES[template_key]
    module_name = package_id.replace("-", "_")

    if not tool_name:
        tool_name = module_name if template_key != "agent" else "run"
    if not summary:
        summary = f"A {template['label'].split('(')[0].strip().lower()} package"
    if not description:
        description = summary
    if not tool_description:
        tool_description = f"Main function of {name}"
    if not capability_id:
        capability_id = f"{module_name}_capability"
    if not agent_goal:
        agent_goal = f"Accomplish tasks using available tools"

    fmt = {
        "package_id": package_id,
        "name": name,
        "module_name": module_name,
        "publisher": publisher,
        "summary": summary,
        "description": description,
        "tool_name": tool_name,
        "tool_description": tool_description,
        "capability_id": capability_id,
        "agent_goal": agent_goal,
    }

    created: list[str] = []
    for rel_path_template, content_template in template["files"].items():
        rel_path = rel_path_template.format(**fmt)
        full_path = target_dir / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content_template.format(**fmt), encoding="utf-8")
        created.append(rel_path)

    return created


def prompt_template_choice() -> str | None:
    """Interactive template selection. Returns template key or None."""
    print()
    print("  Select package type:")
    print()
    choices = list(TEMPLATES.keys())
    for i, key in enumerate(choices, 1):
        tmpl = TEMPLATES[key]
        print(f"    {i}. {tmpl['label']}")
    print()

    try:
        raw = input("  Choice [1-4]: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None

    if not raw.isdigit() or int(raw) < 1 or int(raw) > len(choices):
        return None

    return choices[int(raw) - 1]


def prompt_package_details(template_key: str) -> dict | None:
    """Interactive prompts for package metadata. Returns dict or None on cancel."""
    try:
        print()
        pkg_id = input("  Package ID (e.g. my-tool-pack): ").strip()
        if not pkg_id or not SLUG_RE.match(pkg_id) or len(pkg_id) < 3:
            print("  Error: Package ID must be 3+ lowercase chars, digits, hyphens")
            return None

        name = input(f"  Display name [{pkg_id}]: ").strip() or pkg_id
        publisher = input("  Publisher slug [your-publisher-slug]: ").strip() or "your-publisher-slug"
        summary = input("  Summary (20-200 chars): ").strip()
        if not summary:
            summary = f"A {TEMPLATES[template_key]['label'].split('(')[0].strip().lower()} for AI agents"

        details: dict = {
            "package_id": pkg_id,
            "name": name,
            "publisher": publisher,
            "summary": summary,
        }

        if template_key == "agent":
            goal = input("  Agent goal: ").strip()
            if goal:
                details["agent_goal"] = goal
        else:
            tool = input(f"  Main tool function name [{pkg_id.replace('-', '_')}]: ").strip()
            if tool:
                details["tool_name"] = tool
            cap = input("  Capability ID (from taxonomy): ").strip()
            if cap:
                details["capability_id"] = cap

        return details
    except (EOFError, KeyboardInterrupt):
        return None
