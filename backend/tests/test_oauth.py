"""Tests for OAuth2 PKCE flow (PR 3)."""
import os
import time

import pytest

from app.credentials.oauth import (
    _generate_pkce,
    _get_provider_config,
    _pending_states,
    _STATE_TTL_SECONDS,
    _cleanup_expired_states,
)
from app.shared.exceptions import AppError


@pytest.fixture(autouse=True)
def _clean_state():
    """Clear pending states after each test."""
    _pending_states.clear()
    yield
    _pending_states.clear()


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


class TestStateManagement:
    def test_state_stored_and_retrieved(self):
        _pending_states["abc"] = {
            "user_id": "u1",
            "provider": "github",
            "connector_package_slug": "gh-pack",
            "code_verifier": "verifier",
            "scopes": [],
            "created_at": time.time(),
        }
        assert "abc" in _pending_states
        popped = _pending_states.pop("abc")
        assert popped["provider"] == "github"
        assert "abc" not in _pending_states

    def test_cleanup_expired(self):
        _pending_states["old"] = {
            "user_id": "u1",
            "provider": "github",
            "connector_package_slug": "gh-pack",
            "code_verifier": "v",
            "scopes": [],
            "created_at": time.time() - _STATE_TTL_SECONDS - 10,
        }
        _pending_states["new"] = {
            "user_id": "u2",
            "provider": "slack",
            "connector_package_slug": "sl-pack",
            "code_verifier": "v2",
            "scopes": [],
            "created_at": time.time(),
        }
        _cleanup_expired_states()
        assert "old" not in _pending_states
        assert "new" in _pending_states
