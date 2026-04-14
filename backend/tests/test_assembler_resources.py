"""Tests for resource-asset extraction in the assembler."""
from unittest.mock import MagicMock
from datetime import datetime, timezone

from app.packages.assembler import assemble_package_detail
from app.packages.schemas import ResourceBlock


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


class TestAssemblerResources:
    def test_resources_extracted_from_manifest_raw(self):
        manifest = {
            "capabilities": {
                "resources": [
                    {
                        "name": "api_spec",
                        "capability_id": "api_reference",
                        "uri": "resource://slack/openapi-spec",
                        "description": "Slack API specification",
                        "mime_type": "application/json",
                    },
                ],
            },
        }
        pkg = _mock_package()
        ver = _mock_version(manifest_raw=manifest)
        result = assemble_package_detail(pkg, ver)

        assert len(result.blocks.resources) == 1
        r = result.blocks.resources[0]
        assert isinstance(r, ResourceBlock)
        assert r.name == "api_spec"
        assert r.capability_id == "api_reference"
        assert r.uri == "resource://slack/openapi-spec"
        assert r.description == "Slack API specification"
        assert r.mime_type == "application/json"

    def test_no_resources_returns_empty_list(self):
        pkg = _mock_package()
        ver = _mock_version(manifest_raw={"capabilities": {"tools": []}})
        result = assemble_package_detail(pkg, ver)
        assert result.blocks.resources == []

    def test_resource_without_uri_skipped(self):
        manifest = {
            "capabilities": {
                "resources": [
                    {"name": "bad", "capability_id": "x"},
                    {"name": "good", "capability_id": "y", "uri": "https://example.com/data.json"},
                ],
            },
        }
        pkg = _mock_package()
        ver = _mock_version(manifest_raw=manifest)
        result = assemble_package_detail(pkg, ver)
        assert len(result.blocks.resources) == 1
        assert result.blocks.resources[0].name == "good"

    def test_resource_without_optional_fields(self):
        manifest = {
            "capabilities": {
                "resources": [{
                    "name": "minimal",
                    "capability_id": "x",
                    "uri": "resource://test/data",
                }],
            },
        }
        pkg = _mock_package()
        ver = _mock_version(manifest_raw=manifest)
        result = assemble_package_detail(pkg, ver)
        assert len(result.blocks.resources) == 1
        assert result.blocks.resources[0].description is None
        assert result.blocks.resources[0].mime_type is None

    def test_no_version_returns_empty_resources(self):
        pkg = _mock_package()
        result = assemble_package_detail(pkg, None)
        assert result.blocks.resources == []

    def test_multiple_resources(self):
        manifest = {
            "capabilities": {
                "resources": [
                    {"name": "a", "capability_id": "x", "uri": "resource://a/data"},
                    {"name": "b", "capability_id": "y", "uri": "https://b.example.com/api.json"},
                ],
            },
        }
        pkg = _mock_package()
        ver = _mock_version(manifest_raw=manifest)
        result = assemble_package_detail(pkg, ver)
        assert len(result.blocks.resources) == 2
        names = [r.name for r in result.blocks.resources]
        assert names == ["a", "b"]
