"""Stage 3B-1: consent resolver + INERTNESS. Pure of docker/egress/secret.

The resolver decides authorization; in 3B-1 the live runtime STILL refuses credentialed
execution (no prompt, no container, no egress, no secret read). Store isolated via
AGENTNODE_CONFIG.
"""
from __future__ import annotations

import os

import pytest

from agentnode_sdk.runtimes import mcp_consent_store as store
from agentnode_sdk.runtimes.mcp_consent import (
    CredentialedMcpRefused,
    build_consent_identity,
    build_identity_from_entry,
    consent_key,
    resolve_consent,
)


@pytest.fixture(autouse=True)
def _isolated_store(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path))
    if os.name == "posix":
        os.chmod(tmp_path, 0o700)
    yield


def _ident(**over):
    kw = dict(slug="gh-mcp", version="1.2.3", artifact_hash="sha256:" + "a" * 64,
              env_key_names=["GITHUB_TOKEN"], allowed_domains=["api.github.com"])
    kw.update(over)
    return build_consent_identity(**kw)


def _persist(ident, lifetime=store.LIFETIME_90D, now=1000.0):
    return store.add(
        consent_key=consent_key(ident), slug=ident.slug, version=ident.version,
        artifact_hash=ident.artifact_hash, env_key_names=list(ident.env_key_names),
        allowed_domains=list(ident.allowed_domains), lifetime=lifetime, now=now,
    )


# ---- resolver decisions ----

def test_non_tty_no_grant_refused():
    d = resolve_consent(_ident(), callback=None)
    assert d.authorized is False and d.grant is None


def test_non_tty_valid_grant_authorized():
    ident = _ident()
    _persist(ident, now=1000.0)
    d = resolve_consent(ident, callback=None, now=1000.0 + 86400)
    assert d.authorized is True and d.grant is not None


def test_tty_approve_creates_grant():
    ident = _ident()
    d = resolve_consent(ident, callback=lambda i: (True, store.LIFETIME_30D), now=1000.0)
    assert d.authorized is True
    assert store.find_valid(consent_key(ident), now=1000.0) is not None


def test_tty_reject_refused():
    ident = _ident()
    d = resolve_consent(ident, callback=lambda i: (False, store.LIFETIME_90D))
    assert d.authorized is False
    assert store.find_valid(consent_key(ident)) is None


def test_ephemeral_persists_nothing():
    ident = _ident()
    d = resolve_consent(ident, callback=lambda i: (True, store.LIFETIME_THIS_RUN), now=1000.0)
    assert d.authorized is True and d.grant is None
    assert store.load() == []


def test_default_lifetime_is_90d_never_forever():
    ident = _ident()
    resolve_consent(ident, callback=lambda i: True, now=1000.0)  # bare-True -> default
    g = store.find_valid(consent_key(ident), now=1000.0)
    assert g["lifetime"] == store.LIFETIME_90D and g["expires_at"] is not None


def test_forever_only_when_explicit():
    ident = _ident()
    resolve_consent(ident, callback=lambda i: (True, store.LIFETIME_FOREVER), now=1000.0)
    g = store.find_valid(consent_key(ident), now=1000.0)
    assert g["lifetime"] == store.LIFETIME_FOREVER and g["expires_at"] is None


def test_expired_grant_refused_non_tty():
    ident = _ident()
    _persist(ident, lifetime=store.LIFETIME_7D, now=1000.0)
    d = resolve_consent(ident, callback=None, now=1000.0 + 8 * 86400)
    assert d.authorized is False


def test_revoked_grant_refused_non_tty():
    ident = _ident()
    _persist(ident, now=1000.0)
    store.revoke(ident.slug)
    d = resolve_consent(ident, callback=None, now=1000.0)
    assert d.authorized is False


@pytest.mark.parametrize("field,val", [
    ("artifact_hash", "sha256:" + "b" * 64),
    ("allowed_domains", ["evil.example.com"]),
    ("env_key_names", ["OTHER_TOKEN"]),
    ("version", "9.9.9"),
    ("slug", "other-mcp"),
])
def test_identity_change_invalidates_grant(field, val):
    base = _ident()
    _persist(base, now=1000.0)
    changed = _ident(**{field: val})  # different consent_key
    d = resolve_consent(changed, callback=None, now=1000.0)
    assert d.authorized is False


def test_build_identity_from_entry_binds_sealed_fields():
    entry = {
        "version": "2.0.0",
        "mcp_preinstall": {"artifact_hash": "sha256:" + "c" * 64},
        "mcp_env_keys": ["GITHUB_TOKEN"],
        "mcp_allowed_domains": ["api.github.com"],
    }
    ident = build_identity_from_entry("gh-mcp", entry)
    assert ident.slug == "gh-mcp"
    assert ident.version == "2.0.0"
    assert ident.artifact_hash == "sha256:" + "c" * 64
    assert ident.env_key_names == ("GITHUB_TOKEN",)
    assert ident.allowed_domains == ("api.github.com",)


# ---- INERTNESS: the live runtime still refuses credentialed MCPs (no prompt/container/egress) ----

def test_credentialed_mcp_still_runtime_refused(monkeypatch):
    from agentnode_sdk.runtimes.mcp_runner import MCPServerProcess
    from agentnode_sdk.sandbox import set_default_backend
    from agentnode_sdk.sandbox.container_backend import ContainerBackend
    from agentnode_sdk.sandbox.types import SandboxAvailability

    be = ContainerBackend(runtime="docker")
    monkeypatch.setattr(be, "check_available", lambda: SandboxAvailability(
        available=True, backend="docker", reason="", daemon_ok=True, image_available=True))
    launched = []
    be.run_process = lambda *a, **k: launched.append(("run_process", a))  # would-be container
    set_default_backend(be)

    consent_calls = []
    cb = lambda ident: consent_calls.append(ident) or True  # a callback that WOULD approve

    try:
        proc = MCPServerProcess(
            "gh-mcp", ["npx", "-y", "gh-mcp"], trust_level="verified",
            entry={"version": "1", "mcp_env_keys": ["GITHUB_TOKEN"],
                   "mcp_allowed_domains": ["api.github.com"]},
            confirmation_callback=cb,
        )
        with pytest.raises(CredentialedMcpRefused):
            proc.start(env_keys=["GITHUB_TOKEN"])
        # inert: no container launched, and the consent callback was NEVER invoked in 3B-1
        assert launched == []
        assert consent_calls == []
    finally:
        set_default_backend(None)


def test_egress_proxy_not_started_by_consent_layer(monkeypatch):
    # The consent/store path must never start an egress proxy (that is 3B-2).
    import agentnode_sdk.sandbox.egress as egress

    def _boom(*a, **k):
        raise AssertionError("start_egress_proxy must not be called in 3B-1")

    monkeypatch.setattr(egress, "start_egress_proxy", _boom)
    ident = _ident()
    # full resolver round-trip (persist + read back) — must touch no egress
    resolve_consent(ident, callback=lambda i: (True, store.LIFETIME_90D), now=1000.0)
    assert resolve_consent(ident, callback=None, now=1000.0).authorized is True
