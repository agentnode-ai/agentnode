"""A1-E-Lock Layer 3 — the security-critical core.

Covers: the writer NONE->PENDING->HELD state machine (atomic reservation before any
blocking step, with a deterministic same-process race test), the thread-bound
EnvironmentWriteGuard, the deeply-immutable Prepared struct, the Phase-B ordering contract
(EVERY revalidation BEFORE any new quarantine; the atomic quarantine re-checks CAS to close
the preflight->mutation race), the uniform structured lock errors for both host routes, the
toolpack post-pip publish contract, and the non-host routing tripwires.

Real cross-process installer concurrency and kill-mid-quarantine recovery live in
test_layer3_installer_concurrency.py. The full agent chokepoint is exercised end-to-end by
the M1 suite (test_agent_m1_*).
"""
from __future__ import annotations

import contextlib
import hashlib
import sys
import threading
from types import SimpleNamespace

import pytest

from agentnode_sdk import _env_rwlock as rw
from agentnode_sdk import installer
from agentnode_sdk._env_rwlock import (
    EnvironmentLockTimeout,
    EnvironmentWriteGuard,
    NestedEnvironmentWriteForbidden,
    ReadToWriteUpgradeForbidden,
    env_read_lock,
    env_write_lock,
)
from agentnode_sdk.exceptions import AgentNodeError
from agentnode_sdk.installer import (
    ENVIRONMENT_WRITE_LOCK_FAILED,
    EnvironmentIdentityChanged,
    EnvironmentWriteLockError,
    PreparedInstallStale,
    ToolpackPublishFailed,
    _PreparedAgentInstall,
)


# ===========================================================================
# 1. Writer state machine: NONE -> PENDING -> HELD, atomic pre-acquire
# ===========================================================================

def test_nested_write_refused_and_state_cleared(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path))
    e = "a" * 64
    assert rw._local_writer_active(e) is False
    with env_write_lock(e, timeout=5) as guard:
        assert isinstance(guard, EnvironmentWriteGuard)
        assert guard.env_id == e and guard.owner_thread_id == threading.get_ident()
        assert rw._writer_held_with(e, guard.token) is True
        with pytest.raises(NestedEnvironmentWriteForbidden):
            with env_write_lock(e, timeout=5):
                pass
    assert rw._local_writer_active(e) is False          # HELD -> NONE on normal exit


def test_reader_then_writer_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path))
    e = "b" * 64
    with env_read_lock(e, timeout=5):
        with pytest.raises(ReadToWriteUpgradeForbidden):
            with env_write_lock(e, timeout=5):
                pass


def test_pending_reservation_closes_same_process_race(tmp_path, monkeypatch):
    """Deterministic race: thread A is paused AFTER the interprocess ticket is acquired but
    BEFORE PENDING->HELD promotion. Thread B requests the same writer and must be refused
    IMMEDIATELY (it already sees PENDING) — no second ticket, no wait to the timeout, and no
    registry leak afterwards."""
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path))
    e = "c" * 64
    at_promotion = threading.Event()
    let_promote = threading.Event()
    real_promote = rw._promote_local_writer_held

    def _paused_promote(env_id, token):
        at_promotion.set()
        assert let_promote.wait(10)
        return real_promote(env_id, token)
    monkeypatch.setattr(rw, "_promote_local_writer_held", _paused_promote)

    b_result = {}

    def _thread_a():
        with env_write_lock(e, timeout=10):
            pass

    def _thread_b():
        import time
        assert at_promotion.wait(10)                 # A holds ticket, PENDING, not yet HELD
        t0 = time.monotonic()
        try:
            with env_write_lock(e, timeout=30):      # a 30s timeout would expose any waiting
                b_result["ok"] = "unexpected-acquire"
        except NestedEnvironmentWriteForbidden:
            b_result["nested"] = True
        except EnvironmentLockTimeout:
            b_result["timeout"] = True
        b_result["elapsed"] = time.monotonic() - t0
        let_promote.set()

    ta, tb = threading.Thread(target=_thread_a), threading.Thread(target=_thread_b)
    ta.start()
    tb.start()
    tb.join(20)
    ta.join(20)
    assert b_result.get("nested") is True            # refused, not a timeout / acquire
    assert b_result.get("elapsed", 99) < 5           # immediate, not a 30s wait
    assert rw._local_writer_active(e) is False        # no leaked PENDING/HELD record


def test_timeout_removes_pending_record(tmp_path, monkeypatch):
    """A write-lock timeout (from the interprocess acquire) leaves NO local record."""
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path))
    e = "d" * 64
    real_acquire = rw._acquire

    @contextlib.contextmanager
    def _timeout_acquire(env_id, ttype, timeout):
        raise EnvironmentLockTimeout(ttype)
        yield  # pragma: no cover
    monkeypatch.setattr(rw, "_acquire", _timeout_acquire)
    with pytest.raises(EnvironmentLockTimeout):
        with env_write_lock(e, timeout=0.1):
            pass
    monkeypatch.setattr(rw, "_acquire", real_acquire)
    assert rw._local_writer_active(e) is False


def test_error_before_held_removes_pending_record(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path))
    e = "e" * 64

    @contextlib.contextmanager
    def _boom_acquire(env_id, ttype, timeout):
        raise rw.CounterStateError("counter corrupt")
        yield  # pragma: no cover
    monkeypatch.setattr(rw, "_acquire", _boom_acquire)
    with pytest.raises(rw.CounterStateError):
        with env_write_lock(e, timeout=5):
            pass
    assert rw._local_writer_active(e) is False


