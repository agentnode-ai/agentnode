"""A1-E-Lock Layer 3 — the install-start snapshot: baseline, host-trust policy, and lockfile
path are bound ONCE at the earliest start of an install operation (in the client, before
registry resolution) and threaded unchanged through every host-mutation phase.

Four time-separated concurrency bindings result:
  1. Start-snapshot   — no lost update during registry / version resolution;
  2. Preparation-CAS  — no lost update during download / extraction;
  3. Quarantine-CAS   — no race between the read-only preflight and the durable removal;
  4. Final compare-and-add — no overwrite of an entry published during pip.

This file covers (1) + the policy/path binding; (2)–(4) live in test_layer3_env_write_lock_core.
"""
from __future__ import annotations

import contextlib

import pytest

from agentnode_sdk import _env_rwlock as rw
from agentnode_sdk import installer
from agentnode_sdk.lock_integrity import seal_entry


def _mock_io(monkeypatch, tmp_path, pip_calls):
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "cfg"))
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(tmp_path / "agentnode.lock"))
    pkg = tmp_path / "pk"
    pkg.mkdir(exist_ok=True)
    (pkg / "setup.py").write_text("x")
    monkeypatch.setattr(installer, "download_artifact", lambda *a, **k: None)
    monkeypatch.setattr(installer, "verify_hash", lambda *a, **k: "abc123def456")
    monkeypatch.setattr(installer, "extract_archive", lambda *a, **k: pkg)
    monkeypatch.setattr(installer, "pip_install", lambda *a, **k: pip_calls.append(1))
    return tmp_path / "agentnode.lock"


def _tp_entry(version, artifact_hash):
    return {"version": version, "package_type": "toolpack", "entrypoint": "pk.tool",
            "artifact_hash": artifact_hash}


def _install_tp(**kw):
    return installer.install_package(
        slug="tp", version="1.0", artifact_url="https://x/p.tgz",
        artifact_hash="sha256:abc123def456", entrypoint="pk.tool", trust_level="trusted", **kw)


# ===========================================================================
# 1. Start-snapshot resolve window
# ===========================================================================

@pytest.mark.usefixtures("legacy_default_policy")
def test_start_snapshot_toolpack_existing_entry_replaced_after_snapshot(tmp_path, monkeypatch):
    """A binds its snapshot (baseline E1), then — during registry/version resolution — B
    publishes E2. install_package USES the bound snapshot baseline, so the preparation-CAS
    sees E2 ≠ E1 → toolpack_preparation_stale, E2 byte-identical, no pip."""
    pip = []
    lf = _mock_io(monkeypatch, tmp_path, pip)
    installer.update_lockfile("tp", seal_entry(_tp_entry("0.9", "sha256:" + "b" * 64)), path=lf)
    snap = installer.capture_install_start_snapshot("tp")            # binds baseline E1
    installer.update_lockfile("tp", seal_entry(_tp_entry("2.0", "sha256:" + "c" * 64)), path=lf)
    e2 = installer.read_lockfile(lf)["packages"]["tp"]              # B's newer entry
    with pytest.raises(installer.ToolpackPreparationStale) as e:
        _install_tp(start_snapshot=snap)
    assert e.value.code == "toolpack_preparation_stale"
    assert pip == []
    assert installer.read_lockfile(lf)["packages"]["tp"] == e2      # E2 untouched


@pytest.mark.usefixtures("legacy_default_policy")
def test_start_snapshot_toolpack_absent_then_competitor_publishes(tmp_path, monkeypatch):
    pip = []
    lf = _mock_io(monkeypatch, tmp_path, pip)
    snap = installer.capture_install_start_snapshot("tp")            # baseline ABSENT
    installer.update_lockfile("tp", seal_entry(_tp_entry("2.0", "sha256:" + "c" * 64)), path=lf)
    e2 = installer.read_lockfile(lf)["packages"]["tp"]
    with pytest.raises(installer.ToolpackPreparationStale):
        _install_tp(start_snapshot=snap)
    assert pip == []
    assert installer.read_lockfile(lf)["packages"]["tp"] == e2


