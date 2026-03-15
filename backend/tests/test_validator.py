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
