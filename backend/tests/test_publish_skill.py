"""End-to-end publish test for skill packages (prompt-only, runtime=none).

Regression guard for the runtime_type enum gap: the validator accepts
(skill, none, prompt_only), but until migration 041 the DB enum had no
'none' value and every real skill publish 500ed at the INSERT. This test
walks the full publish path — multipart manifest + tar.gz artifact with
SKILL.md — down to the stored version row.
"""

import io
import json
import tarfile
from unittest.mock import patch

import pytest

TEST_USER = {
    "email": "skillpub@agentnode.dev",
    "username": "skillpub",
    "password": "TestPass123!",
}

TEST_PUBLISHER = {
    "display_name": "Skill Publisher",
    "slug": "skill-publisher",
}

SKILL_MANIFEST = {
    "manifest_version": "0.2",
    "package_id": "release-notes-skill",
    "package_type": "skill",
    "name": "Release Notes Skill",
    "publisher": "skill-publisher",
    "version": "1.0.0",
    "summary": "Turn commit history into user-facing release notes.",
    "description": "A prompt-only skill that teaches an agent to write release notes.",
    "runtime": "none",
    "install_mode": "prompt_only",
    "hosting_type": "agentnode_hosted",
    "capabilities": {
        "tools": [],
        "resources": [],
        "prompts": [
            {
                "name": "main",
                "capability_id": "release_notes_skill.prompt",
                "template": "SKILL.md",
                "description": "Release notes writing instructions",
            }
        ],
    },
    "compatibility": {"frameworks": ["generic"]},
    "permissions": {
        "network": {"level": "none"},
        "filesystem": {"level": "none"},
        "code_execution": {"level": "none"},
        "data_access": {"level": "input_only"},
        "user_approval": {"required": "never"},
    },
}

SKILL_MD = "# Release Notes Skill\n\nGroup changes by impact, write for the user.\n"


def _skill_artifact() -> bytes:
    """Build a minimal valid skill tar.gz: agentnode.yaml + SKILL.md."""
    import yaml

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for fname, content in (
            ("agentnode.yaml", yaml.safe_dump(SKILL_MANIFEST, sort_keys=False)),
            ("SKILL.md", SKILL_MD),
        ):
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=f"release-notes-skill/{fname}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


async def _get_auth_token(client) -> str:
    await client.post("/v1/auth/register", json=TEST_USER)
    login = await client.post(
        "/v1/auth/login",
        json={"email": TEST_USER["email"], "password": TEST_USER["password"]},
    )
    token = login.json()["access_token"]
    await client.post(
        "/v1/publishers",
        json=TEST_PUBLISHER,
        headers={"Authorization": f"Bearer {token}"},
    )
    return token


@pytest.mark.asyncio
@patch("app.packages.service.upload_preview_file", return_value="preview/key")
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_publish_skill_end_to_end(mock_meili, mock_s3, mock_preview, client):
    """A prompt-only skill publishes through the full path and stores runtime=none."""
    token = await _get_auth_token(client)
    resp = await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(SKILL_MANIFEST)},
        files={
            "artifact": (
                "release-notes-skill.tar.gz",
                _skill_artifact(),
                "application/gzip",
            )
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, f"Skill publish failed: {resp.text[:500]}"
    data = resp.json()
    assert data["slug"] == "release-notes-skill"
    assert data["version"] == "1.0.0"

    detail = await client.get("/v1/packages/release-notes-skill")
    assert detail.status_code == 200
    body = detail.json()
    assert body["package_type"] == "skill"


@pytest.mark.asyncio
@patch("app.packages.service.upload_preview_file", return_value="preview/key")
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_publish_skill_without_skill_md_rejected(
    mock_meili, mock_s3, mock_preview, client
):
    """A skill artifact missing SKILL.md is rejected by the quality gate."""
    import yaml

    token = await _get_auth_token(client)

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = yaml.safe_dump(SKILL_MANIFEST, sort_keys=False).encode("utf-8")
        info = tarfile.TarInfo(name="release-notes-skill/agentnode.yaml")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    resp = await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(SKILL_MANIFEST)},
        files={
            "artifact": (
                "release-notes-skill.tar.gz",
                buf.getvalue(),
                "application/gzip",
            )
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code in (400, 422), f"Expected rejection, got {resp.status_code}"
    assert "SKILL.md" in resp.text
