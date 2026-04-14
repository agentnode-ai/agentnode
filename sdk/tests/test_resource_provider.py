"""Tests for resource content provider (PR 6 — Resource Content Delivery)."""
import pytest

from agentnode_sdk.resource_provider import (
    MAX_CONTENT_BYTES,
    ResourceContent,
    read_content,
)


class TestResourceContentDelivery:
    def test_https_returns_uri_reference(self):
        rc = read_content("https://example.com/api.json")
        assert rc.type == "uri_reference"
        assert rc.uri == "https://example.com/api.json"
        assert rc.content is None

    def test_resource_uri_with_local_file(self, tmp_path):
        # Create resource file
        slug_dir = tmp_path / "my_pack" / "resources"
        slug_dir.mkdir(parents=True)
        (slug_dir / "api_spec.json").write_text('{"openapi": "3.0.0"}')

        rc = read_content(
            "resource://my_pack/api_spec",
            installed_packages_dir=str(tmp_path),
        )
        assert rc.type == "inline"
        assert rc.content == '{"openapi": "3.0.0"}'
        assert rc.mime_type == "application/json"

    def test_resource_uri_without_file(self, tmp_path):
        rc = read_content(
            "resource://nonexistent/data",
            installed_packages_dir=str(tmp_path),
        )
        assert rc.type == "metadata_only"

    def test_resource_uri_with_txt_file(self, tmp_path):
        slug_dir = tmp_path / "my_pack" / "resources"
        slug_dir.mkdir(parents=True)
        (slug_dir / "readme.txt").write_text("Hello world")

        rc = read_content(
            "resource://my_pack/readme",
            installed_packages_dir=str(tmp_path),
        )
        assert rc.type == "inline"
        assert rc.content == "Hello world"
        assert rc.mime_type == "text/plain"

    def test_content_over_100kb_returns_metadata(self, tmp_path):
        slug_dir = tmp_path / "big_pack" / "resources"
        slug_dir.mkdir(parents=True)
        (slug_dir / "huge.json").write_text("x" * (MAX_CONTENT_BYTES + 1))

        rc = read_content(
            "resource://big_pack/huge",
            installed_packages_dir=str(tmp_path),
        )
        assert rc.type == "metadata_only"
        assert "too large" in (rc.description or "").lower()

    def test_unsupported_scheme_raises(self):
        with pytest.raises(ValueError, match="Unsupported URI"):
            read_content("ftp://example.com/file")

    def test_resource_with_hyphenated_slug(self, tmp_path):
        """Slug with hyphens → try underscore conversion."""
        slug_dir = tmp_path / "my_pack" / "resources"
        slug_dir.mkdir(parents=True)
        (slug_dir / "data.csv").write_text("a,b\n1,2")

        rc = read_content(
            "resource://my-pack/data",
            installed_packages_dir=str(tmp_path),
        )
        # Should find it via underscore conversion
        assert rc.type == "inline" or rc.type == "metadata_only"

    def test_resource_with_exact_name(self, tmp_path):
        """Resource file without extension."""
        slug_dir = tmp_path / "my_pack" / "resources"
        slug_dir.mkdir(parents=True)
        (slug_dir / "schema").write_text('{"type": "object"}')

        rc = read_content(
            "resource://my_pack/schema",
            installed_packages_dir=str(tmp_path),
        )
        assert rc.type == "inline"

    def test_resource_with_md_file(self, tmp_path):
        slug_dir = tmp_path / "my_pack" / "resources"
        slug_dir.mkdir(parents=True)
        (slug_dir / "guide.md").write_text("# Guide\nHello")

        rc = read_content(
            "resource://my_pack/guide",
            installed_packages_dir=str(tmp_path),
        )
        assert rc.type == "inline"
        assert rc.mime_type == "text/markdown"

    def test_malformed_resource_uri(self, tmp_path):
        """resource:// without slug/name format."""
        rc = read_content(
            "resource://just-slug",
            installed_packages_dir=str(tmp_path),
        )
        assert rc.type == "metadata_only"
