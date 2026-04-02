import base64

import pytest

TEST_USER = {
    "email": "pub@agentnode.dev",
    "username": "pubuser",
    "password": "TestPass123!",
}

TEST_PUBLISHER = {
    "display_name": "Test Publisher",
    "slug": "test-publisher",
}

# Valid 32-byte Ed25519 public key (base64-encoded)
VALID_SIGNING_KEY = base64.b64encode(b"\x01" * 32).decode()


async def get_auth_token(client) -> str:
    await client.post("/v1/auth/register", json=TEST_USER)
    login_resp = await client.post("/v1/auth/login", json={
        "email": TEST_USER["email"],
        "password": TEST_USER["password"],
    })
    return login_resp.json()["access_token"]


@pytest.mark.asyncio
async def test_create_publisher(client):
    token = await get_auth_token(client)
    resp = await client.post(
        "/v1/publishers",
        json=TEST_PUBLISHER,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["slug"] == TEST_PUBLISHER["slug"]
    assert data["display_name"] == TEST_PUBLISHER["display_name"]
    assert data["trust_level"] == "unverified"


@pytest.mark.asyncio
async def test_create_publisher_duplicate_slug(client):
    token = await get_auth_token(client)
    await client.post(
        "/v1/publishers",
        json=TEST_PUBLISHER,
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = await client.post(
        "/v1/publishers",
        json=TEST_PUBLISHER,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_get_publisher(client):
    token = await get_auth_token(client)
    await client.post(
        "/v1/publishers",
        json=TEST_PUBLISHER,
        headers={"Authorization": f"Bearer {token}"},
    )

    resp = await client.get(f"/v1/publishers/{TEST_PUBLISHER['slug']}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["slug"] == TEST_PUBLISHER["slug"]


@pytest.mark.asyncio
async def test_get_publisher_not_found(client):
    resp = await client.get("/v1/publishers/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_publisher_invalid_slug(client):
    token = await get_auth_token(client)
    resp = await client.post(
        "/v1/publishers",
        json={"display_name": "Test", "slug": "AB"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_publisher_unauthenticated(client):
    resp = await client.post("/v1/publishers", json=TEST_PUBLISHER)
    assert resp.status_code == 401


# --- Signing key tests ---


async def _create_publisher_with_token(client) -> str:
    """Helper: register user, create publisher, return auth token."""
    token = await get_auth_token(client)
    await client.post(
        "/v1/publishers",
        json=TEST_PUBLISHER,
        headers={"Authorization": f"Bearer {token}"},
    )
    return token


@pytest.mark.asyncio
async def test_register_signing_key(client):
    token = await _create_publisher_with_token(client)
    resp = await client.put(
        f"/v1/publishers/{TEST_PUBLISHER['slug']}/signing-key",
        json={"public_key": VALID_SIGNING_KEY},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["public_key"] == VALID_SIGNING_KEY
    assert "registered_at" in data


@pytest.mark.asyncio
async def test_get_signing_key(client):
    token = await _create_publisher_with_token(client)
    await client.put(
        f"/v1/publishers/{TEST_PUBLISHER['slug']}/signing-key",
        json={"public_key": VALID_SIGNING_KEY},
        headers={"Authorization": f"Bearer {token}"},
    )

    resp = await client.get(f"/v1/publishers/{TEST_PUBLISHER['slug']}/signing-key")
    assert resp.status_code == 200
    data = resp.json()
    assert data["public_key"] == VALID_SIGNING_KEY
    assert "registered_at" in data


@pytest.mark.asyncio
async def test_get_signing_key_not_registered(client):
    await _create_publisher_with_token(client)
    resp = await client.get(f"/v1/publishers/{TEST_PUBLISHER['slug']}/signing-key")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_register_signing_key_unauthenticated(client):
    await _create_publisher_with_token(client)
    client.cookies.clear()
    resp = await client.put(
        f"/v1/publishers/{TEST_PUBLISHER['slug']}/signing-key",
        json={"public_key": VALID_SIGNING_KEY},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_register_signing_key_not_owner(client):
    """A different user cannot register a signing key on someone else's publisher."""
    await _create_publisher_with_token(client)

    # Register a second user
    other_user = {"email": "other@agentnode.dev", "username": "otheruser", "password": "TestPass123!"}
    await client.post("/v1/auth/register", json=other_user)
    login_resp = await client.post("/v1/auth/login", json={
        "email": other_user["email"], "password": other_user["password"],
    })
    other_token = login_resp.json()["access_token"]

    resp = await client.put(
        f"/v1/publishers/{TEST_PUBLISHER['slug']}/signing-key",
        json={"public_key": VALID_SIGNING_KEY},
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_register_signing_key_invalid_base64(client):
    token = await _create_publisher_with_token(client)
    resp = await client.put(
        f"/v1/publishers/{TEST_PUBLISHER['slug']}/signing-key",
        json={"public_key": "not-valid-base64!!!"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_register_signing_key_wrong_length(client):
    token = await _create_publisher_with_token(client)
    # 16 bytes instead of 32
    bad_key = base64.b64encode(b"\x01" * 16).decode()
    resp = await client.put(
        f"/v1/publishers/{TEST_PUBLISHER['slug']}/signing-key",
        json={"public_key": bad_key},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_register_signing_key_replaces_existing(client):
    token = await _create_publisher_with_token(client)
    await client.put(
        f"/v1/publishers/{TEST_PUBLISHER['slug']}/signing-key",
        json={"public_key": VALID_SIGNING_KEY},
        headers={"Authorization": f"Bearer {token}"},
    )

    new_key = base64.b64encode(b"\x02" * 32).decode()
    resp = await client.put(
        f"/v1/publishers/{TEST_PUBLISHER['slug']}/signing-key",
        json={"public_key": new_key},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["public_key"] == new_key


@pytest.mark.asyncio
async def test_get_signing_key_publisher_not_found(client):
    resp = await client.get("/v1/publishers/nonexistent/signing-key")
    assert resp.status_code == 404