# ===========================================================================
# 2. Thread-bound guard verification (7 cases; only the last authorizes)
# ===========================================================================

def test_write_guard_verification_matrix(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path))
    e = "f" * 64
    with env_write_lock(e, timeout=5) as guard:
        # (valid) the genuine guard on the owner thread authorizes → no raise
        installer._verify_host_write_guard(guard, e)

        # (1) no guard / wrong type
        with pytest.raises(RuntimeError):
            installer._verify_host_write_guard(None, e)
        with pytest.raises(RuntimeError):
            installer._verify_host_write_guard(object(), e)
        # (2) wrong environment
        with pytest.raises(RuntimeError):
            installer._verify_host_write_guard(guard, "0" * 64)
        # (3) forged token (correct env + thread, fake token)
        forged = EnvironmentWriteGuard(e, threading.get_ident(), object())
        with pytest.raises(RuntimeError):
            installer._verify_host_write_guard(forged, e)
        # (4) another thread's guard object used from a different thread
        err = {}

        def _other_thread():
            try:
                installer._verify_host_write_guard(guard, e)   # off owner thread
            except RuntimeError:
                err["off_thread"] = True
        t = threading.Thread(target=_other_thread)
        t.start()
        t.join(5)
        assert err.get("off_thread") is True

    # (5) expired guard — the lock has been released
    with pytest.raises(RuntimeError):
        installer._verify_host_write_guard(guard, e)


# ===========================================================================
# 3. Deeply-immutable Prepared struct
# ===========================================================================

def _make_prepared(tmp_path, *, wheel_bytes=b"WHEELv1", baseline_absent=True,
                   baseline_hash=None, slug="ag", env_id="1" * 64,
                   distribution="ag-agent"):
    wheel = tmp_path / f"{slug}.whl"
    wheel.write_bytes(wheel_bytes)
    sha = hashlib.sha256(wheel_bytes).hexdigest()
    ident = SimpleNamespace(env_id=env_id, purelib="/pl", platlib="/plat")
    snapshot = {"version": "1.0.0", "package_type": "agent", "entrypoint": "ag:run",
                "artifact_hash": "sha256:" + "a" * 64, "publisher_slug": None}
    canonical = installer._canonical_lock_entry_bytes(snapshot)
    digest = installer._prepared_authorization_digest(
        slug=slug, version="1.0.0", artifact_hash="sha256:" + "a" * 64,
        publisher_slug=None, distribution=distribution, distribution_version="1.0.0",
        entrypoint="ag:run", wheel_sha256=sha, env_id=env_id, top="ag",
        lock_entry_canonical_json=canonical)
    return _PreparedAgentInstall(
        slug=slug, lockfile_path=tmp_path / "agentnode.lock",
        baseline_absent=baseline_absent, baseline_hash=baseline_hash, top="ag",
        controlled_env_items=(("PIP_USER", "0"), ("PYTHONNOUSERSITE", "1")),
        canonical_python=sys.executable, expected_env_identity=ident,
        wheel=wheel, wheel_sha256=sha, distribution=distribution,
        distribution_version="1.0.0", entrypoint="ag:run",
        artifact_hash="sha256:" + "a" * 64, publisher_slug=None, version="1.0.0",
        lock_entry_canonical_json=canonical, authorization_digest=digest, policy="default",
        dest=tmp_path)


def test_prepared_controlled_env_is_rebuilt_from_immutable_pairs(tmp_path):
    p = _make_prepared(tmp_path)
    assert isinstance(p.controlled_env_items, tuple)
    env = p.controlled_env()
    assert env == {"PIP_USER": "0", "PYTHONNOUSERSITE": "1"}
    env["EXTRA"] = "x"                       # mutating the rebuilt dict...
    assert "EXTRA" not in dict(p.controlled_env_items)   # ...never touches the prepared pairs


def test_prepared_authorization_is_immutable_bytes_not_a_dict(tmp_path):
    p = _make_prepared(tmp_path)
    # The sole authorization is immutable bytes — there is NO mutable snapshot field.
    assert isinstance(p.lock_entry_canonical_json, bytes)
    assert not hasattr(p, "lock_entry_snapshot")


def test_prepared_commit_entry_is_fresh_from_bytes_each_time(tmp_path):
    p = _make_prepared(tmp_path)
    e1 = p.commit_entry()
    e1["python_distribution"] = "tampered"          # mutate one commit result...
    e2 = p.commit_entry()
    assert "python_distribution" not in e2          # ...next commit is a fresh decode
    assert e1 is not e2


def test_strict_canonicalization_rejects_non_json_types(tmp_path):
    # No default=str: an unexpected type fails-closed as prepared_install_invalid.
    for bad in ({"x": {1, 2}}, {"y": b"bytes"}, {5: "nonstrkey"}, {"z": object()}):
        with pytest.raises(installer.PreparedInstallInvalid) as e:
            installer._canonical_lock_entry_bytes(bad)
        assert e.value.code == "prepared_install_invalid"
    # A JSON-safe entry is accepted and round-trips.
    ok = installer._canonical_lock_entry_bytes(
        {"version": "1.0", "n": 1, "b": True, "none": None, "l": [1, "a"], "d": {"k": "v"}})
    import json
    assert json.loads(ok)["version"] == "1.0"


# ===========================================================================
# 4. Phase B — EVERY revalidation BEFORE any new quarantine
# ===========================================================================

