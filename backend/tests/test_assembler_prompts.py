"""Tests for prompt-asset extraction in the assembler."""
import pytest
from unittest.mock import MagicMock
from datetime import datetime, timezone

from app.packages.assembler import assemble_package_detail
from app.packages.schemas import PromptBlock, PromptArgumentBlock


def _mock_publisher(**overrides):
    pub = MagicMock()
    pub.slug = overrides.get("slug", "test-publisher")
    pub.display_name = overrides.get("display_name", "Test Publisher")
    pub.trust_level = overrides.get("trust_level", "verified")
    return pub


def _mock_package(publisher=None, **overrides):
    pkg = MagicMock()
    pkg.slug = overrides.get("slug", "test-pack")
    pkg.name = overrides.get("name", "Test Pack")
    pkg.package_type = overrides.get("package_type", "toolpack")
    pkg.summary = overrides.get("summary", "A test package")
    pkg.description = overrides.get("description", "Description")
    pkg.download_count = overrides.get("download_count", 0)
    pkg.install_count = overrides.get("install_count", 0)
    pkg.is_deprecated = overrides.get("is_deprecated", False)
    pkg.license_model = overrides.get("license_model", None)
    pkg.publisher = publisher or _mock_publisher()
    return pkg


def _mock_version(manifest_raw=None, **overrides):
    ver = MagicMock()
    ver.version_number = overrides.get("version_number", "1.0.0")
    ver.channel = overrides.get("channel", "stable")
    ver.published_at = overrides.get("published_at", datetime.now(timezone.utc))
    ver.security_reviewed_at = None
    ver.compatibility_reviewed_at = None
    ver.manually_reviewed_at = None
    ver.capabilities = overrides.get("capabilities", [])
    ver.upgrade_metadata = None
    ver.entrypoint = overrides.get("entrypoint", "test_pack.tool")
    ver.compatibility_rules = []
    ver.dependencies = []
    ver.permissions = None
    ver.runtime = "python"
    ver.signature = None
    ver.source_repo_url = None
    ver.security_findings = []
    ver.verification_status = "pending"
    ver.latest_verification_result = None
    ver.readme_md = None
    ver.file_list = None
    ver.env_requirements = None
    ver.use_cases = None
    ver.examples = None
    ver.tags = []
    ver.homepage_url = None
    ver.docs_url = None
    ver.source_url = None
    ver.manifest_raw = manifest_raw or {}
    return ver


class TestAssemblerPrompts:
    def test_prompts_extracted_from_manifest_raw(self):
        manifest = {
            "capabilities": {
                "prompts": [
                    {
                        "name": "summarize",
                        "capability_id": "text_summarization",
                        "template": "Summarize the following: {{text}}",
                        "description": "Summarize text",
                        "arguments": [
                            {"name": "text", "description": "Text to summarize", "required": True},
                            {"name": "style", "description": "Summary style"},
                        ],
                    },
                ],
            },
        }
        pkg = _mock_package()
        ver = _mock_version(manifest_raw=manifest)
        result = assemble_package_detail(pkg, ver)

        assert len(result.blocks.prompts) == 1
        prompt = result.blocks.prompts[0]
        assert isinstance(prompt, PromptBlock)
        assert prompt.name == "summarize"
        assert prompt.capability_id == "text_summarization"
        assert prompt.template == "Summarize the following: {{text}}"
        assert prompt.description == "Summarize text"
        assert len(prompt.arguments) == 2
        assert prompt.arguments[0].name == "text"
        assert prompt.arguments[0].required is True
        assert prompt.arguments[1].name == "style"
        assert prompt.arguments[1].required is False

    def test_no_prompts_returns_empty_list(self):
        pkg = _mock_package()
        ver = _mock_version(manifest_raw={"capabilities": {"tools": []}})
        result = assemble_package_detail(pkg, ver)
        assert result.blocks.prompts == []

    def test_prompt_without_template_skipped(self):
        manifest = {
            "capabilities": {
                "prompts": [
                    {"name": "bad", "capability_id": "x"},
                    {"name": "good", "capability_id": "y", "template": "Hello"},
                ],
            },
        }
        pkg = _mock_package()
        ver = _mock_version(manifest_raw=manifest)
        result = assemble_package_detail(pkg, ver)
        assert len(result.blocks.prompts) == 1
        assert result.blocks.prompts[0].name == "good"

    def test_prompt_without_arguments(self):
        manifest = {
            "capabilities": {
                "prompts": [{
                    "name": "greet",
                    "capability_id": "greeting",
                    "template": "Hello!",
                }],
            },
        }
        pkg = _mock_package()
        ver = _mock_version(manifest_raw=manifest)
        result = assemble_package_detail(pkg, ver)
        assert len(result.blocks.prompts) == 1
        assert result.blocks.prompts[0].arguments is None

    def test_no_version_returns_empty_prompts(self):
        pkg = _mock_package()
        result = assemble_package_detail(pkg, None)
        assert result.blocks.prompts == []

    def test_multiple_prompts(self):
        manifest = {
            "capabilities": {
                "prompts": [
                    {"name": "a", "capability_id": "x", "template": "Template A"},
                    {"name": "b", "capability_id": "y", "template": "Template B"},
                    {"name": "c", "capability_id": "z", "template": "Template C"},
                ],
            },
        }
        pkg = _mock_package()
        ver = _mock_version(manifest_raw=manifest)
        result = assemble_package_detail(pkg, ver)
        assert len(result.blocks.prompts) == 3
        names = [p.name for p in result.blocks.prompts]
        assert names == ["a", "b", "c"]
