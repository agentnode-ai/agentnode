"""Stage 4A: MCP pre-install into a sealed volume (install-side, INERT).

No real registry build: a real ContainerBackend yields the genuine hardened argv,
but its run_process is replaced with a recorder that returns canned HASH:/BINS: marker
output, and subprocess.run (volume rm) is mocked. The run path is NOT exercised here —
Stage 4A only writes lockfile fields; nothing consumes them yet.
"""
from __future__ import annotations

import json
from unittest import mock

import pytest

from agentnode_sdk import installer
from agentnode_sdk.installer import validate_mcp_install
from agentnode_sdk.sandbox import sandbox_volume_name, set_default_backend  # noqa: F401
from agentnode_sdk.sandbox.backend import SandboxBackend
from agentnode_sdk.sandbox.container_backend import (
    ContainerBackend,
    mcp_sandbox_volume_name,
)
from agentnode_sdk.sandbox.types import SandboxAvailability

MCP_CMD = ["npx", "-y", "@scope/some-mcp@1.2.3"]


# --------------------------------------------------------------------------
# pure validator
# --------------------------------------------------------------------------

def test_validate_npm_ok():
    assert validate_mcp_install({"manager": "npm", "package": "@scope/pkg", "version": "1.2.3"}) == \
        ("npm", "@scope/pkg", "1.2.3")


def test_validate_pypi_ok_and_normalized():
    assert validate_mcp_install({"manager": "PyPI", "package": "Some_Package", "version": "1.2.3"}) == \
        ("pypi", "some-package", "1.2.3")


def test_validate_npm_prerelease_ok():
    assert validate_mcp_install({"manager": "npm", "package": "pkg", "version": "1.2.3-rc.1"})[2] == "1.2.3-rc.1"


@pytest.mark.parametrize("version", [
    "latest", "*", "x", "1.x", "1.2.*", "^1.0.0", "~1.2", ">=1.0", "<=2.0", "1 - 2",
    "1.0 || 2.0", "next", "beta", "git+https://e/x.git", "https://e/x.tgz", "file:./x",
    "workspace:*", "github:o/r", "", "  ",
])
def test_validate_rejects_unpinned_npm(version):
    with pytest.raises(ValueError):
        validate_mcp_install({"manager": "npm", "package": "pkg", "version": version})


@pytest.mark.parametrize("version", ["==1.2.3", ">=1.0", "1.*", "latest", "1.2.3 @ https://x", "*"])
def test_validate_rejects_unpinned_pypi(version):
    with pytest.raises(ValueError):
        validate_mcp_install({"manager": "pypi", "package": "pkg", "version": version})


@pytest.mark.parametrize("desc", [
    {"manager": "cargo", "package": "p", "version": "1.0.0"},   # bad manager
    {"manager": "npm", "package": "Bad Name", "version": "1.0.0"},  # space in name
    {"manager": "npm", "package": "pkg@1.0.0", "version": "1.0.0"},  # version in name
    {"manager": "npm", "package": "pkg", "version": "1.0.0", "extra": 1},  # extra key
    {"manager": "npm", "package": "pkg"},  # missing version
    {"manager": "pypi", "package": "pkg[extra]", "version": "1.0.0"},  # extras
    "not-a-dict",
])
def test_validate_rejects_bad_descriptor(desc):
    with pytest.raises(ValueError):
        validate_mcp_install(desc)


# --------------------------------------------------------------------------
# deterministic volume name
# --------------------------------------------------------------------------

def test_volume_name_deterministic_and_descriptor_bound():
    a = mcp_sandbox_volume_name("s", "1.0", "npm", "@scope/pkg", "1.2.3")
    assert a == mcp_sandbox_volume_name("s", "1.0", "npm", "@scope/pkg", "1.2.3")
    assert a.startswith("agentnode-mcp-")
    # any differing DESCRIPTOR input -> different volume (descriptor-bound; no
    # cross-descriptor reuse). Built-tree CONTENT is bound separately via
    # mcp_preinstall.artifact_hash; the run-time content<->hash check is Stage 4B.
    assert a != mcp_sandbox_volume_name("s", "1.0", "npm", "@scope/pkg", "1.2.4")
    assert a != mcp_sandbox_volume_name("s", "1.0", "pypi", "@scope/pkg", "1.2.3")
    assert a != mcp_sandbox_volume_name("s", "1.0", "npm", "@scope/other", "1.2.3")
    assert a != mcp_sandbox_volume_name("s", "2.0", "npm", "@scope/pkg", "1.2.3")


# --------------------------------------------------------------------------
# install-side build + seal (recording backend, no daemon)
# --------------------------------------------------------------------------

class _Recorder:
    def __init__(self, backend):
        self.backend = backend
        self.calls = []
        self.response = (0, "HASH:deadbeefcafe\nBINS:some-mcp\n", "")

    def __call__(self, spec, input_text=None, timeout=120.0):
        self.calls.append(self.backend.wrap_command(spec))
        return self.response

    @property
    def last_argv(self):
        return self.calls[-1]


