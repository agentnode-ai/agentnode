"""Tests for skill install, load, and remove."""
import io
import json
import tarfile
from pathlib import Path

import pytest
import yaml

from agentnode_sdk.installer import (
    read_lockfile,
    update_lockfile,
    remove_skill_directory,
    _install_skill,
    _skills_dir,
)
from agentnode_sdk.skill import load_skill, SkillContext, SkillAsset


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    cfg_file = cfg_dir / "config.json"
    lock_file = tmp_path / "agentnode.lock"
    cfg_file.write_text(json.dumps({"version": "0.1"}))
    lock_file.write_text(json.dumps({"lockfile_version": "0.1", "packages": {}}))
    monkeypatch.setenv("AGENTNODE_CONFIG", str(cfg_file))
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(lock_file))
    monkeypatch.setenv("AGENTNODE_CONFIG_DIR", str(cfg_dir))
    return tmp_path


def _create_skill_dir(tmp_path: Path, slug: str = "test-skill") -> Path:
    """Create a skill directory with SKILL.md and assets."""
    from agentnode_sdk.config import config_dir
    skills = config_dir() / "skills"
    skill_dir = skills / slug
    skill_dir.mkdir(parents=True)

    (skill_dir / "SKILL.md").write_text(
        "# Test Skill\n\nYou are an expert at {{topic}}.\n\n## Instructions\n\nDo the thing.",
        encoding="utf-8",
    )
    (skill_dir / "agentnode.yaml").write_text(yaml.dump({
        "manifest_version": "0.3",
        "package_id": slug,
        "package_type": "skill",
        "name": "Test Skill",
        "publisher": "test-pub",
        "version": "1.0.0",
        "summary": "A test skill for testing purposes.",
        "runtime": "none",
        "install_mode": "prompt_only",
        "capabilities": {
            "prompts": [
                {"name": "main-prompt", "template": "SKILL.md", "description": "Main prompt"},
                {"name": "brief", "template": "BRIEF.md", "description": "Brief prompt"},
            ],
        },
        "assets": [
            {"id": "example-doc", "type": "document", "path": "assets/example.md", "description": "Example"},
        ],
    }), encoding="utf-8")

    (skill_dir / "BRIEF.md").write_text(
        "# Brief\n\nCreate a brief for {{topic}}.",
        encoding="utf-8",
    )

    assets_dir = skill_dir / "assets"
    assets_dir.mkdir()
    (assets_dir / "example.md").write_text("# Example\nSample content.", encoding="utf-8")

    return skill_dir


def _register_skill_in_lockfile(slug: str = "test-skill", version: str = "1.0.0"):
    """Add a skill entry to the lockfile."""
    update_lockfile(slug, {
        "version": version,
        "package_type": "skill",
        "runtime": "none",
        "install_mode": "prompt_only",
        "entrypoint": "",
        "capability_ids": [],
        "tools": [],
        "prompts": [
            {"name": "main-prompt", "template": "SKILL.md", "description": "Main prompt"},
        ],
        "resources": [],
        "assets": [
            {"id": "example-doc", "type": "document", "path": "assets/example.md", "description": "Example"},
        ],
        "artifact_hash": "sha256:abc123",
        "installed_at": "2026-05-12T00:00:00Z",
        "install_path": str(_skills_dir() / slug),
        "source": "sdk",
        "trust_level": "verified",
        "permissions": None,
        "connector": None,
        "agent": None,
    })


# ---------------------------------------------------------------------------
# load_skill tests
# ---------------------------------------------------------------------------

