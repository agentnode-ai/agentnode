"""Credentialed toolpacks — Slice B: consent + sealed egress + name-only
passthrough. No test ever handles a secret value beyond asserting that a
dummy value does NOT appear in the spec."""

from types import SimpleNamespace

import pytest

from agentnode_sdk.runtimes.mcp_consent import consent_key
from agentnode_sdk.runtimes.toolpack_credentials import (
    CredentialedToolpackRefused,
    build_identity_from_toolpack_entry,
    prepare_credentialed_run,
)


def _cred_entry(**overrides):
    e = {
        "version": "1.0.0",
        "artifact_hash": "sha256:abc123",
        "env_requirements": [{"name": "NEEDED_KEY", "required": True}],
        "permissions": {
            "network_level": "restricted",
            "allowed_domains": ["api.example.com"],
        },
    }
    e.update(overrides)
    return e


@pytest.fixture(autouse=True)
def _isolate_grant_store(monkeypatch):
    """Never touch the real grant store: no stored grants, persisting fails loudly."""
    from agentnode_sdk.runtimes import mcp_consent_store as store

    monkeypatch.setattr(store, "find_valid", lambda ck, now=None: None)

    def _no_persist(**kw):
        raise AssertionError("grant must not be persisted in these tests")

    monkeypatch.setattr(store, "add", _no_persist)


# ---------------------------------------------------------------------------
# prepare_credentialed_run — fail-closed gate
# ---------------------------------------------------------------------------


def test_refuses_without_allowed_domains():
    entry = _cred_entry(
        permissions={"network_level": "restricted", "allowed_domains": []}
    )
    with pytest.raises(CredentialedToolpackRefused) as exc:
        prepare_credentialed_run("cred-pack", entry)
    assert exc.value.reason == "missing_or_invalid_allowed_domains"


def test_refuses_non_interactive_without_grant():
    with pytest.raises(CredentialedToolpackRefused) as exc:
        prepare_credentialed_run("cred-pack", _cred_entry(), consent_callback=None)
    assert exc.value.reason == "no_valid_grant_non_interactive"


def test_refuses_when_callback_rejects():
    with pytest.raises(CredentialedToolpackRefused) as exc:
        prepare_credentialed_run(
            "cred-pack", _cred_entry(), consent_callback=lambda identity: False
        )
    assert exc.value.reason == "consent_rejected"


def test_ephemeral_consent_allows_and_filters_passthrough(monkeypatch):
    monkeypatch.setenv("NEEDED_KEY", "value")
    monkeypatch.delenv("OPT_KEY", raising=False)
    entry = _cred_entry(
        env_requirements=[
            {"name": "NEEDED_KEY", "required": True},
            {"name": "OPT_KEY", "required": False},
        ]
    )
    sealed, passthrough = prepare_credentialed_run(
        "cred-pack", entry, consent_callback=lambda identity: (True, "this_run")
    )
    assert sealed == ["api.example.com"]
    # Only present names pass through; absent optional keys are dropped
    assert passthrough == ["NEEDED_KEY"]


def test_consent_binds_to_artifact_hash():
    a = build_identity_from_toolpack_entry("cred-pack", _cred_entry())
    b = build_identity_from_toolpack_entry(
        "cred-pack", _cred_entry(artifact_hash="sha256:OTHER")
    )
    assert consent_key(a) != consent_key(b)


def test_consent_binds_to_domains_and_keys():
    a = build_identity_from_toolpack_entry("cred-pack", _cred_entry())
    b = build_identity_from_toolpack_entry(
        "cred-pack",
        _cred_entry(
            permissions={
                "network_level": "restricted",
                "allowed_domains": ["evil.example.com"],
            }
        ),
    )
    c = build_identity_from_toolpack_entry(
        "cred-pack",
        _cred_entry(env_requirements=[{"name": "OTHER_KEY", "required": True}]),
    )
    assert len({consent_key(a), consent_key(b), consent_key(c)}) == 3


# ---------------------------------------------------------------------------
# _run_container integration — name-only passthrough on an enforced egress
# network; refusals never start a proxy or a container
# ---------------------------------------------------------------------------


