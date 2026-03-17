"""Unit tests for ANP v0.2 manifest validation and normalization."""
import pytest

from app.packages.validator import normalize_manifest, validate_manifest


# ---------------------------------------------------------------------------
# Fixtures: valid v0.1 and v0.2 manifests
# ---------------------------------------------------------------------------

VALID_V01 = {
    "manifest_version": "0.1",
    "package_id": "test-pack",
    "package_type": "toolpack",
    "name": "Test Pack",
    "publisher": "test-publisher",
    "version": "1.0.0",
    "summary": "A test package.",
    "runtime": "python",
    "install_mode": "package",
    "hosting_type": "agentnode_hosted",
    "entrypoint": "test_pack.tool",
    "capabilities": {
        "tools": [{
            "name": "test_tool",
            "capability_id": "pdf_extraction",
            "description": "Test tool",
            "input_schema": {"type": "object", "properties": {"input": {"type": "string"}}},
        }],
        "resources": [],
        "prompts": [],
    },
    "compatibility": {"frameworks": ["generic"]},
    "permissions": {
        "network": {"level": "none", "allowed_domains": []},
        "filesystem": {"level": "temp"},
        "code_execution": {"level": "none"},
        "data_access": {"level": "input_only"},
        "user_approval": {"required": "never"},
    },
    "tags": ["test"],
    "categories": ["data"],
    "dependencies": [],
}

VALID_V02_SINGLE_TOOL = {
    "manifest_version": "0.2",
    "package_id": "single-tool-pack",
    "package_type": "toolpack",
    "name": "Single Tool Pack",
    "publisher": "test-publisher",
    "version": "1.0.0",
    "summary": "A v0.2 single-tool pack.",
    "runtime": "python",
    "install_mode": "package",
    "hosting_type": "agentnode_hosted",
    "entrypoint": "single_tool_pack.tool",
    "capabilities": {
        "tools": [{
            "name": "do_thing",
            "capability_id": "pdf_extraction",
            "description": "Does the thing",
            "entrypoint": "single_tool_pack.tool:do_thing",
        }],
        "resources": [],
        "prompts": [],
    },
    "compatibility": {"frameworks": ["generic"]},
    "permissions": {
        "network": {"level": "none", "allowed_domains": []},
        "filesystem": {"level": "none"},
        "code_execution": {"level": "none"},
        "data_access": {"level": "input_only"},
        "user_approval": {"required": "never"},
    },
    "tags": ["test"],
    "categories": ["data"],
    "dependencies": [],
}

VALID_V02_MULTI_TOOL = {
    "manifest_version": "0.2",
    "package_id": "multi-tool-pack",
    "package_type": "toolpack",
    "name": "Multi Tool Pack",
    "publisher": "test-publisher",
    "version": "1.1.0",
    "summary": "A v0.2 multi-tool pack.",
    "runtime": "python",
    "install_mode": "package",
    "hosting_type": "agentnode_hosted",
    "entrypoint": "multi_tool_pack.tool",
    "capabilities": {
        "tools": [
            {
                "name": "describe",
                "capability_id": "pdf_extraction",
                "description": "Describe data",
                "entrypoint": "multi_tool_pack.tool:describe",
            },
            {
                "name": "filter",
                "capability_id": "web_search",
                "description": "Filter data",
                "entrypoint": "multi_tool_pack.tool:filter_rows",
            },
        ],
        "resources": [],
        "prompts": [],
    },
    "compatibility": {"frameworks": ["generic"]},
    "permissions": {
        "network": {"level": "none", "allowed_domains": []},
        "filesystem": {"level": "none"},
        "code_execution": {"level": "none"},
        "data_access": {"level": "input_only"},
        "user_approval": {"required": "never"},
    },
    "tags": ["test"],
    "categories": ["data"],
    "dependencies": [],
}


# ---------------------------------------------------------------------------
# normalize_manifest() tests
# ---------------------------------------------------------------------------

