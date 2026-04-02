"""Cross-publisher authorization tests (Testing P0.4).

Verify that users cannot perform actions on resources owned by other publishers.
Each test creates two separate users with separate publishers, authenticates as
user A, and tries to perform an action on user B's resource, asserting 403.

Also verifies that an admin CAN override these restrictions where applicable.
"""
import base64
import json
from unittest.mock import patch

import pytest
from sqlalchemy import update

from app.auth.models import User


# --- User / publisher fixtures ---

USER_A = {
    "email": "user-a@agentnode.dev",
    "username": "user_a",
    "password": "TestPass123!",
}
PUBLISHER_A = {
    "display_name": "Publisher A",
    "slug": "publisher-a",
}

USER_B = {
    "email": "user-b@agentnode.dev",
    "username": "user_b",
    "password": "TestPass123!",
}
PUBLISHER_B = {
    "display_name": "Publisher B",
    "slug": "publisher-b",
}

ADMIN_USER = {
    "email": "cross-admin@agentnode.dev",
    "username": "cross_admin",
    "password": "AdminPass123!",
}
ADMIN_PUBLISHER = {
    "display_name": "Admin Publisher",
    "slug": "admin-publisher",
}

# Valid 32-byte Ed25519 public key (base64-encoded)
VALID_SIGNING_KEY = base64.b64encode(b"\x01" * 32).decode()

