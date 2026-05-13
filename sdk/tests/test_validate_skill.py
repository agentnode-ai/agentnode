"""Tests for skill package validation in SDK."""
import json
from pathlib import Path

import pytest
import yaml

from agentnode_sdk.cli.validate import validate_package_dir


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    lock_file = tmp_path / "agentnode.lock"
    cfg_file.write_text(json.dumps({"version": "0.1"}))
    lock_file.write_text(json.dumps({"version": "0.1", "packages": {}}))
    monkeypatch.setenv("AGENTNODE_CONFIG", str(cfg_file))
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(lock_file))
    monkeypatch.setenv("AGENTNODE_CONFIG_DIR", str(tmp_path))
    return tmp_path


def _skill_manifest(**overrides) -> dict:
    m = {
        "package_id": "test-skill",
        "version": "1.0.0",
        "package_type": "skill",
        "summary": "A test skill for validation testing purposes.",
        "runtime": "none",
        "install_mode": "prompt_only",
        "capabilities": {
            "prompts": [
                {
                    "name": "test-prompt",
                    "description": "A test prompt",
                    "template": "SKILL.md",
                },
            ],
        },
        "permissions": {
            "network": {"level": "none"},
            "filesystem": {"level": "none"},
            "code_execution": {"level": "none"},
            "data_access": {"level": "input_only"},
            "user_approval": {"required": "never"},
        },
    }
    m.update(overrides)
    return m


