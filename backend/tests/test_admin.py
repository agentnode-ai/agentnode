"""Integration tests for admin/moderation endpoints."""
import json
from unittest.mock import patch

import pytest
from sqlalchemy import update

from app.auth.models import User

TEST_ADMIN = {
    "email": "admin@agentnode.dev",
    "username": "adminuser",
    "password": "AdminPass123!",
}

TEST_REGULAR = {
    "email": "regular@agentnode.dev",
    "username": "regularuser",
    "password": "RegularPass123!",
}

TEST_PUBLISHER = {
    "display_name": "Mod Publisher",
    "slug": "mod-publisher",
}

TEST_MANIFEST = {
    "manifest_version": "0.1",
    "package_id": "mod-test-pkg",
    "package_type": "toolpack",
    "name": "Mod Test Package",
    "publisher": "mod-publisher",
    "version": "1.0.0",
    "summary": "A package for admin testing.",
    "runtime": "python",
    "install_mode": "package",
    "hosting_type": "agentnode_hosted",
    "entrypoint": "mod_test.tool",
    "capabilities": {
        "tools": [{
            "name": "test_tool",
            "capability_id": "pdf_extraction",
            "description": "Test tool",
            "input_schema": {"type": "object"},
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


async def setup_admin(client, session):
    """Create admin user, publisher, and publish a test package."""
    await client.post("/v1/auth/register", json=TEST_ADMIN)
    login = await client.post("/v1/auth/login", json={
        "email": TEST_ADMIN["email"],
        "password": TEST_ADMIN["password"],
    })
    token = login.json()["access_token"]

    # Make user admin directly in DB
    await session.execute(
        update(User).where(User.username == "adminuser").values(is_admin=True)
    )
    await session.commit()

    await client.post(
        "/v1/publishers",
        json=TEST_PUBLISHER,
        headers={"Authorization": f"Bearer {token}"},
    )
    return token


async def setup_regular(client):
    """Create a regular (non-admin) user."""
    await client.post("/v1/auth/register", json=TEST_REGULAR)
    login = await client.post("/v1/auth/login", json={
        "email": TEST_REGULAR["email"],
        "password": TEST_REGULAR["password"],
    })
    return login.json()["access_token"]


# --- Quarantine tests ---


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_quarantine_version(mock_meili, mock_s3, client, session):
    token = await setup_admin(client, session)

    await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(TEST_MANIFEST)},
        headers={"Authorization": f"Bearer {token}"},
    )

    resp = await client.post(
        "/v1/admin/packages/mod-test-pkg/versions/1.0.0/quarantine",
        json={"reason": "Suspicious code detected"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["quarantine_status"] == "quarantined"
    assert "Suspicious code" in data["message"]


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_clear_quarantine(mock_meili, mock_s3, client, session):
    token = await setup_admin(client, session)

    await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(TEST_MANIFEST)},
        headers={"Authorization": f"Bearer {token}"},
    )

    # Quarantine first
    await client.post(
        "/v1/admin/packages/mod-test-pkg/versions/1.0.0/quarantine",
        json={"reason": "Under review"},
        headers={"Authorization": f"Bearer {token}"},
    )

    # Clear
    resp = await client.post(
        "/v1/admin/packages/mod-test-pkg/versions/1.0.0/clear",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["quarantine_status"] == "cleared"


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_reject_version(mock_meili, mock_s3, client, session):
    token = await setup_admin(client, session)

    await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(TEST_MANIFEST)},
        headers={"Authorization": f"Bearer {token}"},
    )

    await client.post(
        "/v1/admin/packages/mod-test-pkg/versions/1.0.0/quarantine",
        json={"reason": "Malicious"},
        headers={"Authorization": f"Bearer {token}"},
    )

    resp = await client.post(
        "/v1/admin/packages/mod-test-pkg/versions/1.0.0/reject",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["quarantine_status"] == "rejected"


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_clear_non_quarantined_fails(mock_meili, mock_s3, client, session):
    token = await setup_admin(client, session)

    await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(TEST_MANIFEST)},
        headers={"Authorization": f"Bearer {token}"},
    )

    resp = await client.post(
        "/v1/admin/packages/mod-test-pkg/versions/1.0.0/clear",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_list_quarantined(mock_meili, mock_s3, client, session):
    token = await setup_admin(client, session)

    await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(TEST_MANIFEST)},
        headers={"Authorization": f"Bearer {token}"},
    )
    await client.post(
        "/v1/admin/packages/mod-test-pkg/versions/1.0.0/quarantine",
        json={"reason": "Review needed"},
        headers={"Authorization": f"Bearer {token}"},
    )

    resp = await client.get(
        "/v1/admin/quarantined",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["package_slug"] == "mod-test-pkg"


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_quarantined_not_installable(mock_meili, mock_s3, client, session):
    """Quarantined versions should not appear in install metadata."""
    token = await setup_admin(client, session)

    await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(TEST_MANIFEST)},
        headers={"Authorization": f"Bearer {token}"},
    )

    # Quarantine
    await client.post(
        "/v1/admin/packages/mod-test-pkg/versions/1.0.0/quarantine",
        json={"reason": "Under review"},
        headers={"Authorization": f"Bearer {token}"},
    )

    # Try to install — should fail (no installable version)
    resp = await client.get("/v1/packages/mod-test-pkg/install")
    assert resp.status_code == 404