MANIFEST_B = {
    "manifest_version": "0.1",
    "package_id": "cross-test-pkg",
    "package_type": "toolpack",
    "name": "Cross Test Package",
    "publisher": "publisher-b",
    "version": "1.0.0",
    "summary": "A test package owned by publisher B.",
    "runtime": "python",
    "install_mode": "package",
    "hosting_type": "agentnode_hosted",
    "entrypoint": "cross_test.tool",
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


async def _register_and_login(client, user_data: dict) -> str:
    """Register a user and return an access token."""
    await client.post("/v1/auth/register", json=user_data)
    login_resp = await client.post("/v1/auth/login", json={
        "email": user_data["email"],
        "password": user_data["password"],
    })
    return login_resp.json()["access_token"]


async def _create_publisher(client, token: str, publisher_data: dict) -> None:
    """Create a publisher profile for the authenticated user."""
    resp = await client.post(
        "/v1/publishers",
        json=publisher_data,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, f"Failed to create publisher: {resp.json()}"


async def _setup_two_publishers(client):
    """Create user A with publisher A, and user B with publisher B. Return both tokens."""
    token_a = await _register_and_login(client, USER_A)
    await _create_publisher(client, token_a, PUBLISHER_A)

    token_b = await _register_and_login(client, USER_B)
    await _create_publisher(client, token_b, PUBLISHER_B)

    return token_a, token_b


async def _setup_admin(client, session) -> str:
    """Create an admin user with a publisher. Return admin token."""
    token = await _register_and_login(client, ADMIN_USER)
    await _create_publisher(client, token, ADMIN_PUBLISHER)

    # Promote to admin directly in DB
    await session.execute(
        update(User).where(User.username == ADMIN_USER["username"]).values(is_admin=True)
    )
    await session.commit()

    return token


# ============================================================
# 1. Publisher A cannot update Publisher B's profile
# ============================================================

@pytest.mark.asyncio
async def test_cannot_update_other_publishers_profile(client):
    """PUT /v1/publishers/{B-slug} by user A returns 403."""
    token_a, _token_b = await _setup_two_publishers(client)

    resp = await client.put(
        f"/v1/publishers/{PUBLISHER_B['slug']}",
        json={"display_name": "Hacked Name"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "PUBLISHER_NOT_OWNED"


@pytest.mark.asyncio
async def test_update_own_profile_succeeds(client):
    """Sanity check: user A CAN update their own publisher profile."""
    token_a, _token_b = await _setup_two_publishers(client)

    resp = await client.put(
        f"/v1/publishers/{PUBLISHER_A['slug']}",
        json={"display_name": "Updated A Name"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp.status_code == 200
    assert resp.json()["display_name"] == "Updated A Name"


# ============================================================
# 2. Publisher A cannot create webhooks for Publisher B
# ============================================================

@pytest.mark.asyncio
async def test_webhook_isolation_between_publishers(client):
    """Webhooks created by user B should not be visible or deletable by user A."""
    token_a, token_b = await _setup_two_publishers(client)

    # User B creates a webhook
    create_resp = await client.post(
        "/v1/webhooks",
        json={
            "url": "https://example.com/b-hook",
            "events": ["package.published"],
        },
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert create_resp.status_code == 201
    wh_id = create_resp.json()["id"]

    # User A lists webhooks -- should see none (only their own)
    list_resp = await client.get(
        "/v1/webhooks",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] == 0

    # User A tries to delete user B's webhook -- should fail (404, since ownership
    # filter prevents finding it; this is the correct authorization boundary)
    del_resp = await client.delete(
        f"/v1/webhooks/{wh_id}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert del_resp.status_code == 404

    # User A tries to list deliveries for user B's webhook -- should fail
    deliveries_resp = await client.get(
        f"/v1/webhooks/{wh_id}/deliveries",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert deliveries_resp.status_code == 404


# ============================================================
# 3. Publisher A cannot publish to Publisher B's package
# ============================================================

@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_cannot_publish_to_other_publishers_package(mock_meili, mock_s3, client):
    """User A cannot publish a new version to a package owned by publisher B."""
    token_a, token_b = await _setup_two_publishers(client)

    # User B publishes a package
    resp_b = await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(MANIFEST_B)},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp_b.status_code == 200, resp_b.json()

    # User A tries to publish a new version to B's package
    hijack_manifest = {**MANIFEST_B, "version": "2.0.0", "publisher": "publisher-a"}
    resp_a = await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(hijack_manifest)},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp_a.status_code == 403
    assert resp_a.json()["error"]["code"] == "PACKAGE_NOT_OWNED"


# ============================================================
# 4. Publisher A cannot yank Publisher B's version
# ============================================================

@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_cannot_yank_other_publishers_version(mock_meili, mock_s3, client):
    """User A cannot yank a version belonging to publisher B's package."""
    token_a, token_b = await _setup_two_publishers(client)

    # User B publishes a package
    await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(MANIFEST_B)},
        headers={"Authorization": f"Bearer {token_b}"},
    )

    # User A tries to yank B's version
    resp = await client.post(
        f"/v1/packages/{MANIFEST_B['package_id']}/versions/1.0.0/yank",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "PACKAGE_NOT_OWNED"


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_owner_can_yank_own_version(mock_meili, mock_s3, client):
    """Sanity check: user B CAN yank their own version."""
    _token_a, token_b = await _setup_two_publishers(client)

    await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(MANIFEST_B)},
        headers={"Authorization": f"Bearer {token_b}"},
    )

    resp = await client.post(
        f"/v1/packages/{MANIFEST_B['package_id']}/versions/1.0.0/yank",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp.status_code == 200


# ============================================================
# 5. Publisher A cannot deprecate Publisher B's package
# ============================================================

@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_cannot_deprecate_other_publishers_package(mock_meili, mock_s3, client):
    """User A cannot deprecate a package owned by publisher B."""
    token_a, token_b = await _setup_two_publishers(client)

    await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(MANIFEST_B)},
        headers={"Authorization": f"Bearer {token_b}"},
    )

    resp = await client.post(
        f"/v1/packages/{MANIFEST_B['package_id']}/deprecate",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "PACKAGE_NOT_OWNED"


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_owner_can_deprecate_own_package(mock_meili, mock_s3, client):
    """Sanity check: user B CAN deprecate their own package."""
    _token_a, token_b = await _setup_two_publishers(client)

    await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(MANIFEST_B)},
        headers={"Authorization": f"Bearer {token_b}"},
    )

    resp = await client.post(
        f"/v1/packages/{MANIFEST_B['package_id']}/deprecate",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp.status_code == 200


# ============================================================
# 6. Publisher A cannot register signing key for Publisher B
# ============================================================

@pytest.mark.asyncio
async def test_cannot_register_signing_key_for_other_publisher(client):
    """PUT /v1/publishers/{B-slug}/signing-key by user A returns 403."""
    token_a, _token_b = await _setup_two_publishers(client)

    resp = await client.put(
        f"/v1/publishers/{PUBLISHER_B['slug']}/signing-key",
        json={"public_key": VALID_SIGNING_KEY},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "PUBLISHER_NOT_OWNED"


@pytest.mark.asyncio
async def test_owner_can_register_own_signing_key(client):
    """Sanity check: user B CAN register a signing key for their own publisher."""
    _token_a, token_b = await _setup_two_publishers(client)

    resp = await client.put(
        f"/v1/publishers/{PUBLISHER_B['slug']}/signing-key",
        json={"public_key": VALID_SIGNING_KEY},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp.status_code == 200
    assert resp.json()["public_key"] == VALID_SIGNING_KEY


# ============================================================
# 7. Publisher A cannot update/edit Publisher B's package metadata
# ============================================================

@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_cannot_update_other_publishers_package_metadata(mock_meili, mock_s3, client):
    """PATCH /v1/packages/{slug} by non-owner returns 403."""
    token_a, token_b = await _setup_two_publishers(client)

    await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(MANIFEST_B)},
        headers={"Authorization": f"Bearer {token_b}"},
    )

    resp = await client.patch(
        f"/v1/packages/{MANIFEST_B['package_id']}",
        json={"name": "Hijacked Name"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "PACKAGE_NOT_OWNED"


# ============================================================
# 8. Publisher A cannot view all versions (owner-only) of B's package
# ============================================================

@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_cannot_view_all_versions_of_other_publishers_package(mock_meili, mock_s3, client):
    """GET /v1/packages/{slug}/versions/all by non-owner returns 403."""
    token_a, token_b = await _setup_two_publishers(client)

    await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(MANIFEST_B)},
        headers={"Authorization": f"Bearer {token_b}"},
    )

    resp = await client.get(
        f"/v1/packages/{MANIFEST_B['package_id']}/versions/all",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "PACKAGE_NOT_OWNED"


# ============================================================
# 9. Publisher A cannot request re-verification of B's package
# ============================================================

@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_cannot_reverify_other_publishers_package(mock_meili, mock_s3, client):
    """POST /v1/packages/{slug}/request-reverify by non-owner returns 403."""
    token_a, token_b = await _setup_two_publishers(client)

    await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(MANIFEST_B)},
        headers={"Authorization": f"Bearer {token_b}"},
    )

    resp = await client.post(
        f"/v1/packages/{MANIFEST_B['package_id']}/request-reverify",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "PACKAGE_NOT_OWNED"


# ============================================================
# 10. Admin CAN perform cross-publisher actions
# ============================================================

@pytest.mark.asyncio
async def test_admin_can_update_other_publishers_trust_level(client, session):
    """Admin can set trust level on any publisher via /v1/admin/ endpoints."""
    _token_a, _token_b = await _setup_two_publishers(client)
    admin_token = await _setup_admin(client, session)

    resp = await client.put(
        f"/v1/admin/publishers/{PUBLISHER_B['slug']}/trust",
        json={"trust_level": "trusted"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["trust_level"] == "trusted"


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_admin_can_quarantine_other_publishers_version(mock_meili, mock_s3, client, session):
    """Admin can quarantine any publisher's package version."""
    _token_a, token_b = await _setup_two_publishers(client)
    admin_token = await _setup_admin(client, session)

    # User B publishes a package
    await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(MANIFEST_B)},
        headers={"Authorization": f"Bearer {token_b}"},
    )

    resp = await client.post(
        f"/v1/admin/packages/{MANIFEST_B['package_id']}/versions/1.0.0/quarantine",
        json={"reason": "Admin audit quarantine"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["quarantine_status"] == "quarantined"


@pytest.mark.asyncio
async def test_admin_can_suspend_other_publisher(client, session):
    """Admin can suspend any publisher."""
    _token_a, _token_b = await _setup_two_publishers(client)
    admin_token = await _setup_admin(client, session)

    resp = await client.post(
        f"/v1/admin/publishers/{PUBLISHER_B['slug']}/suspend",
        json={"reason": "Policy violation"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["is_suspended"] is True


@pytest.mark.asyncio
async def test_non_admin_cannot_use_admin_endpoints(client):
    """Regular user A cannot access admin endpoints at all."""
    token_a, _token_b = await _setup_two_publishers(client)

    resp = await client.put(
        f"/v1/admin/publishers/{PUBLISHER_B['slug']}/trust",
        json={"trust_level": "trusted"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "ADMIN_REQUIRED"
