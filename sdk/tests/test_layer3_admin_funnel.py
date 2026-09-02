"""A1-E-Lock Layer 3 — administrative funnel behavioral tests (Blocker 6).

The three explicit-install surfaces — direct ``AgentNodeClient.install``, the CLI
``agentnode install``, and ``doctor --fix`` — must all reach the SINGLE environment
write-lock chokepoint in ``install_package``. For a HOST install each performs exactly one
env-write-lock enter + one exit and no inner lock; for a container install, zero.

Network resolution is stubbed with respx; the real installer chokepoint IS reached (pip and
artifact IO are mocked, but ``_host_env_write_lock`` / ``env_write_lock`` run for real).
"""
from __future__ import annotations

import contextlib

import httpx
import pytest
import respx

from agentnode_sdk import _env_rwlock as rw
from agentnode_sdk import installer
from agentnode_sdk.client import AgentNodeClient

BASE = "https://api.agentnode.net"


def _mock_endpoints(slug, *, package_type="toolpack", trust="trusted"):
    info = {
        "slug": slug, "version": "1.0", "package_type": package_type,
        "install_mode": "package", "hosting_type": "agentnode_hosted",
        "runtime": "python", "entrypoint": "pk.tool",
        "artifact": {"url": "https://x/p.tgz", "hash_sha256": "abc123def456"},
        "capabilities": [], "dependencies": [], "permissions": None,
    }
    respx.get(f"{BASE}/v1/packages/{slug}/install-info").mock(
        return_value=httpx.Response(200, json=info))
    respx.get(f"{BASE}/v1/packages/{slug}").mock(return_value=httpx.Response(200, json={
        "slug": slug, "name": slug, "package_type": package_type, "summary": "t",
        "description": None, "download_count": 0, "is_deprecated": False,
        "latest_version": None,
        "publisher": {"slug": "pub", "display_name": "P", "trust_level": trust},
        "blocks": {},
    }))
    respx.post(f"{BASE}/v1/packages/{slug}/install").mock(
        return_value=httpx.Response(200, json={"ok": True}))


def _mock_installer_io(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "cfg"))
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(tmp_path / "agentnode.lock"))
    pkg = tmp_path / "pk"
    pkg.mkdir()
    (pkg / "setup.py").write_text("x")
    monkeypatch.setattr(installer, "download_artifact", lambda *a, **k: None)
    monkeypatch.setattr(installer, "verify_hash", lambda *a, **k: "abc123def456")
    monkeypatch.setattr(installer, "extract_archive", lambda *a, **k: pkg)
    monkeypatch.setattr(installer, "pip_install", lambda *a, **k: None)


def _count_locks(monkeypatch):
    real = rw.env_write_lock
    ev = {"enter": 0, "exit": 0}

    @contextlib.contextmanager
    def _wrapper(env_id, timeout=rw.DEFAULT_ACQUIRE_TIMEOUT):
        ev["enter"] += 1
        with real(env_id, timeout=timeout) as g:
            yield g
        ev["exit"] += 1
    monkeypatch.setattr(rw, "env_write_lock", _wrapper)
    return ev


@respx.mock
@pytest.mark.usefixtures("legacy_default_policy")
def test_client_install_host_toolpack_exactly_one_lock(tmp_path, monkeypatch, bypass_policy):
    _mock_endpoints("tp", trust="trusted")
    _mock_installer_io(monkeypatch, tmp_path)
    ev = _count_locks(monkeypatch)
    with AgentNodeClient() as client:
        res = client.install("tp")
    assert res.installed is True
    assert ev == {"enter": 1, "exit": 1}


@respx.mock
def test_client_install_container_zero_locks(tmp_path, monkeypatch, bypass_policy):
    _mock_endpoints("cp", trust="verified")          # verified → container route
    _mock_installer_io(monkeypatch, tmp_path)
    monkeypatch.setattr(installer, "_container_build_into_volume", lambda *a, **k: "vol")
    ev = _count_locks(monkeypatch)
    with AgentNodeClient() as client:
        res = client.install("cp")
    assert res.installed is True
    assert ev == {"enter": 0, "exit": 0}             # container never touches the host env


