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
    m = _base_skill(
        assets=[
            {
                "id": "example-doc",
                "type": "document",
                "path": "assets/examples/example.md",
                "description": "An example document",
            },
        ]
    )
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
    m["capabilities"]["prompts"].append(
        {
            "name": "second-prompt",
            "template": "BRIEF.md",
        }
    )
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
    m = _base_skill(
        assets=[
            {
                "id": "bad-asset",
                "type": "document",
                "path": "../etc/passwd",
                "description": "Bad path",
            }
        ]
    )
    valid, errors, warnings = await validate_manifest(m)
    assert not valid
    assert any(".." in e for e in errors)


@pytest.mark.asyncio
async def test_asset_absolute_path_rejected():
    m = _base_skill(
        assets=[
            {
                "id": "bad-asset",
                "type": "document",
                "path": "/etc/passwd",
                "description": "Bad path",
            }
        ]
    )
    valid, errors, warnings = await validate_manifest(m)
    assert not valid
    assert any("relative" in e for e in errors)


@pytest.mark.asyncio
async def test_asset_duplicate_id_rejected():
    m = _base_skill(
        assets=[
            {
                "id": "same-id",
                "type": "document",
                "path": "a.md",
                "description": "First",
            },
            {
                "id": "same-id",
                "type": "document",
                "path": "b.md",
                "description": "Second",
            },
        ]
    )
    valid, errors, warnings = await validate_manifest(m)
    assert not valid
    assert any("duplicate" in e.lower() for e in errors)


@pytest.mark.asyncio
async def test_asset_invalid_type_rejected():
    m = _base_skill(
        assets=[
            {
                "id": "bad-type",
                "type": "binary",
                "path": "assets/file.bin",
                "description": "Bad type",
            }
        ]
    )
    valid, errors, warnings = await validate_manifest(m)
    assert not valid
    assert any("type" in e and "binary" in e for e in errors)


@pytest.mark.asyncio
async def test_asset_missing_description():
    m = _base_skill(
        assets=[
            {
                "id": "no-desc",
                "type": "document",
                "path": "assets/file.md",
            }
        ]
    )
    valid, errors, warnings = await validate_manifest(m)
    assert not valid
    assert any("description" in e for e in errors)


@pytest.mark.asyncio
async def test_asset_missing_id():
    m = _base_skill(
        assets=[
            {
                "type": "document",
                "path": "assets/file.md",
                "description": "Missing id",
            }
        ]
    )
    valid, errors, warnings = await validate_manifest(m)
    assert not valid
    assert any("id" in e and "required" in e for e in errors)


# ---------------------------------------------------------------------------
# Skill permissions enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skill_network_not_none_rejected():
    m = _base_skill()
    m["permissions"]["network"]["level"] = "restricted"
    valid, errors, warnings = await validate_manifest(m)
    assert not valid
    assert any("network" in e and "none" in e for e in errors)


@pytest.mark.asyncio
async def test_skill_filesystem_not_none_rejected():
    m = _base_skill()
    m["permissions"]["filesystem"]["level"] = "temp"
    valid, errors, warnings = await validate_manifest(m)
    assert not valid
    assert any("filesystem" in e and "none" in e for e in errors)


@pytest.mark.asyncio
async def test_skill_code_execution_not_none_rejected():
    m = _base_skill()
    m["permissions"]["code_execution"]["level"] = "limited_subprocess"
    valid, errors, warnings = await validate_manifest(m)
    assert not valid
    assert any("code_execution" in e and "none" in e for e in errors)


@pytest.mark.asyncio
async def test_skill_all_permissions_none_accepted():
    m = _base_skill()
    valid, errors, warnings = await validate_manifest(m)
    assert valid, f"Unexpected errors: {errors}"


# ---------------------------------------------------------------------------
# Artifact quality
# ---------------------------------------------------------------------------