# --- Trust level tests ---


@pytest.mark.asyncio
async def test_set_trust_level(client, session):
    token = await setup_admin(client, session)

    resp = await client.put(
        "/v1/admin/publishers/mod-publisher/trust",
        json={"trust_level": "verified"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["trust_level"] == "verified"

    # Verify it persisted
    pub_resp = await client.get("/v1/publishers/mod-publisher")
    assert pub_resp.json()["trust_level"] == "verified"


@pytest.mark.asyncio
async def test_set_invalid_trust_level(client, session):
    token = await setup_admin(client, session)

    resp = await client.put(
        "/v1/admin/publishers/mod-publisher/trust",
        json={"trust_level": "superadmin"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


# --- Suspension tests ---


@pytest.mark.asyncio
async def test_suspend_publisher(client, session):
    token = await setup_admin(client, session)

    resp = await client.post(
        "/v1/admin/publishers/mod-publisher/suspend",
        json={"reason": "Policy violation"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_suspended"] is True
    assert data["suspension_reason"] == "Policy violation"


@pytest.mark.asyncio
async def test_unsuspend_publisher(client, session):
    token = await setup_admin(client, session)

    await client.post(
        "/v1/admin/publishers/mod-publisher/suspend",
        json={"reason": "Test"},
        headers={"Authorization": f"Bearer {token}"},
    )

    resp = await client.post(
        "/v1/admin/publishers/mod-publisher/unsuspend",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["is_suspended"] is False


@pytest.mark.asyncio
async def test_suspend_already_suspended(client, session):
    token = await setup_admin(client, session)

    await client.post(
        "/v1/admin/publishers/mod-publisher/suspend",
        json={"reason": "Test"},
        headers={"Authorization": f"Bearer {token}"},
    )

    resp = await client.post(
        "/v1/admin/publishers/mod-publisher/suspend",
        json={"reason": "Again"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_list_suspended(client, session):
    token = await setup_admin(client, session)

    await client.post(
        "/v1/admin/publishers/mod-publisher/suspend",
        json={"reason": "Violation"},
        headers={"Authorization": f"Bearer {token}"},
    )

    resp = await client.get(
        "/v1/admin/publishers/suspended",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["slug"] == "mod-publisher"


# --- Authorization tests ---


@pytest.mark.asyncio
async def test_admin_endpoint_requires_auth(client):
    resp = await client.get("/v1/admin/quarantined")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_admin_endpoint_rejects_non_admin(client, session):
    # Setup admin first (creates publisher), then regular user
    await setup_admin(client, session)
    regular_token = await setup_regular(client)

    resp = await client.get(
        "/v1/admin/quarantined",
        headers={"Authorization": f"Bearer {regular_token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_quarantine_nonexistent_package(client, session):
    token = await setup_admin(client, session)

    resp = await client.post(
        "/v1/admin/packages/nonexistent/versions/1.0.0/quarantine",
        json={"reason": "Test"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404