@respx.mock
@pytest.mark.usefixtures("legacy_default_policy")
def test_cli_install_host_exactly_one_lock(tmp_path, monkeypatch, bypass_policy):
    from agentnode_sdk.cli import commands
    _mock_endpoints("tp", trust="trusted")
    _mock_installer_io(monkeypatch, tmp_path)
    ev = _count_locks(monkeypatch)
    rc = commands.cmd_install("tp", None, yes=True)
    assert rc == 0
    assert ev == {"enter": 1, "exit": 1}


@respx.mock
@pytest.mark.usefixtures("legacy_default_policy")
def test_doctor_fix_install_host_exactly_one_lock(tmp_path, monkeypatch, bypass_policy):
    # The exact call doctor --fix makes: AgentNodeClient().install(slug, require_*=...).
    _mock_endpoints("tp", trust="trusted")
    _mock_installer_io(monkeypatch, tmp_path)
    ev = _count_locks(monkeypatch)
    with AgentNodeClient() as client:
        res = client.install("tp", require_verified=True, require_trusted=True)
    assert res.installed is True
    assert ev == {"enter": 1, "exit": 1}


@respx.mock
@pytest.mark.usefixtures("legacy_default_policy")
def test_client_binds_start_snapshot_before_registry_resolution(tmp_path, monkeypatch,
                                                                bypass_policy):
    """The client captures the install-start snapshot at the TOP of install(), before the
    registry resolution. A competitor publishing the slug DURING resolution therefore makes
    the operation fail-closed (toolpack_preparation_stale) rather than overwriting it."""
    import httpx as _httpx
    from agentnode_sdk import installer
    from agentnode_sdk.lock_integrity import seal_entry

    _mock_installer_io(monkeypatch, tmp_path)
    lf = tmp_path / "agentnode.lock"
    installer.update_lockfile(
        "tp", seal_entry({"version": "0.9", "package_type": "toolpack",
                          "entrypoint": "pk.tool", "artifact_hash": "sha256:" + "b" * 64}),
        path=lf)   # E1 present when the client starts

    info = {
        "slug": "tp", "version": "1.0", "package_type": "toolpack",
        "install_mode": "package", "hosting_type": "agentnode_hosted", "runtime": "python",
        "entrypoint": "pk.tool",
        "artifact": {"url": "https://x/p.tgz", "hash_sha256": "abc123def456"},
        "capabilities": [], "dependencies": [], "permissions": None,
    }
    published = {}

    def _resolve_side_effect(request):
        # B completes a newer install of the same slug DURING our registry resolution.
        installer.update_lockfile(
            "tp", seal_entry({"version": "2.0", "package_type": "toolpack",
                              "entrypoint": "pk.tool", "artifact_hash": "sha256:" + "c" * 64}),
            path=lf)
        published["b"] = installer.read_lockfile(lf)["packages"]["tp"]
        return _httpx.Response(200, json=info)
    respx.get(f"{BASE}/v1/packages/tp/install-info").mock(side_effect=_resolve_side_effect)
    respx.get(f"{BASE}/v1/packages/tp").mock(return_value=_httpx.Response(200, json={
        "slug": "tp", "name": "tp", "package_type": "toolpack", "summary": "t",
        "description": None, "download_count": 0, "is_deprecated": False,
        "latest_version": None,
        "publisher": {"slug": "pub", "display_name": "P", "trust_level": "trusted"},
        "blocks": {}}))
    respx.post(f"{BASE}/v1/packages/tp/install").mock(
        return_value=_httpx.Response(200, json={"ok": True}))

    with AgentNodeClient() as client:
        with pytest.raises(installer.ToolpackPreparationStale):
            client.install("tp")
    assert installer.read_lockfile(lf)["packages"]["tp"] == published["b"]   # E2 untouched