def _seed_agent_entry(lf, slug):
    """Write a sealed agent entry and return its stored integrity hash (CAS baseline)."""
    from agentnode_sdk.lock_integrity import seal_entry
    entry = {"version": "1.0.0", "package_type": "agent", "entrypoint": "ag:run",
             "artifact_hash": "sha256:" + "a" * 64}
    installer.update_lockfile(slug, seal_entry(entry), path=lf)
    stored = installer.read_lockfile(lf)["packages"][slug]
    return stored["_integrity"]["hash"]


@contextlib.contextmanager
def _held_prepared(tmp_path, monkeypatch, **kw):
    """Seed an existing entry (so quarantine would be observable), build a matching prepared,
    hold the real write-lock, and tripwire pip. Yields (prepared, guard, pip_calls, before_bytes)."""
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "cfg"))
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(tmp_path / "agentnode.lock"))
    lf = tmp_path / "agentnode.lock"
    env_id = "2" * 64
    baseline_hash = _seed_agent_entry(lf, "ag")
    before = lf.read_bytes()
    p = _make_prepared(tmp_path, baseline_absent=False, baseline_hash=baseline_hash,
                       env_id=env_id, **kw)
    pip_calls = []
    monkeypatch.setattr("agentnode_sdk._agent_pip.pip_install_wheel",
                        lambda *a, **k: pip_calls.append(1))
    with env_write_lock(env_id, timeout=5) as guard:
        yield p, guard, pip_calls, before


def test_wheel_tamper_is_stale_before_any_quarantine(tmp_path, monkeypatch):
    with _held_prepared(tmp_path, monkeypatch) as (p, guard, pip_calls, before):
        p.wheel.write_bytes(b"TAMPERED-DIFFERENT")     # changed after prepare
        with pytest.raises(PreparedInstallStale):
            installer._install_agent_prepared_under_env_write_lock(p, guard)
        assert pip_calls == []
        assert p.lockfile_path.read_bytes() == before   # entry byte-identical, NO quarantine


def test_wheel_delete_is_stale_before_any_quarantine(tmp_path, monkeypatch):
    with _held_prepared(tmp_path, monkeypatch) as (p, guard, pip_calls, before):
        p.wheel.unlink()
        with pytest.raises(PreparedInstallStale):
            installer._install_agent_prepared_under_env_write_lock(p, guard)
        assert pip_calls == []
        assert p.lockfile_path.read_bytes() == before


def test_prepared_toctou_commit_uses_immutable_phase_a_bytes(tmp_path, monkeypatch):
    """Blocker-4: after the prepared authorization is checked, an external reference cannot
    change it. The frozen field holds immutable bytes (reassignment refused; a decoded copy
    is detached), so the committed entry always equals the Phase-A bytes."""
    import dataclasses
    with _held_prepared(tmp_path, monkeypatch) as (p, guard, pip_calls, before):
        phase_a = p.commit_entry()

        def _tamper_between_pip_and_commit(*a, **k):
            # (1) the frozen field cannot be reassigned
            with pytest.raises(dataclasses.FrozenInstanceError):
                p.lock_entry_canonical_json = b'{"version":"6.6.6"}'
            # (2) a decoded copy is detached — mutating it changes nothing
            p.lock_entry()["version"] = "6.6.6"
        # runs AFTER the prepared check + quarantine, BEFORE the commit
        monkeypatch.setattr("agentnode_sdk._agent_pip.post_verify",
                            lambda *a, **k: _tamper_between_pip_and_commit())
        installer._install_agent_prepared_under_env_write_lock(p, guard)
    committed = installer.read_lockfile(p.lockfile_path)["packages"]["ag"]
    assert committed["version"] == phase_a["version"] == "1.0.0"   # Phase-A bytes, not tampered


def test_cas_conflict_preflight_no_quarantine_no_pip(tmp_path, monkeypatch):
    # baseline says the slug's hash is X, but the actual entry hash differs → CAS conflict in
    # the READ-ONLY preflight → abort with NO quarantine, entry intact, pip never started.
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "cfg"))
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(tmp_path / "agentnode.lock"))
    lf = tmp_path / "agentnode.lock"
    _seed_agent_entry(lf, "ag")
    before = lf.read_bytes()
    env_id = "3" * 64
    p = _make_prepared(tmp_path, baseline_absent=False, baseline_hash="sha256:" + "f" * 64,
                       env_id=env_id)
    pip_calls = []
    monkeypatch.setattr("agentnode_sdk._agent_pip.pip_install_wheel",
                        lambda *a, **k: pip_calls.append(1))
    with env_write_lock(env_id, timeout=5) as guard:
        with pytest.raises(installer._AgentTransactionAbort, match="modified by another install"):
            installer._install_agent_prepared_under_env_write_lock(p, guard)
    assert pip_calls == []
    assert lf.read_bytes() == before