def _available_backend(monkeypatch):
    be = ContainerBackend(runtime="docker")
    monkeypatch.setattr(be, "check_available", lambda: SandboxAvailability(
        available=True, backend="docker", reason="", daemon_ok=True, image_available=True))
    rec = _Recorder(be)
    be.run_process = rec  # type: ignore[method-assign]
    set_default_backend(be)
    return be, rec


@pytest.fixture(autouse=True)
def _reset_backend(monkeypatch):
    # no real docker volume rm
    monkeypatch.setattr(installer.subprocess, "run",
                        lambda *a, **k: mock.Mock(returncode=0, stdout=b"", stderr=b""))
    yield
    set_default_backend(None)


def _install_mcp(monkeypatch, tmp_path, **over):
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(tmp_path / "agentnode.lock"))
    kw = dict(
        slug="some-mcp", version="1.0", artifact_url=None, runtime="mcp",
        package_type="connector", mcp_command=MCP_CMD, trust_level="verified",
    )
    kw.update(over)
    return installer.install_package(**kw)


def _entry(tmp_path):
    lock = json.loads((tmp_path / "agentnode.lock").read_text())
    return lock["packages"]["some-mcp"]


def test_valid_npm_descriptor_builds_and_seals(monkeypatch, tmp_path):
    _, rec = _available_backend(monkeypatch)
    _install_mcp(monkeypatch, tmp_path,
                 mcp_install={"manager": "npm", "package": "@scope/some-mcp", "version": "1.2.3"})
    argv = rec.last_argv
    assert argv[0] == "docker"
    vol = mcp_sandbox_volume_name("some-mcp", "1.0", "npm", "@scope/some-mcp", "1.2.3")
    assert f"{vol}:/install:rw" in argv
    assert "--network" not in argv          # build keeps network (registry fetch)
    assert argv[-3] == "sh" and argv[-2] == "-c"
    assert "npm install -g --prefix /install '@scope/some-mcp@1.2.3'" in argv[-1]

    e = _entry(tmp_path)
    assert e["mcp_preinstalled"] is True
    assert e["mcp_sandbox_volume"] == vol
    assert e["mcp_preinstall"] == {
        "manager": "npm", "package": "@scope/some-mcp", "version": "1.2.3",
        "artifact_hash": "sha256:deadbeefcafe",
    }
    assert e["mcp_preinstall_command"] == ["node", "/install/bin/some-mcp"]
    # PFLICHT: existing mcp_command is UNTOUCHED
    assert e["mcp_command"] == MCP_CMD


def test_build_script_uses_robust_python_hash_not_find_printf(monkeypatch, tmp_path):
    """The tree-hash must be computed by our deterministic Python hasher (unambiguous,
    length-prefixed JSON per entry) — NOT a line-based `find -printf` text manifest that
    breaks on filenames containing tabs/newlines."""
    _, rec = _available_backend(monkeypatch)
    _install_mcp(monkeypatch, tmp_path,
                 mcp_install={"manager": "npm", "package": "pkg", "version": "1.0.0"})
    script = rec.last_argv[-1]
    # old, fragile text-manifest logic is gone
    assert "-printf" not in script
    assert "find ." not in script
    # robust python/json hashing is used inside the build container
    assert "python3 -c" in script
    for token in ("os.walk", "os.lstat", "os.readlink", "json.dumps",
                  "to_bytes", "hashlib.sha256"):
        assert token in script, token


def test_valid_pypi_descriptor_builds(monkeypatch, tmp_path):
    _, rec = _available_backend(monkeypatch)
    rec.response = (0, "HASH:abc123\nBINS:some-mcp\n", "")
    _install_mcp(monkeypatch, tmp_path,
                 mcp_install={"manager": "pypi", "package": "some-mcp", "version": "2.0.0"})
    assert "uv pip install --target /install 'some-mcp==2.0.0'" in rec.last_argv[-1]
    e = _entry(tmp_path)
    assert e["mcp_preinstall_command"] == ["python", "/install/bin/some-mcp"]
    assert e["mcp_command"] == MCP_CMD


def test_missing_descriptor_is_metadata_only(monkeypatch, tmp_path):
    _, rec = _available_backend(monkeypatch)
    _install_mcp(monkeypatch, tmp_path)  # no mcp_install
    assert rec.calls == []               # no build
    e = _entry(tmp_path)
    for f in ("mcp_preinstalled", "mcp_preinstall", "mcp_sandbox_volume", "mcp_preinstall_command"):
        assert f not in e                # no preinstall fields
    assert e["mcp_command"] == MCP_CMD


def test_invalid_descriptor_raises_before_build(monkeypatch, tmp_path):
    _, rec = _available_backend(monkeypatch)
    with pytest.raises(ValueError):
        _install_mcp(monkeypatch, tmp_path,
                     mcp_install={"manager": "npm", "package": "pkg", "version": "^1.0.0"})
    assert rec.calls == []               # nothing built
    assert not (tmp_path / "agentnode.lock").exists()  # nothing written


