"""Tests for OAuth2 PKCE flow — Redis state store + PKCE + provider config."""
import json
import os
import time
from unittest.mock import AsyncMock, patch

import pytest

from app.credentials.oauth import (
    _generate_pkce,
    _get_provider_config,
    _fallback_states,
    _store_state,
    _pop_state,
    _STATE_TTL_SECONDS,
)
from app.shared.exceptions import AppError


@pytest.fixture(autouse=True)
def _clean_state():
    """Clear fallback states after each test."""
    _fallback_states.clear()
    yield
    _fallback_states.clear()


class TestPKCE:
    def test_generates_verifier_and_challenge(self):
        verifier, challenge = _generate_pkce()
        assert len(verifier) > 40
        assert len(challenge) > 20
        assert verifier != challenge

    def test_verifier_challenge_are_different_each_time(self):
        v1, c1 = _generate_pkce()
        v2, c2 = _generate_pkce()
        assert v1 != v2
        assert c1 != c2

    def test_challenge_is_s256_of_verifier(self):
        import hashlib
        from base64 import urlsafe_b64encode

        verifier, challenge = _generate_pkce()
        expected = urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        assert challenge == expected


class TestProviderConfig:
    def test_github_requires_env(self, monkeypatch):
        monkeypatch.delenv("OAUTH_GITHUB_CLIENT_ID", raising=False)
        monkeypatch.delenv("OAUTH_GITHUB_CLIENT_SECRET", raising=False)
        with pytest.raises(AppError, match="not configured"):
            _get_provider_config("github")

    def test_github_returns_config(self, monkeypatch):
        monkeypatch.setenv("OAUTH_GITHUB_CLIENT_ID", "test-id")
        monkeypatch.setenv("OAUTH_GITHUB_CLIENT_SECRET", "test-secret")
        config = _get_provider_config("github")
        assert config["provider"] == "github"
        assert config["client_id"] == "test-id"
        assert "authorize_url" in config
        assert "token_url" in config

    def test_slack_requires_env(self, monkeypatch):
        monkeypatch.delenv("OAUTH_SLACK_CLIENT_ID", raising=False)
        monkeypatch.delenv("OAUTH_SLACK_CLIENT_SECRET", raising=False)
        with pytest.raises(AppError, match="not configured"):
            _get_provider_config("slack")

    def test_slack_returns_config(self, monkeypatch):
        monkeypatch.setenv("OAUTH_SLACK_CLIENT_ID", "slack-id")
        monkeypatch.setenv("OAUTH_SLACK_CLIENT_SECRET", "slack-secret")
        config = _get_provider_config("slack")
        assert config["provider"] == "slack"

    def test_unsupported_provider(self):
        with pytest.raises(AppError, match="not supported"):
            _get_provider_config("notion")


class TestRedisStateStore:
    """Test the Redis-backed state store with mocked Redis."""

    @pytest.mark.asyncio
    async def test_store_and_pop_with_redis(self):
        """State stored in Redis is retrieved and deleted correctly."""
        mock_redis = AsyncMock()
        stored = {}

        async def fake_set(key, value, ex=None):
            stored[key] = value

        async def fake_get(key):
            return stored.get(key)

        async def fake_delete(key):
            stored.pop(key, None)

        mock_redis.set = fake_set
        mock_redis.get = fake_get
        mock_redis.delete = fake_delete
        mock_redis.ping = AsyncMock()

        payload = {
            "user_id": "u1",
            "provider": "github",
            "connector_package_slug": "gh-pack",
            "code_verifier": "verifier123",
            "scopes": ["repo"],
            "created_at": time.time(),
        }

        with patch("app.credentials.oauth._get_redis", return_value=mock_redis):
            await _store_state("state-abc", payload)
            assert "oauth:state:state-abc" in stored

            result = await _pop_state("state-abc")
            assert result is not None
            assert result["provider"] == "github"
            assert result["code_verifier"] == "verifier123"
            assert "oauth:state:state-abc" not in stored

    @pytest.mark.asyncio
    async def test_pop_missing_state_returns_none(self):
        """Popping a non-existent state returns None."""
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.ping = AsyncMock()

        with patch("app.credentials.oauth._get_redis", return_value=mock_redis):
            result = await _pop_state("nonexistent")
            assert result is None


class TestFallbackStateStore:
    """Test the in-memory fallback when Redis is unavailable (dev only)."""

    @pytest.mark.asyncio
    async def test_fallback_store_and_pop(self, monkeypatch):
        """Without Redis in dev, state is stored in memory."""
        monkeypatch.setenv("ENVIRONMENT", "development")

        payload = {
            "user_id": "u1",
            "provider": "github",
            "connector_package_slug": "gh-pack",
            "code_verifier": "v",
            "scopes": [],
            "created_at": time.time(),
        }

        with patch("app.credentials.oauth._get_redis", return_value=None):
            await _store_state("abc", payload)
            assert "abc" in _fallback_states

            result = await _pop_state("abc")
            assert result["provider"] == "github"
            assert "abc" not in _fallback_states

    @pytest.mark.asyncio
    async def test_fallback_expired_state_returns_none(self, monkeypatch):
        """Expired state in fallback returns None."""
        monkeypatch.setenv("ENVIRONMENT", "development")

        payload = {
            "user_id": "u1",
            "provider": "github",
            "connector_package_slug": "gh-pack",
            "code_verifier": "v",
            "scopes": [],
            "created_at": time.time() - _STATE_TTL_SECONDS - 10,
        }

        with patch("app.credentials.oauth._get_redis", return_value=None):
            await _store_state("old", payload)
            result = await _pop_state("old")
            assert result is None

    @pytest.mark.asyncio
    async def test_production_without_redis_fails(self, monkeypatch):
        """In production, missing Redis raises an error."""
        monkeypatch.setenv("ENVIRONMENT", "production")

        with patch("app.credentials.oauth._get_redis", return_value=None):
            with pytest.raises(AppError, match="Redis is required"):
                await _store_state("x", {"created_at": time.time()})

    @pytest.mark.asyncio
    async def test_production_pop_without_redis_fails(self, monkeypatch):
        """In production, popping without Redis raises an error."""
        monkeypatch.setenv("ENVIRONMENT", "production")

        with patch("app.credentials.oauth._get_redis", return_value=None):
            with pytest.raises(AppError, match="Redis is required"):
                await _pop_state("x")