class TestNormalizeManifest:
    def test_v01_passes_through_unchanged(self):
        """v0.1 manifests must NOT be modified by normalization."""
        result = normalize_manifest(VALID_V01)
        assert result is VALID_V01  # same object, not a copy

    def test_v02_applies_defaults(self):
        """v0.2 compact manifest should get defaults applied."""
        compact = {
            "manifest_version": "0.2",
            "package_id": "compact-pack",
            "package_type": "toolpack",
            "name": "Compact Pack",
            "publisher": "test-pub",
            "version": "1.0.0",
            "summary": "Compact.",
            "capabilities": {
                "tools": [{"name": "t", "capability_id": "pdf_extraction"}],
            },
        }
        result = normalize_manifest(compact)

        # Defaults should be applied
        assert result["runtime"] == "python"
        assert result["install_mode"] == "package"
        assert result["hosting_type"] == "agentnode_hosted"
        assert result["dependencies"] == []
        assert result["tags"] == []
        assert result["categories"] == []
        assert result["permissions"]["network"]["level"] == "none"
        assert result["permissions"]["filesystem"]["level"] == "none"
        assert result["permissions"]["code_execution"]["level"] == "none"
        assert result["permissions"]["data_access"]["level"] == "input_only"
        assert result["permissions"]["user_approval"]["required"] == "never"

    def test_v02_preserves_user_values(self):
        """User-provided values must override defaults."""
        compact = {
            "manifest_version": "0.2",
            "package_id": "custom-pack",
            "package_type": "toolpack",
            "name": "Custom Pack",
            "publisher": "test-pub",
            "version": "1.0.0",
            "summary": "Custom.",
            "runtime": "python",
            "tags": ["my-tag"],
            "permissions": {
                "network": {"level": "unrestricted", "allowed_domains": ["api.example.com"]},
            },
            "capabilities": {
                "tools": [{"name": "t", "capability_id": "pdf_extraction"}],
            },
        }
        result = normalize_manifest(compact)

        assert result["tags"] == ["my-tag"]
        assert result["permissions"]["network"]["level"] == "unrestricted"
        assert result["permissions"]["network"]["allowed_domains"] == ["api.example.com"]

    def test_v02_adds_resources_and_prompts_arrays(self):
        """capabilities should get resources/prompts arrays if missing."""
        compact = {
            "manifest_version": "0.2",
            "package_id": "x-pack",
            "capabilities": {"tools": [{"name": "t", "capability_id": "pdf_extraction"}]},
        }
        result = normalize_manifest(compact)
        assert result["capabilities"]["resources"] == []
        assert result["capabilities"]["prompts"] == []

    def test_v02_does_not_modify_original(self):
        """normalize_manifest should return a new dict, not modify the input."""
        compact = {
            "manifest_version": "0.2",
            "package_id": "x-pack",
            "capabilities": {"tools": []},
        }
        original_keys = set(compact.keys())
        normalize_manifest(compact)
        assert set(compact.keys()) == original_keys  # original unchanged


# ---------------------------------------------------------------------------
# v0.1 backward compatibility tests
# ---------------------------------------------------------------------------

class TestV01BackwardCompatibility:
    @pytest.mark.asyncio
    async def test_v01_still_valid(self):
        """Existing v0.1 manifests must still validate."""
        valid, errors, warnings = await validate_manifest(VALID_V01)
        assert valid is True, f"v0.1 should be valid, errors: {errors}"
        assert errors == []

    @pytest.mark.asyncio
    async def test_v01_requires_package_entrypoint(self):
        """v0.1 must reject manifests without package-level entrypoint."""
        m = {**VALID_V01}
        del m["entrypoint"]
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("entrypoint" in e for e in errors)

    @pytest.mark.asyncio
    async def test_v01_rejects_colon_entrypoint(self):
        """v0.1 must reject module:function format."""
        m = {**VALID_V01, "entrypoint": "test_pack.tool:run"}
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("module path" in e.lower() or "entrypoint" in e.lower() for e in errors)


# ---------------------------------------------------------------------------
# v0.2 Entrypoint validation tests
# ---------------------------------------------------------------------------

