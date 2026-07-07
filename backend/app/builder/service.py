"""Heuristic skill builder — generates ANP skill manifests and SKILL.md drafts
from natural-language descriptions without requiring an external AI API.

The builder is skills-only by product decision: a described capability becomes a
prompt-only skill package (agentnode.yaml + SKILL.md). Code-based packages
(toolpacks, agents) are authored via import or manual upload on /publish instead.
"""

from __future__ import annotations

import re

import yaml

from app.builder.schemas import (
    BuilderGenerateResponse,
    BuilderMetadata,
    CodeFile,
)

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
    skip = {
        "a",
        "an",
        "the",
        "that",
        "which",
        "tool",
        "skill",
        "to",
        "for",
        "and",
        "or",
        "is",
        "it",
    }
    meaningful = [w for w in words if w not in skip][:3]
    return " ".join(meaningful)


# ---------------------------------------------------------------------------
# Manifest assembly (shared by the heuristic and AI paths)
# ---------------------------------------------------------------------------


def build_skill_manifest(
    package_id: str,
    name: str,
    summary: str,
    description: str,
    use_cases: list[str] | None = None,
) -> dict:
    """Assemble a publish-ready ANP skill manifest.

    Field shape mirrors the web publish form's skill manifest
    (web/src/app/publish/lib/manifest.ts) so builder output stays
    interchangeable with manually authored skills.
    """
    cap_id = f"{package_id.replace('-', '_')}.prompt"
    manifest: dict = {
        "manifest_version": "0.2",
        "package_id": package_id,
        "package_type": "skill",
        "name": name,
        "publisher": "",
        "version": "1.0.0",
        "summary": summary,
        "description": description,
        "runtime": "none",
        "install_mode": "prompt_only",
        "hosting_type": "agentnode_hosted",
        "capabilities": {
            "tools": [],
            "resources": [],
            "prompts": [
                {
                    "name": "main",
                    "capability_id": cap_id,
                    "template": "SKILL.md",
                    "description": summary,
                }
            ],
        },
        "compatibility": {"frameworks": ["generic"]},
        "permissions": {
            "network": {"level": "none"},
            "filesystem": {"level": "none"},
            "code_execution": {"level": "none"},
            "data_access": {"level": "input_only"},
            "user_approval": {"required": "never"},
        },
    }
    if use_cases:
        manifest["use_cases"] = use_cases
    return manifest


def skill_manifest_yaml(manifest: dict) -> str:
    return yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True)


# ---------------------------------------------------------------------------
# Heuristic generation (fallback when no AI API key is configured)
# ---------------------------------------------------------------------------

_SKILL_MD_TEMPLATE = """\
# {name}

{description}

## When to use this skill

Use this skill when the task involves: {core}.

## Instructions

1. Read the provided input ({{{{input}}}}) carefully.
2. Apply the skill as described: {description}
3. Present the result clearly and concisely.

## Guidelines

- Stay focused on the task described above.
- If required information is missing, ask for it instead of guessing.

## Output format

Provide the result as structured Markdown.
"""


def generate_skill_capability(description: str) -> BuilderGenerateResponse:
    """Generate a draft skill package from a description, without AI.

    Produces a valid manifest and a SKILL.md scaffold that embeds the
    description. Marked not publish-ready: the instructions need a human pass.
    """
    core = _extract_core_action(description) or "the described task"
    name = " ".join(w.capitalize() for w in core.split()[:4]) or "Custom Skill"
    if not name.lower().endswith("skill"):
        name = f"{name} Skill"
    package_id = _slugify(name) or "custom-skill"

    skill_md = _SKILL_MD_TEMPLATE.format(
        name=name, description=description.strip(), core=core
    )
    manifest = build_skill_manifest(
        package_id=package_id,
        name=name,
        summary=description.strip()[:200],
        description=description.strip(),
    )

    metadata = BuilderMetadata(
        package_id=package_id,
        package_name=name,
        tool_count=0,
        detected_capability_ids=[],
        detected_framework="generic",
        publish_ready=False,
        warnings=[
            "Heuristic draft — review and refine the SKILL.md instructions "
            "before publishing."
        ],
    )

    return BuilderGenerateResponse(
        manifest_yaml=skill_manifest_yaml(manifest),
        manifest_json=manifest,
        code_files=[CodeFile(path="SKILL.md", content=skill_md)],
        metadata=metadata,
    )
