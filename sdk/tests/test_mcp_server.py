"""Tests for AgentNode MCP server handlers."""
import json
import os
from pathlib import Path

import pytest
import yaml

from agentnode_sdk.installer import read_lockfile, update_lockfile, _skills_dir


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    cfg_file = cfg_dir / "config.json"
    lock_file = tmp_path / "agentnode.lock"
    cfg_file.write_text(json.dumps({"version": "0.1"}))
    lock_file.write_text(json.dumps({"version": "0.1", "packages": {}}))
    monkeypatch.setenv("AGENTNODE_CONFIG", str(cfg_file))
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(lock_file))
    monkeypatch.setenv("AGENTNODE_CONFIG_DIR", str(cfg_dir))
    return tmp_path


def _create_skill(tmp_path: Path, slug: str = "test-skill") -> Path:
    """Create a skill directory and register in lockfile."""
    from agentnode_sdk.config import config_dir
    skills = config_dir() / "skills"
    skill_dir = skills / slug
    skill_dir.mkdir(parents=True)

    (skill_dir / "SKILL.md").write_text(
        "# Test Skill\n\nYou are an expert at {{topic}}.\n\n## Instructions\n\nDo the thing.",
        encoding="utf-8",
    )
    (skill_dir / "BRIEF.md").write_text(
        "# Brief\n\nCreate a brief for {{topic}}.",
        encoding="utf-8",
    )
    (skill_dir / "agentnode.yaml").write_text(yaml.dump({
        "manifest_version": "0.3",
        "package_id": slug,
        "package_type": "skill",
        "name": "Test Skill",
        "version": "1.0.0",
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

    assets_dir = skill_dir / "assets"
    assets_dir.mkdir()
    (assets_dir / "example.md").write_text("# Example\nSample content.", encoding="utf-8")

    update_lockfile(slug, {
        "version": "1.0.0",
        "package_type": "skill",
        "runtime": "none",
        "install_mode": "prompt_only",
        "entrypoint": "",
        "capability_ids": [],
        "tools": [],
        "prompts": [
            {"name": "main-prompt", "template": "SKILL.md", "description": "Main prompt"},
            {"name": "brief", "template": "BRIEF.md", "description": "Brief prompt"},
        ],
        "resources": [],
        "assets": [
            {"id": "example-doc", "type": "document", "path": "assets/example.md", "description": "Example"},
        ],
        "artifact_hash": "sha256:abc123",
        "installed_at": "2026-05-12T00:00:00Z",
        "install_path": str(skill_dir),
        "source": "sdk",
        "trust_level": "verified",
        "permissions": None,
        "connector": None,
        "agent": None,
    })
    return skill_dir


# ---------------------------------------------------------------------------
# list_prompts
# ---------------------------------------------------------------------------

class TestListPrompts:
    def test_returns_namespaced_prompts(self, tmp_path):
        from agentnode_sdk.mcp_server import _build_prompt_list
        _create_skill(tmp_path)
        prompts = _build_prompt_list()
        names = [p.name for p in prompts]
        assert "test-skill/main-prompt" in names
        assert "test-skill/brief" in names

    def test_empty_when_no_skills(self, tmp_path):
        from agentnode_sdk.mcp_server import _build_prompt_list
        assert _build_prompt_list() == []

    def test_excludes_non_skill_packages(self, tmp_path):
        from agentnode_sdk.mcp_server import _build_prompt_list
        update_lockfile("my-tool", {
            "version": "1.0.0",
            "package_type": "toolpack",
            "prompts": [{"name": "greet", "template": "Hello"}],
        })
        assert _build_prompt_list() == []

    def test_infers_arguments_from_placeholders(self, tmp_path):
        from agentnode_sdk.mcp_server import _build_prompt_list
        _create_skill(tmp_path)
        prompts = _build_prompt_list()
        main = next(p for p in prompts if p.name == "test-skill/main-prompt")
        assert main.arguments is not None
        arg_names = [a.name for a in main.arguments]
        assert "topic" in arg_names

    def test_infers_arguments_from_whitespace_placeholders(self, tmp_path):
        from agentnode_sdk.mcp_server import _build_prompt_list
        from agentnode_sdk.config import config_dir

        skill_dir = _create_skill(tmp_path)
        (skill_dir / "SKILL.md").write_text(
            "Write about {{ topic }} for {{ audience }}.",
            encoding="utf-8",
        )
        prompts = _build_prompt_list()
        main = next(p for p in prompts if p.name == "test-skill/main-prompt")
        assert main.arguments is not None
        arg_names = [a.name for a in main.arguments]
        assert "topic" in arg_names
        assert "audience" in arg_names

    def test_uses_manifest_arguments_when_present(self, tmp_path):
        from agentnode_sdk.mcp_server import _build_prompt_list
        skill_dir = _create_skill(tmp_path)
        lock = read_lockfile()
        lock["packages"]["test-skill"]["prompts"] = [{
            "name": "main-prompt",
            "template": "SKILL.md",
            "description": "Main",
            "arguments": [
                {"name": "topic", "description": "The topic", "required": True},
            ],
        }]
        from agentnode_sdk._fileutil import atomic_write_json
        from agentnode_sdk.installer import _lockfile_path
        atomic_write_json(_lockfile_path(), lock)

        prompts = _build_prompt_list()
        main = prompts[0]
        assert main.arguments[0].name == "topic"
        assert main.arguments[0].description == "The topic"
        assert main.arguments[0].required is True

    def test_description_from_manifest(self, tmp_path):
        from agentnode_sdk.mcp_server import _build_prompt_list
        _create_skill(tmp_path)
        prompts = _build_prompt_list()
        main = next(p for p in prompts if p.name == "test-skill/main-prompt")
        assert main.description == "Main prompt"


# ---------------------------------------------------------------------------
# get_prompt (render)
# ---------------------------------------------------------------------------

class TestGetPrompt:
    def test_renders_with_arguments(self, tmp_path):
        from agentnode_sdk.mcp_server import _render_prompt
        _create_skill(tmp_path)
        result = _render_prompt("test-skill/main-prompt", {"topic": "Python"})
        assert len(result.messages) == 1
        text = result.messages[0].content.text
        assert "Python" in text
        assert "{{topic}}" not in text

    def test_returns_unrendered_without_arguments(self, tmp_path):
        from agentnode_sdk.mcp_server import _render_prompt
        _create_skill(tmp_path)
        result = _render_prompt("test-skill/main-prompt", None)
        text = result.messages[0].content.text
        assert "{{topic}}" in text

    def test_renders_named_prompt(self, tmp_path):
        from agentnode_sdk.mcp_server import _render_prompt
        _create_skill(tmp_path)
        result = _render_prompt("test-skill/brief", {"topic": "AI"})
        text = result.messages[0].content.text
        assert "AI" in text
        assert "Brief" in text

    def test_unknown_prompt_raises(self, tmp_path):
        from agentnode_sdk.mcp_server import _render_prompt
        from mcp.shared.exceptions import McpError
        _create_skill(tmp_path)
        with pytest.raises(McpError, match="not found"):
            _render_prompt("test-skill/nonexistent", None)

    def test_unknown_skill_raises(self, tmp_path):
        from agentnode_sdk.mcp_server import _render_prompt
        from mcp.shared.exceptions import McpError
        with pytest.raises(McpError, match="not installed"):
            _render_prompt("nonexistent/main", None)

    def test_invalid_name_format_raises(self, tmp_path):
        from agentnode_sdk.mcp_server import _render_prompt
        from mcp.shared.exceptions import McpError
        with pytest.raises(McpError, match="Invalid prompt name"):
            _render_prompt("no-slash", None)

    def test_non_skill_package_raises(self, tmp_path):
        from agentnode_sdk.mcp_server import _render_prompt
        from mcp.shared.exceptions import McpError
        update_lockfile("my-tool", {
            "version": "1.0.0",
            "package_type": "toolpack",
        })
        with pytest.raises(McpError, match="not a skill"):
            _render_prompt("my-tool/main", None)


# ---------------------------------------------------------------------------
# list_resources
# ---------------------------------------------------------------------------

class TestListResources:
    def test_returns_skill_assets(self, tmp_path):
        from agentnode_sdk.mcp_server import _build_resource_list
        _create_skill(tmp_path)
        resources = _build_resource_list()
        assert len(resources) == 1
        assert resources[0].name == "example-doc"
        assert str(resources[0].uri) == "agentnode://skills/test-skill/assets/example-doc"

    def test_mime_type_mapping(self, tmp_path):
        from agentnode_sdk.mcp_server import _build_resource_list
        _create_skill(tmp_path)
        resources = _build_resource_list()
        assert resources[0].mimeType == "text/markdown"

    def test_empty_when_no_skills(self, tmp_path):
        from agentnode_sdk.mcp_server import _build_resource_list
        assert _build_resource_list() == []

    def test_excludes_non_skill_packages(self, tmp_path):
        from agentnode_sdk.mcp_server import _build_resource_list
        update_lockfile("my-tool", {
            "version": "1.0.0",
            "package_type": "toolpack",
            "assets": [{"id": "x", "type": "document", "path": "x.md"}],
        })
        assert _build_resource_list() == []


# ---------------------------------------------------------------------------
# read_resource
# ---------------------------------------------------------------------------

class TestReadResource:
    def test_returns_asset_content(self, tmp_path):
        from agentnode_sdk.mcp_server import _read_resource
        _create_skill(tmp_path)
        content = _read_resource("agentnode://skills/test-skill/assets/example-doc")
        assert "Example" in content
        assert "Sample content" in content

    def test_unknown_uri_raises(self, tmp_path):
        from agentnode_sdk.mcp_server import _read_resource
        from mcp.shared.exceptions import McpError
        with pytest.raises(McpError, match="not found"):
            _read_resource("https://example.com/foo")

    def test_file_uri_raises(self, tmp_path):
        from agentnode_sdk.mcp_server import _read_resource
        from mcp.shared.exceptions import McpError
        with pytest.raises(McpError, match="not found"):
            _read_resource("file:///etc/passwd")

    def test_nonexistent_asset_raises(self, tmp_path):
        from agentnode_sdk.mcp_server import _read_resource
        from mcp.shared.exceptions import McpError
        _create_skill(tmp_path)
        with pytest.raises(McpError, match="not found"):
            _read_resource("agentnode://skills/test-skill/assets/nonexistent")

    def test_path_traversal_blocked(self, tmp_path):
        from agentnode_sdk.mcp_server import _read_resource
        from mcp.shared.exceptions import McpError
        _create_skill(tmp_path)
        secret = tmp_path / "secret.txt"
        secret.write_text("TOP SECRET")
        lock = read_lockfile()
        lock["packages"]["test-skill"]["assets"] = [
            {"id": "evil", "type": "document", "path": "../../secret.txt", "description": "Evil"},
        ]
        from agentnode_sdk._fileutil import atomic_write_json
        from agentnode_sdk.installer import _lockfile_path
        atomic_write_json(_lockfile_path(), lock)

        with pytest.raises(McpError, match="not found"):
            _read_resource("agentnode://skills/test-skill/assets/evil")