def test_preflight_to_quarantine_cas_race_refused(tmp_path, monkeypatch):
    """The read-only preflight passes, then a concurrent writer inserts the slug BEFORE the
    atomic quarantine mutator runs — the in-mutator CAS re-check refuses (no pip)."""
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "cfg"))
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(tmp_path / "agentnode.lock"))
    lf = tmp_path / "agentnode.lock"
    env_id = "4" * 64
    # baseline_absent=True: prepared expects the slug ABSENT. Preflight (empty lockfile) passes.
    p = _make_prepared(tmp_path, baseline_absent=True, baseline_hash=None, env_id=env_id)
    pip_calls = []
    monkeypatch.setattr("agentnode_sdk._agent_pip.pip_install_wheel",
                        lambda *a, **k: pip_calls.append(1))
    real_mutate = installer.mutate_lockfile
    raced = {"done": False}

    def _racing_mutate(apply, **kw):
        # A concurrent install completes right after our preflight, inserting the slug.
        # Guard against re-entry: _seed_agent_entry -> update_lockfile -> mutate_lockfile.
        if not raced["done"]:
            raced["done"] = True
            _seed_agent_entry(lf, "ag")
        return real_mutate(apply, **kw)
    monkeypatch.setattr(installer, "mutate_lockfile", _racing_mutate)
    with env_write_lock(env_id, timeout=5) as guard:
        with pytest.raises(installer._AgentTransactionAbort, match="modified by another install"):
            installer._install_agent_prepared_under_env_write_lock(p, guard)
    assert pip_calls == []


def test_phase_b_refuses_without_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "cfg"))
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(tmp_path / "agentnode.lock"))
    p = _make_prepared(tmp_path, env_id="5" * 64)
    # A guard for a lock we do NOT hold must be refused (no HELD record).
    forged = EnvironmentWriteGuard("5" * 64, threading.get_ident(), object())
    with pytest.raises(RuntimeError, match="write guard|write-lock"):
        installer._install_agent_prepared_under_env_write_lock(p, forged)


# ===========================================================================
# 5. Uniform structured lock errors for BOTH host routes
# ===========================================================================

@pytest.mark.parametrize("raiser,expected_code", [
    (lambda: (_ for _ in ()).throw(EnvironmentLockTimeout("w")), "environment_write_lock_timeout"),
    (lambda: (_ for _ in ()).throw(ReadToWriteUpgradeForbidden("r")), "read_to_write_upgrade_forbidden"),
    (lambda: (_ for _ in ()).throw(NestedEnvironmentWriteForbidden("n")), "environment_write_lock_nested"),
    (lambda: (_ for _ in ()).throw(rw.CounterStateError("x")), ENVIRONMENT_WRITE_LOCK_FAILED),
    (lambda: (_ for _ in ()).throw(rw.QueueStateError("x")), ENVIRONMENT_WRITE_LOCK_FAILED),
    (lambda: (_ for _ in ()).throw(OSError("disk")), ENVIRONMENT_WRITE_LOCK_FAILED),
])
def test_host_chokepoint_translates_lock_errors(monkeypatch, raiser, expected_code):
    """Every acquisition error → a structured EnvironmentWriteLockError with a stable code.
    The chokepoint is shared by agent AND toolpack, so both routes get this contract."""
    @contextlib.contextmanager
    def _raising_env_write_lock(env_id, timeout=None):
        raiser()
        yield  # pragma: no cover
    monkeypatch.setattr(rw, "env_write_lock", _raising_env_write_lock)
    ident = SimpleNamespace(env_id="6" * 64, purelib="/pl", platlib="/plat")
    with pytest.raises(EnvironmentWriteLockError) as e:
        with installer._host_env_write_lock(ident, "pyX"):
            pass
    assert e.value.code == expected_code
    assert isinstance(e.value, AgentNodeError)      # renders traceback-free at the CLI


def test_toolpack_nested_write_surfaces_structured(tmp_path, monkeypatch):
    """A host TOOLPACK install whose target env is already write-locked by this process
    surfaces the SAME structured nested-write error (uniform agent/toolpack contract)."""
    from agentnode_sdk._env_lock import resolve_env_identity
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "cfg"))
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(tmp_path / "agentnode.lock"))
    pkg = tmp_path / "ex" / "pk"
    pkg.mkdir(parents=True)
    (pkg / "setup.py").write_text("x")
    monkeypatch.setattr(installer, "download_artifact", lambda *a, **k: None)
    monkeypatch.setattr(installer, "verify_hash", lambda *a, **k: "abc123def456")
    monkeypatch.setattr(installer, "extract_archive", lambda *a, **k: pkg)
    monkeypatch.setattr(installer, "pip_install", lambda *a, **k: None)
    env_id = resolve_env_identity(sys.executable).env_id
    with env_write_lock(env_id, timeout=5):
        with pytest.raises(EnvironmentWriteLockError) as e:
            installer.install_package(
                slug="tp", version="1.0", artifact_url="https://x/p.tgz",
                artifact_hash="sha256:abc123def456", entrypoint="pk.tool",
                trust_level="trusted",
            )
        assert e.value.code == "environment_write_lock_nested"


# ===========================================================================
# 6. Toolpack post-pip publish-failure contract
# ===========================================================================

