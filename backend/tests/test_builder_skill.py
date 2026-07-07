"""Tests for the skill branch of the artifact builder (`build_skill_artifact`).

The builder's toolpack/agent path writes `manifest.json` and injects `tests/*.py`;
both are rejected by the skill artifact quality gate. These tests prove the skill
branch produces an artifact that the real gate accepts.
"""
import io
import tarfile

import pytest

from app.builder.router import build_skill_artifact
from app.packages.validator import validate_artifact_quality
from app.shared.exceptions import AppError


def _skill_manifest() -> dict:
    return {
        "manifest_version": "0.2",
        "package_id": "my-skill",
        "package_type": "skill",
        "name": "My Skill",
        "version": "1.0.0",
        "summary": "A prompt-only skill for testing purposes.",
        "runtime": "none",
        "install_mode": "prompt_only",
        "capabilities": {
            "prompts": [
                {"name": "main", "template": "SKILL.md", "capability_id": "my.skill"}
            ]
        },
        "permissions": {
            "network": {"level": "none"},
            "filesystem": {"level": "none"},
            "code_execution": {"level": "none"},
        },
    }


def _names(artifact: bytes) -> list[str]:
    with tarfile.open(fileobj=io.BytesIO(artifact), mode="r:gz") as tar:
        return tar.getnames()


def test_build_skill_artifact_passes_quality_gate():
    artifact = build_skill_artifact(
        "my-skill",
        _skill_manifest(),
        [("SKILL.md", "# My Skill\nDo the thing.")],
    )
    errors, _ = validate_artifact_quality(artifact, "my-skill", package_type="skill")
    assert not errors, f"Unexpected errors: {errors}"


def test_build_skill_artifact_layout():
    artifact = build_skill_artifact(
        "my-skill",
        _skill_manifest(),
        [("SKILL.md", "# My Skill")],
    )
    # Paths are prefixed with the slug (matches the CLI artifact convention).
    normalized = [n.split("/", 1)[-1] for n in _names(artifact)]
    assert "agentnode.yaml" in normalized
    assert "SKILL.md" in normalized
    # The two things the skill quality gate forbids must NOT be present.
    assert "manifest.json" not in normalized
    assert not any(n.endswith(".py") for n in normalized)


def test_build_skill_artifact_with_declared_asset_passes():
    manifest = _skill_manifest()
    manifest["assets"] = [{"path": "assets/logo.png"}]
    artifact = build_skill_artifact(
        "my-skill",
        manifest,
        [("SKILL.md", "# My Skill"), ("assets/logo.png", "binary-ish")],
    )
    errors, _ = validate_artifact_quality(artifact, "my-skill", package_type="skill")
    assert not errors, f"Unexpected errors: {errors}"


def test_build_skill_artifact_rejects_path_traversal():
    with pytest.raises(AppError):
        build_skill_artifact(
            "my-skill", _skill_manifest(), [("../evil.md", "x")]
        )