class TestV02Entrypoints:
    @pytest.mark.asyncio
    async def test_v02_single_tool_with_both_entrypoints_valid(self):
        """Single-tool v0.2 with both package-level and tool-level entrypoints."""
        valid, errors, _ = await validate_manifest(VALID_V02_SINGLE_TOOL)
        assert valid is True, f"errors: {errors}"

    @pytest.mark.asyncio
    async def test_v02_single_tool_package_entrypoint_only(self):
        """Single-tool v0.2 with only package-level entrypoint is valid."""
        m = {**VALID_V02_SINGLE_TOOL}
        m["capabilities"] = {
            **m["capabilities"],
            "tools": [{
                "name": "do_thing",
                "capability_id": "pdf_extraction",
                "description": "Does the thing",
                # no tool-level entrypoint
            }],
        }
        valid, errors, _ = await validate_manifest(m)
        assert valid is True, f"errors: {errors}"

    @pytest.mark.asyncio
    async def test_v02_single_tool_tool_entrypoint_only(self):
        """Single-tool v0.2 with only tool-level entrypoint (no package-level) is valid."""
        m = {**VALID_V02_SINGLE_TOOL}
        del m["entrypoint"]  # remove package-level
        # tool still has its entrypoint
        valid, errors, _ = await validate_manifest(m)
        assert valid is True, f"errors: {errors}"

    @pytest.mark.asyncio
    async def test_v02_single_tool_no_entrypoint_at_all_invalid(self):
        """Single-tool v0.2 with NO entrypoint at all is invalid."""
        m = {**VALID_V02_SINGLE_TOOL}
        del m["entrypoint"]
        m["capabilities"] = {
            **m["capabilities"],
            "tools": [{
                "name": "do_thing",
                "capability_id": "pdf_extraction",
                "description": "Does the thing",
                # no entrypoint anywhere
            }],
        }
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("entrypoint" in e.lower() for e in errors)

    @pytest.mark.asyncio
    async def test_v02_multi_tool_all_entrypoints_valid(self):
        """Multi-tool v0.2 where every tool has its own entrypoint."""
        valid, errors, _ = await validate_manifest(VALID_V02_MULTI_TOOL)
        assert valid is True, f"errors: {errors}"

    @pytest.mark.asyncio
    async def test_v02_multi_tool_missing_entrypoint_invalid(self):
        """Multi-tool v0.2 where one tool is missing entrypoint is INVALID."""
        m = {**VALID_V02_MULTI_TOOL}
        m["capabilities"] = {
            **m["capabilities"],
            "tools": [
                {
                    "name": "describe",
                    "capability_id": "pdf_extraction",
                    "description": "Describe data",
                    "entrypoint": "multi_tool_pack.tool:describe",
                },
                {
                    "name": "filter",
                    "capability_id": "web_search",
                    "description": "Filter data",
                    # MISSING entrypoint
                },
            ],
        }
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("entrypoint is required" in e for e in errors)

    @pytest.mark.asyncio
    async def test_v02_multi_tool_no_entrypoints_invalid(self):
        """Multi-tool v0.2 where ALL tools lack entrypoints is INVALID."""
        m = {**VALID_V02_MULTI_TOOL}
        m["capabilities"] = {
            **m["capabilities"],
            "tools": [
                {
                    "name": "describe",
                    "capability_id": "pdf_extraction",
                    "description": "Describe data",
                },
                {
                    "name": "filter",
                    "capability_id": "web_search",
                    "description": "Filter data",
                },
            ],
        }
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        # Should have an error for each tool
        entrypoint_errors = [e for e in errors if "entrypoint is required" in e]
        assert len(entrypoint_errors) == 2

    @pytest.mark.asyncio
    async def test_v02_tool_entrypoint_wrong_format_invalid(self):
        """Tool-level entrypoint must be module.path:function format."""
        m = {**VALID_V02_SINGLE_TOOL}
        m["capabilities"] = {
            **m["capabilities"],
            "tools": [{
                "name": "do_thing",
                "capability_id": "pdf_extraction",
                "description": "Does the thing",
                "entrypoint": "just_a_module.path",  # missing :function
            }],
        }
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("module.path:function" in e for e in errors)

    @pytest.mark.asyncio
    async def test_v02_tool_entrypoint_invalid_chars(self):
        """Tool-level entrypoint with invalid characters."""
        m = {**VALID_V02_SINGLE_TOOL}
        m["capabilities"] = {
            **m["capabilities"],
            "tools": [{
                "name": "do_thing",
                "capability_id": "pdf_extraction",
                "description": "Does the thing",
                "entrypoint": "my-pack.tool:run",  # hyphens not allowed
            }],
        }
        valid, errors, _ = await validate_manifest(m)
        assert valid is False

    @pytest.mark.asyncio
    async def test_v02_package_entrypoint_allows_v1_format(self):
        """Package-level entrypoint in v0.2 uses v1 format (no :function)."""
        m = {**VALID_V02_MULTI_TOOL, "entrypoint": "multi_tool_pack.tool"}
        valid, errors, _ = await validate_manifest(m)
        assert valid is True, f"errors: {errors}"

    @pytest.mark.asyncio
    async def test_v02_package_entrypoint_rejects_colon_format(self):
        """Package-level entrypoint must NOT have :function suffix."""
        m = {**VALID_V02_MULTI_TOOL, "entrypoint": "multi_tool_pack.tool:run"}
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("module path" in e.lower() for e in errors)


# ---------------------------------------------------------------------------
# v0.2 normalization + validation integration
# ---------------------------------------------------------------------------

