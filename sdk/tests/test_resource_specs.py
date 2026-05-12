"""Tests for resource-asset discovery in runtime and lockfile."""
import json
import os
import tempfile

import pytest

from agentnode_sdk.models import ResourceSpec


def _make_lockfile(packages: dict) -> str:
    data = {"lockfile_version": "0.1", "updated_at": "", "packages": packages}
    tf = tempfile.NamedTemporaryFile(mode="w", suffix=".lock", delete=False)
    json.dump(data, tf)
    tf.close()
    return tf.name


@pytest.fixture(autouse=True)
def _clean_lockfile_env():
    old = os.environ.get("AGENTNODE_LOCKFILE")
    yield
    if old is not None:
        os.environ["AGENTNODE_LOCKFILE"] = old
    else:
        os.environ.pop("AGENTNODE_LOCKFILE", None)


def _make_runtime():
    from agentnode_sdk.runtime import AgentNodeRuntime
    rt = AgentNodeRuntime.__new__(AgentNodeRuntime)
    rt._minimum_trust_level = "verified"
    return rt


class TestResourceSpecs:
    def test_returns_resource_specs_from_lockfile(self):
        path = _make_lockfile({
            "slack-pack": {
                "version": "1.0.0",
                "resources": [{
                    "name": "api_spec",
                    "capability_id": "api_reference",
                    "uri": "resource://slack/openapi-spec",
                    "description": "Slack API specification",
                    "mime_type": "application/json",
                }],
            },
        })
        os.environ["AGENTNODE_LOCKFILE"] = path
        try:
            rt = _make_runtime()
            specs = rt.resource_specs()
            assert len(specs) == 1
            assert isinstance(specs[0], ResourceSpec)
            assert specs[0].name == "api_spec"
            assert specs[0].uri == "resource://slack/openapi-spec"
            assert specs[0].capability_id == "api_reference"
            assert specs[0].mime_type == "application/json"
        finally:
            os.unlink(path)

    def test_empty_resources_returns_empty(self):
        path = _make_lockfile({
            "test-pack": {"version": "1.0.0", "resources": []},
        })
        os.environ["AGENTNODE_LOCKFILE"] = path
        try:
            assert _make_runtime().resource_specs() == []
        finally:
            os.unlink(path)

    def test_missing_resources_field_returns_empty(self):
        """Pre-v0.3 lockfile without resources field."""
        path = _make_lockfile({
            "old-pack": {"version": "1.0.0", "tools": []},
        })
        os.environ["AGENTNODE_LOCKFILE"] = path
        try:
            assert _make_runtime().resource_specs() == []
        finally:
            os.unlink(path)

    def test_skips_resources_without_uri(self):
        path = _make_lockfile({
            "test-pack": {
                "version": "1.0.0",
                "resources": [
                    {"name": "bad", "capability_id": "x"},  # no uri
                    {"name": "good", "capability_id": "y", "uri": "resource://test/data"},
                ],
            },
        })
        os.environ["AGENTNODE_LOCKFILE"] = path
        try:
            specs = _make_runtime().resource_specs()
            assert len(specs) == 1
            assert specs[0].name == "good"
        finally:
            os.unlink(path)

    def test_multiple_packages_merged(self):
        path = _make_lockfile({
            "pack-a": {
                "version": "1.0.0",
                "resources": [{"name": "r_a", "capability_id": "a", "uri": "resource://a/data"}],
            },
            "pack-b": {
                "version": "2.0.0",
                "resources": [{"name": "r_b", "capability_id": "b", "uri": "https://b.example.com/api"}],
            },
        })
        os.environ["AGENTNODE_LOCKFILE"] = path
        try:
            specs = _make_runtime().resource_specs()
            assert len(specs) == 2
            names = {s.name for s in specs}
            assert names == {"r_a", "r_b"}
        finally:
            os.unlink(path)

    def test_resource_without_optional_fields(self):
        path = _make_lockfile({
            "test-pack": {
                "version": "1.0.0",
                "resources": [{
                    "name": "minimal",
                    "capability_id": "x",
                    "uri": "resource://test/minimal",
                }],
            },
        })
        os.environ["AGENTNODE_LOCKFILE"] = path
        try:
            specs = _make_runtime().resource_specs()
            assert len(specs) == 1
            assert specs[0].description is None
            assert specs[0].mime_type is None
        finally:
            os.unlink(path)


class TestSkillAssetResources:
    """Skill assets are mapped to ResourceSpec with agentnode:// URIs."""

    def test_skill_assets_appear_as_resources(self):
        path = _make_lockfile({
            "seo-skill": {
                "version": "1.0.0",
                "package_type": "skill",
                "resources": [],
                "assets": [
                    {"id": "keyword-guide", "type": "document", "path": "assets/keywords.md", "description": "Keywords"},
                    {"id": "data-file", "type": "data", "path": "assets/data.json", "description": "Data"},
                ],
            },
        })
        os.environ["AGENTNODE_LOCKFILE"] = path
        try:
            specs = _make_runtime().resource_specs()
            assert len(specs) == 2
            names = {s.name for s in specs}
            assert names == {"keyword-guide", "data-file"}
        finally:
            os.unlink(path)

    def test_asset_uri_uses_agentnode_scheme(self):
        path = _make_lockfile({
            "my-skill": {
                "version": "1.0.0",
                "package_type": "skill",
                "assets": [{"id": "doc", "type": "document", "path": "doc.md", "description": "D"}],
            },
        })
        os.environ["AGENTNODE_LOCKFILE"] = path
        try:
            specs = _make_runtime().resource_specs()
            assert len(specs) == 1
            assert specs[0].uri == "agentnode://skills/my-skill/assets/doc"
        finally:
            os.unlink(path)

    def test_mime_type_mapping(self):
        path = _make_lockfile({
            "mime-skill": {
                "version": "1.0.0",
                "package_type": "skill",
                "assets": [
                    {"id": "a", "type": "document", "path": "a.md"},
                    {"id": "b", "type": "data", "path": "b.json"},
                    {"id": "c", "type": "template", "path": "c.txt"},
                    {"id": "d", "type": "unknown", "path": "d.bin"},
                ],
            },
        })
        os.environ["AGENTNODE_LOCKFILE"] = path
        try:
            specs = _make_runtime().resource_specs()
            by_name = {s.name: s for s in specs}
            assert by_name["a"].mime_type == "text/markdown"
            assert by_name["b"].mime_type == "application/json"
            assert by_name["c"].mime_type == "text/plain"
            assert by_name["d"].mime_type == "text/plain"
        finally:
            os.unlink(path)

    def test_non_skill_no_asset_resources(self):
        path = _make_lockfile({
            "tool-pack": {
                "version": "1.0.0",
                "package_type": "toolpack",
                "assets": [{"id": "x", "type": "document", "path": "x.md"}],
            },
        })
        os.environ["AGENTNODE_LOCKFILE"] = path
        try:
            specs = _make_runtime().resource_specs()
            assert len(specs) == 0
        finally:
            os.unlink(path)

    def test_asset_without_id_skipped(self):
        path = _make_lockfile({
            "bad-skill": {
                "version": "1.0.0",
                "package_type": "skill",
                "assets": [{"type": "document", "path": "x.md"}],
            },
        })
        os.environ["AGENTNODE_LOCKFILE"] = path
        try:
            specs = _make_runtime().resource_specs()
            assert len(specs) == 0
        finally:
            os.unlink(path)
