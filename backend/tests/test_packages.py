"""Integration tests for packages endpoints."""
import io
import json
import tarfile
from unittest.mock import patch

import pytest


def _make_minimal_artifact() -> bytes:
    """Create a minimal valid tar.gz artifact that passes validate_artifact_quality."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        # Add a stub test file (required by quality gate)
        test_content = b"def test_placeholder(): pass\n"
        info = tarfile.TarInfo(name="tests/test_stub.py")
        info.size = len(test_content)
        tar.addfile(info, io.BytesIO(test_content))
    return buf.getvalue()

TEST_USER = {
    "email": "pkguser@agentnode.dev",
    "username": "pkguser",
    "password": "TestPass123!",
}

TEST_PUBLISHER = {
    "display_name": "Pkg Publisher",
    "slug": "pkg-publisher",
}

TEST_MANIFEST = {
    "manifest_version": "0.1",
    "package_id": "test-pack",
    "package_type": "toolpack",
    "name": "Test Pack",
    "publisher": "pkg-publisher",
    "version": "1.0.0",
    "summary": "A test package for integration testing.",
    "runtime": "python",
    "install_mode": "package",
    "hosting_type": "agentnode_hosted",
    "entrypoint": "test_pack.tool",
    "capabilities": {
        "tools": [{
            "name": "test_tool",
            "capability_id": "pdf_extraction",
            "description": "Test tool",
            "input_schema": {"type": "object", "properties": {"input": {"type": "string"}}, "required": ["input"]},
            "output_schema": {"type": "object", "properties": {"output": {"type": "string"}}},
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
    "security": {"signature": "", "provenance": {"source_repo": "", "commit": "", "build_system": ""}},
    "support": {"homepage": "", "issues": ""},
}


async def get_auth_token(client) -> str:
    await client.post("/v1/auth/register", json=TEST_USER)
    login_resp = await client.post("/v1/auth/login", json={
        "email": TEST_USER["email"],
        "password": TEST_USER["password"],
    })
    token = login_resp.json()["access_token"]
    await client.post(
        "/v1/publishers",
        json=TEST_PUBLISHER,
        headers={"Authorization": f"Bearer {token}"},
    )
    return token


@pytest.mark.asyncio
async def test_validate_valid_manifest(client):
    token = await get_auth_token(client)
    resp = await client.post(
        "/v1/packages/validate",
        json={"manifest": TEST_MANIFEST},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is True
    assert data["errors"] == []


@pytest.mark.asyncio
async def test_validate_invalid_manifest(client):
    token = await get_auth_token(client)
    bad_manifest = {**TEST_MANIFEST, "manifest_version": "9.9", "runtime": "rust"}
    resp = await client.post(
        "/v1/packages/validate",
        json={"manifest": bad_manifest},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is False
    assert len(data["errors"]) >= 2


@pytest.mark.asyncio
async def test_get_package_not_found(client):
    resp = await client.get("/v1/packages/nonexistent-pack")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_validate_unauthenticated(client):
    resp = await client.post("/v1/packages/validate", json={"manifest": TEST_MANIFEST})
    assert resp.status_code == 401


# --- Publish tests ---


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_publish_new_package(mock_meili, mock_s3, client, session):
    token = await get_auth_token(client)
    # Mark publisher as trusted so versions are not quarantined
    from app.publishers.models import Publisher
    from sqlalchemy import select
    result = await session.execute(select(Publisher).where(Publisher.slug == "pkg-publisher"))
    pub = result.scalar_one()
    pub.trust_level = "trusted"
    await session.flush()

    resp = await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(TEST_MANIFEST)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.json()
    data = resp.json()
    assert data["slug"] == "test-pack"
    assert data["version"] == "1.0.0"
    assert data["package_type"] == "toolpack"
    mock_meili.assert_called_once()


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_publish_then_get_detail(mock_meili, mock_s3, client):
    token = await get_auth_token(client)
    await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(TEST_MANIFEST)},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = await client.get("/v1/packages/test-pack")
    assert resp.status_code == 200
    data = resp.json()
    assert data["slug"] == "test-pack"
    assert data["name"] == "Test Pack"
    assert data["latest_version"]["version_number"] == "1.0.0"
    assert len(data["blocks"]["capabilities"]) == 1
    assert data["blocks"]["capabilities"][0]["capability_id"] == "pdf_extraction"
    assert data["blocks"]["permissions"]["network_level"] == "none"
    assert data["blocks"]["compatibility"]["frameworks"] == ["generic"]


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_publish_duplicate_version(mock_meili, mock_s3, client):
    token = await get_auth_token(client)
    await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(TEST_MANIFEST)},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(TEST_MANIFEST)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409
    assert "already exists" in resp.json()["error"]["message"]


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_publish_new_version(mock_meili, mock_s3, client):
    token = await get_auth_token(client)
    await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(TEST_MANIFEST)},
        headers={"Authorization": f"Bearer {token}"},
    )
    v2_manifest = {**TEST_MANIFEST, "version": "2.0.0"}
    resp = await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(v2_manifest)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"] == "2.0.0"

    # Latest version should be 2.0.0
    detail = await client.get("/v1/packages/test-pack")
    assert detail.json()["latest_version"]["version_number"] == "2.0.0"


@pytest.mark.asyncio
@patch("app.packages.service.upload_preview_file", return_value="previews/mock.py")
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_publish_with_artifact(mock_meili, mock_s3, mock_preview, client, session):
    token = await get_auth_token(client)
    # Mark publisher as trusted so versions are not quarantined
    from app.publishers.models import Publisher
    from sqlalchemy import select
    result = await session.execute(select(Publisher).where(Publisher.slug == "pkg-publisher"))
    pub = result.scalar_one()
    pub.trust_level = "trusted"
    await session.flush()
    artifact_bytes = _make_minimal_artifact()
    resp = await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(TEST_MANIFEST)},
        files={"artifact": ("test.tar.gz", artifact_bytes, "application/gzip")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    mock_s3.assert_called_once()


@pytest.mark.asyncio
async def test_publish_unauthenticated(client):
    resp = await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(TEST_MANIFEST)},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_publish_invalid_manifest(mock_meili, mock_s3, client):
    token = await get_auth_token(client)
    bad = {**TEST_MANIFEST, "runtime": "rust"}
    resp = await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(bad)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_publish_versions_list(mock_meili, mock_s3, client, session):
    token = await get_auth_token(client)
    # Mark publisher as trusted so versions are not quarantined
    # (new unverified publishers trigger auto-quarantine, hiding versions from public list)
    from app.publishers.models import Publisher
    from sqlalchemy import select
    result = await session.execute(select(Publisher).where(Publisher.slug == "pkg-publisher"))
    pub = result.scalar_one()
    pub.trust_level = "trusted"
    await session.flush()

    await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(TEST_MANIFEST)},
        headers={"Authorization": f"Bearer {token}"},
    )
    v2 = {**TEST_MANIFEST, "version": "2.0.0"}
    await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(v2)},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = await client.get("/v1/packages/test-pack/versions")
    assert resp.status_code == 200
    versions = resp.json()["versions"]
    assert len(versions) == 2
    assert versions[0]["version_number"] == "2.0.0"  # newest first