@pytest.mark.usefixtures("legacy_default_policy")
def test_start_snapshot_agent_baseline_and_path_flow(tmp_path, monkeypatch):
    """The agent route receives the SNAPSHOT baseline + bound lockfile path (not a fresh read
    taken after a competitor published during resolution)."""
    pip = []
    lf = _mock_io(monkeypatch, tmp_path, pip)
    snap = installer.capture_install_start_snapshot("ag")           # baseline ABSENT
    installer.update_lockfile(
        "ag", seal_entry({"version": "2.0", "package_type": "agent", "entrypoint": "ag:run",
                          "artifact_hash": "sha256:" + "c" * 64,
                          "python_distribution": "ag-agent",
                          "python_distribution_version": "2.0"}), path=lf)
    seen = {}
    monkeypatch.setattr(installer, "_install_agent_host_transaction",
                        lambda slug, **kw: seen.update(kw, slug=slug))
    installer.install_package(
        slug="ag", version="1.0", artifact_url="https://x/p.tgz",
        artifact_hash="sha256:abc123def456", entrypoint="ag:run",
        trust_level="trusted", package_type="agent", start_snapshot=snap)
    assert seen["slug"] == "ag"
    assert seen["prepared_baseline"] == (True, None)                # PRE-competitor baseline
    assert seen["lockfile_path"] == snap.lockfile_path


# ===========================================================================
# 2. Host-trust policy bound once + re-checked under the lock
# ===========================================================================

def _count_locks(monkeypatch):
    real = rw.env_write_lock
    ev = []

    @contextlib.contextmanager
    def _w(env_id, timeout=rw.DEFAULT_ACQUIRE_TIMEOUT):
        ev.append(env_id)
        with real(env_id, timeout=timeout) as g:
            yield g
    monkeypatch.setattr(rw, "env_write_lock", _w)
    return ev


def test_policy_none_at_start_stays_container_even_if_loosened(tmp_path, monkeypatch):
    """Snapshot policy 'none' → container route; even if the policy loosens to 'default'
    mid-operation, the route stays container (bound) — no host mutation, no host lock."""
    pip = []
    lf = _mock_io(monkeypatch, tmp_path, pip)
    monkeypatch.setattr(installer, "_container_build_into_volume", lambda *a, **k: "vol")
    pol = {"v": "none"}
    monkeypatch.setattr("agentnode_sdk.config.host_trust_policy", lambda: pol["v"])
    snap = installer.capture_install_start_snapshot("tp")           # policy 'none'
    pol["v"] = "default"                                            # loosened mid-op
    ev = _count_locks(monkeypatch)
    res = _install_tp(start_snapshot=snap)
    assert res["installed"] is True
    assert ev == []                                                 # NO host env write-lock
    entry = installer.read_lockfile(lf)["packages"]["tp"]
    assert entry.get("sandboxed") is True
    assert entry["effective_host_trust_policy_at_install"] == "none"   # the BOUND policy


def test_policy_tightened_to_none_under_lock_refused(tmp_path, monkeypatch):
    """Snapshot policy 'default' → host route; the policy tightens to 'none' before the first
    host mutation under the lock → host_policy_changed_during_install, no mutation."""
    pip = []
    lf = _mock_io(monkeypatch, tmp_path, pip)
    pol = {"v": "default"}
    monkeypatch.setattr("agentnode_sdk.config.host_trust_policy", lambda: pol["v"])
    snap = installer.capture_install_start_snapshot("tp")           # policy 'default' → host
    pol["v"] = "none"                                              # tightened mid-op
    with pytest.raises(installer.HostPolicyChangedDuringInstall) as e:
        _install_tp(start_snapshot=snap)
    assert e.value.code == "host_policy_changed_during_install"
    assert pip == []
    assert "tp" not in installer.read_lockfile(lf).get("packages", {})