def _build_skill_artifact(files: dict[str, bytes]) -> bytes:
    """Build a tar.gz with given files under test-skill/ prefix."""
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name=f"test-skill/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _skill_manifest_yaml(**overrides) -> bytes:
    """Build minimal skill manifest YAML bytes."""
    import yaml

    m = {
        "package_id": "test-skill",
        "package_type": "skill",
        "capabilities": {
            "prompts": [{"name": "main", "template": "SKILL.md"}],
        },
    }
    m.update(overrides)
    return yaml.dump(m).encode()


def test_artifact_quality_skill_with_skill_md():
    """Skills with SKILL.md and only declared files pass quality gate."""
    artifact = _build_skill_artifact(
        {
            "agentnode.yaml": _skill_manifest_yaml(),
            "SKILL.md": b"# My Skill\nInstructions here.",
        }
    )
    errors, warnings = validate_artifact_quality(
        artifact, "test-skill", package_type="skill"
    )
    assert not errors, f"Unexpected errors: {errors}"


def test_artifact_quality_skill_without_skill_md():
    """Skills without SKILL.md fail quality gate."""
    artifact = _build_skill_artifact(
        {
            "agentnode.yaml": _skill_manifest_yaml(),
        }
    )
    errors, warnings = validate_artifact_quality(
        artifact, "test-skill", package_type="skill"
    )
    assert any("SKILL.md" in e for e in errors)


def test_artifact_quality_skill_no_tests_required():
    """Skills don't need test files."""
    artifact = _build_skill_artifact(
        {
            "agentnode.yaml": _skill_manifest_yaml(),
            "SKILL.md": b"# My Skill",
        }
    )
    errors, warnings = validate_artifact_quality(
        artifact, "test-skill", package_type="skill"
    )
    assert not any("test files" in e.lower() for e in errors)


def test_artifact_quality_skill_py_file_rejected():
    """Python files in skill artifacts are blocked."""
    import yaml

    manifest = yaml.dump(
        {
            "package_id": "test-skill",
            "package_type": "skill",
            "capabilities": {"prompts": [{"name": "main", "template": "SKILL.md"}]},
            "assets": [{"id": "helper", "type": "document", "path": "helper.py"}],
        }
    ).encode()
    artifact = _build_skill_artifact(
        {
            "agentnode.yaml": manifest,
            "SKILL.md": b"# Skill",
            "helper.py": b"print('hello')",
        }
    )
    errors, warnings = validate_artifact_quality(
        artifact, "test-skill", package_type="skill"
    )
    assert any("forbidden extension" in e and ".py" in e for e in errors)


def test_artifact_quality_skill_undeclared_file_rejected():
    """Files not declared in manifest are blocked."""
    artifact = _build_skill_artifact(
        {
            "agentnode.yaml": _skill_manifest_yaml(),
            "SKILL.md": b"# Skill",
            "extra.md": b"# Extra file",
        }
    )
    errors, warnings = validate_artifact_quality(
        artifact, "test-skill", package_type="skill"
    )
    assert any("undeclared" in e.lower() and "extra.md" in e for e in errors)


def test_artifact_quality_skill_pyproject_rejected():
    """pyproject.toml in skill artifacts is blocked."""
    import yaml

    manifest = yaml.dump(
        {
            "package_id": "test-skill",
            "package_type": "skill",
            "capabilities": {"prompts": [{"name": "main", "template": "SKILL.md"}]},
            "assets": [{"id": "proj", "type": "data", "path": "pyproject.toml"}],
        }
    ).encode()
    artifact = _build_skill_artifact(
        {
            "agentnode.yaml": manifest,
            "SKILL.md": b"# Skill",
            "pyproject.toml": b"[project]\nname = 'evil'",
        }
    )
    errors, warnings = validate_artifact_quality(
        artifact, "test-skill", package_type="skill"
    )
    assert any("forbidden" in e.lower() and "pyproject" in e.lower() for e in errors)


