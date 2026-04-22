"""Integration tests for install flow and download tracking."""
import json
from unittest.mock import patch

import pytest
from sqlalchemy import select, update
from app.publishers.models import Publisher

TEST_USER = {
    "email": "installer@agentnode.dev",
    "username": "installer",
    "password": "TestPass123!",
}

TEST_PUBLISHER = {
    "display_name": "Install Publisher",
    "slug": "install-pub",
}

TEST_MANIFEST = {
    "manifest_version": "0.1",
    "package_id": "install-test-pkg",
    "package_type": "toolpack",
    "name": "Install Test Package",
    "publisher": "install-pub",
    "version": "1.0.0",
    "summary": "A package for install flow testing.",
    "runtime": "python",
    "install_mode": "package",
    "hosting_type": "agentnode_hosted",
    "entrypoint": "install_test.tool",
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
    "security": {"signature": "", "provenance": {"source_repo": "", "commit": "", "build_system": ""}},
}


async def publish_test_package(client, token, manifest=None, artifact=None):
    m = manifest or TEST_MANIFEST
    files = {}
    if artifact:
        files["artifact"] = ("test.tar.gz", artifact, "application/gzip")
    return await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(m)},
        files=files or None,
        headers={"Authorization": f"Bearer {token}"},
    )


async def get_auth_token(client, session):
    await client.post("/v1/auth/register", json=TEST_USER)
    login = await client.post("/v1/auth/login", json={
        "email": TEST_USER["email"],
        "password": TEST_USER["password"],
    })
    token = login.json()["access_token"]
    await client.post(
        "/v1/publishers",
        json=TEST_PUBLISHER,
        headers={"Authorization": f"Bearer {token}"},
    )
    # Make publisher trusted to bypass new-publisher quarantine
    await session.execute(
        update(Publisher)
        .where(Publisher.slug == TEST_PUBLISHER["slug"])
        .values(trust_level="trusted")
    )
    await session.commit()
    return token


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_get_install_metadata(mock_meili, mock_s3, client, session):
    token = await get_auth_token(client, session)
    await publish_test_package(client, token)

    resp = await client.get("/v1/packages/install-test-pkg/install-info")
    assert resp.status_code == 200
    data = resp.json()
    assert data["slug"] == "install-test-pkg"
    assert data["version"] == "1.0.0"
    assert data["runtime"] == "python"
    assert data["install_mode"] == "package"
    assert data["entrypoint"] == "install_test.tool"
    assert len(data["capabilities"]) == 1
    assert data["capabilities"][0]["capability_id"] == "pdf_extraction"
    assert data["permissions"]["network_level"] == "none"
    assert data["permissions"]["filesystem_level"] == "temp"
    assert data["artifact"] is None  # No artifact uploaded
    assert data["agent"] is None  # Not an agent package


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_install_metadata_agent_field(mock_meili, mock_s3, client, session):
    """Agent packages should return the agent config from manifest."""
    token = await get_auth_token(client, session)
    agent_manifest = {
        **TEST_MANIFEST,
        "package_id": "test-agent-pkg",
        "package_type": "agent",
        "agent": {
            "entrypoint": "agent_main.core:run",
            "goal": "Test agent goal",
            "tool_access": {"allowed_packages": ["test_tool"]},
            "limits": {"max_iterations": 10, "max_tool_calls": 50},
        },
    }
    pub_resp = await publish_test_package(client, token, manifest=agent_manifest)
    assert pub_resp.status_code == 201, f"Publish failed: {pub_resp.json()}"

    resp = await client.get("/v1/packages/test-agent-pkg/install-info")
    assert resp.status_code == 200
    data = resp.json()
    assert data["package_type"] == "agent"
    assert data["agent"] is not None
    assert data["agent"]["entrypoint"] == "agent_main.core:run"
    assert data["agent"]["goal"] == "Test agent goal"
    assert data["agent"]["tool_access"] == {"allowed_packages": ["test_tool"]}


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_get_install_metadata_specific_version(mock_meili, mock_s3, client, session):
    token = await get_auth_token(client, session)
    await publish_test_package(client, token)
    v2 = {**TEST_MANIFEST, "version": "2.0.0"}
    await publish_test_package(client, token, manifest=v2)

    # Request specific version 1.0.0
    resp = await client.get("/v1/packages/install-test-pkg/install-info?version=1.0.0")
    assert resp.status_code == 200
    assert resp.json()["version"] == "1.0.0"

    # Default should be latest (2.0.0)
    resp2 = await client.get("/v1/packages/install-test-pkg/install-info")
    assert resp2.status_code == 200
    assert resp2.json()["version"] == "2.0.0"


@pytest.mark.asyncio
@patch("app.install.service.generate_presigned_url", return_value="https://s3.example.com/presigned")
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_get_install_with_artifact(mock_meili, mock_s3, mock_presign, client, session):
    token = await get_auth_token(client, session)
    await publish_test_package(client, token, artifact=b"fake-artifact-data")

    resp = await client.get("/v1/packages/install-test-pkg/install-info")
    assert resp.status_code == 200
    data = resp.json()
    assert data["artifact"] is not None
    assert data["artifact"]["url"] == "https://s3.example.com/presigned"
    assert data["artifact"]["hash_sha256"] is not None
    assert data["artifact"]["size_bytes"] == len(b"fake-artifact-data")


@pytest.mark.asyncio
async def test_install_not_found(client):
    resp = await client.get("/v1/packages/nonexistent/install-info")
    assert resp.status_code == 404


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_download_increments_count(mock_meili, mock_s3, client, session):
    token = await get_auth_token(client, session)
    await publish_test_package(client, token)

    # First download
    resp1 = await client.post("/v1/packages/install-test-pkg/download")
    assert resp1.status_code == 200
    assert resp1.json()["download_count"] == 1

    # Second download from same IP — deduplicated, count stays at 1
    resp2 = await client.post("/v1/packages/install-test-pkg/download")
    assert resp2.status_code == 200
    assert resp2.json()["download_count"] == 1


@pytest.mark.asyncio
@patch("app.install.service.generate_presigned_url", return_value="https://s3.example.com/presigned")
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_download_returns_url(mock_meili, mock_s3, mock_presign, client, session):
    token = await get_auth_token(client, session)
    await publish_test_package(client, token, artifact=b"artifact-bytes")

    resp = await client.post("/v1/packages/install-test-pkg/download")
    assert resp.status_code == 200
    data = resp.json()
    assert data["download_url"] == "https://s3.example.com/presigned"
    assert data["slug"] == "install-test-pkg"
    assert data["version"] == "1.0.0"


@pytest.mark.asyncio
async def test_download_not_found(client):
    resp = await client.post("/v1/packages/nonexistent/download")
    assert resp.status_code == 404


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_download_count_reflected_in_detail(mock_meili, mock_s3, client, session):
    token = await get_auth_token(client, session)
    await publish_test_package(client, token)

    # Three downloads from same IP — only the first increments due to dedup
    await client.post("/v1/packages/install-test-pkg/download")
    await client.post("/v1/packages/install-test-pkg/download")
    await client.post("/v1/packages/install-test-pkg/download")

    resp = await client.get("/v1/packages/install-test-pkg")
    assert resp.status_code == 200
    assert resp.json()["download_count"] == 1
