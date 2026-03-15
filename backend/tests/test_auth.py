import pytest

TEST_USER = {
    "email": "test@agentnode.dev",
    "username": "testuser",
    "password": "TestPass123!",
}


@pytest.mark.asyncio
async def test_register(client):
    resp = await client.post("/v1/auth/register", json=TEST_USER)
    assert resp.status_code == 201
    data = resp.json()
    assert data["email"] == TEST_USER["email"]
    assert data["username"] == TEST_USER["username"]
    assert "id" in data


@pytest.mark.asyncio
async def test_register_duplicate_email(client):
    await client.post("/v1/auth/register", json=TEST_USER)
    resp = await client.post("/v1/auth/register", json=TEST_USER)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_register_invalid_username(client):
    resp = await client.post("/v1/auth/register", json={
        "email": "test2@agentnode.dev",
        "username": "AB",
        "password": "TestPass123!",
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_register_short_password(client):
    resp = await client.post("/v1/auth/register", json={
        "email": "test3@agentnode.dev",
        "username": "testuser3",
        "password": "short",
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_login(client):
    await client.post("/v1/auth/register", json=TEST_USER)
    resp = await client.post("/v1/auth/login", json={
        "email": TEST_USER["email"],
        "password": TEST_USER["password"],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_wrong_password(client):
    await client.post("/v1/auth/register", json=TEST_USER)
    resp = await client.post("/v1/auth/login", json={
        "email": TEST_USER["email"],
        "password": "wrongpassword",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_refresh_token(client):
    await client.post("/v1/auth/register", json=TEST_USER)
    login_resp = await client.post("/v1/auth/login", json={
        "email": TEST_USER["email"],
        "password": TEST_USER["password"],
    })
    refresh_token = login_resp.json()["refresh_token"]

    resp = await client.post("/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200
    assert "access_token" in resp.json()


@pytest.mark.asyncio
async def test_refresh_with_access_token_fails(client):
    await client.post("/v1/auth/register", json=TEST_USER)
    login_resp = await client.post("/v1/auth/login", json={
        "email": TEST_USER["email"],
        "password": TEST_USER["password"],
    })
    access_token = login_resp.json()["access_token"]

    resp = await client.post("/v1/auth/refresh", json={"refresh_token": access_token})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me(client):
    await client.post("/v1/auth/register", json=TEST_USER)
    login_resp = await client.post("/v1/auth/login", json={
        "email": TEST_USER["email"],
        "password": TEST_USER["password"],
    })
    token = login_resp.json()["access_token"]

    resp = await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == TEST_USER["email"]
    assert data["username"] == TEST_USER["username"]
    assert data["publisher"] is None
    assert data["two_factor_enabled"] is False


@pytest.mark.asyncio
async def test_me_unauthenticated(client):
    resp = await client.get("/v1/auth/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_api_key(client):
    await client.post("/v1/auth/register", json=TEST_USER)
    login_resp = await client.post("/v1/auth/login", json={
        "email": TEST_USER["email"],
        "password": TEST_USER["password"],
    })
    token = login_resp.json()["access_token"]

    resp = await client.post(
        "/v1/auth/api-keys",
        json={"label": "test-key"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["key"].startswith("ank_")
    assert data["label"] == "test-key"
    assert "id" in data


@pytest.mark.asyncio
async def test_api_key_auth(client):
    await client.post("/v1/auth/register", json=TEST_USER)
    login_resp = await client.post("/v1/auth/login", json={
        "email": TEST_USER["email"],
        "password": TEST_USER["password"],
    })
    token = login_resp.json()["access_token"]

    key_resp = await client.post(
        "/v1/auth/api-keys",
        json={"label": "test"},
        headers={"Authorization": f"Bearer {token}"},
    )
    api_key = key_resp.json()["key"]

    resp = await client.get("/v1/auth/me", headers={"X-API-Key": api_key})
    assert resp.status_code == 200
    assert resp.json()["email"] == TEST_USER["email"]


@pytest.mark.asyncio
async def test_2fa_setup_and_verify(client):
    await client.post("/v1/auth/register", json=TEST_USER)
    login_resp = await client.post("/v1/auth/login", json={
        "email": TEST_USER["email"],
        "password": TEST_USER["password"],
    })
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Setup
    setup_resp = await client.post("/v1/auth/2fa/setup", headers=headers)
    assert setup_resp.status_code == 200
    data = setup_resp.json()
    assert "secret" in data
    assert "qr_uri" in data

    # Verify with correct code
    import pyotp
    totp = pyotp.TOTP(data["secret"])
    verify_resp = await client.post(
        "/v1/auth/2fa/verify",
        json={"totp_code": totp.now()},
        headers=headers,
    )
    assert verify_resp.status_code == 200
    assert verify_resp.json()["two_factor_enabled"] is True
