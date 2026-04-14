"""Unit tests for ANP v0.3 taxonomy validation: type combinations, prompts,
resources, connector, and agent sections."""
import pytest

from app.packages.validator import validate_manifest


# ---------------------------------------------------------------------------
# Base manifests
# ---------------------------------------------------------------------------

def _base_toolpack(**overrides) -> dict:
    """Valid toolpack+python+package manifest."""
    m = {
        "manifest_version": "0.2",
        "package_id": "test-toolpack",
        "package_type": "toolpack",
        "name": "Test Toolpack",
        "publisher": "test-publisher",
        "version": "1.0.0",
        "summary": "A valid test toolpack manifest.",
        "runtime": "python",
        "install_mode": "package",
        "hosting_type": "agentnode_hosted",
        "entrypoint": "test_pack.tool",
        "capabilities": {
            "tools": [{
                "name": "test_tool",
                "capability_id": "pdf_extraction",
                "description": "Test tool",
                "entrypoint": "test_pack.tool:run",
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
    m.update(overrides)
    return m


def _base_agent(**overrides) -> dict:
    """Valid agent+python+package manifest."""
    m = {
        "manifest_version": "0.2",
        "package_id": "test-agent",
        "package_type": "agent",
        "name": "Test Agent",
        "publisher": "test-publisher",
        "version": "1.0.0",
        "summary": "A valid test agent manifest.",
        "runtime": "python",
        "install_mode": "package",
        "hosting_type": "agentnode_hosted",
        "capabilities": {
            "tools": [],
            "resources": [],
            "prompts": [],
        },
        "agent": {
            "entrypoint": "test_agent.main:run_agent",
            "goal": "Research and summarize documents",
            "tool_access": {"allowed_packages": ["pdf-reader-pack"]},
            "limits": {
                "max_iterations": 12,
                "max_tool_calls": 40,
                "max_runtime_seconds": 180,
            },
            "termination": {
                "stop_on_final_answer": True,
                "stop_on_consecutive_errors": 3,
            },
            "state": {"persistence": "none"},
        },
        "compatibility": {"frameworks": ["generic"]},
        "permissions": {
            "network": {"level": "none", "allowed_domains": []},
            "filesystem": {"level": "none"},
            "code_execution": {"level": "none"},
            "data_access": {"level": "input_only"},
            "user_approval": {"required": "never"},
        },
        "tags": ["agent"],
        "categories": ["research"],
        "dependencies": [],
    }
    m.update(overrides)
    return m


def _base_upgrade(**overrides) -> dict:
    """Valid upgrade+python+package manifest."""
    m = {
        "manifest_version": "0.2",
        "package_id": "test-upgrade",
        "package_type": "upgrade",
        "name": "Test Upgrade",
        "publisher": "test-publisher",
        "version": "1.0.0",
        "summary": "A valid test upgrade manifest.",
        "runtime": "python",
        "install_mode": "package",
        "hosting_type": "agentnode_hosted",
        "capabilities": {
            "tools": [],
            "resources": [],
            "prompts": [],
        },
        "upgrade_metadata": {
            "recommended_for": ["pdf-reader-pack"],
            "roles": ["enhancement"],
        },
        "compatibility": {"frameworks": ["generic"]},
        "permissions": {
            "network": {"level": "none", "allowed_domains": []},
            "filesystem": {"level": "none"},
            "code_execution": {"level": "none"},
            "data_access": {"level": "input_only"},
            "user_approval": {"required": "never"},
        },
        "tags": ["upgrade"],
        "categories": ["enhancement"],
        "dependencies": [],
    }
    m.update(overrides)
    return m


# ---------------------------------------------------------------------------
# Type Combination Validation (S5)
# ---------------------------------------------------------------------------

class TestTypeCombinations:
    @pytest.mark.asyncio
    async def test_valid_toolpack_python_package(self):
        valid, errors, _ = await validate_manifest(_base_toolpack())
        assert valid is True, f"errors: {errors}"

    @pytest.mark.asyncio
    async def test_valid_toolpack_mcp_package(self):
        m = _base_toolpack(runtime="mcp")
        valid, errors, _ = await validate_manifest(m)
        assert valid is True, f"errors: {errors}"

    @pytest.mark.asyncio
    async def test_valid_toolpack_remote_remote_endpoint(self):
        m = _base_toolpack(runtime="remote", install_mode="remote_endpoint")
        valid, errors, _ = await validate_manifest(m)
        assert valid is True, f"errors: {errors}"

    @pytest.mark.asyncio
    async def test_valid_agent_python_package(self):
        valid, errors, _ = await validate_manifest(_base_agent())
        assert valid is True, f"errors: {errors}"

    @pytest.mark.asyncio
    async def test_valid_upgrade_python_package(self):
        valid, errors, _ = await validate_manifest(_base_upgrade())
        assert valid is True, f"errors: {errors}"

    @pytest.mark.asyncio
    async def test_invalid_toolpack_python_remote_endpoint(self):
        """toolpack+python+remote_endpoint is NOT a valid combination."""
        m = _base_toolpack(install_mode="remote_endpoint")
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("Invalid combination" in e for e in errors)

    @pytest.mark.asyncio
    async def test_invalid_agent_remote(self):
        """agent+remote+* is not allowed in v1."""
        m = _base_agent(runtime="remote", install_mode="remote_endpoint")
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("Invalid combination" in e for e in errors)

    @pytest.mark.asyncio
    async def test_invalid_agent_mcp(self):
        """agent+mcp+* is not allowed in v1."""
        m = _base_agent(runtime="mcp")
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("Invalid combination" in e for e in errors)

    @pytest.mark.asyncio
    async def test_invalid_agent_missing_agent_section(self):
        """package_type=agent without agent: section is invalid."""
        m = _base_agent()
        del m["agent"]
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("requires an 'agent:' section" in e for e in errors)

    @pytest.mark.asyncio
    async def test_connector_only_on_toolpack(self):
        """connector: section on agent is invalid."""
        m = _base_agent()
        m["connector"] = {"provider": "slack", "auth_type": "api_key"}
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("connector: section only valid for package_type=toolpack" in e for e in errors)

    @pytest.mark.asyncio
    async def test_invalid_upgrade_mcp_package(self):
        """upgrade+mcp+package is not a valid combination."""
        m = _base_upgrade(runtime="mcp")
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("Invalid combination" in e for e in errors)


# ---------------------------------------------------------------------------
# S9: Upgrade restrictions
# ---------------------------------------------------------------------------

class TestUpgradeRestrictions:
    @pytest.mark.asyncio
    async def test_upgrade_with_agent_section_invalid(self):
        m = _base_upgrade()
        m["agent"] = {"entrypoint": "x.y:z", "goal": "bad"}
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("upgrade packages must not have an 'agent:' section" in e for e in errors)

    @pytest.mark.asyncio
    async def test_upgrade_with_connector_section_invalid(self):
        m = _base_upgrade()
        m["connector"] = {"provider": "slack"}
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("upgrade packages must not have a 'connector:' section" in e for e in errors)

    @pytest.mark.asyncio
    async def test_upgrade_with_tools_invalid(self):
        m = _base_upgrade()
        m["capabilities"]["tools"] = [{"name": "t", "capability_id": "x"}]
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("upgrade packages must not declare executable tools" in e for e in errors)


# ---------------------------------------------------------------------------
# Prompt Validation (S2, S6, S11)
# ---------------------------------------------------------------------------

class TestPromptValidation:
    @pytest.mark.asyncio
    async def test_valid_prompt(self):
        m = _base_toolpack()
        m["capabilities"]["prompts"] = [{
            "name": "summarize",
            "capability_id": "text_summarization",
            "template": "Summarize the following: {{text}}",
            "description": "Summarize text",
            "arguments": [
                {"name": "text", "description": "The text to summarize", "required": True},
            ],
        }]
        valid, errors, _ = await validate_manifest(m)
        assert valid is True, f"errors: {errors}"

    @pytest.mark.asyncio
    async def test_prompt_missing_name(self):
        m = _base_toolpack()
        m["capabilities"]["prompts"] = [{
            "capability_id": "text_summarization",
            "template": "Summarize: {{text}}",
        }]
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("prompts[0].name is required" in e for e in errors)

    @pytest.mark.asyncio
    async def test_prompt_missing_capability_id(self):
        m = _base_toolpack()
        m["capabilities"]["prompts"] = [{
            "name": "summarize",
            "template": "Summarize: {{text}}",
        }]
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("prompts[0].capability_id is required" in e for e in errors)

    @pytest.mark.asyncio
    async def test_prompt_missing_template(self):
        m = _base_toolpack()
        m["capabilities"]["prompts"] = [{
            "name": "summarize",
            "capability_id": "text_summarization",
        }]
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("prompts[0].template is required" in e for e in errors)

    @pytest.mark.asyncio
    async def test_prompt_rejects_entrypoint(self):
        """Prompts are non-executable — entrypoint not allowed."""
        m = _base_toolpack()
        m["capabilities"]["prompts"] = [{
            "name": "summarize",
            "capability_id": "text_summarization",
            "template": "Summarize: {{text}}",
            "entrypoint": "bad.module:fn",
        }]
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("entrypoint is not allowed" in e for e in errors)

    @pytest.mark.asyncio
    async def test_prompt_rejects_input_schema(self):
        """Prompts are non-executable — input_schema not allowed."""
        m = _base_toolpack()
        m["capabilities"]["prompts"] = [{
            "name": "summarize",
            "capability_id": "text_summarization",
            "template": "Summarize: {{text}}",
            "input_schema": {"type": "object"},
        }]
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("input_schema is not allowed" in e for e in errors)

    @pytest.mark.asyncio
    async def test_prompt_argument_missing_name(self):
        m = _base_toolpack()
        m["capabilities"]["prompts"] = [{
            "name": "summarize",
            "capability_id": "text_summarization",
            "template": "Summarize: {{text}}",
            "arguments": [{"description": "no name"}],
        }]
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("arguments[0].name is required" in e for e in errors)

    @pytest.mark.asyncio
    async def test_prompt_arguments_not_list(self):
        m = _base_toolpack()
        m["capabilities"]["prompts"] = [{
            "name": "summarize",
            "capability_id": "text_summarization",
            "template": "Summarize: {{text}}",
            "arguments": "not-a-list",
        }]
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("arguments must be an array" in e for e in errors)


# ---------------------------------------------------------------------------
# Resource Validation (S2, S6, S10)
# ---------------------------------------------------------------------------

class TestResourceValidation:
    @pytest.mark.asyncio
    async def test_valid_resource_with_resource_uri(self):
        m = _base_toolpack()
        m["capabilities"]["resources"] = [{
            "name": "api_spec",
            "capability_id": "api_reference",
            "uri": "resource://slack/openapi-spec",
            "description": "Slack API specification",
            "mime_type": "application/json",
        }]
        valid, errors, _ = await validate_manifest(m)
        assert valid is True, f"errors: {errors}"

    @pytest.mark.asyncio
    async def test_valid_resource_with_https_uri(self):
        m = _base_toolpack()
        m["capabilities"]["resources"] = [{
            "name": "docs",
            "capability_id": "documentation",
            "uri": "https://docs.example.com/api.json",
        }]
        valid, errors, _ = await validate_manifest(m)
        assert valid is True, f"errors: {errors}"

    @pytest.mark.asyncio
    async def test_resource_missing_name(self):
        m = _base_toolpack()
        m["capabilities"]["resources"] = [{
            "capability_id": "api_reference",
            "uri": "resource://test/resource",
        }]
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("resources[0].name is required" in e for e in errors)

    @pytest.mark.asyncio
    async def test_resource_missing_capability_id(self):
        m = _base_toolpack()
        m["capabilities"]["resources"] = [{
            "name": "test",
            "uri": "resource://test/resource",
        }]
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("resources[0].capability_id is required" in e for e in errors)

    @pytest.mark.asyncio
    async def test_resource_missing_uri(self):
        m = _base_toolpack()
        m["capabilities"]["resources"] = [{
            "name": "test",
            "capability_id": "api_reference",
        }]
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("resources[0].uri is required" in e for e in errors)

    @pytest.mark.asyncio
    async def test_resource_file_uri_rejected(self):
        """S10: file:// URIs are not allowed."""
        m = _base_toolpack()
        m["capabilities"]["resources"] = [{
            "name": "local",
            "capability_id": "api_reference",
            "uri": "file:///etc/passwd",
        }]
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("resource://" in e and "https://" in e for e in errors)

    @pytest.mark.asyncio
    async def test_resource_http_uri_rejected(self):
        """Only resource:// and https:// — no plain http://."""
        m = _base_toolpack()
        m["capabilities"]["resources"] = [{
            "name": "insecure",
            "capability_id": "api_reference",
            "uri": "http://insecure.example.com/data",
        }]
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("resource://" in e and "https://" in e for e in errors)

    @pytest.mark.asyncio
    async def test_resource_rejects_entrypoint(self):
        m = _base_toolpack()
        m["capabilities"]["resources"] = [{
            "name": "test",
            "capability_id": "api_reference",
            "uri": "resource://test/data",
            "entrypoint": "bad.module:fn",
        }]
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("entrypoint is not allowed" in e for e in errors)

    @pytest.mark.asyncio
    async def test_resource_rejects_input_schema(self):
        m = _base_toolpack()
        m["capabilities"]["resources"] = [{
            "name": "test",
            "capability_id": "api_reference",
            "uri": "resource://test/data",
            "input_schema": {"type": "object"},
        }]
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("input_schema is not allowed" in e for e in errors)


# ---------------------------------------------------------------------------
# Connector Block Validation (S3, S8)
# ---------------------------------------------------------------------------

class TestConnectorValidation:
    @pytest.mark.asyncio
    async def test_valid_connector(self):
        m = _base_toolpack()
        m["connector"] = {
            "provider": "slack",
            "auth_type": "oauth2",
            "scopes": ["channels:read", "chat:write"],
            "token_refresh": True,
            "health_check": {
                "endpoint": "https://slack.com/api/auth.test",
                "interval_seconds": 300,
            },
            "rate_limits": {"requests_per_minute": 60},
        }
        valid, errors, _ = await validate_manifest(m)
        assert valid is True, f"errors: {errors}"

    @pytest.mark.asyncio
    async def test_connector_missing_provider(self):
        m = _base_toolpack()
        m["connector"] = {"auth_type": "api_key"}
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("connector.provider is required" in e for e in errors)

    @pytest.mark.asyncio
    async def test_connector_custom_auth_rejected(self):
        """auth_type='custom' is NOT allowed in v0.3."""
        m = _base_toolpack()
        m["connector"] = {"provider": "slack", "auth_type": "custom"}
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("connector.auth_type" in e for e in errors)

    @pytest.mark.asyncio
    async def test_connector_api_key_valid(self):
        m = _base_toolpack()
        m["connector"] = {"provider": "github", "auth_type": "api_key"}
        valid, errors, _ = await validate_manifest(m)
        assert valid is True, f"errors: {errors}"

    @pytest.mark.asyncio
    async def test_connector_health_check_missing_endpoint(self):
        m = _base_toolpack()
        m["connector"] = {
            "provider": "slack",
            "health_check": {"interval_seconds": 60},
        }
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("health_check.endpoint is required" in e for e in errors)

    @pytest.mark.asyncio
    async def test_connector_rate_limits_invalid(self):
        m = _base_toolpack()
        m["connector"] = {
            "provider": "slack",
            "rate_limits": {"requests_per_minute": -1},
        }
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("requests_per_minute" in e for e in errors)

    @pytest.mark.asyncio
    async def test_connector_scopes_must_be_strings(self):
        m = _base_toolpack()
        m["connector"] = {"provider": "slack", "scopes": [1, 2]}
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("scopes entries must be strings" in e for e in errors)


# ---------------------------------------------------------------------------
# Agent Block Validation (S4)
# ---------------------------------------------------------------------------

class TestAgentValidation:
    @pytest.mark.asyncio
    async def test_valid_agent(self):
        valid, errors, _ = await validate_manifest(_base_agent())
        assert valid is True, f"errors: {errors}"

    @pytest.mark.asyncio
    async def test_agent_missing_entrypoint(self):
        m = _base_agent()
        del m["agent"]["entrypoint"]
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("agent.entrypoint is required" in e for e in errors)

    @pytest.mark.asyncio
    async def test_agent_missing_goal(self):
        m = _base_agent()
        del m["agent"]["goal"]
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("agent.goal is required" in e for e in errors)

    @pytest.mark.asyncio
    async def test_agent_entrypoint_wrong_format(self):
        m = _base_agent()
        m["agent"]["entrypoint"] = "just.a.module.path"  # missing :function
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("module.path:function" in e for e in errors)

    @pytest.mark.asyncio
    async def test_agent_max_iterations_zero(self):
        m = _base_agent()
        m["agent"]["limits"]["max_iterations"] = 0
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("max_iterations" in e and "between" in e for e in errors)

    @pytest.mark.asyncio
    async def test_agent_max_iterations_too_high(self):
        m = _base_agent()
        m["agent"]["limits"]["max_iterations"] = 200
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("max_iterations" in e and "between" in e for e in errors)

    @pytest.mark.asyncio
    async def test_agent_max_tool_calls_too_high(self):
        m = _base_agent()
        m["agent"]["limits"]["max_tool_calls"] = 1000
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("max_tool_calls" in e for e in errors)

    @pytest.mark.asyncio
    async def test_agent_max_runtime_seconds_too_high(self):
        m = _base_agent()
        m["agent"]["limits"]["max_runtime_seconds"] = 7200
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("max_runtime_seconds" in e for e in errors)

    @pytest.mark.asyncio
    async def test_agent_stop_on_consecutive_errors_range(self):
        m = _base_agent()
        m["agent"]["termination"]["stop_on_consecutive_errors"] = 50
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("stop_on_consecutive_errors" in e for e in errors)

    @pytest.mark.asyncio
    async def test_agent_persistence_invalid(self):
        m = _base_agent()
        m["agent"]["state"]["persistence"] = "persistent"
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("persistence" in e for e in errors)

    @pytest.mark.asyncio
    async def test_agent_deferred_max_tokens_rejected(self):
        m = _base_agent()
        m["agent"]["max_tokens"] = 4096
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("max_tokens" in e and "not supported" in e for e in errors)

    @pytest.mark.asyncio
    async def test_agent_orchestration_parallel_rejected(self):
        """orchestration is now supported, but only mode=sequential."""
        m = _base_agent()
        m["agent"]["orchestration"] = {"mode": "parallel"}
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("must be 'sequential'" in e for e in errors)

    @pytest.mark.asyncio
    async def test_agent_tool_access_allowed_packages(self):
        """Agent with explicit tool allowlist is valid."""
        m = _base_agent()
        m["agent"]["tool_access"] = {
            "allowed_packages": ["pdf-reader-pack", "web-scraper-pack"],
        }
        valid, errors, _ = await validate_manifest(m)
        assert valid is True, f"errors: {errors}"

    @pytest.mark.asyncio
    async def test_agent_tool_access_invalid_type(self):
        m = _base_agent()
        m["agent"]["tool_access"] = {"allowed_packages": "not-a-list"}
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("allowed_packages must be an array" in e for e in errors)

    @pytest.mark.asyncio
    async def test_agent_minimal_valid(self):
        """Agent with only required fields is valid."""
        m = _base_agent()
        m["agent"] = {
            "entrypoint": "test_agent.main:run_agent",
            "goal": "Research documents",
        }
        valid, errors, _ = await validate_manifest(m)
        assert valid is True, f"errors: {errors}"


# ---------------------------------------------------------------------------
# Toolpack requires tools, agent does not
# ---------------------------------------------------------------------------

class TestToolsRequirement:
    @pytest.mark.asyncio
    async def test_toolpack_requires_tools(self):
        m = _base_toolpack()
        m["capabilities"]["tools"] = []
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("at least 1 tool" in e for e in errors)

    @pytest.mark.asyncio
    async def test_agent_does_not_require_tools(self):
        """Agents don't provide tools, they consume them."""
        m = _base_agent()
        assert m["capabilities"]["tools"] == []
        valid, errors, _ = await validate_manifest(m)
        assert valid is True, f"errors: {errors}"

    @pytest.mark.asyncio
    async def test_upgrade_does_not_require_tools(self):
        m = _base_upgrade()
        assert m["capabilities"]["tools"] == []
        valid, errors, _ = await validate_manifest(m)
        assert valid is True, f"errors: {errors}"


# ---------------------------------------------------------------------------
# Defaulting behavior — _validate_type_combination uses defaults
# ---------------------------------------------------------------------------

class TestCombinationDefaulting:
    """Verify that missing runtime/install_mode/package_type default to
    valid values so _validate_type_combination doesn't false-positive."""

    @pytest.mark.asyncio
    async def test_missing_runtime_defaults_to_python(self):
        """Missing runtime should default to 'python' in combination check."""
        m = _base_toolpack()
        del m["runtime"]
        valid, errors, _ = await validate_manifest(m)
        # Fails on missing runtime in the enum check, but NOT on combination
        combo_errors = [e for e in errors if "Invalid combination" in e]
        assert combo_errors == [], f"Unexpected combo error: {combo_errors}"

    @pytest.mark.asyncio
    async def test_missing_install_mode_defaults_to_package(self):
        """Missing install_mode should default to 'package' in combination check."""
        m = _base_toolpack()
        del m["install_mode"]
        valid, errors, _ = await validate_manifest(m)
        combo_errors = [e for e in errors if "Invalid combination" in e]
        assert combo_errors == [], f"Unexpected combo error: {combo_errors}"

    @pytest.mark.asyncio
    async def test_missing_package_type_defaults_to_toolpack(self):
        """Missing package_type should default to 'toolpack' in combination check."""
        m = _base_toolpack()
        del m["package_type"]
        valid, errors, _ = await validate_manifest(m)
        combo_errors = [e for e in errors if "Invalid combination" in e]
        assert combo_errors == [], f"Unexpected combo error: {combo_errors}"

    @pytest.mark.asyncio
    async def test_all_defaults_together(self):
        """Missing all three fields should default to toolpack+python+package."""
        m = _base_toolpack()
        del m["package_type"]
        del m["runtime"]
        del m["install_mode"]
        valid, errors, _ = await validate_manifest(m)
        combo_errors = [e for e in errors if "Invalid combination" in e]
        assert combo_errors == [], f"Unexpected combo error: {combo_errors}"


# ---------------------------------------------------------------------------
# Error message quality
# ---------------------------------------------------------------------------

class TestErrorMessageQuality:
    """Verify error messages are specific and actionable."""

    @pytest.mark.asyncio
    async def test_agent_combo_error_mentions_python(self):
        """Agent combo error should tell user what IS valid."""
        m = _base_agent(runtime="remote", install_mode="remote_endpoint")
        _, errors, _ = await validate_manifest(m)
        combo_errors = [e for e in errors if "Invalid combination" in e]
        assert len(combo_errors) == 1
        assert "runtime=python" in combo_errors[0]
        assert "install_mode=package" in combo_errors[0]

    @pytest.mark.asyncio
    async def test_toolpack_combo_error_lists_options(self):
        """Toolpack combo error should list valid options."""
        m = _base_toolpack(runtime="mcp", install_mode="remote_endpoint")
        _, errors, _ = await validate_manifest(m)
        combo_errors = [e for e in errors if "Invalid combination" in e]
        assert len(combo_errors) == 1
        assert "python+package" in combo_errors[0] or "Toolpacks support" in combo_errors[0]

    @pytest.mark.asyncio
    async def test_upgrade_combo_error_mentions_python(self):
        m = _base_upgrade(runtime="mcp")
        _, errors, _ = await validate_manifest(m)
        combo_errors = [e for e in errors if "Invalid combination" in e]
        assert len(combo_errors) == 1
        assert "runtime=python" in combo_errors[0]


# ---------------------------------------------------------------------------
# PR 7: Orchestration validation
# ---------------------------------------------------------------------------

def _sequential_agent(**overrides) -> dict:
    """Valid agent with sequential orchestration (no entrypoint needed)."""
    m = _base_agent()
    m["agent"] = {
        "goal": "Multi-step data pipeline",
        "orchestration": {
            "mode": "sequential",
            "steps": [
                {
                    "name": "extract",
                    "tool": "csv-pack:read_csv",
                    "input_mapping": {"file_path": "$input.path"},
                },
                {
                    "name": "transform",
                    "tool": "transform-pack:clean",
                    "input_mapping": {"data": "$steps.extract"},
                },
            ],
        },
        "tool_access": {"allowed_packages": ["csv-pack", "transform-pack"]},
        "limits": {"max_runtime_seconds": 120},
    }
    m.update(overrides)
    return m


class TestOrchestrationValidation:
    """PR 7: sequential orchestration validation."""

    @pytest.mark.asyncio
    async def test_valid_sequential_orchestration(self):
        m = _sequential_agent()
        _, errors, _ = await validate_manifest(m)
        orch_errors = [e for e in errors if "orchestration" in e.lower()]
        assert orch_errors == []

    @pytest.mark.asyncio
    async def test_sequential_no_entrypoint_ok(self):
        """Sequential agents don't need entrypoint."""
        m = _sequential_agent()
        assert "entrypoint" not in m["agent"]
        _, errors, _ = await validate_manifest(m)
        entrypoint_errors = [e for e in errors if "entrypoint" in e]
        assert entrypoint_errors == []

    @pytest.mark.asyncio
    async def test_non_sequential_requires_entrypoint(self):
        """Non-orchestrated agents still require entrypoint."""
        m = _base_agent()
        del m["agent"]["entrypoint"]
        _, errors, _ = await validate_manifest(m)
        assert any("entrypoint is required" in e for e in errors)

    @pytest.mark.asyncio
    async def test_orchestration_mode_must_be_sequential(self):
        m = _sequential_agent()
        m["agent"]["orchestration"]["mode"] = "parallel"
        _, errors, _ = await validate_manifest(m)
        assert any("must be 'sequential'" in e for e in errors)

    @pytest.mark.asyncio
    async def test_orchestration_not_dict_rejected(self):
        m = _base_agent()
        m["agent"]["orchestration"] = "invalid"
        _, errors, _ = await validate_manifest(m)
        assert any("must be an object" in e for e in errors)

    @pytest.mark.asyncio
    async def test_steps_required_for_sequential(self):
        m = _sequential_agent()
        del m["agent"]["orchestration"]["steps"]
        _, errors, _ = await validate_manifest(m)
        assert any("steps is required" in e for e in errors)

    @pytest.mark.asyncio
    async def test_steps_must_be_array(self):
        m = _sequential_agent()
        m["agent"]["orchestration"]["steps"] = "not-array"
        _, errors, _ = await validate_manifest(m)
        assert any("must be an array" in e for e in errors)

    @pytest.mark.asyncio
    async def test_step_must_be_object(self):
        m = _sequential_agent()
        m["agent"]["orchestration"]["steps"] = ["not-object"]
        _, errors, _ = await validate_manifest(m)
        assert any("must be an object" in e for e in errors)

    @pytest.mark.asyncio
    async def test_step_tool_required(self):
        m = _sequential_agent()
        m["agent"]["orchestration"]["steps"] = [
            {"name": "no-tool", "input_mapping": {}},
        ]
        _, errors, _ = await validate_manifest(m)
        assert any(".tool is required" in e for e in errors)

    @pytest.mark.asyncio
    async def test_step_duplicate_names_rejected(self):
        m = _sequential_agent()
        m["agent"]["orchestration"]["steps"] = [
            {"name": "dup", "tool": "pack-a:run"},
            {"name": "dup", "tool": "pack-b:run"},
        ]
        _, errors, _ = await validate_manifest(m)
        assert any("not unique" in e for e in errors)

    @pytest.mark.asyncio
    async def test_step_input_mapping_must_be_dict(self):
        m = _sequential_agent()
        m["agent"]["orchestration"]["steps"] = [
            {"name": "s1", "tool": "pack:run", "input_mapping": "bad"},
        ]
        _, errors, _ = await validate_manifest(m)
        assert any("input_mapping must be an object" in e for e in errors)

    @pytest.mark.asyncio
    async def test_sequential_with_entrypoint_also_valid(self):
        """Having both orchestration and entrypoint is not an error."""
        m = _sequential_agent()
        m["agent"]["entrypoint"] = "my_agent.main:run"
        _, errors, _ = await validate_manifest(m)
        orch_errors = [e for e in errors if "orchestration" in e.lower()]
        assert orch_errors == []

    @pytest.mark.asyncio
    async def test_orchestration_no_longer_deferred(self):
        """orchestration is now supported, should not produce 'not supported' error."""
        m = _sequential_agent()
        _, errors, _ = await validate_manifest(m)
        deferred_errors = [e for e in errors if "not supported in v0.3" in e]
        assert deferred_errors == []

    @pytest.mark.asyncio
    async def test_planning_still_deferred(self):
        m = _base_agent()
        m["agent"]["planning"] = True
        _, errors, _ = await validate_manifest(m)
        assert any("planning" in e and "not supported" in e for e in errors)


# ---------------------------------------------------------------------------
# PR 4: agent.isolation validation
# ---------------------------------------------------------------------------

class TestAgentIsolationValidation:
    @pytest.mark.asyncio
    async def test_isolation_process_valid(self):
        m = _base_agent()
        m["agent"]["isolation"] = "process"
        valid, errors, _ = await validate_manifest(m)
        isolation_errors = [e for e in errors if "isolation" in e]
        assert isolation_errors == []

    @pytest.mark.asyncio
    async def test_isolation_thread_valid(self):
        m = _base_agent()
        m["agent"]["isolation"] = "thread"
        valid, errors, _ = await validate_manifest(m)
        isolation_errors = [e for e in errors if "isolation" in e]
        assert isolation_errors == []

    @pytest.mark.asyncio
    async def test_isolation_invalid_value(self):
        m = _base_agent()
        m["agent"]["isolation"] = "container"
        valid, errors, _ = await validate_manifest(m)
        assert any("isolation" in e and "'process' or 'thread'" in e for e in errors)

    @pytest.mark.asyncio
    async def test_no_isolation_field_ok(self):
        """Omitting isolation is fine — defaults at runtime."""
        m = _base_agent()
        assert "isolation" not in m["agent"]
        valid, errors, _ = await validate_manifest(m)
        isolation_errors = [e for e in errors if "isolation" in e]
        assert isolation_errors == []


# ---------------------------------------------------------------------------
# PR 7: when condition validation
# ---------------------------------------------------------------------------

class TestWhenConditionValidation:
    @pytest.mark.asyncio
    async def test_valid_when_equals(self):
        m = _sequential_agent()
        m["agent"]["orchestration"]["steps"][0]["when"] = "$input.flag == true"
        _, errors, _ = await validate_manifest(m)
        when_errors = [e for e in errors if ".when" in e]
        assert when_errors == [], f"Unexpected when errors: {when_errors}"

    @pytest.mark.asyncio
    async def test_valid_when_not_equals(self):
        m = _sequential_agent()
        m["agent"]["orchestration"]["steps"][0]["when"] = "$steps.prev != null"
        _, errors, _ = await validate_manifest(m)
        when_errors = [e for e in errors if ".when" in e]
        assert when_errors == []

    @pytest.mark.asyncio
    async def test_valid_when_is_null(self):
        m = _sequential_agent()
        m["agent"]["orchestration"]["steps"][0]["when"] = "$steps.prev is null"
        _, errors, _ = await validate_manifest(m)
        when_errors = [e for e in errors if ".when" in e]
        assert when_errors == []

    @pytest.mark.asyncio
    async def test_valid_when_is_not_null(self):
        m = _sequential_agent()
        m["agent"]["orchestration"]["steps"][0]["when"] = "$steps.prev is not null"
        _, errors, _ = await validate_manifest(m)
        when_errors = [e for e in errors if ".when" in e]
        assert when_errors == []

    @pytest.mark.asyncio
    async def test_invalid_when_syntax(self):
        m = _sequential_agent()
        m["agent"]["orchestration"]["steps"][0]["when"] = "some random stuff"
        _, errors, _ = await validate_manifest(m)
        assert any(".when" in e and "invalid syntax" in e for e in errors)

    @pytest.mark.asyncio
    async def test_when_must_have_dollar_ref(self):
        m = _sequential_agent()
        m["agent"]["orchestration"]["steps"][0]["when"] = "flag == true"
        _, errors, _ = await validate_manifest(m)
        assert any("$reference" in e for e in errors)

    @pytest.mark.asyncio
    async def test_when_not_string_rejected(self):
        m = _sequential_agent()
        m["agent"]["orchestration"]["steps"][0]["when"] = 42
        _, errors, _ = await validate_manifest(m)
        assert any(".when must be a string" in e for e in errors)

    @pytest.mark.asyncio
    async def test_empty_when_rejected(self):
        m = _sequential_agent()
        m["agent"]["orchestration"]["steps"][0]["when"] = ""
        _, errors, _ = await validate_manifest(m)
        assert any(".when must not be empty" in e for e in errors)

    @pytest.mark.asyncio
    async def test_no_when_field_ok(self):
        """Steps without when run unconditionally — no error."""
        m = _sequential_agent()
        assert "when" not in m["agent"]["orchestration"]["steps"][0]
        _, errors, _ = await validate_manifest(m)
        when_errors = [e for e in errors if ".when" in e]
        assert when_errors == []


# ---------------------------------------------------------------------------
# PR 6: resource.content_path validation
# ---------------------------------------------------------------------------

class TestResourceContentPathValidation:
    @pytest.mark.asyncio
    async def test_valid_content_path(self):
        m = _base_toolpack()
        m["capabilities"]["resources"] = [{
            "name": "api_spec",
            "capability_id": "api_reference",
            "uri": "resource://test/spec",
            "content_path": "resources/api_spec.json",
        }]
        valid, errors, _ = await validate_manifest(m)
        cp_errors = [e for e in errors if "content_path" in e]
        assert cp_errors == [], f"Unexpected errors: {cp_errors}"

    @pytest.mark.asyncio
    async def test_content_path_no_traversal(self):
        m = _base_toolpack()
        m["capabilities"]["resources"] = [{
            "name": "evil",
            "capability_id": "api_reference",
            "uri": "resource://test/evil",
            "content_path": "../../../etc/passwd",
        }]
        valid, errors, _ = await validate_manifest(m)
        assert any("content_path" in e and ".." in e for e in errors)

    @pytest.mark.asyncio
    async def test_content_path_no_absolute(self):
        m = _base_toolpack()
        m["capabilities"]["resources"] = [{
            "name": "abs",
            "capability_id": "api_reference",
            "uri": "resource://test/abs",
            "content_path": "/etc/passwd",
        }]
        valid, errors, _ = await validate_manifest(m)
        assert any("content_path" in e for e in errors)