class TestLoadSkill:
    def test_load_returns_context(self, tmp_path):
        _create_skill_dir(tmp_path)
        _register_skill_in_lockfile()

        ctx = load_skill("test-skill")
        assert isinstance(ctx, SkillContext)
        assert ctx.slug == "test-skill"
        assert ctx.version == "1.0.0"

    def test_prompt_text_contains_content(self, tmp_path):
        _create_skill_dir(tmp_path)
        _register_skill_in_lockfile()

        ctx = load_skill("test-skill")
        assert "{{topic}}" in ctx.prompt_text
        assert "Test Skill" in ctx.prompt_text

    def test_render_substitutes_variables(self, tmp_path):
        _create_skill_dir(tmp_path)
        _register_skill_in_lockfile()

        ctx = load_skill("test-skill")
        rendered = ctx.render(topic="Python")
        assert "Python" in rendered
        assert "{{topic}}" not in rendered

    def test_multiple_prompts(self, tmp_path):
        _create_skill_dir(tmp_path)
        _register_skill_in_lockfile()

        ctx = load_skill("test-skill")
        assert "main-prompt" in ctx.prompts
        assert "brief" in ctx.prompts

    def test_render_named_prompt(self, tmp_path):
        _create_skill_dir(tmp_path)
        _register_skill_in_lockfile()

        ctx = load_skill("test-skill")
        rendered = ctx.render_prompt("brief", topic="testing")
        assert "testing" in rendered
        assert "Brief" in rendered

    def test_render_unknown_prompt_raises(self, tmp_path):
        _create_skill_dir(tmp_path)
        _register_skill_in_lockfile()

        ctx = load_skill("test-skill")
        with pytest.raises(KeyError, match="nonexistent"):
            ctx.render_prompt("nonexistent")

    def test_assets_loaded(self, tmp_path):
        _create_skill_dir(tmp_path)
        _register_skill_in_lockfile()

        ctx = load_skill("test-skill")
        assert len(ctx.assets) == 1
        assert ctx.assets[0].id == "example-doc"
        assert ctx.assets[0].type == "document"

    def test_asset_read_text(self, tmp_path):
        _create_skill_dir(tmp_path)
        _register_skill_in_lockfile()

        ctx = load_skill("test-skill")
        asset = ctx.asset("example-doc")
        content = asset.read_text()
        assert "Example" in content
        assert "Sample content" in content

    def test_asset_not_found_raises(self, tmp_path):
        _create_skill_dir(tmp_path)
        _register_skill_in_lockfile()

        ctx = load_skill("test-skill")
        with pytest.raises(KeyError, match="nonexistent"):
            ctx.asset("nonexistent")

    def test_not_installed_raises(self, tmp_path):
        with pytest.raises(ImportError, match="not installed"):
            load_skill("nonexistent-skill")

    def test_non_skill_package_raises(self, tmp_path):
        update_lockfile("my-tool", {
            "version": "1.0.0",
            "package_type": "toolpack",
        })
        with pytest.raises(TypeError, match="not a skill"):
            load_skill("my-tool")

    def test_missing_directory_raises(self, tmp_path):
        _register_skill_in_lockfile()
        with pytest.raises(FileNotFoundError, match="not found"):
            load_skill("test-skill")

    def test_metadata_contains_manifest(self, tmp_path):
        _create_skill_dir(tmp_path)
        _register_skill_in_lockfile()

        ctx = load_skill("test-skill")
        assert ctx.metadata.get("package_type") == "skill"
        assert ctx.metadata.get("manifest_version") == "0.3"


# ---------------------------------------------------------------------------
# remove_skill_directory tests
# ---------------------------------------------------------------------------

class TestRemoveSkill:
    def test_removes_directory(self, tmp_path):
        skill_dir = _create_skill_dir(tmp_path)
        assert skill_dir.exists()

        removed = remove_skill_directory("test-skill")
        assert removed
        assert not skill_dir.exists()

    def test_returns_false_if_absent(self, tmp_path):
        removed = remove_skill_directory("nonexistent-skill")
        assert not removed

    def test_cmd_remove_cleans_up_skill(self, tmp_path):
        from agentnode_sdk.cli.commands import cmd_remove

        _create_skill_dir(tmp_path)
        _register_skill_in_lockfile()

        exit_code = cmd_remove("test-skill", yes=True)
        assert exit_code == 0

        lock = read_lockfile()
        assert "test-skill" not in lock["packages"]

        skill_dir = _skills_dir() / "test-skill"
        assert not skill_dir.exists()


# ---------------------------------------------------------------------------
# _install_skill integration test
# ---------------------------------------------------------------------------