def test_backend_unavailable_failclosed(monkeypatch, tmp_path):
    class _Un(SandboxBackend):
        def check_available(self):
            return SandboxAvailability(available=False, backend="none", reason="no runtime")
        def wrap_command(self, spec):  # pragma: no cover
            raise AssertionError("must not build without a sandbox")
    set_default_backend(_Un())
    from agentnode_sdk.sandbox.types import SandboxRequiredError
    with pytest.raises(SandboxRequiredError):
        _install_mcp(monkeypatch, tmp_path,
                     mcp_install={"manager": "npm", "package": "pkg", "version": "1.0.0"})
    assert not (tmp_path / "agentnode.lock").exists()


def test_mcp_command_is_never_parsed(monkeypatch, tmp_path):
    """No descriptor + a version-bearing mcp_command must NOT trigger a build, and the
    package@version in the command must never reach the build helper."""
    _, rec = _available_backend(monkeypatch)
    called = []
    monkeypatch.setattr(installer, "_container_build_mcp_volume",
                        lambda *a, **k: called.append((a, k)) or ("v", "sha256:x", ["node", "x"]))
    _install_mcp(monkeypatch, tmp_path, mcp_command=["npx", "-y", "@scope/pkg@9.9.9"])
    assert called == []                  # build helper never invoked from a command
    e = _entry(tmp_path)
    assert "mcp_preinstall" not in e


def test_build_container_has_no_host_env_or_secrets(monkeypatch, tmp_path):
    _, rec = _available_backend(monkeypatch)
    monkeypatch.setenv("HOME", "/home/realuser")
    monkeypatch.setenv("NPM_TOKEN", "sekret-token")
    _install_mcp(monkeypatch, tmp_path,
                 mcp_install={"manager": "npm", "package": "pkg", "version": "1.0.0"})
    joined = " ".join(rec.last_argv)
    assert "HOME=/sandbox-home" in rec.last_argv     # clean container HOME
    assert "/home/realuser" not in joined
    assert "sekret-token" not in joined and "NPM_TOKEN" not in joined
    assert ".npmrc" not in joined and ".pypirc" not in joined
    # only the volume is mounted (no /src, no host paths)
    mounts = [rec.last_argv[i + 1] for i, a in enumerate(rec.last_argv) if a == "-v"]
    assert all(m.endswith(":/install:rw") for m in mounts) and len(mounts) == 1


def test_signature_gate_runs_before_build(monkeypatch, tmp_path):
    """Blocker 1: the publisher-signature gate must run BEFORE the preinstall build —
    an invalid/blocked signature ⇒ no registry fetch / build / volume / lockfile."""
    _, rec = _available_backend(monkeypatch)
    built = []
    monkeypatch.setattr(installer, "_container_build_mcp_volume",
                        lambda *a, **k: built.append(a) or ("v", "sha256:x", ["node", "x"]))
    monkeypatch.setattr(installer, "_verify_publisher_signature",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("signature invalid")))
    with pytest.raises(RuntimeError, match="signature"):
        _install_mcp(monkeypatch, tmp_path,
                     mcp_install={"manager": "npm", "package": "pkg", "version": "1.0.0"})
    assert built == []                                  # build never ran (gate first)
    assert not (tmp_path / "agentnode.lock").exists()   # nothing written


def test_build_no_bins_failclosed(monkeypatch, tmp_path):
    _, rec = _available_backend(monkeypatch)
    rec.response = (0, "HASH:abc\nBINS:\n", "")    # nothing installed a bin
    with pytest.raises(RuntimeError, match="no entrypoint"):
        _install_mcp(monkeypatch, tmp_path,
                     mcp_install={"manager": "npm", "package": "pkg", "version": "1.0.0"})


# --------------------------------------------------------------------------
# sealing backward-compat
# --------------------------------------------------------------------------

def test_seal_backward_compatible_without_new_fields():
    from agentnode_sdk import lock_integrity
    entry = {"version": "1.0", "package_type": "connector", "runtime": "mcp",
             "mcp_command": ["npx", "x"]}
    sealed = lock_integrity.seal_entry(dict(entry))
    res = lock_integrity.verify_entry("some-mcp", sealed)
    assert res.status == "verified"


def test_seal_detects_tampered_volume():
    from agentnode_sdk import lock_integrity
    entry = lock_integrity.seal_entry({
        "version": "1.0", "runtime": "mcp", "mcp_command": ["npx", "x"],
        "mcp_preinstalled": True, "mcp_sandbox_volume": "agentnode-mcp-real",
        "mcp_preinstall": {"manager": "npm", "package": "p", "version": "1.0.0",
                           "artifact_hash": "sha256:abc"},
    })
    entry["mcp_sandbox_volume"] = "agentnode-mcp-spoofed"
    res = lock_integrity.verify_entry("some-mcp", entry)
    assert res.status == "mismatch"