def _write_skill(tmp_path: Path, manifest: dict, skill_md: str = "# Test Skill\nInstructions.", assets: dict | None = None) -> Path:
    """Write a skill package to tmp_path."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "agentnode.yaml").write_text(yaml.dump(manifest), encoding="utf-8")
    (pkg / "SKILL.md").write_text(skill_md, encoding="utf-8")
    if assets:
        for path, content in assets.items():
            full = pkg / path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")
    return pkg


class TestSkillValidation:
    def test_valid_skill_passes(self, tmp_path):
        pkg = _write_skill(tmp_path, _skill_manifest())
        result = validate_package_dir(pkg)
        assert not result.has_errors, [c for c in result.checks if not c.passed]

    def test_skill_package_type_accepted(self, tmp_path):
        pkg = _write_skill(tmp_path, _skill_manifest())
        result = validate_package_dir(pkg)
        type_checks = [c for c in result.checks if c.label == "package_type"]
        assert type_checks[0].passed
        assert type_checks[0].detail == "skill"

    def test_missing_skill_md_fails(self, tmp_path):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "agentnode.yaml").write_text(yaml.dump(_skill_manifest()), encoding="utf-8")
        result = validate_package_dir(pkg)
        skill_checks = [c for c in result.checks if c.label == "SKILL.md"]
        assert skill_checks and not skill_checks[0].passed

    def test_empty_skill_md_fails(self, tmp_path):
        pkg = _write_skill(tmp_path, _skill_manifest(), skill_md="")
        result = validate_package_dir(pkg)
        skill_checks = [c for c in result.checks if c.label == "SKILL.md"]
        assert skill_checks and not skill_checks[0].passed

    def test_skill_without_prompts_fails(self, tmp_path):
        m = _skill_manifest()
        m["capabilities"]["prompts"] = []
        pkg = _write_skill(tmp_path, m)
        result = validate_package_dir(pkg)
        prompt_checks = [c for c in result.checks if c.label == "prompts"]
        assert prompt_checks and not prompt_checks[0].passed

    def test_skill_prompt_missing_template_fails(self, tmp_path):
        m = _skill_manifest()
        m["capabilities"]["prompts"] = [{"name": "test"}]
        pkg = _write_skill(tmp_path, m)
        result = validate_package_dir(pkg)
        prompt_checks = [c for c in result.checks if c.label == "prompts"]
        assert prompt_checks and not prompt_checks[0].passed

    def test_skill_verification_mode_is_static(self, tmp_path):
        pkg = _write_skill(tmp_path, _skill_manifest())
        result = validate_package_dir(pkg)
        assert result.verification_mode == "static"
        assert result.max_tier == "verified"

    def test_skill_risk_level_low(self, tmp_path):
        pkg = _write_skill(tmp_path, _skill_manifest())
        result = validate_package_dir(pkg)
        assert result.risk_level == "low"

    def test_skill_with_valid_assets(self, tmp_path):
        m = _skill_manifest(assets=[
            {
                "id": "example-doc",
                "type": "document",
                "path": "assets/examples/example.md",
                "description": "An example",
            },
        ])
        pkg = _write_skill(tmp_path, m, assets={
            "assets/examples/example.md": "# Example\nContent here.",
        })
        result = validate_package_dir(pkg)
        assert not result.has_errors, [c for c in result.checks if not c.passed]

    def test_skill_with_missing_asset_file(self, tmp_path):
        m = _skill_manifest(assets=[
            {
                "id": "missing-doc",
                "type": "document",
                "path": "assets/missing.md",
                "description": "This file does not exist",
            },
        ])
        pkg = _write_skill(tmp_path, m)
        result = validate_package_dir(pkg)
        path_checks = [c for c in result.checks if "assets[0].path" in c.label]
        assert path_checks and not path_checks[0].passed

    def test_asset_path_traversal_rejected(self, tmp_path):
        m = _skill_manifest(assets=[
            {
                "id": "bad-path",
                "type": "document",
                "path": "../etc/passwd",
                "description": "Bad",
            },
        ])
        pkg = _write_skill(tmp_path, m)
        result = validate_package_dir(pkg)
        path_checks = [c for c in result.checks if "assets[0].path" in c.label]
        assert path_checks and not path_checks[0].passed

    def test_asset_duplicate_id_rejected(self, tmp_path):
        m = _skill_manifest(assets=[
            {"id": "same-id", "type": "document", "path": "a.md", "description": "First"},
            {"id": "same-id", "type": "document", "path": "b.md", "description": "Second"},
        ])
        pkg = _write_skill(tmp_path, m, assets={"a.md": "a", "b.md": "b"})
        result = validate_package_dir(pkg)
        dup_checks = [c for c in result.checks if "duplicate" in (c.detail or "").lower()]
        assert dup_checks

    def test_asset_invalid_type_rejected(self, tmp_path):
        m = _skill_manifest(assets=[
            {"id": "bad-type", "type": "binary", "path": "file.bin", "description": "Bad"},
        ])
        pkg = _write_skill(tmp_path, m)
        result = validate_package_dir(pkg)
        type_checks = [c for c in result.checks if "assets[0].type" in c.label]
        assert type_checks and not type_checks[0].passed


class TestSkillInit:
    def test_init_template_creates_files(self, tmp_path):
        from agentnode_sdk.cli.init import scaffold_package

        target = tmp_path / "my-skill"
        target.mkdir()
        created = scaffold_package(
            "skill", target,
            package_id="my-skill",
            name="My Skill",
            publisher="test-pub",
            summary="A test skill package for testing purposes.",
        )

        assert "agentnode.yaml" in created
        assert "SKILL.md" in created
        assert "assets/examples/example.md" in created

        manifest = yaml.safe_load((target / "agentnode.yaml").read_text())
        assert manifest["package_type"] == "skill"
        assert manifest["runtime"] == "none"
        assert manifest["install_mode"] == "prompt_only"
        assert manifest["manifest_version"] == "0.3"
        assert len(manifest["capabilities"]["prompts"]) >= 1

        skill_md = (target / "SKILL.md").read_text()
        assert "{{topic}}" in skill_md

        assert (target / "assets" / "examples" / "example.md").exists()

    def test_init_skill_validates(self, tmp_path):
        from agentnode_sdk.cli.init import scaffold_package

        target = tmp_path / "my-skill"
        target.mkdir()
        scaffold_package(
            "skill", target,
            package_id="my-skill",
            name="My Skill",
            publisher="test-pub",
            summary="A test skill package for testing purposes.",
        )

        result = validate_package_dir(target)
        assert not result.has_errors, [c for c in result.checks if not c.passed]


class TestSkillPublishDryRun:
    def test_build_artifact_includes_skill_files(self, tmp_path):
        from agentnode_sdk.cli.init import scaffold_package
        from agentnode_sdk.cli.publish import _build_artifact

        target = tmp_path / "my-skill"
        target.mkdir()
        scaffold_package(
            "skill", target,
            package_id="my-skill",
            name="My Skill",
            publisher="test-pub",
            summary="A test skill package for testing purposes.",
        )

        artifact_bytes, file_count = _build_artifact(target, "my-skill")
        assert artifact_bytes
        assert file_count >= 3

        import io
        import tarfile
        with tarfile.open(fileobj=io.BytesIO(artifact_bytes), mode="r:gz") as tar:
            names = tar.getnames()

        normalized = []
        for n in names:
            parts = n.split("/", 1)
            normalized.append(parts[1] if len(parts) > 1 else parts[0])

        assert "agentnode.yaml" in normalized
        assert "SKILL.md" in normalized
        assert any("example.md" in n for n in normalized)

    def test_build_artifact_excludes_py_files(self, tmp_path):
        from agentnode_sdk.cli.publish import _build_artifact

        target = tmp_path / "skill-with-py"
        target.mkdir()
        (target / "agentnode.yaml").write_text(yaml.dump({
            "package_id": "skill-with-py",
            "package_type": "skill",
            "version": "1.0.0",
            "capabilities": {"prompts": [{"name": "main", "template": "SKILL.md"}]},
            "assets": [],
        }), encoding="utf-8")
        (target / "SKILL.md").write_text("# Skill", encoding="utf-8")
        (target / "evil.py").write_text("import os", encoding="utf-8")

        artifact_bytes, file_count = _build_artifact(target, "skill-with-py")

        import io
        import tarfile
        with tarfile.open(fileobj=io.BytesIO(artifact_bytes), mode="r:gz") as tar:
            names = tar.getnames()

        normalized = [n.split("/", 1)[1] if "/" in n else n for n in names]
        assert "evil.py" not in normalized
        assert "SKILL.md" in normalized

    def test_build_artifact_excludes_undeclared_files(self, tmp_path):
        from agentnode_sdk.cli.publish import _build_artifact

        target = tmp_path / "skill-extra"
        target.mkdir()
        (target / "agentnode.yaml").write_text(yaml.dump({
            "package_id": "skill-extra",
            "package_type": "skill",
            "version": "1.0.0",
            "capabilities": {"prompts": [{"name": "main", "template": "SKILL.md"}]},
            "assets": [],
        }), encoding="utf-8")
        (target / "SKILL.md").write_text("# Skill", encoding="utf-8")
        (target / "readme.md").write_text("# Not declared", encoding="utf-8")
        (target / "notes.txt").write_text("Not declared either", encoding="utf-8")

        artifact_bytes, file_count = _build_artifact(target, "skill-extra")

        import io
        import tarfile
        with tarfile.open(fileobj=io.BytesIO(artifact_bytes), mode="r:gz") as tar:
            names = tar.getnames()

        normalized = [n.split("/", 1)[1] if "/" in n else n for n in names]
        file_names = [n for n in normalized if not n.endswith("/")]
        assert "readme.md" not in file_names
        assert "notes.txt" not in file_names
        assert "SKILL.md" in file_names
        assert "agentnode.yaml" in file_names

    def test_build_artifact_excludes_forbidden_extensions(self, tmp_path):
        from agentnode_sdk.cli.publish import _build_artifact

        target = tmp_path / "skill-js"
        target.mkdir()
        (target / "agentnode.yaml").write_text(yaml.dump({
            "package_id": "skill-js",
            "package_type": "skill",
            "version": "1.0.0",
            "capabilities": {"prompts": [{"name": "main", "template": "SKILL.md"}]},
            "assets": [
                {"id": "script", "type": "data", "path": "helper.js"},
            ],
        }), encoding="utf-8")
        (target / "SKILL.md").write_text("# Skill", encoding="utf-8")
        (target / "helper.js").write_text("console.log('hi')", encoding="utf-8")

        artifact_bytes, file_count = _build_artifact(target, "skill-js")

        import io
        import tarfile
        with tarfile.open(fileobj=io.BytesIO(artifact_bytes), mode="r:gz") as tar:
            names = tar.getnames()

        normalized = [n.split("/", 1)[1] if "/" in n else n for n in names]
        assert "helper.js" not in normalized

    def test_build_artifact_includes_declared_assets(self, tmp_path):
        from agentnode_sdk.cli.publish import _build_artifact

        target = tmp_path / "skill-assets"
        target.mkdir()
        assets_dir = target / "assets"
        assets_dir.mkdir()
        (target / "agentnode.yaml").write_text(yaml.dump({
            "package_id": "skill-assets",
            "package_type": "skill",
            "version": "1.0.0",
            "capabilities": {"prompts": [{"name": "main", "template": "SKILL.md"}]},
            "assets": [
                {"id": "doc", "type": "document", "path": "assets/guide.md"},
                {"id": "data", "type": "data", "path": "assets/config.json"},
            ],
        }), encoding="utf-8")
        (target / "SKILL.md").write_text("# Skill", encoding="utf-8")
        (assets_dir / "guide.md").write_text("# Guide", encoding="utf-8")
        (assets_dir / "config.json").write_text('{}', encoding="utf-8")

        artifact_bytes, file_count = _build_artifact(target, "skill-assets")

        import io
        import tarfile
        with tarfile.open(fileobj=io.BytesIO(artifact_bytes), mode="r:gz") as tar:
            names = tar.getnames()

        normalized = [n.split("/", 1)[1] if "/" in n else n for n in names]
        file_names = [n for n in normalized if not n.endswith("/")]
        assert "assets/guide.md" in file_names
        assert "assets/config.json" in file_names

    def test_non_skill_packages_not_filtered(self, tmp_path):
        """Toolpacks should not be affected by skill filtering."""
        from agentnode_sdk.cli.publish import _build_artifact

        target = tmp_path / "my-tool"
        target.mkdir()
        (target / "agentnode.yaml").write_text(yaml.dump({
            "package_id": "my-tool",
            "package_type": "toolpack",
            "version": "1.0.0",
        }), encoding="utf-8")
        (target / "tool.py").write_text("def run(): pass", encoding="utf-8")
        (target / "setup.py").write_text("# setup", encoding="utf-8")

        artifact_bytes, file_count = _build_artifact(target, "my-tool")

        import io
        import tarfile
        with tarfile.open(fileobj=io.BytesIO(artifact_bytes), mode="r:gz") as tar:
            names = tar.getnames()

        normalized = [n.split("/", 1)[1] if "/" in n else n for n in names]
        assert "tool.py" in normalized
        assert "setup.py" in normalized