def test_toolpack_pip_ok_publish_fail_is_reparable(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "cfg"))
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(tmp_path / "agentnode.lock"))
    pkg = tmp_path / "ex" / "pk"
    pkg.mkdir(parents=True)
    (pkg / "setup.py").write_text("x")
    monkeypatch.setattr(installer, "download_artifact", lambda *a, **k: None)
    monkeypatch.setattr(installer, "verify_hash", lambda *a, **k: "abc123def456")
    monkeypatch.setattr(installer, "extract_archive", lambda *a, **k: pkg)
    pip_calls = []
    monkeypatch.setattr(installer, "pip_install", lambda *a, **k: pip_calls.append(1))
    # pip succeeds; the final compare-and-add commit then fails (durability, no entry landed).
    real_commit = installer._commit_toolpack_entry
    fail = {"on": True}

    def _flaky_commit(slug, lock_entry, path):
        if fail["on"]:
            raise ToolpackPublishFailed()
        return real_commit(slug, lock_entry, path)
    monkeypatch.setattr(installer, "_commit_toolpack_entry", _flaky_commit)

    with pytest.raises(ToolpackPublishFailed) as e:
        installer.install_package(
            slug="tp", version="1.0", artifact_url="https://x/p.tgz",
            artifact_hash="sha256:abc123def456", entrypoint="pk.tool",
            trust_level="trusted",
        )
    assert e.value.code == "toolpack_publish_failed"
    assert pip_calls == [1]                    # pip DID mutate the env

    # Reparable: a re-install (publish now works) records it.
    fail["on"] = False
    res = installer.install_package(
        slug="tp", version="1.0", artifact_url="https://x/p.tgz",
        artifact_hash="sha256:abc123def456", entrypoint="pk.tool", trust_level="trusted",
    )
    assert res["installed"] is True


def _seed_toolpack_entry(lf, slug, *, version, artifact_hash):
    from agentnode_sdk.lock_integrity import seal_entry
    entry = {"version": version, "package_type": "toolpack", "entrypoint": "pk.tool",
             "artifact_hash": artifact_hash}
    installer.update_lockfile(slug, seal_entry(entry), path=lf)
    return installer.read_lockfile(lf)["packages"][slug]["_integrity"]["hash"]


def _toolpack_io(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "cfg"))
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(tmp_path / "agentnode.lock"))
    pkg = tmp_path / "ex" / "pk"
    pkg.mkdir(parents=True)
    (pkg / "setup.py").write_text("x")
    monkeypatch.setattr(installer, "download_artifact", lambda *a, **k: None)
    monkeypatch.setattr(installer, "verify_hash", lambda *a, **k: "abc123def456")
    monkeypatch.setattr(installer, "extract_archive", lambda *a, **k: pkg)
    pip_calls = []
    monkeypatch.setattr(installer, "pip_install", lambda *a, **k: pip_calls.append(1))
    return tmp_path / "agentnode.lock", pip_calls


@pytest.mark.parametrize("new_version,new_hash", [
    ("2.0", "sha256:abc123def456"),   # upgrade
    ("1.0", "sha256:abc123def456"),   # reinstall, same version, different bytes (E1 hash differs)
])
def test_toolpack_upgrade_publish_fail_leaves_slug_absent(tmp_path, monkeypatch,
                                                          new_version, new_hash):
    """Blocker-1: an EXISTING toolpack entry is DURABLY quarantined before pip. If the publish
    then fails, the OLD entry is gone (never executable against the changed env) and the slug
    is fail-closed ABSENT — not a stale-but-valid old entry, not an apparent success."""
    lf, pip_calls = _toolpack_io(monkeypatch, tmp_path)
    _seed_toolpack_entry(lf, "tp", version="0.9", artifact_hash="sha256:" + "b" * 64)
    assert "tp" in installer.read_lockfile(lf)["packages"]

    monkeypatch.setattr(installer, "_commit_toolpack_entry",
                        lambda *a, **k: (_ for _ in ()).throw(ToolpackPublishFailed()))
    with pytest.raises(ToolpackPublishFailed):
        installer.install_package(
            slug="tp", version=new_version, artifact_url="https://x/p.tgz",
            artifact_hash=new_hash, entrypoint="pk.tool", trust_level="trusted",
        )
    assert pip_calls == [1]                                   # pip ran (env may have changed)
    assert "tp" not in installer.read_lockfile(lf).get("packages", {})   # OLD entry GONE, absent


def test_toolpack_cas_race_refused_no_pip(tmp_path, monkeypatch):
    """Toolpack preflight sees the slug absent, then a concurrent writer inserts it before the
    atomic quarantine — the in-mutator CAS re-check refuses and pip never runs."""
    lf, pip_calls = _toolpack_io(monkeypatch, tmp_path)
    real_mutate = installer.mutate_lockfile
    raced = {"done": False}

    def _racing_mutate(apply, **kw):
        if not raced["done"]:
            raced["done"] = True
            _seed_toolpack_entry(lf, "tp", version="0.9", artifact_hash="sha256:" + "b" * 64)
        return real_mutate(apply, **kw)
    monkeypatch.setattr(installer, "mutate_lockfile", _racing_mutate)
    with pytest.raises(RuntimeError, match="modified by another install"):
        installer.install_package(
            slug="tp", version="1.0", artifact_url="https://x/p.tgz",
            artifact_hash="sha256:abc123def456", entrypoint="pk.tool", trust_level="trusted",
        )
    assert pip_calls == []


