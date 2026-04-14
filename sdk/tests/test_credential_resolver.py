"""Tests for credential resolution from environment variables."""
import os

import pytest

from agentnode_sdk.credential_resolver import resolve_handle


@pytest.fixture(autouse=True)
def _clean_env():
    """Remove test credential env vars after each test."""
    keys_to_clean = []
    yield keys_to_clean
    for key in keys_to_clean:
        os.environ.pop(key, None)


class TestCredentialResolver:
    def test_resolves_api_key_from_env(self, _clean_env):
        os.environ["AGENTNODE_CRED_SLACK"] = "xoxb-test-token"
        _clean_env.append("AGENTNODE_CRED_SLACK")

        handle = resolve_handle("slack", "api_key")
        assert handle is not None
        assert handle.provider == "slack"
        assert handle.auth_type == "api_key"

    def test_resolves_oauth2_from_env(self, _clean_env):
        os.environ["AGENTNODE_CRED_GITHUB"] = "gho_test_token"
        _clean_env.append("AGENTNODE_CRED_GITHUB")

        handle = resolve_handle("github", "oauth2")
        assert handle is not None
        assert handle.provider == "github"
        assert handle.auth_type == "oauth2"

    def test_returns_none_when_no_env_var(self):
        # Ensure no matching env var exists
        os.environ.pop("AGENTNODE_CRED_NONEXISTENT", None)

        handle = resolve_handle("nonexistent", "api_key")
        assert handle is None

    def test_passes_scopes_and_domains(self, _clean_env):
        os.environ["AGENTNODE_CRED_SLACK"] = "xoxb-token"
        _clean_env.append("AGENTNODE_CRED_SLACK")

        handle = resolve_handle(
            "slack", "api_key",
            scopes=["channels:read"],
            allowed_domains=["api.slack.com"],
        )
        assert handle is not None
        assert handle.scopes == ["channels:read"]
        assert handle.allowed_domains == ["api.slack.com"]

    def test_hyphenated_provider_uses_underscore(self, _clean_env):
        os.environ["AGENTNODE_CRED_MY_SERVICE"] = "key-123"
        _clean_env.append("AGENTNODE_CRED_MY_SERVICE")

        handle = resolve_handle("my-service", "api_key")
        assert handle is not None
        assert handle.provider == "my-service"

    def test_api_key_handle_produces_correct_headers(self, _clean_env):
        os.environ["AGENTNODE_CRED_TEST"] = "sk-secret"
        _clean_env.append("AGENTNODE_CRED_TEST")

        handle = resolve_handle("test", "api_key", allowed_domains=[])
        headers = handle.authorized_request_headers("https://api.test.com")
        assert headers == {"Authorization": "Bearer sk-secret"}

    def test_oauth2_handle_produces_correct_headers(self, _clean_env):
        os.environ["AGENTNODE_CRED_TEST"] = "oauth-tok"
        _clean_env.append("AGENTNODE_CRED_TEST")

        handle = resolve_handle("test", "oauth2", allowed_domains=[])
        headers = handle.authorized_request_headers("https://api.test.com")
        assert headers == {"Authorization": "Bearer oauth-tok"}