class _FakeBackend:
    def __init__(self):
        self.spec = None
        self.build_spec_called = False

    def check_available(self):
        return SimpleNamespace(available=True, reason=None, backend="docker")

    def build_process_spec(self, command, **kw):
        self.build_spec_called = True
        from agentnode_sdk.sandbox.types import ProcessSpec

        return ProcessSpec(command=list(command), **kw)

    def run_process(self, spec, input_text=None, timeout=120.0):
        self.spec = spec
        return (0, '{"ok": true, "result": {"n": 1}}', "")


@pytest.fixture()
def container_env(monkeypatch):
    """Fake backend + volume inspect + egress proxy for _run_container tests."""
    from agentnode_sdk.runtimes import python_runner as pr
    from agentnode_sdk.sandbox import egress as egress_mod
    from agentnode_sdk.sandbox import policy as sandbox_policy

    backend = _FakeBackend()
    sandbox_policy.set_default_backend(backend)

    monkeypatch.setattr(
        pr.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=b"", stderr=b""),
    )

    proxy = {"started": 0, "stopped": 0}
    fake_handle = SimpleNamespace(spec=SimpleNamespace(proxy_url="http://proxy:3128"))

    def _start(domains, **kw):
        proxy["started"] += 1
        proxy["domains"] = list(domains)
        return fake_handle

    def _stop(handle):
        proxy["stopped"] += 1

    monkeypatch.setattr(egress_mod, "start_egress_proxy", _start)
    monkeypatch.setattr(egress_mod, "stop_egress_proxy", _stop)

    yield backend, proxy, fake_handle
    sandbox_policy.set_default_backend(None)


def _sandboxed_entry(slug, **overrides):
    from agentnode_sdk.sandbox import sandbox_volume_name

    e = _cred_entry(**overrides)
    e["trust_level"] = "community"
    e["runtime"] = "python"
    e["entrypoint"] = "cred_pack.tool"
    e["sandboxed"] = True
    e["sandbox_volume"] = sandbox_volume_name(slug, e["version"], e["artifact_hash"])
    return e


def test_container_credentialed_happy_path(monkeypatch, container_env):
    backend, proxy, fake_handle = container_env
    from agentnode_sdk.runtimes.python_runner import _run_container

    monkeypatch.setenv("NEEDED_KEY", "secret-value")
    entry = _sandboxed_entry("cred-pack")

    result, error, timed_out = _run_container(
        "cred-pack",
        None,
        {"x": 1},
        30.0,
        entry,
        None,
        consent_callback=lambda identity: (True, "this_run"),
    )

    assert error is None and timed_out is False
    assert result == {"n": 1}
    spec = backend.spec
    assert spec.network == "egress"
    assert spec.egress is fake_handle.spec
    assert spec.env_passthrough == ["NEEDED_KEY"]
    # The VALUE never lands in the spec: names only
    assert "NEEDED_KEY" not in spec.env
    assert "secret-value" not in str(spec.env)
    assert spec.env == {"PYTHONPATH": "/pack"}
    assert all(m.read_only for m in spec.mounts)
    assert proxy == {"started": 1, "stopped": 1, "domains": ["api.example.com"]}
    assert backend.build_spec_called is False  # credentialed path builds its own spec


def test_container_refusal_starts_nothing(monkeypatch, container_env):
    backend, proxy, _ = container_env
    from agentnode_sdk.runtimes.python_runner import _run_container

    monkeypatch.setenv("NEEDED_KEY", "secret-value")
    entry = _sandboxed_entry(
        "cred-pack",
        permissions={"network_level": "restricted", "allowed_domains": []},
    )

    result, error, timed_out = _run_container(
        "cred-pack",
        None,
        {},
        30.0,
        entry,
        None,
        consent_callback=lambda identity: (True, "this_run"),
    )

    assert result is None
    assert "allowed_domains" in (error or "")
    assert proxy["started"] == 0
    assert backend.spec is None  # no container ran


def test_container_non_credentialed_path_unchanged(container_env):
    backend, proxy, _ = container_env
    from agentnode_sdk.runtimes.python_runner import _run_container

    entry = _sandboxed_entry("plain-pack", env_requirements=[])

    result, error, timed_out = _run_container("plain-pack", None, {}, 30.0, entry, None)

    assert error is None
    assert result == {"n": 1}
    assert backend.build_spec_called is True
    assert backend.spec.network in ("default", "none")
    assert backend.spec.env_passthrough == []
    assert proxy["started"] == 0