class TestInstallSkill:
    def test_install_extracts_to_skills_dir(self, tmp_path, monkeypatch):
        pkg_dir = tmp_path / "build"
        pkg_dir.mkdir()
        (pkg_dir / "SKILL.md").write_text("# My Skill\n\nInstructions for {{topic}}.")
        (pkg_dir / "agentnode.yaml").write_text(yaml.dump({
            "manifest_version": "0.3",
            "package_id": "my-new-skill",
            "package_type": "skill",
            "version": "1.0.0",
            "capabilities": {"prompts": [{"name": "default", "template": "SKILL.md"}]},
            "assets": [],
        }))

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for item in pkg_dir.rglob("*"):
                if item.is_file():
                    rel = item.relative_to(pkg_dir)
                    tar.add(str(item), arcname=f"my-new-skill/{rel.as_posix()}")

        artifact_path = tmp_path / "artifact.tar.gz"
        artifact_path.write_bytes(buf.getvalue())

        monkeypatch.setattr(
            "agentnode_sdk.installer.download_artifact",
            lambda url, dest, **kw: dest.write_bytes(artifact_path.read_bytes()),
        )

        result = _install_skill(
            slug="my-new-skill",
            version="1.0.0",
            artifact_url="https://fake.example.com/artifact.tar.gz",
            artifact_hash=None,
            previous_version=None,
            trust_level="verified",
            permissions=None,
            prompts=None,
            resources=None,
            assets=None,
        )

        assert result["installed"]
        assert result["slug"] == "my-new-skill"

        skill_dir = _skills_dir() / "my-new-skill"
        assert skill_dir.is_dir()
        assert (skill_dir / "SKILL.md").is_file()
        assert (skill_dir / "agentnode.yaml").is_file()

        lock = read_lockfile()
        entry = lock["packages"]["my-new-skill"]
        assert entry["package_type"] == "skill"
        assert entry["runtime"] == "none"
        assert entry["install_mode"] == "prompt_only"
        assert entry["install_path"] == str(skill_dir)

    def test_install_then_load(self, tmp_path, monkeypatch):
        pkg_dir = tmp_path / "build2"
        pkg_dir.mkdir()
        (pkg_dir / "SKILL.md").write_text("# Writing\n\nWrite about {{topic}} clearly.")
        (pkg_dir / "agentnode.yaml").write_text(yaml.dump({
            "manifest_version": "0.3",
            "package_id": "writing-skill",
            "package_type": "skill",
            "version": "1.0.0",
            "capabilities": {"prompts": [{"name": "writer", "template": "SKILL.md"}]},
            "assets": [],
        }))

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for item in pkg_dir.rglob("*"):
                if item.is_file():
                    rel = item.relative_to(pkg_dir)
                    tar.add(str(item), arcname=f"writing-skill/{rel.as_posix()}")

        artifact_path = tmp_path / "artifact2.tar.gz"
        artifact_path.write_bytes(buf.getvalue())

        monkeypatch.setattr(
            "agentnode_sdk.installer.download_artifact",
            lambda url, dest, **kw: dest.write_bytes(artifact_path.read_bytes()),
        )

        _install_skill(
            slug="writing-skill",
            version="1.0.0",
            artifact_url="https://fake.example.com/artifact.tar.gz",
            artifact_hash=None,
            previous_version=None,
            trust_level=None,
            permissions=None,
            prompts=None,
            resources=None,
            assets=None,
        )

        ctx = load_skill("writing-skill")
        assert ctx.slug == "writing-skill"
        assert "{{topic}}" in ctx.prompt_text
        rendered = ctx.render(topic="AI safety")
        assert "AI safety" in rendered
        assert "{{topic}}" not in rendered


# ---------------------------------------------------------------------------
# SkillAsset tests
# ---------------------------------------------------------------------------

