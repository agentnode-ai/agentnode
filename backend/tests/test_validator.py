"""Unit tests for ANP manifest validator."""
import pytest

from app.packages.validator import validate_manifest

VALID_MANIFEST = {
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
    "compatibility": {"frameworks": ["generic"], "python": ">=3.10"},
    "permissions": {
        "network": {"level": "none", "allowed_domains": []},
        "filesystem": {"level": "temp"},
        "code_execution": {"level": "none"},
        "data_access": {"level": "input_only"},
        "user_approval": {"required": "never"},
        "external_integrations": [],
    },
    "tags": ["test"],
    "categories": ["document-processing"],
    "dependencies": [],
}


@pytest.mark.asyncio
async def test_valid_manifest():
    valid, errors, warnings = await validate_manifest(VALID_MANIFEST)
    assert valid is True
    assert errors == []


@pytest.mark.asyncio
async def test_wrong_manifest_version():
    m = {**VALID_MANIFEST, "manifest_version": "2.0"}
    valid, errors, _ = await validate_manifest(m)
    assert valid is False
    assert any("manifest_version" in e for e in errors)


@pytest.mark.asyncio
async def test_invalid_package_id():
    m = {**VALID_MANIFEST, "package_id": "AB"}
    valid, errors, _ = await validate_manifest(m)
    assert valid is False
    assert any("package_id" in e for e in errors)


@pytest.mark.asyncio
async def test_invalid_package_type():
    m = {**VALID_MANIFEST, "package_type": "invalid"}
    valid, errors, _ = await validate_manifest(m)
    assert valid is False


@pytest.mark.asyncio
async def test_missing_name():
    m = {**VALID_MANIFEST, "name": ""}
    valid, errors, _ = await validate_manifest(m)
    assert valid is False


@pytest.mark.asyncio
async def test_invalid_version():
    m = {**VALID_MANIFEST, "version": "not-semver"}
    valid, errors, _ = await validate_manifest(m)
    assert valid is False


@pytest.mark.asyncio
async def test_summary_too_long():
    m = {**VALID_MANIFEST, "summary": "x" * 201}
    valid, errors, _ = await validate_manifest(m)
    assert valid is False


@pytest.mark.asyncio
async def test_runtime_must_be_python():
    m = {**VALID_MANIFEST, "runtime": "typescript"}
    valid, errors, _ = await validate_manifest(m)
    assert valid is False
    assert any("python" in e for e in errors)


@pytest.mark.asyncio
async def test_no_tools():
    m = {**VALID_MANIFEST, "capabilities": {"tools": [], "resources": [], "prompts": []}}
    valid, errors, _ = await validate_manifest(m)
    assert valid is False
    assert any("at least 1 tool" in e for e in errors)


@pytest.mark.asyncio
async def test_missing_permissions():
    m = {k: v for k, v in VALID_MANIFEST.items() if k != "permissions"}
    valid, errors, _ = await validate_manifest(m)
    assert valid is False


@pytest.mark.asyncio
async def test_missing_frameworks():
    m = {**VALID_MANIFEST, "compatibility": {"frameworks": []}}
    valid, errors, _ = await validate_manifest(m)
    assert valid is False


# --- MCP Server Validation Tests ---

VALID_MCP_MANIFEST = {
    "manifest_version": "0.3",
    "package_id": "mcp-test-server",
    "package_type": "toolpack",
    "name": "Test MCP Server",
    "publisher": "test-publisher",
    "version": "1.0.0",
    "summary": "A test MCP server package for validation.",
    "runtime": "mcp",
    "install_mode": "package",
    "hosting_type": "agentnode_hosted",
    "entrypoint": "mcp_test.server",
    "capabilities": {
        "tools": [{
            "name": "test_tool",
            "capability_id": "general",
            "description": "Test tool",
            "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
        }],
        "resources": [],
        "prompts": [],
    },
    "mcp_server": {
        "command": ["npx", "-y", "@modelcontextprotocol/server-test@1.0.0"],
        "transport": "stdio",
        "npm_package": "@modelcontextprotocol/server-test",
        "source_repo": "https://github.com/modelcontextprotocol/servers",
        "env_keys": ["TEST_API_KEY"],
    },
    "permissions": {
        "network": {"level": "none", "allowed_domains": []},
        "filesystem": {"level": "none"},
        "code_execution": {"level": "none"},
        "data_access": {"level": "input_only"},
        "user_approval": {"required": "once"},
        "external_integrations": [],
    },
    "tags": ["mcp", "mcp-server"],
    "categories": ["general"],
    "compatibility": {"frameworks": ["mcp"]},
}


@pytest.mark.asyncio
async def test_mcp_valid_manifest():
    valid, errors, warnings = await validate_manifest(VALID_MCP_MANIFEST)
    assert valid is True, f"Errors: {errors}"
    assert errors == []


@pytest.mark.asyncio
async def test_mcp_missing_command():
    m = {**VALID_MCP_MANIFEST, "mcp_server": {**VALID_MCP_MANIFEST["mcp_server"]}}
    del m["mcp_server"]["command"]
    valid, errors, _ = await validate_manifest(m)
    assert valid is False
    assert any("command" in e for e in errors)


@pytest.mark.asyncio
async def test_mcp_command_not_list():
    m = {**VALID_MCP_MANIFEST, "mcp_server": {**VALID_MCP_MANIFEST["mcp_server"], "command": "npx foo"}}
    valid, errors, _ = await validate_manifest(m)
    assert valid is False
    assert any("command" in e and "list" in e for e in errors)


