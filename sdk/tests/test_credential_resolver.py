"""Tests for credential resolution from environment variables and local file."""
import json
import os

import pytest

from agentnode_sdk.credential_resolver import resolve_handle, _resolve_from_local_file


@pytest.fixture(autouse=True)
def _clean_env():
    """Remove test credential env vars after each test."""
    keys_to_clean = []
    yield keys_to_clean
    for key in keys_to_clean:
        os.environ.pop(key, None)


@pytest.fixture()
def _isolated_creds(tmp_path, monkeypatch):
    """Point credential store to a temp directory."""
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "config.json"))
    return tmp_path


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

    def test_env_source_is_env(self, _clean_env):
        os.environ["AGENTNODE_CRED_TEST"] = "tok"
        _clean_env.append("AGENTNODE_CRED_TEST")

        handle = resolve_handle("test", "oauth2")
        assert handle is not None
        assert handle.source == "env"


class TestLocalFileResolver:
    """Tests for _resolve_from_local_file()."""

    def test_resolves_oauth2_from_local_file(self, _isolated_creds):
        from agentnode_sdk.credential_store import set_credential
        set_credential("github", "ghp_local_token", scopes=["repo"])

        handle = _resolve_from_local_file("github", "oauth2")
        assert handle is not None
        assert handle.provider == "github"
        assert handle.auth_type == "oauth2"
        assert handle.source == "local_file"

    def test_returns_none_when_no_local_credential(self, _isolated_creds):
        handle = _resolve_from_local_file("nonexistent", "oauth2")
        assert handle is None

    def test_returns_none_for_empty_token(self, _isolated_creds):
        from agentnode_sdk.credential_store import save_credentials
        save_credentials({
            "version": 1,
            "providers": {"github": {"access_token": "", "auth_type": "oauth2"}},
        })
        handle = _resolve_from_local_file("github", "oauth2")
        assert handle is None

    def test_uses_stored_scopes_when_no_explicit_scopes(self, _isolated_creds):
        from agentnode_sdk.credential_store import set_credential
        set_credential("github", "ghp_tok", scopes=["repo", "read:user"])

        handle = _resolve_from_local_file("github", "oauth2")
        assert handle is not None
        assert handle.scopes == ["repo", "read:user"]

    def test_explicit_scopes_override_stored(self, _isolated_creds):
        from agentnode_sdk.credential_store import set_credential
        set_credential("github", "ghp_tok", scopes=["repo"])

        handle = _resolve_from_local_file("github", "oauth2", scopes=["admin"])
        assert handle is not None
        assert handle.scopes == ["admin"]

    def test_produces_correct_oauth2_headers(self, _isolated_creds):
        from agentnode_sdk.credential_store import set_credential
        set_credential("github", "ghp_local_secret")

        handle = _resolve_from_local_file("github", "oauth2", allowed_domains=[])
        headers = handle.authorized_request_headers("https://api.github.com/user")
        assert headers == {"Authorization": "Bearer ghp_local_secret"}

    def test_produces_correct_api_key_headers(self, _isolated_creds):
        from agentnode_sdk.credential_store import set_credential
        set_credential("myapi", "key-123", auth_type="api_key")

        handle = _resolve_from_local_file("myapi", "api_key", allowed_domains=[])
        headers = handle.authorized_request_headers("https://api.example.com")
        assert headers == {"Authorization": "Bearer key-123"}


class TestResolverChain:
    """Test the full resolution chain: env → local file → API."""

    def test_env_takes_priority_over_local_file(self, _clean_env, _isolated_creds):
        """Env var should win over local file credential."""
        from agentnode_sdk.credential_store import set_credential
        set_credential("github", "ghp_local")

        os.environ["AGENTNODE_CRED_GITHUB"] = "ghp_env"
        _clean_env.append("AGENTNODE_CRED_GITHUB")

        handle = resolve_handle("github", "oauth2")
        assert handle is not None
        assert handle.source == "env"

    def test_local_file_used_when_no_env(self, _isolated_creds):
        """Local file should be used when no env var is set."""
        from agentnode_sdk.credential_store import set_credential
        set_credential("github", "ghp_local")
        os.environ.pop("AGENTNODE_CRED_GITHUB", None)

        handle = resolve_handle("github", "oauth2")
        assert handle is not None
        assert handle.source == "local_file"

    def test_none_when_no_source_available(self, _isolated_creds):
        """Returns None when no env, no local file, and no API config."""
        os.environ.pop("AGENTNODE_CRED_MISSING", None)
        os.environ.pop("AGENTNODE_SESSION_TOKEN", None)
        os.environ.pop("AGENTNODE_API_URL", None)

        handle = resolve_handle("missing", "oauth2")
        assert handle is None


class TestResolveMode:
    """Test explicit resolve_mode settings."""

    def test_local_mode_uses_only_local_file(self, _clean_env, _isolated_creds):
        """resolve_mode='local' ignores env vars."""
        from agentnode_sdk.credential_store import set_credential
        from agentnode_sdk.config import save_config, load_config
        set_credential("github", "ghp_local")

        # Also set env var — should be ignored in local mode
        os.environ["AGENTNODE_CRED_GITHUB"] = "ghp_env"
        _clean_env.append("AGENTNODE_CRED_GITHUB")

        cfg = load_config()
        cfg.setdefault("credentials", {})["resolve_mode"] = "local"
        save_config(cfg)

        handle = resolve_handle("github", "oauth2")
        assert handle is not None
        assert handle.source == "local_file"

    def test_local_mode_returns_none_when_no_local(self, _isolated_creds):
        """resolve_mode='local' returns None when no local credential exists."""
        from agentnode_sdk.config import save_config, load_config
        cfg = load_config()
        cfg.setdefault("credentials", {})["resolve_mode"] = "local"
        save_config(cfg)

        os.environ.pop("AGENTNODE_CRED_GITHUB", None)
        handle = resolve_handle("github", "oauth2")
        assert handle is None

    def test_env_mode_ignores_local_file(self, _clean_env, _isolated_creds):
        """resolve_mode='env' does not check local file."""
        from agentnode_sdk.credential_store import set_credential
        from agentnode_sdk.config import save_config, load_config
        set_credential("github", "ghp_local")

        cfg = load_config()
        cfg.setdefault("credentials", {})["resolve_mode"] = "env"
        save_config(cfg)

        os.environ.pop("AGENTNODE_CRED_GITHUB", None)
        handle = resolve_handle("github", "oauth2")
        assert handle is None