@pytest.mark.parametrize("preexisting", [False, True])
def test_toolpack_final_cas_concurrent_publish_after_pip(tmp_path, monkeypatch, preexisting):
    """Blocker-1 core: while pip runs, ANOTHER path publishes the same slug. The final
    compare-and-add commit must refuse (toolpack_commit_conflict_after_pip) and leave the
    concurrent entry byte-identical — never overwrite it, never report success. Covered for
    both a pre-existing upgrade entry and a brand-new slug."""
    from agentnode_sdk.lock_integrity import seal_entry
    lf, pip_calls = _toolpack_io(monkeypatch, tmp_path)
    if preexisting:
        _seed_toolpack_entry(lf, "tp", version="0.9", artifact_hash="sha256:" + "b" * 64)

    # A concurrent path (e.g. a container install / a different target env) publishes "tp"
    # DURING our pip — after our quarantine, before our commit.
    foreign = seal_entry({"version": "7.7", "package_type": "toolpack", "entrypoint": "pk.tool",
                          "artifact_hash": "sha256:" + "c" * 64, "sandboxed": True})

    published = {}

    def _pip_then_foreign_publish(*a, **k):
        pip_calls.append(1)
        installer.update_lockfile("tp", dict(foreign), path=lf)   # B wins the slug DURING pip
        published["b"] = installer.read_lockfile(lf)["packages"]["tp"]
    monkeypatch.setattr(installer, "pip_install", _pip_then_foreign_publish)

    with pytest.raises(installer.ToolpackCommitConflict) as e:
        installer.install_package(
            slug="tp", version="2.0", artifact_url="https://x/p.tgz",
            artifact_hash="sha256:abc123def456", entrypoint="pk.tool", trust_level="trusted",
        )
    assert e.value.code == "toolpack_commit_conflict_after_pip"
    assert pip_calls == [1]
    after = installer.read_lockfile(lf)["packages"]["tp"]
    assert after == published["b"]                                # B's entry untouched
    assert after["version"] == "7.7"                              # NOT overwritten by A's 2.0


def test_commit_toolpack_entry_durability_readback(tmp_path, monkeypatch):
    """The final commit's durability contract, unit-tested directly."""
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "cfg"))
    lf = tmp_path / "agentnode.lock"
    entry = {"version": "1.0", "package_type": "toolpack", "entrypoint": "pk.tool",
             "artifact_hash": "sha256:" + "a" * 64, "build_mode": "host"}

    # (a) slug absent → commits the exact sealed entry.
    installer._commit_toolpack_entry("tp", dict(entry), lf)
    assert installer.read_lockfile(lf)["packages"]["tp"]["version"] == "1.0"

    # (b) slug already present (a foreign entry) → conflict, foreign untouched.
    with pytest.raises(installer.ToolpackCommitConflict):
        installer._commit_toolpack_entry("tp", {**entry, "version": "9.9"}, lf)
    assert installer.read_lockfile(lf)["packages"]["tp"]["version"] == "1.0"

    # (c) durability error but OUR exact entry landed → treated as committed (no re-raise).
    lf2 = tmp_path / "lock2.lock"
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(lf2))
    real_mutate = installer.mutate_lockfile

    def _write_then_raise(apply, **kw):
        real_mutate(apply, **kw)                       # the entry lands durably...
        raise OSError("fsync flaked after replace")    # ...then the durable step errors
    monkeypatch.setattr(installer, "mutate_lockfile", _write_then_raise)
    installer._commit_toolpack_entry("tp2", {**entry, "version": "3.0"}, lf2)  # no raise
    monkeypatch.setattr(installer, "mutate_lockfile", real_mutate)
    assert installer.read_lockfile(lf2)["packages"]["tp2"]["version"] == "3.0"

    # (d) durability error and NOTHING landed → reparable ToolpackPublishFailed.
    lf3 = tmp_path / "lock3.lock"
    monkeypatch.setattr(installer, "mutate_lockfile",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no write")))
    with pytest.raises(ToolpackPublishFailed):
        installer._commit_toolpack_entry("tp3", dict(entry), lf3)


def test_agent_final_cas_concurrent_publish_refused(tmp_path, monkeypatch):
    """Agent counterpart of the toolpack final-CAS: a foreign entry published after the agent
    quarantine, before the commit, is NOT overwritten (concurrent-update refusal). This is the
    same guarantee as M1's test_agent_m1_amendment.test_commit_refuses_foreign_entry_no_overwrite;
    proven here directly against the reworked _commit_agent_entry."""
    from agentnode_sdk.lock_integrity import seal_entry
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "cfg"))
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(tmp_path / "agentnode.lock"))
    lf = tmp_path / "agentnode.lock"
    foreign = seal_entry({"version": "8.8", "package_type": "agent", "entrypoint": "ag:run",
                          "artifact_hash": "sha256:" + "d" * 64,
                          "python_distribution": "ag-agent",
                          "python_distribution_version": "8.8"})
    installer.update_lockfile("ag", dict(foreign), path=lf)
    before = installer.read_lockfile(lf)["packages"]["ag"]
    with pytest.raises(RuntimeError, match="concurrent lockfile update"):
        installer._commit_agent_entry("ag", {
            "version": "1.0.0", "package_type": "agent", "entrypoint": "ag:run",
            "artifact_hash": "sha256:" + "a" * 64, "python_distribution": "ag-agent",
            "python_distribution_version": "1.0.0"}, lf)
    assert installer.read_lockfile(lf)["packages"]["ag"] == before   # foreign untouched


# ---------------------------------------------------------------------------
# Toolpack PREPARATION-CAS: the baseline is bound BEFORE download; a newer install that
# completes during our preparation is never adopted-as-baseline and overwritten.
# ---------------------------------------------------------------------------

def _b_publishes_during_download(monkeypatch, lf, entry):
    """Patch download_artifact so that a concurrent installer B publishes *entry* for 'tp'
    DURING our download — i.e. AFTER our pre-download baseline was bound."""
    from agentnode_sdk.lock_integrity import seal_entry
    published = {}

    def _download(*a, **k):
        installer.update_lockfile("tp", seal_entry(entry), path=lf)
        published["b"] = installer.read_lockfile(lf)["packages"]["tp"]
    monkeypatch.setattr(installer, "download_artifact", _download)
    return published