class TestV02NormalizeAndValidate:
    @pytest.mark.asyncio
    async def test_compact_v02_normalized_then_valid(self):
        """A compact v0.2 manifest should validate after normalization."""
        compact = {
            "manifest_version": "0.2",
            "package_id": "compact-test",
            "package_type": "toolpack",
            "name": "Compact Test",
            "publisher": "test-pub",
            "version": "1.0.0",
            "summary": "A compact v0.2 manifest.",
            "entrypoint": "compact_test.tool",
            "capabilities": {
                "tools": [{
                    "name": "do_it",
                    "capability_id": "pdf_extraction",
                    "description": "Does it",
                    "entrypoint": "compact_test.tool:do_it",
                }],
            },
            "compatibility": {"frameworks": ["generic"]},
            "tags": ["test"],
            "categories": ["data"],
        }
        normalized = normalize_manifest(compact)
        valid, errors, _ = await validate_manifest(normalized)
        assert valid is True, f"errors: {errors}"

    @pytest.mark.asyncio
    async def test_v02_without_normalization_misses_defaults(self):
        """A compact v0.2 manifest without normalization should fail validation."""
        compact = {
            "manifest_version": "0.2",
            "package_id": "raw-compact",
            "package_type": "toolpack",
            "name": "Raw Compact",
            "publisher": "test-pub",
            "version": "1.0.0",
            "summary": "Missing defaults.",
            "entrypoint": "raw_compact.tool",
            "capabilities": {
                "tools": [{
                    "name": "t",
                    "capability_id": "pdf_extraction",
                    "description": "T",
                    "entrypoint": "raw_compact.tool:t",
                }],
            },
            "compatibility": {"frameworks": ["generic"]},
            # No permissions, no runtime, no install_mode, no hosting_type
        }
        valid, errors, _ = await validate_manifest(compact)
        assert valid is False
        # Should fail on missing permissions, runtime, etc.
        assert any("permissions" in e for e in errors)


# ---------------------------------------------------------------------------
# manifest_version acceptance
# ---------------------------------------------------------------------------

class TestManifestVersionAcceptance:
    @pytest.mark.asyncio
    async def test_accepts_v01(self):
        valid, errors, _ = await validate_manifest(VALID_V01)
        assert valid is True

    @pytest.mark.asyncio
    async def test_accepts_v02(self):
        valid, errors, _ = await validate_manifest(VALID_V02_SINGLE_TOOL)
        assert valid is True

    @pytest.mark.asyncio
    async def test_rejects_v03(self):
        m = {**VALID_V01, "manifest_version": "0.3"}
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("0.1" in e or "0.2" in e for e in errors)

    @pytest.mark.asyncio
    async def test_rejects_v10(self):
        m = {**VALID_V01, "manifest_version": "1.0"}
        valid, errors, _ = await validate_manifest(m)
        assert valid is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestV02EdgeCases:
    @pytest.mark.asyncio
    async def test_v02_empty_tools_array_invalid(self):
        """v0.2 with empty tools array is invalid (same as v0.1)."""
        m = {**VALID_V02_SINGLE_TOOL}
        m["capabilities"] = {"tools": [], "resources": [], "prompts": []}
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("at least 1 tool" in e for e in errors)

    @pytest.mark.asyncio
    async def test_v02_tool_missing_name_invalid(self):
        """Tool without name is invalid in v0.2."""
        m = {**VALID_V02_SINGLE_TOOL}
        m["capabilities"] = {
            **m["capabilities"],
            "tools": [{
                "capability_id": "pdf_extraction",
                "description": "No name",
                "entrypoint": "test.tool:fn",
            }],
        }
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("name" in e for e in errors)

    @pytest.mark.asyncio
    async def test_v02_tool_missing_capability_id_invalid(self):
        """Tool without capability_id is invalid in v0.2."""
        m = {**VALID_V02_SINGLE_TOOL}
        m["capabilities"] = {
            **m["capabilities"],
            "tools": [{
                "name": "some_tool",
                "description": "No cap id",
                "entrypoint": "test.tool:fn",
            }],
        }
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("capability_id" in e for e in errors)

    @pytest.mark.asyncio
    async def test_v02_tool_with_input_schema_validated(self):
        """Tool input_schema in v0.2 is validated as JSON Schema."""
        m = {**VALID_V02_SINGLE_TOOL}
        m["capabilities"] = {
            **m["capabilities"],
            "tools": [{
                "name": "do_thing",
                "capability_id": "pdf_extraction",
                "description": "Does the thing",
                "entrypoint": "single_tool_pack.tool:do_thing",
                "input_schema": {"type": "invalid_type"},
            }],
        }
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("not a valid JSON Schema type" in e for e in errors)

    @pytest.mark.asyncio
    async def test_v02_multi_tool_package_entrypoint_optional_when_all_tools_have_ep(self):
        """Package-level entrypoint is optional when all tools have their own."""
        m = {**VALID_V02_MULTI_TOOL}
        del m["entrypoint"]  # remove package-level
        valid, errors, _ = await validate_manifest(m)
        assert valid is True, f"errors: {errors}"