class TestSkillAsset:
    def test_frozen_dataclass(self):
        asset = SkillAsset(id="test", type="document", path=Path("/fake"), description="Test")
        with pytest.raises(AttributeError):
            asset.id = "changed"

    def test_read_bytes(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_bytes(b"binary content")
        asset = SkillAsset(id="test", type="document", path=f, description="Test")
        assert asset.read_bytes() == b"binary content"


# ---------------------------------------------------------------------------
# skill list / show CLI tests
# ---------------------------------------------------------------------------

class TestSkillCLI:
    def test_skill_list_empty(self, tmp_path, capsys):
        from agentnode_sdk.cli.commands import cmd_skill_list
        exit_code = cmd_skill_list()
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "No skills installed" in out

    def test_skill_list_shows_skills(self, tmp_path, capsys):
        from agentnode_sdk.cli.commands import cmd_skill_list
        _create_skill_dir(tmp_path)
        _register_skill_in_lockfile()
        exit_code = cmd_skill_list()
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "test-skill" in out
        assert "1 skill" in out

    def test_skill_list_ignores_toolpacks(self, tmp_path, capsys):
        from agentnode_sdk.cli.commands import cmd_skill_list
        update_lockfile("my-tool", {
            "version": "1.0.0",
            "package_type": "toolpack",
        })
        exit_code = cmd_skill_list()
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "No skills installed" in out

    def test_skill_show(self, tmp_path, capsys):
        from agentnode_sdk.cli.commands import cmd_skill_show
        _create_skill_dir(tmp_path)
        _register_skill_in_lockfile()
        exit_code = cmd_skill_show("test-skill")
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "test-skill" in out
        assert "1.0.0" in out
        assert "prompt_only" in out
        assert "main-prompt" in out

    def test_skill_show_not_installed(self, tmp_path, capsys):
        from agentnode_sdk.cli.commands import cmd_skill_show
        exit_code = cmd_skill_show("nonexistent")
        assert exit_code == 1

    def test_skill_show_not_a_skill(self, tmp_path, capsys):
        from agentnode_sdk.cli.commands import cmd_skill_show
        update_lockfile("my-tool", {
            "version": "1.0.0",
            "package_type": "toolpack",
        })
        exit_code = cmd_skill_show("my-tool")
        assert exit_code == 1
        err = capsys.readouterr().err
        assert "not a skill" in err

    def test_skill_show_with_render(self, tmp_path, capsys):
        from agentnode_sdk.cli.commands import cmd_skill_show
        _create_skill_dir(tmp_path)
        _register_skill_in_lockfile()
        exit_code = cmd_skill_show("test-skill", render_vars={"topic": "Python"})
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "Python" in out
        assert "Rendered Prompt" in out

    def test_skill_show_raw(self, tmp_path, capsys):
        from agentnode_sdk.cli.commands import cmd_skill_show
        _create_skill_dir(tmp_path)
        _register_skill_in_lockfile()
        exit_code = cmd_skill_show("test-skill", raw=True)
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "{{topic}}" in out
        assert "test-skill\n=====" not in out

    def test_skill_show_raw_with_render(self, tmp_path, capsys):
        from agentnode_sdk.cli.commands import cmd_skill_show
        _create_skill_dir(tmp_path)
        _register_skill_in_lockfile()
        exit_code = cmd_skill_show("test-skill", render_vars={"topic": "Python"}, raw=True)
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "Python" in out
        assert "{{topic}}" not in out
        assert "=====" not in out

    def test_skill_show_raw_not_installed(self, tmp_path, capsys):
        from agentnode_sdk.cli.commands import cmd_skill_show
        exit_code = cmd_skill_show("nonexistent", raw=True)
        assert exit_code == 1

    def test_skill_show_assets(self, tmp_path, capsys):
        from agentnode_sdk.cli.commands import cmd_skill_show
        _create_skill_dir(tmp_path)
        _register_skill_in_lockfile()
        exit_code = cmd_skill_show("test-skill")
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "example-doc" in out
        assert "document" in out

    def test_render_arg_validation_no_equals(self, tmp_path, capsys):
        from agentnode_sdk.cli.main import main
        _create_skill_dir(tmp_path)
        _register_skill_in_lockfile()
        exit_code = main(["skill", "show", "test-skill", "--render", "topic"])
        assert exit_code == 1
        err = capsys.readouterr().err
        assert "expected KEY=VALUE" in err

    def test_render_arg_validation_empty_key(self, tmp_path, capsys):
        from agentnode_sdk.cli.main import main
        _create_skill_dir(tmp_path)
        _register_skill_in_lockfile()
        exit_code = main(["skill", "show", "test-skill", "--render", "=foo"])
        assert exit_code == 1
        err = capsys.readouterr().err
        assert "Invalid key" in err

    def test_skill_list_multiple(self, tmp_path, capsys):
        from agentnode_sdk.cli.commands import cmd_skill_list
        _create_skill_dir(tmp_path, slug="skill-a")
        _register_skill_in_lockfile(slug="skill-a")
        _create_skill_dir(tmp_path, slug="skill-b")
        _register_skill_in_lockfile(slug="skill-b")
        exit_code = cmd_skill_list()
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "skill-a" in out
        assert "skill-b" in out
        assert "2 skills" in out


# ---------------------------------------------------------------------------
# read_resource tests
# ---------------------------------------------------------------------------

def _make_runtime():
    from agentnode_sdk.runtime import AgentNodeRuntime
    rt = AgentNodeRuntime.__new__(AgentNodeRuntime)
    rt._minimum_trust_level = "verified"
    return rt


class TestReadResource:
    def test_returns_asset_content(self, tmp_path):
        _create_skill_dir(tmp_path)
        _register_skill_in_lockfile()
        rt = _make_runtime()
        content = rt.read_resource("agentnode://skills/test-skill/assets/example-doc")
        assert content is not None
        assert "Example" in content
        assert "Sample content" in content

    def test_unknown_uri_returns_none(self, tmp_path):
        rt = _make_runtime()
        assert rt.read_resource("https://example.com/foo") is None
        assert rt.read_resource("file:///etc/passwd") is None
        assert rt.read_resource("") is None

    def test_nonexistent_skill_returns_none(self, tmp_path):
        rt = _make_runtime()
        assert rt.read_resource("agentnode://skills/nonexistent/assets/doc") is None

    def test_nonexistent_asset_returns_none(self, tmp_path):
        _create_skill_dir(tmp_path)
        _register_skill_in_lockfile()
        rt = _make_runtime()
        assert rt.read_resource("agentnode://skills/test-skill/assets/no-such") is None

    def test_non_skill_package_returns_none(self, tmp_path):
        update_lockfile("my-tool", {
            "version": "1.0.0",
            "package_type": "toolpack",
            "assets": [{"id": "x", "path": "x.md"}],
        })
        rt = _make_runtime()
        assert rt.read_resource("agentnode://skills/my-tool/assets/x") is None

    def test_path_traversal_blocked(self, tmp_path):
        _create_skill_dir(tmp_path)
        secret = tmp_path / "secret.txt"
        secret.write_text("TOP SECRET")
        update_lockfile("test-skill", {
            "version": "1.0.0",
            "package_type": "skill",
            "install_path": str(_skills_dir() / "test-skill"),
            "assets": [
                {"id": "evil", "type": "document", "path": "../../secret.txt", "description": "Evil"},
            ],
        })
        rt = _make_runtime()
        assert rt.read_resource("agentnode://skills/test-skill/assets/evil") is None

    def test_absolute_asset_path_blocked(self, tmp_path):
        _create_skill_dir(tmp_path)
        secret = tmp_path / "secret.txt"
        secret.write_text("TOP SECRET")
        update_lockfile("test-skill", {
            "version": "1.0.0",
            "package_type": "skill",
            "install_path": str(_skills_dir() / "test-skill"),
            "assets": [
                {"id": "abs", "type": "document", "path": str(secret), "description": "Abs"},
            ],
        })
        rt = _make_runtime()
        assert rt.read_resource("agentnode://skills/test-skill/assets/abs") is None

    def test_missing_install_path_returns_none(self, tmp_path):
        update_lockfile("no-path", {
            "version": "1.0.0",
            "package_type": "skill",
            "assets": [{"id": "doc", "type": "document", "path": "doc.md", "description": "D"}],
        })
        rt = _make_runtime()
        assert rt.read_resource("agentnode://skills/no-path/assets/doc") is None

    def test_malformed_uri_returns_none(self, tmp_path):
        rt = _make_runtime()
        assert rt.read_resource("agentnode://skills/") is None
        assert rt.read_resource("agentnode://skills/slug/wrong/id") is None
        assert rt.read_resource("agentnode://other/path") is None


# ---------------------------------------------------------------------------
# Install revalidation — _validate_skill_contents
# ---------------------------------------------------------------------------

class TestValidateSkillContents:
    def test_valid_skill_passes(self, tmp_path):
        from agentnode_sdk.installer import _validate_skill_contents

        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "agentnode.yaml").write_text("package_type: skill")
        (pkg / "SKILL.md").write_text("# Skill")

        manifest = {
            "capabilities": {"prompts": [{"name": "main", "template": "SKILL.md"}]},
            "assets": [],
        }
        errors = _validate_skill_contents(pkg, manifest)
        assert not errors

    def test_declared_assets_pass(self, tmp_path):
        from agentnode_sdk.installer import _validate_skill_contents

        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "agentnode.yaml").write_text("package_type: skill")
        (pkg / "SKILL.md").write_text("# Skill")
        assets_dir = pkg / "assets"
        assets_dir.mkdir()
        (assets_dir / "example.md").write_text("# Example")
        (assets_dir / "data.json").write_text('{"key": "value"}')

        manifest = {
            "capabilities": {"prompts": [{"name": "main", "template": "SKILL.md"}]},
            "assets": [
                {"id": "ex", "type": "document", "path": "assets/example.md"},
                {"id": "d", "type": "data", "path": "assets/data.json"},
            ],
        }
        errors = _validate_skill_contents(pkg, manifest)
        assert not errors

    def test_undeclared_file_rejected(self, tmp_path):
        from agentnode_sdk.installer import _validate_skill_contents

        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "agentnode.yaml").write_text("package_type: skill")
        (pkg / "SKILL.md").write_text("# Skill")
        (pkg / "extra.md").write_text("# Extra")

        manifest = {
            "capabilities": {"prompts": [{"name": "main", "template": "SKILL.md"}]},
            "assets": [],
        }
        errors = _validate_skill_contents(pkg, manifest)
        assert any("Undeclared" in e and "extra.md" in e for e in errors)

    def test_py_file_rejected(self, tmp_path):
        from agentnode_sdk.installer import _validate_skill_contents

        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "agentnode.yaml").write_text("package_type: skill")
        (pkg / "SKILL.md").write_text("# Skill")
        (pkg / "evil.py").write_text("import os; os.system('rm -rf /')")

        manifest = {
            "capabilities": {"prompts": [{"name": "main", "template": "SKILL.md"}]},
            "assets": [{"id": "evil", "type": "document", "path": "evil.py"}],
        }
        errors = _validate_skill_contents(pkg, manifest)
        assert any(".py" in e for e in errors)

    def test_pyproject_rejected(self, tmp_path):
        from agentnode_sdk.installer import _validate_skill_contents

        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "agentnode.yaml").write_text("package_type: skill")
        (pkg / "SKILL.md").write_text("# Skill")
        (pkg / "pyproject.toml").write_text("[project]")

        manifest = {
            "capabilities": {"prompts": [{"name": "main", "template": "SKILL.md"}]},
            "assets": [{"id": "proj", "type": "data", "path": "pyproject.toml"}],
        }
        errors = _validate_skill_contents(pkg, manifest)
        assert any("Forbidden" in e and "pyproject" in e.lower() for e in errors)

    def test_shell_script_rejected(self, tmp_path):
        from agentnode_sdk.installer import _validate_skill_contents

        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "agentnode.yaml").write_text("package_type: skill")
        (pkg / "SKILL.md").write_text("# Skill")
        (pkg / "run.sh").write_text("#!/bin/bash")

        manifest = {
            "capabilities": {"prompts": [{"name": "main", "template": "SKILL.md"}]},
            "assets": [{"id": "run", "type": "document", "path": "run.sh"}],
        }
        errors = _validate_skill_contents(pkg, manifest)
        assert any(".sh" in e for e in errors)