def _install_tp(version="1.0"):
    return installer.install_package(
        slug="tp", version=version, artifact_url="https://x/p.tgz",
        artifact_hash="sha256:abc123def456", entrypoint="pk.tool", trust_level="trusted")


def test_toolpack_preparation_stale_existing_entry_replaced(tmp_path, monkeypatch):
    """A binds baseline E1 → pauses in download → B publishes E2 → A acquires the writer →
    A must NOT quarantine/overwrite E2. Fail-closed toolpack_preparation_stale, E2 intact."""
    lf, pip_calls = _toolpack_io(monkeypatch, tmp_path)
    _seed_toolpack_entry(lf, "tp", version="0.9", artifact_hash="sha256:" + "b" * 64)  # E1
    published = _b_publishes_during_download(
        monkeypatch, lf, {"version": "2.0", "package_type": "toolpack",
                          "entrypoint": "pk.tool", "artifact_hash": "sha256:" + "c" * 64})
    with pytest.raises(installer.ToolpackPreparationStale) as e:
        _install_tp()
    assert e.value.code == "toolpack_preparation_stale"
    assert pip_calls == []
    assert installer.read_lockfile(lf)["packages"]["tp"] == published["b"]   # E2 untouched


def test_toolpack_preparation_stale_absent_then_competitor_publishes(tmp_path, monkeypatch):
    """Originally absent baseline; B publishes E2 during A's download → stale, E2 intact."""
    lf, pip_calls = _toolpack_io(monkeypatch, tmp_path)
    published = _b_publishes_during_download(
        monkeypatch, lf, {"version": "2.0", "package_type": "toolpack",
                          "entrypoint": "pk.tool", "artifact_hash": "sha256:" + "c" * 64})
    with pytest.raises(installer.ToolpackPreparationStale):
        _install_tp()
    assert pip_calls == []
    assert installer.read_lockfile(lf)["packages"]["tp"] == published["b"]


def test_toolpack_preparation_stale_when_original_entry_removed(tmp_path, monkeypatch):
    """A binds baseline E1 (present); B REMOVES it during A's download → A must not proceed as
    a fresh install (the state no longer matches its bound baseline)."""
    lf, pip_calls = _toolpack_io(monkeypatch, tmp_path)
    _seed_toolpack_entry(lf, "tp", version="0.9", artifact_hash="sha256:" + "b" * 64)

    def _download_removes(*a, **k):
        installer.remove_from_lockfile("tp", path=lf)
    monkeypatch.setattr(installer, "download_artifact", _download_removes)
    with pytest.raises(installer.ToolpackPreparationStale):
        _install_tp()
    assert pip_calls == []
    assert "tp" not in installer.read_lockfile(lf).get("packages", {})


def test_toolpack_preparation_unchanged_baseline_proceeds(tmp_path, monkeypatch):
    """No concurrency: baseline matches under the lock → the install proceeds normally."""
    lf, pip_calls = _toolpack_io(monkeypatch, tmp_path)
    _seed_toolpack_entry(lf, "tp", version="0.9", artifact_hash="sha256:" + "b" * 64)
    res = _install_tp()
    assert res["installed"] is True
    assert pip_calls == [1]
    assert installer.read_lockfile(lf)["packages"]["tp"]["version"] == "1.0"   # A's new entry


def test_agent_baseline_bound_before_download(tmp_path, monkeypatch):
    """install_package binds the AGENT same-slug baseline BEFORE the download and passes it to
    the transaction — so a competitor publishing during the download cannot be adopted as the
    agent's baseline either. Verified without a real build by spying the transaction."""
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "cfg"))
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(tmp_path / "agentnode.lock"))
    lf = tmp_path / "agentnode.lock"
    pkg = tmp_path / "pk"
    pkg.mkdir()
    monkeypatch.setattr(installer, "verify_hash", lambda *a, **k: "abc123def456")
    monkeypatch.setattr(installer, "extract_archive", lambda *a, **k: pkg)
    from agentnode_sdk.lock_integrity import seal_entry
    e2 = {"version": "2.0", "package_type": "agent", "entrypoint": "ag:run",
          "artifact_hash": "sha256:" + "c" * 64, "python_distribution": "ag-agent",
          "python_distribution_version": "2.0"}

    def _download_b_publishes(*a, **k):
        installer.update_lockfile("ag", seal_entry(e2), path=lf)   # B publishes DURING download
    monkeypatch.setattr(installer, "download_artifact", _download_b_publishes)

    seen = {}

    def _spy_tx(slug, **kw):
        seen["prepared_baseline"] = kw.get("prepared_baseline")
    monkeypatch.setattr(installer, "_install_agent_host_transaction", _spy_tx)

    installer.install_package(
        slug="ag", version="1.0", artifact_url="https://x/p.tgz",
        artifact_hash="sha256:abc123def456", entrypoint="ag:run",
        trust_level="trusted", package_type="agent")
    # The baseline passed to the transaction reflects the PRE-download state (slug absent),
    # NOT B's entry published during the download.
    assert seen["prepared_baseline"] == (True, None)


# ===========================================================================
# 7. Exactly-one lock, identity change, no inner lock, non-host tripwires
# ===========================================================================

