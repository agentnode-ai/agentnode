"""UX-2 — credential vault: OS-keychain primary, honest file fallback.

All keyring access goes through the ``_get_keyring_backend()`` seam, faked
here — the real keyring library never enters the unit-test import graph.
"""
from __future__ import annotations

import json
import time

import pytest

from agentnode_sdk import credential_store as cs


class FakeKeyring:
    """Dict-backed keyring lookalike (get/set/delete_password)."""

    def __init__(self):
        self.store: dict = {}
        self.get_calls = 0

    def set_password(self, service, user, pw):
        self.store[(service, user)] = pw

    def get_password(self, service, user):
        self.get_calls += 1
        return self.store.get((service, user))

    def delete_password(self, service, user):
        if (service, user) not in self.store:
            raise RuntimeError("no such item")
        del self.store[(service, user)]


class BlockingKeyring(FakeKeyring):
    """Simulates a locked Secret Service that hangs on access."""

    def get_password(self, service, user):
        time.sleep(30)
        return None


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Isolated credentials file + fresh keyring-probe state per test."""
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setitem(cs._keyring_state, "available", None)
    yield


def _use_keyring(monkeypatch, fake):
    monkeypatch.setattr(cs, "_get_keyring_backend", lambda: fake)


def _raw_json():
    return json.loads(cs._credentials_path().read_text(encoding="utf-8"))


# --- keyring path ------------------------------------------------------------

def test_set_with_keyring_secret_not_in_json(monkeypatch):
    fake = FakeKeyring()
    _use_keyring(monkeypatch, fake)
    storage = cs.set_credential("openai", "sk-vault-secret-1234", auth_type="api_key")
    assert storage == "keyring"
    # secret ONLY in the keychain, under the collision-safe service name
    assert fake.store[("agentnode:openai", "token")] == "sk-vault-secret-1234"
    raw = _raw_json()
    entry = raw["providers"]["openai"]
    assert entry["storage"] == "keyring"
    assert "access_token" not in entry
    assert "sk-vault-secret-1234" not in json.dumps(raw)
    # resolution round-trip
    assert cs.get_credential("openai")["access_token"] == "sk-vault-secret-1234"
    assert cs.get_llm_api_key("openai") == "sk-vault-secret-1234"


def test_remove_deletes_keychain_and_metadata(monkeypatch):
    fake = FakeKeyring()
    _use_keyring(monkeypatch, fake)
    cs.set_credential("openai", "sk-x-12345678")
    assert cs.remove_credential("openai") is True
    assert ("agentnode:openai", "token") not in fake.store
    assert cs.get_credential("openai") is None


def test_remove_tolerates_missing_keychain_item(monkeypatch):
    fake = FakeKeyring()
    _use_keyring(monkeypatch, fake)
    cs.set_credential("openai", "sk-x-12345678")
    del fake.store[("agentnode:openai", "token")]  # orphaned metadata
    assert cs.remove_credential("openai") is True  # delete error tolerated
    assert "openai" not in _raw_json()["providers"]


def test_metadata_keyring_but_item_missing_returns_none(monkeypatch):
    fake = FakeKeyring()
    _use_keyring(monkeypatch, fake)
    cs.set_credential("openai", "sk-x-12345678")
    del fake.store[("agentnode:openai", "token")]
    assert cs.get_credential("openai") is None      # no exception
    assert cs.get_llm_api_key("openai") is None


def test_keyring_write_failure_falls_back_to_file(monkeypatch):
    class WriteFails(FakeKeyring):
        def set_password(self, *a):
            raise RuntimeError("locked")

    _use_keyring(monkeypatch, WriteFails())
    storage = cs.set_credential("openai", "sk-y-12345678")
    assert storage == "file"
    entry = _raw_json()["providers"]["openai"]
    assert entry["storage"] == "file"
    assert entry["access_token"] == "sk-y-12345678"


# --- fallback path -----------------------------------------------------------

def test_no_keyring_falls_back_to_file_with_honest_label(monkeypatch):
    _use_keyring(monkeypatch, None)
    storage = cs.set_credential("github", "ghp-token-12345678")
    assert storage == "file"
    entry = _raw_json()["providers"]["github"]
    assert entry["storage"] == "file"
    assert entry["access_token"] == "ghp-token-12345678"
    assert cs.get_credential("github")["access_token"] == "ghp-token-12345678"
    assert "encrypted" not in cs.storage_label("file").lower()
    assert "plaintext" in cs.storage_label("file")


def test_probe_timeout_falls_back(monkeypatch):
    monkeypatch.setattr(cs, "_PROBE_TIMEOUT_S", 0.2)
    _use_keyring(monkeypatch, BlockingKeyring())
    assert cs._keyring_available() is False         # no hang, clean fallback
    storage = cs.set_credential("openai", "sk-z-12345678")
    assert storage == "file"


def test_probe_runs_once_per_process(monkeypatch):
    fake = FakeKeyring()
    _use_keyring(monkeypatch, fake)
    cs._keyring_available()
    calls_after_probe = fake.get_calls
    cs._keyring_available()
    assert fake.get_calls == calls_after_probe      # cached, no second probe


# --- legacy + migration ------------------------------------------------------

def test_legacy_plaintext_entry_still_resolves(monkeypatch):
    _use_keyring(monkeypatch, None)
    # hand-written pre-vault entry: no "storage" marker
    path = cs._credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "version": 1,
        "providers": {"github": {"access_token": "ghp-legacy-1234",
                                 "auth_type": "oauth2", "scopes": []}},
    }), encoding="utf-8")
    assert cs.get_credential("github")["access_token"] == "ghp-legacy-1234"
    assert cs.has_credential("github") is True
    assert cs.list_credentials()["github"]["storage"] == "file"


def test_write_migrates_legacy_plaintext_to_keyring(monkeypatch):
    fake = FakeKeyring()
    _use_keyring(monkeypatch, fake)
    path = cs._credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "version": 1,
        "providers": {"openai": {"access_token": "sk-OLD-PLAINTEXT-1234",
                                 "auth_type": "api_key", "scopes": []}},
    }), encoding="utf-8")
    cs.set_credential("openai", "sk-NEW-secret-1234", auth_type="api_key")
    raw = path.read_text(encoding="utf-8")
    assert "sk-OLD-PLAINTEXT-1234" not in raw        # legacy token stripped
    assert "sk-NEW-secret-1234" not in raw           # new one keychain-only
    assert fake.store[("agentnode:openai", "token")] == "sk-NEW-secret-1234"


def test_read_never_migrates(monkeypatch):
    """Reads are side-effect-free: no keychain writes, no file rewrites."""
    fake = FakeKeyring()
    _use_keyring(monkeypatch, fake)
    path = cs._credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "version": 1,
        "providers": {"openai": {"access_token": "sk-legacy-1234",
                                 "auth_type": "api_key", "scopes": []}},
    }), encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    cs.get_credential("openai")
    assert path.read_text(encoding="utf-8") == before
    assert fake.store == {}                          # nothing written


# --- metadata-only contracts -------------------------------------------------

def test_list_credentials_never_touches_keychain(monkeypatch):
    fake = FakeKeyring()
    _use_keyring(monkeypatch, fake)
    cs.set_credential("openai", "sk-a-12345678")
    fake.get_calls = 0
    out = cs.list_credentials()
    assert fake.get_calls == 0                       # metadata only, no prompt
    assert out["openai"]["storage"] == "keyring"
    assert "access_token" not in out["openai"]
    assert "sk-a-12345678" not in json.dumps(out)


def test_storage_labels_never_claim_encryption():
    for storage in ("keyring", "file"):
        assert "encrypt" not in cs.storage_label(storage).lower()
