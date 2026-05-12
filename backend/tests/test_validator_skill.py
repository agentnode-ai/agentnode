"""Unit tests for skill package validation."""
import pytest

from app.packages.validator import validate_manifest, validate_artifact_quality


def _base_skill(**overrides) -> dict:
    """Valid skill+none+prompt_only manifest."""
    m = {
        "manifest_version": "0.3",
        "package_id": "test-skill",
        "package_type": "skill",
        "name": "Test Skill",
        "publisher": "test-publisher",
        "version": "1.0.0",
        "summary": "A valid test skill manifest for testing.",
        "runtime": "none",
        "install_mode": "prompt_only",
        "hosting_type": "agentnode_hosted",
        "capabilities": {
            "tools": [],
            "resources": [],
            "prompts": [
                {
                    "name": "test-prompt",
                    "description": "A test prompt",
                    "template": "SKILL.md",
                    "arguments": [
                        {"name": "topic", "required": True},
                    ],
                },
            ],
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
        "categories": ["writing"],
        "dependencies": [],
    }
    m.update(overrides)
    return m


# ---------------------------------------------------------------------------
# Valid manifests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_valid_skill():
    valid, errors, warnings = await validate_manifest(_base_skill())
    assert valid, f"Unexpected errors: {errors}"


@pytest.mark.asyncio
async def test_valid_skill_with_assets():
    m = _base_skill(assets=[
        {
            "id": "example-doc",
            "type": "document",
            "path": "assets/examples/example.md",
            "description": "An example document",
        },
    ])
    valid, errors, warnings = await validate_manifest(m)
    assert valid, f"Unexpected errors: {errors}"


@pytest.mark.asyncio
async def test_valid_skill_no_tools():
    m = _base_skill()
    m["capabilities"]["tools"] = []
    valid, errors, warnings = await validate_manifest(m)
    assert valid, f"Unexpected errors: {errors}"


@pytest.mark.asyncio
async def test_valid_skill_tools_absent():
    m = _base_skill()
    del m["capabilities"]["tools"]
    valid, errors, warnings = await validate_manifest(m)
    assert valid, f"Unexpected errors: {errors}"


@pytest.mark.asyncio
async def test_manifest_version_03_accepted():
    m = _base_skill()
    assert m["manifest_version"] == "0.3"
    valid, errors, warnings = await validate_manifest(m)
    assert valid, f"Unexpected errors: {errors}"


@pytest.mark.asyncio
async def test_skill_multiple_prompts():
    m = _base_skill()
    m["capabilities"]["prompts"].append({
        "name": "second-prompt",
        "template": "BRIEF.md",
    })
    valid, errors, warnings = await validate_manifest(m)
    assert valid, f"Unexpected errors: {errors}"


# ---------------------------------------------------------------------------
# Invalid manifests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_skill_without_prompts():
    m = _base_skill()
    m["capabilities"]["prompts"] = []
    valid, errors, warnings = await validate_manifest(m)
    assert not valid
    assert any("at least 1 prompt" in e for e in errors)


@pytest.mark.asyncio
async def test_skill_prompt_missing_name():
    m = _base_skill()
    m["capabilities"]["prompts"] = [{"template": "SKILL.md"}]
    valid, errors, warnings = await validate_manifest(m)
    assert not valid
    assert any("name is required" in e for e in errors)


@pytest.mark.asyncio
async def test_skill_prompt_missing_template():
    m = _base_skill()
    m["capabilities"]["prompts"] = [{"name": "test"}]
    valid, errors, warnings = await validate_manifest(m)
    assert not valid
    assert any("template is required" in e for e in errors)


@pytest.mark.asyncio
async def test_skill_with_entrypoint_rejected():
    m = _base_skill(entrypoint="my_skill.tool")
    valid, errors, warnings = await validate_manifest(m)
    assert not valid
    assert any("entrypoint" in e and "skill" in e.lower() for e in errors)


@pytest.mark.asyncio
async def test_skill_with_agent_section_rejected():
    m = _base_skill(agent={"entrypoint": "test:run", "goal": "test"})
    valid, errors, warnings = await validate_manifest(m)
    assert not valid
    assert any("agent" in e and "skill" in e.lower() for e in errors)


@pytest.mark.asyncio
async def test_skill_with_connector_section_rejected():
    m = _base_skill(connector={"provider": "slack", "auth_type": "oauth2"})
    valid, errors, warnings = await validate_manifest(m)
    assert not valid
    assert any("connector" in e and "skill" in e.lower() for e in errors)


@pytest.mark.asyncio
async def test_skill_invalid_runtime():
    m = _base_skill(runtime="python")
    valid, errors, warnings = await validate_manifest(m)
    assert not valid
    assert any("Invalid combination" in e for e in errors)


@pytest.mark.asyncio
async def test_skill_invalid_install_mode():
    m = _base_skill(install_mode="package")
    valid, errors, warnings = await validate_manifest(m)
    assert not valid
    assert any("Invalid combination" in e for e in errors)


# ---------------------------------------------------------------------------
# Asset validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_asset_path_traversal_rejected():
    m = _base_skill(assets=[{
        "id": "bad-asset",
        "type": "document",
        "path": "../etc/passwd",
        "description": "Bad path",
    }])
    valid, errors, warnings = await validate_manifest(m)
    assert not valid
    assert any(".." in e for e in errors)


@pytest.mark.asyncio
async def test_asset_absolute_path_rejected():
    m = _base_skill(assets=[{
        "id": "bad-asset",
        "type": "document",
        "path": "/etc/passwd",
        "description": "Bad path",
    }])
    valid, errors, warnings = await validate_manifest(m)
    assert not valid
    assert any("relative" in e for e in errors)


@pytest.mark.asyncio
async def test_asset_duplicate_id_rejected():
    m = _base_skill(assets=[
        {"id": "same-id", "type": "document", "path": "a.md", "description": "First"},
        {"id": "same-id", "type": "document", "path": "b.md", "description": "Second"},
    ])
    valid, errors, warnings = await validate_manifest(m)
    assert not valid
    assert any("duplicate" in e.lower() for e in errors)


@pytest.mark.asyncio
async def test_asset_invalid_type_rejected():
    m = _base_skill(assets=[{
        "id": "bad-type",
        "type": "binary",
        "path": "assets/file.bin",
        "description": "Bad type",
    }])
    valid, errors, warnings = await validate_manifest(m)
    assert not valid
    assert any("type" in e and "binary" in e for e in errors)


@pytest.mark.asyncio
async def test_asset_missing_description():
    m = _base_skill(assets=[{
        "id": "no-desc",
        "type": "document",
        "path": "assets/file.md",
    }])
    valid, errors, warnings = await validate_manifest(m)
    assert not valid
    assert any("description" in e for e in errors)


@pytest.mark.asyncio
async def test_asset_missing_id():
    m = _base_skill(assets=[{
        "type": "document",
        "path": "assets/file.md",
        "description": "Missing id",
    }])
    valid, errors, warnings = await validate_manifest(m)
    assert not valid
    assert any("id" in e and "required" in e for e in errors)


# ---------------------------------------------------------------------------
# Artifact quality
# ---------------------------------------------------------------------------

def test_artifact_quality_skill_with_skill_md():
    """Skills with SKILL.md pass quality gate."""
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = b"# My Skill\nInstructions here."
        info = tarfile.TarInfo(name="test-skill/SKILL.md")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

        manifest = b"package_id: test-skill\npackage_type: skill"
        info2 = tarfile.TarInfo(name="test-skill/agentnode.yaml")
        info2.size = len(manifest)
        tar.addfile(info2, io.BytesIO(manifest))

    errors, warnings = validate_artifact_quality(buf.getvalue(), "test-skill", package_type="skill")
    assert not errors, f"Unexpected errors: {errors}"


def test_artifact_quality_skill_without_skill_md():
    """Skills without SKILL.md fail quality gate."""
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        manifest = b"package_id: test-skill\npackage_type: skill"
        info = tarfile.TarInfo(name="test-skill/agentnode.yaml")
        info.size = len(manifest)
        tar.addfile(info, io.BytesIO(manifest))

    errors, warnings = validate_artifact_quality(buf.getvalue(), "test-skill", package_type="skill")
    assert any("SKILL.md" in e for e in errors)


def test_artifact_quality_skill_no_tests_required():
    """Skills don't need test files."""
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = b"# My Skill"
        info = tarfile.TarInfo(name="test-skill/SKILL.md")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    errors, warnings = validate_artifact_quality(buf.getvalue(), "test-skill", package_type="skill")
    assert not any("test files" in e.lower() for e in errors)
