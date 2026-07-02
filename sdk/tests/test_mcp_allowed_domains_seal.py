"""Stage 5: install-side sealing of the publisher-declared egress allowlist.

Mirrors the 4A no-daemon harness (recording backend; subprocess.run mocked). Verifies
mcp_allowed_domains is canonicalized + sealed, integrity-bound, backward-compatible, and
that mcp_preinstall stays bound to the mcp_install descriptor. The run path is NOT
exercised (Stage 5 seals only; nothing consumes mcp_allowed_domains at run time)."""
from __future__ import annotations

import json
from unittest import mock

import pytest

from agentnode_sdk import installer, lock_integrity
from agentnode_sdk.sandbox import set_default_backend
from agentnode_sdk.sandbox.container_backend import ContainerBackend, mcp_sandbox_volume_name
from agentnode_sdk.sandbox.domain_policy import DomainPolicyError
from agentnode_sdk.sandbox.types import SandboxAvailability

MCP_CMD = ["npx", "-y", "@scope/some-mcp@1.2.3"]
INSTALL = {"manager": "npm", "package": "@scope/some-mcp", "version": "1.2.3"}


class _Recorder:
    def __init__(self, backend):
        self.backend = backend
        self.calls = []
        self.response = (0, "HASH:deadbeefcafe\nBINS:some-mcp\n", "")

    def __call__(self, spec, input_text=None, timeout=120.0):
        self.calls.append(self.backend.wrap_command(spec))
        return self.response


def _available_backend(monkeypatch):
    be = ContainerBackend(runtime="docker")
    monkeypatch.setattr(be, "check_available", lambda: SandboxAvailability(
        available=True, backend="docker", reason="", daemon_ok=True, image_available=True))
    be.run_process = _Recorder(be)  # type: ignore[method-assign]
    set_default_backend(be)
    return be


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(installer.subprocess, "run",
                        lambda *a, **k: mock.Mock(returncode=0, stdout=b"", stderr=b""))
    yield
    set_default_backend(None)


def _install(monkeypatch, tmp_path, **over):
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(tmp_path / "agentnode.lock"))
    kw = dict(
        slug="some-mcp", version="1.0", artifact_url=None, runtime="mcp",
        package_type="connector", mcp_command=MCP_CMD, trust_level="verified",
        mcp_install=INSTALL,
    )
    kw.update(over)
    return installer.install_package(**kw)


def _entry(tmp_path):
    return json.loads((tmp_path / "agentnode.lock").read_text())["packages"]["some-mcp"]


def test_allowed_domains_sealed_canonical(monkeypatch, tmp_path):
    _available_backend(monkeypatch)
    _install(monkeypatch, tmp_path,
             mcp_allowed_domains=["API.GITHUB.COM", "api.github.com", "files.pythonhosted.org"])
    e = _entry(tmp_path)
    # canonicalized: lowercased, de-duped, sorted
    assert e["mcp_allowed_domains"] == ["api.github.com", "files.pythonhosted.org"]
    # descriptor still bound to mcp_install
    assert e["mcp_preinstall"]["manager"] == "npm"
    assert e["mcp_preinstall"]["package"] == "@scope/some-mcp"
    assert e["mcp_preinstall"]["version"] == "1.2.3"
    assert e["mcp_sandbox_volume"] == mcp_sandbox_volume_name(
        "some-mcp", "1.0", "npm", "@scope/some-mcp", "1.2.3")


def test_invalid_allowed_domains_refused(monkeypatch, tmp_path):
    _available_backend(monkeypatch)
    with pytest.raises(DomainPolicyError):
        _install(monkeypatch, tmp_path, mcp_allowed_domains=["http://evil.com"])


def test_no_allowed_domains_field_when_absent(monkeypatch, tmp_path):
    _available_backend(monkeypatch)
    _install(monkeypatch, tmp_path)  # no mcp_allowed_domains
    e = _entry(tmp_path)
    assert "mcp_allowed_domains" not in e
    # still preinstalled + sealed
    assert e["mcp_preinstalled"] is True


def test_sealed_entry_with_allowed_domains_verifies(monkeypatch, tmp_path):
    _available_backend(monkeypatch)
    _install(monkeypatch, tmp_path, mcp_allowed_domains=["api.github.com"])
    e = _entry(tmp_path)
    assert lock_integrity.verify_entry("some-mcp", e).status == "verified"


def test_tampered_allowed_domains_breaks_integrity():
    entry = lock_integrity.seal_entry({
        "version": "1.0", "runtime": "mcp", "mcp_command": ["npx", "x"],
        "mcp_preinstalled": True,
        "mcp_sandbox_volume": "agentnode-mcp-real",
        "mcp_preinstall": {"manager": "npm", "package": "p", "version": "1.0.0",
                           "artifact_hash": "sha256:" + "a" * 64},
        "mcp_allowed_domains": ["api.github.com"],
    })
    entry["mcp_allowed_domains"] = ["evil.example.com"]
    assert lock_integrity.verify_entry("some-mcp", entry).status == "mismatch"


def test_backward_compatible_without_field():
    # an entry without mcp_allowed_domains hashes identically to a pre-Stage-5 entry
    base = {"version": "1.0", "package_type": "connector", "runtime": "mcp",
            "mcp_command": ["npx", "x"]}
    sealed = lock_integrity.seal_entry(dict(base))
    assert lock_integrity.verify_entry("some-mcp", sealed).status == "verified"
