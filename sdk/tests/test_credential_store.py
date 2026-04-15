"""Tests for local credential store (~/.agentnode/credentials.json)."""
import json
import os

import pytest

from agentnode_sdk.credential_store import (
    _credentials_path,
    get_credential,
    has_credential,
    list_credentials,
    load_credentials,
    remove_credential,
    save_credentials,
    set_credential,
)


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Point credentials to a temp directory so tests don't touch real config."""
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "config.json"))
    return tmp_path


class TestLoadCredentials:
    def test_returns_empty_when_no_file(self):
        data = load_credentials()
        assert data == {"version": 1, "providers": {}}

    def test_reads_valid_file(self, _isolated_config):
        path = _credentials_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "version": 1,
            "providers": {
                "github": {
                    "access_token": "ghp_test",
                    "auth_type": "oauth2",
                    "scopes": ["repo"],
                    "stored_at": "2026-04-15T00:00:00Z",
                }
            }
        }))
        data = load_credentials()
        assert "github" in data["providers"]
        assert data["providers"]["github"]["access_token"] == "ghp_test"

    def test_handles_invalid_json(self, _isolated_config):
        path = _credentials_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not valid json {{{")
        data = load_credentials()
        assert data == {"version": 1, "providers": {}}

    def test_handles_non_object_json(self, _isolated_config):
        path = _credentials_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('"just a string"')
        data = load_credentials()
        assert data == {"version": 1, "providers": {}}

    def test_handles_missing_version(self, _isolated_config):
        path = _credentials_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"providers": {"github": {"access_token": "t"}}}))
        data = load_credentials()
        assert data["version"] == 1
        assert "github" in data["providers"]

    def test_handles_missing_providers(self, _isolated_config):
        path = _credentials_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 1}))
        data = load_credentials()
        assert data["providers"] == {}


class TestSaveCredentials:
    def test_creates_file(self, _isolated_config):
        save_credentials({"version": 1, "providers": {}})
        path = _credentials_path()
        assert path.is_file()
        data = json.loads(path.read_text())
        assert data["version"] == 1

    def test_creates_parent_directory(self, tmp_path, monkeypatch):
        deep = tmp_path / "nested" / "dir"
        monkeypatch.setenv("AGENTNODE_CONFIG", str(deep / "config.json"))
        save_credentials({"version": 1, "providers": {}})
        assert _credentials_path().is_file()

    def test_atomic_write_preserves_existing_on_read(self, _isolated_config):
        """If save succeeds, the file should be readable."""
        set_credential("github", "ghp_first", scopes=["repo"])
        set_credential("slack", "xoxb_second", scopes=["chat:write"])

        data = load_credentials()
        assert "github" in data["providers"]
        assert "slack" in data["providers"]


class TestSetCredential:
    def test_stores_new_credential(self):
        set_credential("github", "ghp_test", scopes=["repo", "read:user"])
        entry = get_credential("github")
        assert entry is not None
        assert entry["access_token"] == "ghp_test"
        assert entry["auth_type"] == "oauth2"
        assert entry["scopes"] == ["repo", "read:user"]
        assert "stored_at" in entry

    def test_normalizes_provider_to_lowercase(self):
        set_credential("GitHub", "ghp_test")
        assert has_credential("github")
        assert has_credential("GitHub")

    def test_overwrites_existing_credential(self):
        set_credential("github", "ghp_old")
        set_credential("github", "ghp_new")
        entry = get_credential("github")
        assert entry["access_token"] == "ghp_new"

    def test_custom_auth_type(self):
        set_credential("myapi", "key-123", auth_type="api_key")
        entry = get_credential("myapi")
        assert entry["auth_type"] == "api_key"


class TestGetCredential:
    def test_returns_none_when_missing(self):
        assert get_credential("nonexistent") is None

    def test_returns_none_for_non_dict_entry(self, _isolated_config):
        path = _credentials_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "version": 1,
            "providers": {"broken": "not a dict"},
        }))
        assert get_credential("broken") is None

    def test_normalizes_provider(self):
        set_credential("slack", "xoxb-test")
        assert get_credential("Slack") is not None
        assert get_credential("SLACK") is not None


class TestHasCredential:
    def test_true_when_exists(self):
        set_credential("github", "ghp_test")
        assert has_credential("github") is True

    def test_false_when_missing(self):
        assert has_credential("nonexistent") is False


class TestRemoveCredential:
    def test_removes_existing(self):
        set_credential("github", "ghp_test")
        assert remove_credential("github") is True
        assert has_credential("github") is False

    def test_returns_false_when_missing(self):
        assert remove_credential("nonexistent") is False

    def test_preserves_other_credentials(self):
        set_credential("github", "ghp_test")
        set_credential("slack", "xoxb_test")
        remove_credential("github")
        assert has_credential("slack") is True
        assert has_credential("github") is False


class TestListCredentials:
    def test_empty_when_no_credentials(self):
        assert list_credentials() == {}

    def test_returns_metadata_without_tokens(self):
        set_credential("github", "ghp_secret", scopes=["repo"])
        set_credential("slack", "xoxb_secret", scopes=["chat:write"])
        result = list_credentials()
        assert "github" in result
        assert "slack" in result
        # Must NOT contain token values
        assert "access_token" not in result["github"]
        assert "ghp_secret" not in str(result)
        assert "xoxb_secret" not in str(result)
        # Must contain metadata
        assert result["github"]["auth_type"] == "oauth2"
        assert result["github"]["scopes"] == ["repo"]
