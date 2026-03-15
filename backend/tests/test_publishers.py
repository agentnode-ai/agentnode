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