def test_effective_policy_matches_bound_snapshot(tmp_path, monkeypatch):
    pip = []
    lf = _mock_io(monkeypatch, tmp_path, pip)
    monkeypatch.setattr("agentnode_sdk.config.host_trust_policy", lambda: "default")
    snap = installer.capture_install_start_snapshot("tp")
    _install_tp(start_snapshot=snap)
    entry = installer.read_lockfile(lf)["packages"]["tp"]
    assert entry["effective_host_trust_policy_at_install"] == snap.host_policy_snapshot == "default"


# ===========================================================================
# 3. Lockfile path bound once (env / CWD drift must not re-target)
# ===========================================================================

@pytest.mark.usefixtures("legacy_default_policy")
def test_lockfile_path_bound_env_var_change(tmp_path, monkeypatch):
    lfA = tmp_path / "A.lock"
    lfB = tmp_path / "B.lock"
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "cfg"))
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(lfA))
    pkg = tmp_path / "pk"
    pkg.mkdir()
    (pkg / "setup.py").write_text("x")
    monkeypatch.setattr(installer, "download_artifact", lambda *a, **k: None)
    monkeypatch.setattr(installer, "verify_hash", lambda *a, **k: "abc123def456")
    monkeypatch.setattr(installer, "extract_archive", lambda *a, **k: pkg)
    monkeypatch.setattr(installer, "pip_install", lambda *a, **k: None)
    snap = installer.capture_install_start_snapshot("tp")           # binds absolute lfA
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(lfB))              # env changes mid-op
    _install_tp(start_snapshot=snap)
    assert "tp" in installer.read_lockfile(lfA)["packages"]        # bound path A used
    assert not lfB.exists()                                        # B never touched


@pytest.mark.usefixtures("legacy_default_policy")
def test_relative_lockfile_path_survives_cwd_change(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "cfg"))
    monkeypatch.setenv("AGENTNODE_LOCKFILE", "rel.lock")           # RELATIVE
    monkeypatch.chdir(work)
    pkg = tmp_path / "pk"
    pkg.mkdir()
    (pkg / "setup.py").write_text("x")
    monkeypatch.setattr(installer, "download_artifact", lambda *a, **k: None)
    monkeypatch.setattr(installer, "verify_hash", lambda *a, **k: "abc123def456")
    monkeypatch.setattr(installer, "extract_archive", lambda *a, **k: pkg)
    monkeypatch.setattr(installer, "pip_install", lambda *a, **k: None)
    snap = installer.capture_install_start_snapshot("tp")           # binds absolute work/rel.lock
    assert snap.lockfile_path.is_absolute()
    monkeypatch.chdir(other)                                       # CWD changes mid-op
    _install_tp(start_snapshot=snap)
    assert (work / "rel.lock").exists()                           # bound absolute path used
    assert not (other / "rel.lock").exists()


# ===========================================================================
# 4. Snapshot validation — never a silent re-capture
# ===========================================================================

def test_snapshot_for_a_different_slug_is_rejected(tmp_path, monkeypatch):
    pip = []
    _mock_io(monkeypatch, tmp_path, pip)
    snap_other = installer.capture_install_start_snapshot("other-slug")
    with pytest.raises(installer.InstallStartSnapshotInvalid) as e:
        _install_tp(start_snapshot=snap_other)
    assert e.value.code == "install_start_snapshot_invalid"
    assert pip == []


@pytest.mark.usefixtures("legacy_default_policy")
def test_direct_call_without_snapshot_captures_at_entry(tmp_path, monkeypatch):
    """A legacy/direct install_package call (no snapshot) still binds one at entry."""
    pip = []
    lf = _mock_io(monkeypatch, tmp_path, pip)
    res = _install_tp()                                            # no start_snapshot
    assert res["installed"] is True
    assert installer.read_lockfile(lf)["packages"]["tp"]["version"] == "1.0"