@pytest.mark.asyncio
async def test_mcp_command_empty_entry():
    m = {**VALID_MCP_MANIFEST, "mcp_server": {**VALID_MCP_MANIFEST["mcp_server"], "command": ["npx", ""]}}
    valid, errors, _ = await validate_manifest(m)
    assert valid is False
    assert any("empty" in e for e in errors)


@pytest.mark.asyncio
async def test_mcp_command_too_many_elements():
    m = {**VALID_MCP_MANIFEST, "mcp_server": {**VALID_MCP_MANIFEST["mcp_server"], "command": ["arg"] * 21}}
    valid, errors, _ = await validate_manifest(m)
    assert valid is False
    assert any("max 20" in e for e in errors)


@pytest.mark.asyncio
async def test_mcp_command_element_too_long():
    m = {**VALID_MCP_MANIFEST, "mcp_server": {**VALID_MCP_MANIFEST["mcp_server"], "command": ["npx", "x" * 501]}}
    valid, errors, _ = await validate_manifest(m)
    assert valid is False
    assert any("500 characters" in e for e in errors)


@pytest.mark.asyncio
async def test_mcp_command_total_size_exceeded():
    m = {**VALID_MCP_MANIFEST, "mcp_server": {**VALID_MCP_MANIFEST["mcp_server"], "command": ["x" * 400] * 11}}
    valid, errors, _ = await validate_manifest(m)
    assert valid is False
    assert any("4000" in e for e in errors)


@pytest.mark.asyncio
async def test_mcp_command_null_bytes():
    m = {**VALID_MCP_MANIFEST, "mcp_server": {**VALID_MCP_MANIFEST["mcp_server"], "command": ["npx", "foo\x00bar"]}}
    valid, errors, _ = await validate_manifest(m)
    assert valid is False
    assert any("null" in e for e in errors)


@pytest.mark.asyncio
async def test_mcp_command_unknown_executable_warning():
    m = {**VALID_MCP_MANIFEST, "mcp_server": {**VALID_MCP_MANIFEST["mcp_server"], "command": ["custom-bin", "--serve"]}}
    valid, errors, warnings = await validate_manifest(m)
    assert valid is True
    assert any("known MCP executable" in w for w in warnings)


@pytest.mark.asyncio
async def test_mcp_command_relative_path_warning():
    m = {**VALID_MCP_MANIFEST, "mcp_server": {**VALID_MCP_MANIFEST["mcp_server"], "command": ["./run.sh"]}}
    valid, errors, warnings = await validate_manifest(m)
    assert valid is True
    assert any("relative path" in w for w in warnings)


@pytest.mark.asyncio
async def test_mcp_invalid_transport():
    m = {**VALID_MCP_MANIFEST, "mcp_server": {**VALID_MCP_MANIFEST["mcp_server"], "transport": "websocket"}}
    valid, errors, _ = await validate_manifest(m)
    assert valid is False
    assert any("transport" in e for e in errors)


@pytest.mark.asyncio
async def test_mcp_source_repo_not_https():
    m = {**VALID_MCP_MANIFEST, "mcp_server": {**VALID_MCP_MANIFEST["mcp_server"], "source_repo": "http://github.com/foo/bar"}}
    valid, errors, _ = await validate_manifest(m)
    assert valid is False
    assert any("https" in e for e in errors)


@pytest.mark.asyncio
async def test_mcp_source_repo_invalid_host():
    m = {**VALID_MCP_MANIFEST, "mcp_server": {**VALID_MCP_MANIFEST["mcp_server"], "source_repo": "https://evil.com/foo/bar"}}
    valid, errors, _ = await validate_manifest(m)
    assert valid is False
    assert any("host" in e for e in errors)


@pytest.mark.asyncio
async def test_mcp_source_repo_too_long():
    m = {**VALID_MCP_MANIFEST, "mcp_server": {**VALID_MCP_MANIFEST["mcp_server"], "source_repo": "https://github.com/" + "x" * 1000}}
    valid, errors, _ = await validate_manifest(m)
    assert valid is False
    assert any("1000" in e for e in errors)


@pytest.mark.asyncio
async def test_mcp_env_keys_invalid_format():
    m = {**VALID_MCP_MANIFEST, "mcp_server": {**VALID_MCP_MANIFEST["mcp_server"], "env_keys": ["valid_KEY", "invalid-key"]}}
    valid, errors, _ = await validate_manifest(m)
    assert valid is False
    assert any("env_keys" in e for e in errors)


@pytest.mark.asyncio
async def test_mcp_env_keys_valid():
    m = {**VALID_MCP_MANIFEST, "mcp_server": {**VALID_MCP_MANIFEST["mcp_server"], "env_keys": ["API_KEY", "DATABASE_URL"]}}
    valid, errors, _ = await validate_manifest(m)
    assert valid is True


@pytest.mark.asyncio
async def test_mcp_server_without_mcp_runtime():
    m = {**VALID_MANIFEST, "mcp_server": {"command": ["npx", "foo"]}}
    valid, errors, _ = await validate_manifest(m)
    assert valid is False
    assert any("requires runtime=mcp" in e for e in errors)


@pytest.mark.asyncio
async def test_mcp_runtime_without_mcp_server_warning():
    m = {**VALID_MCP_MANIFEST}
    del m["mcp_server"]
    _, _, warnings = await validate_manifest(m)
    assert any("mcp_server" in w for w in warnings)


@pytest.mark.asyncio
async def test_mcp_gitlab_source_repo():
    m = {**VALID_MCP_MANIFEST, "mcp_server": {**VALID_MCP_MANIFEST["mcp_server"], "source_repo": "https://gitlab.com/foo/bar"}}
    valid, errors, _ = await validate_manifest(m)
    assert valid is True