class TestInstallSkillRevalidation:
    def test_install_rejects_py_file(self, tmp_path, monkeypatch):
        pkg_dir = tmp_path / "build-bad"
        pkg_dir.mkdir()
        (pkg_dir / "SKILL.md").write_text("# Skill")
        (pkg_dir / "agentnode.yaml").write_text(yaml.dump({
            "manifest_version": "0.3",
            "package_id": "bad-skill",
            "package_type": "skill",
            "version": "1.0.0",
            "capabilities": {"prompts": [{"name": "main", "template": "SKILL.md"}]},
            "assets": [{"id": "evil", "type": "document", "path": "evil.py"}],
        }))
        (pkg_dir / "evil.py").write_text("import os")

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for item in pkg_dir.rglob("*"):
                if item.is_file():
                    rel = item.relative_to(pkg_dir)
                    tar.add(str(item), arcname=f"bad-skill/{rel.as_posix()}")

        artifact_path = tmp_path / "bad.tar.gz"
        artifact_path.write_bytes(buf.getvalue())

        monkeypatch.setattr(
            "agentnode_sdk.installer.download_artifact",
            lambda url, dest, **kw: dest.write_bytes(artifact_path.read_bytes()),
        )

        with pytest.raises(RuntimeError, match="forbidden files"):
            _install_skill(
                slug="bad-skill",
                version="1.0.0",
                artifact_url="https://fake.example.com/bad.tar.gz",
                artifact_hash=None,
                previous_version=None,
                trust_level=None,
                permissions=None,
                prompts=None,
                resources=None,
                assets=None,
            )

        # Existing skill dir must not exist (fresh install that failed)
        assert not (_skills_dir() / "bad-skill").exists()

    def test_install_preserves_existing_on_failure(self, tmp_path, monkeypatch):
        """When a bad upgrade is attempted, the existing install survives."""
        # First: install a good skill
        good_dir = tmp_path / "build-good"
        good_dir.mkdir()
        (good_dir / "SKILL.md").write_text("# Good skill v1")
        (good_dir / "agentnode.yaml").write_text(yaml.dump({
            "manifest_version": "0.3",
            "package_id": "upgrade-test",
            "package_type": "skill",
            "version": "1.0.0",
            "capabilities": {"prompts": [{"name": "main", "template": "SKILL.md"}]},
            "assets": [],
        }))

        good_buf = io.BytesIO()
        with tarfile.open(fileobj=good_buf, mode="w:gz") as tar:
            for item in good_dir.rglob("*"):
                if item.is_file():
                    rel = item.relative_to(good_dir)
                    tar.add(str(item), arcname=f"upgrade-test/{rel.as_posix()}")
        good_artifact = tmp_path / "good.tar.gz"
        good_artifact.write_bytes(good_buf.getvalue())

        monkeypatch.setattr(
            "agentnode_sdk.installer.download_artifact",
            lambda url, dest, **kw: dest.write_bytes(good_artifact.read_bytes()),
        )
        _install_skill(
            slug="upgrade-test", version="1.0.0",
            artifact_url="https://fake/good.tar.gz",
            artifact_hash=None, previous_version=None,
            trust_level=None, permissions=None,
            prompts=None, resources=None, assets=None,
        )
        assert (_skills_dir() / "upgrade-test" / "SKILL.md").read_text() == "# Good skill v1"

        # Now: attempt bad upgrade
        bad_dir = tmp_path / "build-bad2"
        bad_dir.mkdir()
        (bad_dir / "SKILL.md").write_text("# Bad skill v2")
        (bad_dir / "agentnode.yaml").write_text(yaml.dump({
            "manifest_version": "0.3",
            "package_id": "upgrade-test",
            "package_type": "skill",
            "version": "2.0.0",
            "capabilities": {"prompts": [{"name": "main", "template": "SKILL.md"}]},
            "assets": [{"id": "x", "type": "document", "path": "exploit.sh"}],
        }))
        (bad_dir / "exploit.sh").write_text("#!/bin/bash\nrm -rf /")

        bad_buf = io.BytesIO()
        with tarfile.open(fileobj=bad_buf, mode="w:gz") as tar:
            for item in bad_dir.rglob("*"):
                if item.is_file():
                    rel = item.relative_to(bad_dir)
                    tar.add(str(item), arcname=f"upgrade-test/{rel.as_posix()}")
        bad_artifact = tmp_path / "bad2.tar.gz"
        bad_artifact.write_bytes(bad_buf.getvalue())

        monkeypatch.setattr(
            "agentnode_sdk.installer.download_artifact",
            lambda url, dest, **kw: dest.write_bytes(bad_artifact.read_bytes()),
        )

        with pytest.raises(RuntimeError, match="forbidden files"):
            _install_skill(
                slug="upgrade-test", version="2.0.0",
                artifact_url="https://fake/bad2.tar.gz",
                artifact_hash=None, previous_version="1.0.0",
                trust_level=None, permissions=None,
                prompts=None, resources=None, assets=None,
            )

        # v1 must still be intact
        assert (_skills_dir() / "upgrade-test" / "SKILL.md").read_text() == "# Good skill v1"
