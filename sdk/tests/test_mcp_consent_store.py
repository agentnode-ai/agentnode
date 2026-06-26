"""Stage 3B-1: persistent consent-grant store. Pure metadata; no secrets/docker/egress.

The store lives at ``config_dir()/consent_grants.json``; tests isolate it via AGENTNODE_CONFIG.
Permission-bit assertions are POSIX-only (Windows ACLs differ — perm enforcement is skipped
there by the store itself).
"""
from __future__ import annotations

import json
import os
import stat

import pytest

from agentnode_sdk.runtimes import mcp_consent_store as store

CK = "ck-" + "a" * 60


@pytest.fixture(autouse=True)
def _isolated_store(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path))
    if os.name == "posix":
        os.chmod(tmp_path, 0o700)  # a secure config dir for the happy path
    yield


def _add(lifetime=store.LIFETIME_90D, ck=CK, now=1000.0, slug="gh-mcp"):
    return store.add(
        consent_key=ck, slug=slug, version="1.2.3", artifact_hash="sha256:" + "a" * 64,
        env_key_names=["GITHUB_TOKEN"], allowed_domains=["api.github.com"],
        lifetime=lifetime, now=now,
    )


def test_missing_file_is_empty_store():
    assert store.load() == []
    assert store.find_valid(CK) is None


def test_add_and_find_valid():
    g = _add(now=1000.0)
    assert g["consent_key"] == CK
    found = store.find_valid(CK, now=1000.0 + 86400)
    assert found is not None and found["slug"] == "gh-mcp"


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_dir_0700_file_0600():
    _add()
    assert stat.S_IMODE(os.stat(store.config_dir()).st_mode) & 0o077 == 0
    assert stat.S_IMODE(os.stat(store.grants_path()).st_mode) & 0o077 == 0


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink semantics")
def test_symlink_store_refused():
    _add()
    f = store.grants_path()
    real = f.with_suffix(".real")
    os.replace(f, real)
    os.symlink(real, f)
    with pytest.raises(store.GrantStoreError):
        store.load()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_too_open_file_perms_refused():
    _add()
    os.chmod(store.grants_path(), 0o644)
    with pytest.raises(store.GrantStoreError):
        store.load()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_too_open_dir_perms_refused():
    os.chmod(store.config_dir(), 0o755)
    with pytest.raises(store.GrantStoreError):
        _add()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_load_refuses_too_open_dir_perms():
    # An existing grant in a SECURE store, then the config dir is widened: READING must
    # fail-closed (a too-open dir could host an attacker-planted grant). find_valid must
    # also refuse, never authorize.
    _add(now=1000.0)
    os.chmod(store.config_dir(), 0o755)
    with pytest.raises(store.GrantStoreError):
        store.load()
    with pytest.raises(store.GrantStoreError):
        store.find_valid(CK, now=1000.0)


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink semantics")
def test_broken_symlink_store_refused():
    # A symlink to a non-existent target must NOT be silently treated as "missing file ⇒ []".
    os.symlink(str(store.grants_path().parent / "nonexistent-target.json"), store.grants_path())
    with pytest.raises(store.GrantStoreError):
        store.load()


def test_corrupt_json_refused():
    store.grants_path().write_text("{ not valid json", encoding="utf-8")
    with pytest.raises(store.GrantStoreError):
        store.load()


def test_unknown_schema_refused():
    store.grants_path().write_text(
        json.dumps({"schema_version": 999, "grants": []}), encoding="utf-8")
    with pytest.raises(store.GrantStoreError):
        store.load()


def test_invalid_grant_refused():
    store.grants_path().write_text(
        json.dumps({"schema_version": store.SCHEMA_VERSION, "grants": [{"slug": "x"}]}),
        encoding="utf-8")
    with pytest.raises(store.GrantStoreError):
        store.load()


def test_secret_bearing_field_refused():
    bad = {"schema_version": store.SCHEMA_VERSION, "grants": [{
        "consent_key": CK, "slug": "s", "version": "1", "artifact_hash": "sha256:x",
        "env_key_names": [], "allowed_domains": [], "created_at": 1.0, "lifetime": "90d",
        "api_key": "ghp_leaked_value",
    }]}
    store.grants_path().write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(store.GrantStoreError):
        store.load()


def test_identity_mismatch_not_found():
    _add(ck=CK)
    # a different consent_key (= any change to slug/version/artifact_hash/keys/domains) misses
    assert store.find_valid("ck-" + "b" * 60) is None


def test_expired_grant_refused():
    _add(lifetime=store.LIFETIME_7D, now=1000.0)
    assert store.find_valid(CK, now=1000.0 + 6 * 86400) is not None
    assert store.find_valid(CK, now=1000.0 + 8 * 86400) is None


def test_revoked_grant_refused():
    _add(now=1000.0)
    assert store.revoke("gh-mcp") == 1
    assert store.find_valid(CK, now=1000.0) is None


def test_revoke_specific_key_only():
    _add(ck="ck-" + "a" * 60, slug="gh-mcp", now=1000.0)
    _add(ck="ck-" + "c" * 60, slug="gh-mcp", now=1000.0)
    assert store.revoke("gh-mcp", key="ck-" + "a" * 60) == 1
    assert store.find_valid("ck-" + "a" * 60, now=1000.0) is None
    assert store.find_valid("ck-" + "c" * 60, now=1000.0) is not None


def test_revoke_all():
    _add(ck="ck-" + "a" * 60, slug="a", now=1000.0)
    _add(ck="ck-" + "b" * 60, slug="b", now=1000.0)
    assert store.revoke_all() == 2
    assert store.load() and all(g["revoked"] for g in store.load())


def test_forever_has_no_expiry():
    g = _add(lifetime=store.LIFETIME_FOREVER, now=1000.0)
    assert g["expires_at"] is None
    assert store.find_valid(CK, now=1000.0 + 10 ** 9) is not None


def test_ephemeral_cannot_be_persisted():
    with pytest.raises(store.GrantStoreError):
        _add(lifetime=store.LIFETIME_THIS_RUN)


def test_grant_holds_only_metadata_fields():
    _add()
    g = store.load()[0]
    assert set(g) <= {
        "schema_version", "consent_key", "slug", "version", "artifact_hash",
        "env_key_names", "allowed_domains", "created_at", "expires_at", "lifetime",
        "revoked", "revoked_at",
    }
    # env_key_names are NAMES only; there is no value-bearing field
    assert "GITHUB_TOKEN" in g["env_key_names"]
