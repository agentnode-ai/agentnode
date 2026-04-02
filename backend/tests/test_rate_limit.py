"""Tests for rate limiting.

The rate limiter uses a sliding window backed by Redis sorted sets.
The Redis mock in conftest.py now tracks sorted set state, so these
tests exercise real rate-limit enforcement (429 responses).
"""
import pytest


@pytest.mark.asyncio
async def test_rate_limit_register_enforced(client):
    """Register endpoint (rate_limit(5, 60)) should return 429 after 5 requests."""
    status_codes = []
    for i in range(8):
        resp = await client.post("/v1/auth/register", json={
            "email": f"ratelimit{i}@test.dev",
            "username": f"ratelimituser{i}",
            "password": "TestPass123!",
        })
        status_codes.append(resp.status_code)

    # First 5 should succeed (201) or fail for non-rate-limit reasons (e.g., 422)
    # but should NOT be 429
    for code in status_codes[:5]:
        assert code != 429, f"Got 429 too early (within first 5 requests)"

    # At least one request after the 5th should be rate-limited
    assert 429 in status_codes[5:], (
        f"Expected 429 after exceeding limit of 5, got: {status_codes}"
    )


@pytest.mark.asyncio
async def test_rate_limit_429_body(client):
    """429 response body should contain rate limit error details."""
    # Exhaust the register limit (5 requests)
    for i in range(6):
        resp = await client.post("/v1/auth/register", json={
            "email": f"body{i}@test.dev",
            "username": f"bodyuser{i}",
            "password": "TestPass123!",
        })

    # The 6th should be 429
    assert resp.status_code == 429
    body = resp.json()
    assert body["error"]["code"] == "RATE_LIMITED"
    assert "Rate limit" in body["error"]["message"]
    assert body["error"]["details"]["limit"] == 5
    assert body["error"]["details"]["retry_after"] == 60


@pytest.mark.asyncio
async def test_rate_limit_headers_present(client):
    """Responses from rate-limited endpoints should include X-RateLimit-* headers."""
    resp = await client.post("/v1/auth/register", json={
        "email": "headers@test.dev",
        "username": "headersuser",
        "password": "TestPass123!",
    })
    assert "x-ratelimit-limit" in resp.headers
    assert "x-ratelimit-remaining" in resp.headers
    assert "x-ratelimit-reset" in resp.headers
    assert resp.headers["x-ratelimit-limit"] == "5"


@pytest.mark.asyncio
async def test_rate_limit_remaining_decreases(client):
    """X-RateLimit-Remaining should decrease with each request."""
    remaining_values = []
    for i in range(3):
        resp = await client.post("/v1/auth/register", json={
            "email": f"remaining{i}@test.dev",
            "username": f"remaininguser{i}",
            "password": "TestPass123!",
        })
        if resp.status_code != 429:
            remaining_values.append(int(resp.headers["x-ratelimit-remaining"]))

    # Remaining should be strictly decreasing
    assert len(remaining_values) >= 2, f"Expected at least 2 non-429 responses, got {remaining_values}"
    for j in range(1, len(remaining_values)):
        assert remaining_values[j] < remaining_values[j - 1], (
            f"Remaining did not decrease: {remaining_values}"
        )


@pytest.mark.asyncio
async def test_rate_limit_different_paths_independent(client):
    """Rate limits on different paths should be tracked independently."""
    # Hit register 5 times (exhausts its limit)
    for i in range(5):
        await client.post("/v1/auth/register", json={
            "email": f"indep{i}@test.dev",
            "username": f"indepuser{i}",
            "password": "TestPass123!",
        })

    # Register should now be rate-limited
    resp = await client.post("/v1/auth/register", json={
        "email": "indep_extra@test.dev",
        "username": "indepextra",
        "password": "TestPass123!",
    })
    assert resp.status_code == 429

    # But a different endpoint (e.g., healthz which has no rate limit, or
    # another rate-limited endpoint with its own key) should still work.
    resp = await client.get("/healthz")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_healthz_not_rate_limited(client):
    """Health check has no rate limit dependency and should always succeed."""
    for _ in range(25):
        resp = await client.get("/healthz")
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_rate_limit_download_endpoint(client):
    """Download endpoint should enforce rate limits (not just crash)."""
    statuses = []
    # Download rate_limit(30, 60) — send 32 requests
    for _ in range(32):
        resp = await client.post("/v1/packages/nonexistent/download")
        statuses.append(resp.status_code)

    # First 30 should be 404 (package not found), not 429
    for code in statuses[:30]:
        assert code != 429, f"Got 429 too early (within first 30 requests)"

    # After 30, at least one should be 429
    assert 429 in statuses[30:], (
        f"Expected 429 after exceeding limit of 30, got codes after 30th: {statuses[30:]}"
    )
