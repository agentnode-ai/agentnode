"""Tests for connector-block extraction in the assembler."""
from unittest.mock import MagicMock
from datetime import datetime, timezone

from app.packages.assembler import assemble_package_detail
from app.packages.schemas import ConnectorBlock


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


class TestAssemblerConnector:
    def test_connector_extracted_from_manifest_raw(self):
        manifest = {
            "connector": {
                "provider": "slack",
                "auth_type": "oauth2",
                "scopes": ["channels:read", "chat:write"],
                "token_refresh": True,
                "health_check": {
                    "endpoint": "https://slack.com/api/auth.test",
                    "interval_seconds": 300,
                },
                "rate_limits": {
                    "requests_per_minute": 60,
                },
            },
        }
        pkg = _mock_package()
        ver = _mock_version(manifest_raw=manifest)
        result = assemble_package_detail(pkg, ver)

        assert result.blocks.connector is not None
        c = result.blocks.connector
        assert isinstance(c, ConnectorBlock)
        assert c.provider == "slack"
        assert c.auth_type == "oauth2"
        assert c.scopes == ["channels:read", "chat:write"]
        assert c.token_refresh is True
        assert c.health_check_endpoint == "https://slack.com/api/auth.test"
        assert c.rate_limit_rpm == 60

    def test_no_connector_returns_none(self):
        pkg = _mock_package()
        ver = _mock_version(manifest_raw={"capabilities": {"tools": []}})
        result = assemble_package_detail(pkg, ver)
        assert result.blocks.connector is None

    def test_connector_without_optional_fields(self):
        manifest = {
            "connector": {
                "provider": "github",
            },
        }
        pkg = _mock_package()
        ver = _mock_version(manifest_raw=manifest)
        result = assemble_package_detail(pkg, ver)

        c = result.blocks.connector
        assert c is not None
        assert c.provider == "github"
        assert c.auth_type is None
        assert c.scopes == []
        assert c.token_refresh is False
        assert c.health_check_endpoint is None
        assert c.rate_limit_rpm is None

    def test_connector_without_provider_skipped(self):
        """Connector without provider is invalid — skip it."""
        manifest = {
            "connector": {
                "auth_type": "api_key",
            },
        }
        pkg = _mock_package()
        ver = _mock_version(manifest_raw=manifest)
        result = assemble_package_detail(pkg, ver)
        assert result.blocks.connector is None

    def test_no_version_returns_no_connector(self):
        pkg = _mock_package()
        result = assemble_package_detail(pkg, None)
        assert result.blocks.connector is None

    def test_connector_with_api_key_auth(self):
        manifest = {
            "connector": {
                "provider": "openai",
                "auth_type": "api_key",
                "health_check": {
                    "endpoint": "https://api.openai.com/v1/models",
                },
            },
        }
        pkg = _mock_package()
        ver = _mock_version(manifest_raw=manifest)
        result = assemble_package_detail(pkg, ver)

        c = result.blocks.connector
        assert c.provider == "openai"
        assert c.auth_type == "api_key"
        assert c.health_check_endpoint == "https://api.openai.com/v1/models"

    def test_connector_non_dict_ignored(self):
        """If connector is a string or other type, ignore it."""
        manifest = {"connector": "not-a-dict"}
        pkg = _mock_package()
        ver = _mock_version(manifest_raw=manifest)
        result = assemble_package_detail(pkg, ver)
        assert result.blocks.connector is None