def test_artifact_quality_skill_shell_script_rejected():
    """Shell scripts in skill artifacts are blocked."""
    import yaml

    manifest = yaml.dump(
        {
            "package_id": "test-skill",
            "package_type": "skill",
            "capabilities": {"prompts": [{"name": "main", "template": "SKILL.md"}]},
            "assets": [{"id": "run", "type": "document", "path": "run.sh"}],
        }
    ).encode()
    artifact = _build_skill_artifact(
        {
            "agentnode.yaml": manifest,
            "SKILL.md": b"# Skill",
            "run.sh": b"#!/bin/bash\necho pwned",
        }
    )
    errors, warnings = validate_artifact_quality(
        artifact, "test-skill", package_type="skill"
    )
    assert any("forbidden extension" in e and ".sh" in e for e in errors)


def test_artifact_quality_skill_js_rejected():
    """.js files in skill artifacts are blocked."""
    import yaml

    manifest = yaml.dump(
        {
            "package_id": "test-skill",
            "package_type": "skill",
            "capabilities": {"prompts": [{"name": "main", "template": "SKILL.md"}]},
            "assets": [{"id": "script", "type": "data", "path": "helper.js"}],
        }
    ).encode()
    artifact = _build_skill_artifact(
        {
            "agentnode.yaml": manifest,
            "SKILL.md": b"# Skill",
            "helper.js": b"console.log('pwned')",
        }
    )
    errors, warnings = validate_artifact_quality(
        artifact, "test-skill", package_type="skill"
    )
    assert any("forbidden extension" in e and ".js" in e for e in errors)


def test_artifact_quality_skill_declared_asset_accepted():
    """Declared assets with allowed extensions pass."""
    import yaml

    manifest = yaml.dump(
        {
            "package_id": "test-skill",
            "package_type": "skill",
            "capabilities": {"prompts": [{"name": "main", "template": "SKILL.md"}]},
            "assets": [
                {"id": "example", "type": "document", "path": "assets/example.md"},
                {"id": "data", "type": "data", "path": "assets/data.json"},
            ],
        }
    ).encode()
    artifact = _build_skill_artifact(
        {
            "agentnode.yaml": manifest,
            "SKILL.md": b"# Skill",
            "assets/example.md": b"# Example",
            "assets/data.json": b'{"key": "value"}',
        }
    )
    errors, warnings = validate_artifact_quality(
        artifact, "test-skill", package_type="skill"
    )
    assert not errors, f"Unexpected errors: {errors}"


def test_artifact_quality_skill_prompt_template_accepted():
    """Prompt template files declared in capabilities.prompts pass."""
    import yaml

    manifest = yaml.dump(
        {
            "package_id": "test-skill",
            "package_type": "skill",
            "capabilities": {
                "prompts": [
                    {"name": "main", "template": "SKILL.md"},
                    {"name": "brief", "template": "BRIEF.md"},
                ],
            },
        }
    ).encode()
    artifact = _build_skill_artifact(
        {
            "agentnode.yaml": manifest,
            "SKILL.md": b"# Main",
            "BRIEF.md": b"# Brief",
        }
    )
    errors, warnings = validate_artifact_quality(
        artifact, "test-skill", package_type="skill"
    )
    assert not errors, f"Unexpected errors: {errors}"


def test_artifact_quality_skill_exe_rejected():
    """Binary executables in skill artifacts are blocked."""
    import yaml

    manifest = yaml.dump(
        {
            "package_id": "test-skill",
            "package_type": "skill",
            "capabilities": {"prompts": [{"name": "main", "template": "SKILL.md"}]},
            "assets": [{"id": "bin", "type": "data", "path": "tool.exe"}],
        }
    ).encode()
    artifact = _build_skill_artifact(
        {
            "agentnode.yaml": manifest,
            "SKILL.md": b"# Skill",
            "tool.exe": b"\x4d\x5a\x90\x00",
        }
    )
    errors, warnings = validate_artifact_quality(
        artifact, "test-skill", package_type="skill"
    )
    assert any("forbidden extension" in e and ".exe" in e for e in errors)
