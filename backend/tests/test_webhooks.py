"""Integration tests for webhook management."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

TEST_USER = {
    "email": "hookuser@agentnode.dev",
    "username": "hookuser",
    "password": "TestPass123!",
}

TEST_PUBLISHER = {
    "display_name": "Hook Publisher",
    "slug": "hook-publisher",
}

TEST_MANIFEST = {
    "manifest_version": "0.1",
    "package_id": "hook-test-pkg",
    "package_type": "toolpack",
    "name": "Hook Test Package",
    "publisher": "hook-publisher",
    "version": "1.0.0",
    "summary": "A package for webhook testing.",
    "runtime": "python",
    "install_mode": "package",
    "hosting_type": "agentnode_hosted",
    "entrypoint": "hook_test.tool",
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


async def get_auth_token(client):
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
    return token


@pytest.mark.asyncio
async def test_create_webhook(client):
    token = await get_auth_token(client)
    resp = await client.post(
        "/v1/webhooks",
        json={
            "url": "https://example.com/webhook",
            "events": ["package.published", "version.yanked"],
            "secret": "mysecret",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["url"] == "https://example.com/webhook"
    assert data["events"] == ["package.published", "version.yanked"]
    assert data["is_active"] is True


@pytest.mark.asyncio
async def test_create_webhook_invalid_event(client):
    token = await get_auth_token(client)
    resp = await client.post(
        "/v1/webhooks",
        json={
            "url": "https://example.com/webhook",
            "events": ["invalid.event"],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_list_webhooks(client):
    token = await get_auth_token(client)
    await client.post(
        "/v1/webhooks",
        json={
            "url": "https://example.com/hook1",
            "events": ["package.published"],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    await client.post(
        "/v1/webhooks",
        json={
            "url": "https://example.com/hook2",
            "events": ["version.yanked"],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = await client.get(
        "/v1/webhooks",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_delete_webhook(client):
    token = await get_auth_token(client)
    create_resp = await client.post(
        "/v1/webhooks",
        json={
            "url": "https://example.com/hook",
            "events": ["package.published"],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    wh_id = create_resp.json()["id"]

    del_resp = await client.delete(
        f"/v1/webhooks/{wh_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert del_resp.status_code == 200

    # Should be gone
    list_resp = await client.get(
        "/v1/webhooks",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert len(list_resp.json()) == 0


@pytest.mark.asyncio
async def test_webhook_deliveries_empty(client):
    token = await get_auth_token(client)
    create_resp = await client.post(
        "/v1/webhooks",
        json={
            "url": "https://example.com/hook",
            "events": ["package.published"],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    wh_id = create_resp.json()["id"]

    resp = await client.get(
        f"/v1/webhooks/{wh_id}/deliveries",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_webhook_requires_publisher(client):
    # Register user without publisher
    user = {"email": "nopub@agentnode.dev", "username": "nopubuser", "password": "TestPass123!"}
    await client.post("/v1/auth/register", json=user)
    login = await client.post("/v1/auth/login", json={
        "email": user["email"], "password": user["password"],
    })
    token = login.json()["access_token"]

    resp = await client.post(
        "/v1/webhooks",
        json={"url": "https://example.com/hook", "events": ["package.published"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
