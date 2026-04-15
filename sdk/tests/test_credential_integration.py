"""Integration tests for the local credential system.

These tests verify end-to-end flows across credential_store, credential_resolver,
and credential_handle — the same paths real users hit.
"""
import json
import os

import pytest

from agentnode_sdk.credential_handle import CredentialHandle
from agentnode_sdk.credential_resolver import resolve_handle
from agentnode_sdk.credential_store import (
    _credentials_path,
    get_credential,
    has_credential,
    load_credentials,
    save_credentials,
    set_credential,
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Isolate all file and env state per test."""
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "config.json"))
    # Clear credential-related env vars
    for key in list(os.environ):
        if key.startswith("AGENTNODE_CRED_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("AGENTNODE_SESSION_TOKEN", raising=False)
    monkeypatch.delenv("AGENTNODE_API_URL", raising=False)
    return tmp_path


# ---------------------------------------------------------------------------
# Integration Test 1: Resolver priority across all sources
# ---------------------------------------------------------------------------


class TestResolverPriorityIntegration:
    """Verify resolver picks the right source when multiple are available."""

    def test_auto_prefers_env_over_local_over_api(self, monkeypatch):
        """auto mode: env wins even when local file has a credential."""
        set_credential("github", "ghp_from_local", scopes=["repo"])
        monkeypatch.setenv("AGENTNODE_CRED_GITHUB", "ghp_from_env")

        handle = resolve_handle("github", "oauth2")
        assert handle is not None
        assert handle.source == "env"
        # Verify it's actually the env token by checking headers
        headers = handle.authorized_request_headers("https://api.github.com/user")
        assert headers["Authorization"] == "Bearer ghp_from_env"

    def test_auto_falls_back_to_local_when_no_env(self):
        """auto mode: local file is used when no env var exists."""
        set_credential("github", "ghp_from_local", scopes=["repo"])

        handle = resolve_handle("github", "oauth2")
        assert handle is not None
        assert handle.source == "local_file"
        headers = handle.authorized_request_headers("https://api.github.com/user")
        assert headers["Authorization"] == "Bearer ghp_from_local"

    def test_auto_returns_none_when_nothing_configured(self):
        """auto mode: None when no env, no local, no API session."""
        handle = resolve_handle("github", "oauth2")
        assert handle is None

    def test_local_mode_ignores_env(self, _isolated, monkeypatch):
        """local mode: only checks local file, ignores env."""
        from agentnode_sdk.config import load_config, save_config

        set_credential("github", "ghp_local")
        monkeypatch.setenv("AGENTNODE_CRED_GITHUB", "ghp_env")

        cfg = load_config()
        cfg.setdefault("credentials", {})["resolve_mode"] = "local"
        save_config(cfg)

        handle = resolve_handle("github", "oauth2")
        assert handle is not None
        assert handle.source == "local_file"
        headers = handle.authorized_request_headers("https://api.github.com/user")
        assert headers["Authorization"] == "Bearer ghp_local"

    def test_env_mode_ignores_local(self, _isolated, monkeypatch):
        """env mode: only checks env, ignores local file."""
        from agentnode_sdk.config import load_config, save_config

        set_credential("github", "ghp_local")

        cfg = load_config()
        cfg.setdefault("credentials", {})["resolve_mode"] = "env"
        save_config(cfg)

        handle = resolve_handle("github", "oauth2")
        assert handle is None  # env var not set, local file ignored

    def test_multiple_providers_independent(self):
        """Different providers resolve independently."""
        set_credential("github", "ghp_token", scopes=["repo"])
        set_credential("slack", "xoxb_token", scopes=["chat:write"])

        gh = resolve_handle("github", "oauth2")
        sl = resolve_handle("slack", "oauth2")

        assert gh is not None
        assert sl is not None
        assert gh.provider == "github"
        assert sl.provider == "slack"
        assert gh.source == "local_file"
        assert sl.source == "local_file"

        # Headers are provider-specific
        gh_h = gh.authorized_request_headers("https://api.github.com/user")
        sl_h = sl.authorized_request_headers("https://slack.com/api/auth.test")
        assert gh_h["Authorization"] == "Bearer ghp_token"
        assert sl_h["Authorization"] == "Bearer xoxb_token"


# ---------------------------------------------------------------------------
# Integration Test 2: CLI write → SDK read → Handle works
# ---------------------------------------------------------------------------


class TestCliSdkIntegration:
    """Simulate CLI writing credentials, SDK reading them."""

    def test_cli_write_sdk_read(self, _isolated):
        """Simulate what `agentnode auth github` does, then verify SDK reads it."""
        # --- CLI side: write credential (mirrors saveLocalCredentials in auth.ts) ---
        creds_path = _credentials_path()
        creds_path.parent.mkdir(parents=True, exist_ok=True)
        creds_data = {
            "version": 1,
            "providers": {
                "github": {
                    "access_token": "ghp_cli_written_token",
                    "auth_type": "oauth2",
                    "scopes": ["repo", "read:user"],
                    "stored_at": "2026-04-15T10:00:00Z",
                }
            },
        }
        creds_path.write_text(json.dumps(creds_data, indent=2) + "\n")

        # --- SDK side: resolve credential ---
        handle = resolve_handle("github", "oauth2")
        assert handle is not None
        assert handle.provider == "github"
        assert handle.auth_type == "oauth2"
        assert handle.source == "local_file"
        assert handle.scopes == ["repo", "read:user"]

        # Handle produces correct auth headers
        headers = handle.authorized_request_headers("https://api.github.com/user")
        assert headers == {"Authorization": "Bearer ghp_cli_written_token"}

    def test_sdk_write_cli_read(self, _isolated):
        """SDK writes credential, verify raw file matches CLI expectations."""
        # --- SDK side: store credential ---
        set_credential("slack", "xoxb_sdk_token", scopes=["chat:write"])

        # --- CLI side: read raw file (mirrors loadLocalCredentials in auth.ts) ---
        creds_path = _credentials_path()
        raw = json.loads(creds_path.read_text(encoding="utf-8"))

        assert raw["version"] == 1
        assert "slack" in raw["providers"]
        entry = raw["providers"]["slack"]
        assert entry["access_token"] == "xoxb_sdk_token"
        assert entry["auth_type"] == "oauth2"
        assert entry["scopes"] == ["chat:write"]
        assert "stored_at" in entry

    def test_credential_survives_roundtrip(self, _isolated):
        """Write → read → write another → both survive."""
        set_credential("github", "ghp_first")
        set_credential("slack", "xoxb_second")

        # Both present
        assert has_credential("github")
        assert has_credential("slack")

        # Resolve both
        gh = resolve_handle("github", "oauth2")
        sl = resolve_handle("slack", "oauth2")
        assert gh is not None and gh.source == "local_file"
        assert sl is not None and sl.source == "local_file"

        # Remove one, other survives
        from agentnode_sdk.credential_store import remove_credential
        remove_credential("github")

        assert not has_credential("github")
        assert has_credential("slack")
        assert resolve_handle("github", "oauth2") is None
        assert resolve_handle("slack", "oauth2") is not None


# ---------------------------------------------------------------------------
# Integration Test 3: Broken file recovery
# ---------------------------------------------------------------------------


class TestBrokenFileRecovery:
    """Verify the system degrades gracefully with corrupted credential files."""

    def test_broken_json_does_not_block_new_write(self, _isolated):
        """Broken credentials.json → load returns empty → set_credential overwrites."""
        path = _credentials_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{this is not valid json!!!")

        # load_credentials returns empty, no crash
        data = load_credentials()
        assert data["providers"] == {}

        # New credential can still be written (overwrites broken file)
        set_credential("github", "ghp_fresh")
        assert has_credential("github")

        # File is now valid
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["providers"]["github"]["access_token"] == "ghp_fresh"

    def test_partial_json_missing_fields(self, _isolated):
        """File with missing fields doesn't crash, fills defaults."""
        path = _credentials_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # No "version" key, provider has minimal data
        path.write_text(json.dumps({
            "providers": {"github": {"access_token": "ghp_minimal"}}
        }))

        # Resolve still works
        handle = resolve_handle("github", "oauth2")
        assert handle is not None
        assert handle.source == "local_file"

        # get_credential returns whatever is there
        entry = get_credential("github")
        assert entry["access_token"] == "ghp_minimal"


# ---------------------------------------------------------------------------
# Integration Test 4: Config interaction
# ---------------------------------------------------------------------------


class TestConfigInteraction:
    """Verify credential config settings affect resolver behavior."""

    def test_require_before_auto_install_default_true(self, _isolated):
        """Default config has require_before_auto_install=true."""
        from agentnode_sdk.config import load_config
        cfg = load_config()
        assert cfg["credentials"]["require_before_auto_install"] is True

    def test_config_roundtrip(self, _isolated):
        """Set credentials config, save, reload, verify."""
        from agentnode_sdk.config import load_config, save_config, set_value
        cfg = load_config()
        set_value(cfg, "credentials.require_before_auto_install", "false")
        save_config(cfg)

        reloaded = load_config()
        assert reloaded["credentials"]["require_before_auto_install"] is False