def _mock_host_io(monkeypatch, tmp_path):
    pkg = tmp_path / "ex" / "pk"
    pkg.mkdir(parents=True)
    (pkg / "setup.py").write_text("x")
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(tmp_path / "agentnode.lock"))
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "cfg"))
    monkeypatch.setattr(installer, "download_artifact", lambda *a, **k: None)
    monkeypatch.setattr(installer, "verify_hash", lambda *a, **k: "abc123def456")
    monkeypatch.setattr(installer, "extract_archive", lambda *a, **k: pkg)
    pip = []
    monkeypatch.setattr(installer, "pip_install", lambda *a, **k: pip.append(1))
    return pip


def test_host_toolpack_acquires_exactly_one_write_lock(tmp_path, monkeypatch):
    import json
    pip = _mock_host_io(monkeypatch, tmp_path)
    events = []
    real = rw.env_write_lock

    @contextlib.contextmanager
    def _counting(env_id, timeout=rw.DEFAULT_ACQUIRE_TIMEOUT):
        events.append(env_id)
        with real(env_id, timeout=timeout) as g:
            yield g
    monkeypatch.setattr(rw, "env_write_lock", _counting)
    res = installer.install_package(
        slug="tp", version="1.0", artifact_url="https://x/p.tgz",
        artifact_hash="sha256:abc123def456", entrypoint="pk.tool", trust_level="trusted",
    )
    assert res["installed"] is True and pip == [1]
    assert len(events) == 1                       # exactly ONE acquisition
    entry = json.loads((tmp_path / "agentnode.lock").read_text())["packages"]["tp"]
    assert entry["build_mode"] == "host" and "sandbox_volume" not in entry


def test_identity_change_refused_before_body(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path))
    expected = SimpleNamespace(env_id="7" * 64, purelib="/pl", platlib="/plat")
    changed = SimpleNamespace(env_id="8" * 64, purelib="/pl", platlib="/plat")
    monkeypatch.setattr("agentnode_sdk._env_lock.resolve_env_identity",
                        lambda *a, **k: changed)
    ran = []
    with pytest.raises(EnvironmentIdentityChanged):
        with installer._host_env_write_lock(expected, "pyX"):
            ran.append(1)
    assert ran == []


def test_mutation_helpers_have_no_inner_lock():
    import inspect
    for helper in (installer._install_agent_prepared_under_env_write_lock,
                   installer._install_toolpack_prepared_under_env_write_lock):
        src = inspect.getsource(helper)
        assert "with env_write_lock(" not in src
        assert "_host_env_write_lock(" not in src
        assert "env_lock(" not in src
    choke = inspect.getsource(installer._host_env_write_lock)
    assert choke.count("rw.env_write_lock(") == 1     # exactly one acquisition
    for tx_fn in (installer._install_agent_host_transaction,
                  installer._install_toolpack_host_transaction):
        tx = inspect.getsource(tx_fn)
        assert tx.count("with _host_env_write_lock(") == 1   # each host route: one chokepoint
        assert "with env_write_lock(" not in tx
        assert "env_lock(" not in tx


def test_mcp_route_never_locks(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(tmp_path / "agentnode.lock"))
    trip = []
    monkeypatch.setattr(installer, "resolve_python", lambda *a, **k: trip.append("py"))
    monkeypatch.setattr("agentnode_sdk._env_lock.resolve_env_identity",
                        lambda *a, **k: trip.append("id"))
    monkeypatch.setattr(rw, "env_write_lock", lambda *a, **k: trip.append("lock"))
    with contextlib.suppress(Exception):
        installer._install_mcp(slug="mcp-x", version="1.0", package_type="mcp",
                               mcp_command=["node", "s.js"], trust_level="verified")
    assert trip == []


def test_community_agent_container_route_no_host_mutation(tmp_path, monkeypatch):
    pkg = tmp_path / "ex" / "pk"
    pkg.mkdir(parents=True)
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(tmp_path / "agentnode.lock"))
    monkeypatch.setattr(installer, "download_artifact", lambda *a, **k: None)
    monkeypatch.setattr(installer, "verify_hash", lambda *a, **k: "abc123def456")
    monkeypatch.setattr(installer, "extract_archive", lambda *a, **k: pkg)
    monkeypatch.setattr(installer, "_container_build_into_volume", lambda *a, **k: "vol")
    trip = []
    monkeypatch.setattr(installer, "resolve_python", lambda *a, **k: trip.append("py"))
    monkeypatch.setattr(installer, "_install_agent_host_transaction",
                        lambda *a, **k: trip.append("host_tx"))
    monkeypatch.setattr(rw, "env_write_lock", lambda *a, **k: trip.append("lock"))
    res = installer.install_package(
        slug="ca", version="1.0", artifact_url="https://x/p.tgz",
        artifact_hash="sha256:abc123def456", entrypoint="ca.agent:run",
        trust_level="verified", package_type="agent",
    )
    assert res["installed"] is True and trip == []


# ===========================================================================
# 8. Stable codes (single source)
# ===========================================================================

def test_core_error_codes_are_stable():
    assert EnvironmentIdentityChanged.code == "environment_identity_changed"
    assert PreparedInstallStale.code == "prepared_install_stale"
    assert ToolpackPublishFailed.code == "toolpack_publish_failed"
    assert ENVIRONMENT_WRITE_LOCK_FAILED == "environment_write_lock_failed"
    assert NestedEnvironmentWriteForbidden.code == "environment_write_lock_nested"
    assert ReadToWriteUpgradeForbidden.code == "read_to_write_upgrade_forbidden"
    assert EnvironmentLockTimeout("w").code == "environment_write_lock_timeout"
