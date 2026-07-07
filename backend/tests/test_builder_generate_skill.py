"""Tests for the skills-only builder generate path."""

import pytest
from pydantic import ValidationError

from app.builder.ai import _extract_json, _response_from_skill_data
from app.builder.schemas import BuilderGenerateRequest
from app.builder.service import (
    build_skill_manifest,
    generate_skill_capability,
)
from app.packages.validator import validate_manifest


# ---------------------------------------------------------------------------
# Request schema: skills-only
# ---------------------------------------------------------------------------


def test_generate_request_defaults_to_skill():
    req = BuilderGenerateRequest(description="A skill that writes release notes")
    assert req.package_type == "skill"


@pytest.mark.parametrize("rejected", ["toolpack", "agent", "upgrade", "mcp"])
def test_generate_request_rejects_non_skill(rejected):
    with pytest.raises(ValidationError):
        BuilderGenerateRequest(
            description="A skill that writes release notes",
            package_type=rejected,
        )


# ---------------------------------------------------------------------------
# Shared manifest assembly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_skill_manifest_passes_validator():
    manifest = build_skill_manifest(
        package_id="release-notes-skill",
        name="Release Notes Skill",
        summary="Writes concise release notes from commit history.",
        description="Turns raw commit logs into readable release notes.",
    )
    # publisher is filled at publish time; the validator requires it
    manifest["publisher"] = "test-publisher"
    valid, errors, _warnings = await validate_manifest(manifest)
    assert valid, f"Unexpected errors: {errors}"


def test_build_skill_manifest_is_prompt_only():
    manifest = build_skill_manifest(
        package_id="my-skill",
        name="My Skill",
        summary="s",
        description="d",
    )
    assert manifest["package_type"] == "skill"
    assert manifest["runtime"] == "none"
    assert manifest["install_mode"] == "prompt_only"
    assert "entrypoint" not in manifest
    assert manifest["capabilities"]["tools"] == []
    prompts = manifest["capabilities"]["prompts"]
    assert len(prompts) == 1
    assert prompts[0]["template"] == "SKILL.md"
    assert prompts[0]["capability_id"] == "my_skill.prompt"
    for key in ("network", "filesystem", "code_execution"):
        assert manifest["permissions"][key]["level"] == "none"


# ---------------------------------------------------------------------------
# Heuristic generation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heuristic_skill_is_valid_and_prompt_only():
    result = generate_skill_capability(
        "A skill that summarizes webpages into three bullet points"
    )
    manifest = dict(result.manifest_json)
    manifest["publisher"] = "test-publisher"
    valid, errors, _warnings = await validate_manifest(manifest)
    assert valid, f"Unexpected errors: {errors}"

    assert result.metadata.tool_count == 0
    assert result.metadata.publish_ready is False
    assert result.metadata.warnings  # heuristic drafts carry a review warning

    assert len(result.code_files) == 1
    skill_md = result.code_files[0]
    assert skill_md.path == "SKILL.md"
    assert skill_md.content.startswith("# ")
    assert "## Instructions" in skill_md.content


def test_heuristic_handles_terse_description():
    result = generate_skill_capability("summarize meeting notes quickly")
    assert result.manifest_json["package_id"]
    assert result.manifest_json["package_type"] == "skill"
    assert result.code_files[0].content


# ---------------------------------------------------------------------------
# AI response mapping (pure function, no API call)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ai_data_maps_to_valid_skill():
    data = {
        "package_id": "email-tone-skill",
        "package_name": "Email Tone Skill",
        "summary": "Adjusts email drafts to a requested tone.",
        "description": "Rewrites email drafts to match a target tone.",
        "skill_md": "# Email Tone Skill\n\n## Instructions\n\n1. Read {{input}}.\n",
        "use_cases": ["Rewrite emails politely"],
        "warnings": [],
    }
    result = _response_from_skill_data(data)
    manifest = dict(result.manifest_json)
    manifest["publisher"] = "test-publisher"
    valid, errors, _warnings = await validate_manifest(manifest)
    assert valid, f"Unexpected errors: {errors}"

    assert result.metadata.publish_ready is True
    assert result.manifest_json["use_cases"] == ["Rewrite emails politely"]
    assert result.code_files[0].path == "SKILL.md"


def test_ai_data_without_skill_md_is_rejected():
    with pytest.raises(ValueError):
        _response_from_skill_data(
            {"package_id": "x-skill", "package_name": "X", "skill_md": "  "}
        )


def test_ai_data_normalizes_bad_package_id():
    result = _response_from_skill_data(
        {
            "package_id": "Email Tone!! Skill",
            "package_name": "Email Tone Skill",
            "summary": "s",
            "description": "d",
            "skill_md": "# X\n",
        }
    )
    assert result.manifest_json["package_id"] == "email-tone-skill"


def test_extract_json_strips_code_fences():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
