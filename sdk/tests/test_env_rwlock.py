"""A1-E-Lock: FIFO-ticket inter-process reader/writer lock."""
from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agentnode_sdk import _env_rwlock as rw

REPO_ROOT = str(Path(__file__).resolve().parents[1])
WORKER = str(Path(__file__).resolve().parent / "rwlock_worker.py")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path))
    # Isolate the process-global registries between tests (all share one process).
    rw._local_readers.clear()
    rw._all_lock_fds.clear()
    rw._depths().clear()
    return {"config": str(tmp_path), "id": "testenv", "mk": tmp_path}


def _spawn(env, mode, prefix, hold=0.0):
    return subprocess.Popen(
        [sys.executable, WORKER, REPO_ROOT, env["config"], env["id"], mode,
         str(env["mk"] / prefix)] + ([str(hold)] if hold else [])
    )


def _wait_file(path: Path, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if path.exists():
            return True
        time.sleep(0.01)
    return False


def _release(env, prefix):
    (env["mk"] / (prefix + ".release")).write_text("go")


# ---------------------------------------------------------------------------
# In-process basics
# ---------------------------------------------------------------------------

def test_read_then_write_sequential(env):
    with rw.env_read_lock(env["id"]):
        pass
    with rw.env_write_lock(env["id"]):
        pass  # no leftover blocks the next acquire


def test_reentrant_readers_same_process(env):
    with rw.env_read_lock(env["id"]):
        with rw.env_read_lock(env["id"]):
            pass  # nested readers must not deadlock


def test_read_to_write_upgrade_forbidden_same_thread(env):
    with rw.env_read_lock(env["id"]):
        with pytest.raises(rw.ReadToWriteUpgradeForbidden):
            with rw.env_write_lock(env["id"]):
                pass


def test_read_to_write_upgrade_forbidden_other_thread(env):
    import threading
    started = threading.Event()
    stop = threading.Event()

    def hold_reader():
        with rw.env_read_lock(env["id"]):
            started.set()
            stop.wait(5)

    t = threading.Thread(target=hold_reader)
    t.start()
    assert started.wait(5)
    try:
        with pytest.raises(rw.ReadToWriteUpgradeForbidden):
            with rw.env_write_lock(env["id"]):
                pass
    finally:
        stop.set()
        t.join(5)


# ---------------------------------------------------------------------------
# Cross-process semantics
# ---------------------------------------------------------------------------

def test_write_excludes_read(env):
    w = _spawn(env, "write", "w1", hold=1.5)
    assert _wait_file(env["mk"] / "w1.acq", 10)
    t0 = time.monotonic()
    with rw.env_read_lock(env["id"]):   # must wait until the writer releases
        waited = time.monotonic() - t0
    w.wait(10)
    assert waited >= 1.0, f"read did not wait for the writer (waited {waited:.2f}s)"


def test_readers_concurrent(env):
    r1 = _spawn(env, "read", "r1", hold=30)
    r2 = _spawn(env, "read", "r2", hold=30)
    try:
        assert _wait_file(env["mk"] / "r1.acq", 10)
        assert _wait_file(env["mk"] / "r2.acq", 10)  # BOTH held at once → concurrent
    finally:
        _release(env, "r1")
        _release(env, "r2")
        r1.wait(10)
        r2.wait(10)


def test_orphan_ticket_cleaned(env):
    c = _spawn(env, "crash-read", "c1")
    c.wait(10)                                    # crashed while holding
    assert (env["mk"] / "c1.acq").exists()
    # an orphan ticket file remains, but its OS lock is free → next acquire succeeds
    t0 = time.monotonic()
    with rw.env_write_lock(env["id"]):
        pass
    assert time.monotonic() - t0 < 8


def test_multiple_orphans_do_not_block(env):
    for i in range(3):
        c = _spawn(env, "crash-read", f"m{i}")
        c.wait(10)
    with rw.env_write_lock(env["id"]):
        pass  # all three orphans cleaned, writer admitted


def test_active_plus_orphan(env):
    orphan = _spawn(env, "crash-read", "orph")
    orphan.wait(10)
    live = _spawn(env, "read", "liv", hold=30)
    try:
        assert _wait_file(env["mk"] / "liv.acq", 10)
        # a writer must wait for the LIVE reader but not be blocked by the orphan
        w = _spawn(env, "write", "wo", hold=0.2)
        assert not _wait_file(env["mk"] / "wo.acq", 1.5)  # blocked by the live reader
        _release(env, "liv")
        assert _wait_file(env["mk"] / "wo.acq", 10)        # admitted after reader ends
        w.wait(10)
    finally:
        _release(env, "liv")
        live.wait(10)


def test_no_overtake_writer_before_later_reader(env):
    # Register order (ticket order): R1, then W, then R2. After R1 releases, the
    # writer (lower ticket) must be admitted before the later reader R2.
    r1 = _spawn(env, "read", "n_r1", hold=30)  # noqa: F841 — released via marker file
    assert _wait_file(env["mk"] / "n_r1.acq", 10)
    w = _spawn(env, "write", "n_w", hold=0.3)
    time.sleep(0.8)   # let W allocate its ticket (lower than R2's)
    r2 = _spawn(env, "read", "n_r2", hold=0.3)
    time.sleep(0.8)   # let R2 allocate its (higher) ticket
    _release(env, "n_r1")
    assert _wait_file(env["mk"] / "n_w.acq", 10)
    assert _wait_file(env["mk"] / "n_r2.acq", 10)
    w_at = float((env["mk"] / "n_w.acq").read_text())
    r2_at = float((env["mk"] / "n_r2.acq").read_text())
    assert w_at < r2_at, "later reader overtook the waiting writer (FIFO violated)"
    w.wait(10)
    r2.wait(10)


def test_corrupt_counter_fail_closed(env):
    with rw.env_read_lock(env["id"]):
        pass  # creates the counter
    counter = env["mk"] / "locks" / f"rw-{env['id']}" / "counter"
    counter.write_text("not-a-number")            # tamper / partial-write simulation
    with pytest.raises(rw.CounterStateError):
        with rw.env_read_lock(env["id"]):
            pass


def test_counter_is_a_plain_integer_after_use(env):
    for _ in range(3):
        with rw.env_read_lock(env["id"]):
            pass
    counter = env["mk"] / "locks" / f"rw-{env['id']}" / "counter"
    assert counter.read_text().strip().isdigit()
    assert int(counter.read_text()) == 3          # monotone, durable, atomic


def test_acquire_timeout_and_ticket_cleanup(env):
    w = _spawn(env, "write", "tw", hold=10)
    assert _wait_file(env["mk"] / "tw.acq", 10)
    t0 = time.monotonic()
    with pytest.raises(rw.EnvironmentLockTimeout) as ei:
        with rw.env_read_lock(env["id"], timeout=1.0):
            pass
    assert ei.value.code == "environment_read_lock_timeout"
    assert time.monotonic() - t0 >= 0.9           # honoured the total deadline
    # On an expired-deadline timeout the token is left as a DEAD orphan (Blocker 2 —
    # no new time budget to unlink): any leftover .r. token must be unlocked/dead.
    qdir = env["mk"] / "locks" / f"rw-{env['id']}" / "queue"
    for p in (p for p in qdir.iterdir() if ".r." in p.name) if qdir.exists() else []:
        fd = os.open(str(p), os.O_RDWR)
        try:
            assert rw._try_lock(fd), "leftover reader token is still locked (not dead)"
            rw._unlock(fd)
        finally:
            os.close(fd)
    _release(env, "tw")
    w.wait(10)


def _lockdir(env):
    return env["mk"] / "locks" / f"rw-{env['id']}"


def test_release_no_ticket_leak(env):
    with rw.env_read_lock(env["id"]):
        pass
    with rw.env_write_lock(env["id"]):
        pass
    qdir = _lockdir(env) / "queue"
    assert (list(qdir.iterdir()) if qdir.exists() else []) == []


def test_all_queue_mutation_under_mutex_source_scan():
    import inspect

    from agentnode_sdk import _env_rwlock as m
    clean = inspect.getsource(m._cleanup_ticket)
    # the only unlink of our own ticket happens inside the queue-mutex block
    assert clean.index("_safe_unlink(ticket_path)") > clean.index("_queue_mutex(")
    acq = inspect.getsource(m._acquire)
    assert "_reserve_ticket(" in acq and "_blocked(" in acq
    # _blocked / _reserve_ticket appear only after a `with _queue_mutex(` in _acquire
    for call in ("_reserve_ticket(", "_blocked("):
        assert acq.index(call) > acq.index("with _queue_mutex(")


def test_counter_rollback_fail_closed(env):
    # a live reader holds ticket 1 (counter == 1); force the counter BELOW it
    r = _spawn(env, "read", "rb", hold=30)
    try:
        assert _wait_file(env["mk"] / "rb.acq", 10)
        (_lockdir(env) / "counter").write_text("0")     # rollback below visible ticket 1
        with pytest.raises(rw.CounterRollbackError):
            with rw.env_read_lock(env["id"]):
                pass
    finally:
        _release(env, "rb")
        r.wait(10)


def test_malformed_queue_entry_fail_closed(env):
    (_lockdir(env) / "queue").mkdir(parents=True, exist_ok=True)
    (_lockdir(env) / "queue" / "garbage.txt").write_text("x")   # not a ticket name
    with pytest.raises(rw.QueueStateError):
        with rw.env_read_lock(env["id"]):
            pass


def test_ticket_gap_does_not_block(env):
    # simulate a crash after a durable counter bump but before token publication:
    # marker present, counter=5, no ticket files → next participant gets 6, not blocked.
    with rw.env_read_lock(env["id"]):     # init marker + counter
        pass
    (_lockdir(env) / "counter").write_text("5")
    t0 = time.monotonic()
    with rw.env_read_lock(env["id"], timeout=5):
        pass
    assert time.monotonic() - t0 < 3
    assert int((_lockdir(env) / "counter").read_text()) == 6


def test_three_readers_concurrent(env):
    r1 = _spawn(env, "read", "g1", hold=30)
    r2 = _spawn(env, "read", "g2", hold=30)
    r3 = _spawn(env, "read", "g3", hold=30)
    try:
        # all three must be able to hold simultaneously
        assert _wait_file(env["mk"] / "g1.acq", 10)
        assert _wait_file(env["mk"] / "g2.acq", 10)
        assert _wait_file(env["mk"] / "g3.acq", 10)
    finally:
        for p in ("g1", "g2", "g3"):
            _release(env, p)
        for r in (r1, r2, r3):
            r.wait(10)


def test_nested_reader_no_deadlock_behind_writer(env):
    import threading
    inner_ok = threading.Event()
    released = threading.Event()

    def holder():
        with rw.env_read_lock(env["id"]):          # R1 outermost
            time.sleep(0.6)                         # let the writer register + wait
            with rw.env_read_lock(env["id"]):       # nested R3 — must enter immediately
                inner_ok.set()
        released.set()

    t = threading.Thread(target=holder)
    t.start()
    time.sleep(0.2)
    w = _spawn(env, "write", "nd_w", hold=0.2)     # registers a ticket after R1, waits
    assert inner_ok.wait(10), "nested reader deadlocked behind the writer"
    assert released.wait(10)
    assert _wait_file(env["mk"] / "nd_w.acq", 10)  # writer enters after the outer release
    w.wait(10)
    t.join(10)


def test_nested_reader_takes_no_new_ticket(env):
    with rw.env_read_lock(env["id"]):
        qdir = _lockdir(env) / "queue"
        before = len(list(qdir.iterdir()))
        with rw.env_read_lock(env["id"]):
            assert len(list(qdir.iterdir())) == before   # no new interprocess ticket


def test_timeout_cleanup_no_new_budget(env):
    w = _spawn(env, "write", "cd_w", hold=1.5)
    assert _wait_file(env["mk"] / "cd_w.acq", 10)
    t0 = time.monotonic()
    with pytest.raises(rw.EnvironmentLockTimeout):
        with rw.env_read_lock(env["id"], timeout=0.4):
            pass
    assert time.monotonic() - t0 < 2.0             # returned ~at the deadline (no +10s)
    _release(env, "cd_w")
    w.wait(10)
    with rw.env_read_lock(env["id"]):              # next scanner removes any dead orphan
        pass
    qdir = _lockdir(env) / "queue"
    assert (list(qdir.iterdir()) if qdir.exists() else []) == []


def test_ticket_exhaustion_fail_closed(env):
    with rw.env_read_lock(env["id"]):              # init marker + counter
        pass
    (_lockdir(env) / "counter").write_text(str(rw._MAX_TICKET - 1))
    with rw.env_read_lock(env["id"]):              # reserves _MAX_TICKET — ok
        pass
    assert int((_lockdir(env) / "counter").read_text()) == rw._MAX_TICKET
    (_lockdir(env) / "counter").write_text(str(rw._MAX_TICKET))
    with pytest.raises(rw.TicketExhausted):
        with rw.env_read_lock(env["id"]):
            pass
    assert int((_lockdir(env) / "counter").read_text()) == rw._MAX_TICKET  # unchanged
    qdir = _lockdir(env) / "queue"
    assert (list(qdir.iterdir()) if qdir.exists() else []) == []           # no token


def test_counter_write_fault_keeps_old_value(env, monkeypatch):
    with rw.env_read_lock(env["id"]):              # counter -> 1
        pass
    import agentnode_sdk._fileutil as fu

    def boom(*a, **k):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(fu.os, "replace", boom)    # fault during the durable counter write
    with pytest.raises(OSError):
        with rw.env_read_lock(env["id"]):
            pass
    monkeypatch.undo()
    assert int((_lockdir(env) / "counter").read_text()) == 1               # fully old
    leftover = [p for p in _lockdir(env).iterdir() if p.name.startswith(".counter")]
    assert leftover == []                                                  # no partial temp


def test_max_visible_ticket_numeric_not_lexical(env):
    qdir = _lockdir(env) / "queue"
    qdir.mkdir(parents=True, exist_ok=True)
    for n in (2, 9, 10, 100):
        (qdir / f"{n:020d}.r.{'a' * 32}.lock").write_text("")
    assert rw._max_visible_ticket(_lockdir(env)) == 100     # not "9" > "100"


@pytest.mark.parametrize("name", [
    "bogus", "5.r.abc.lock", "0000000000000000000a.r." + "a" * 32 + ".lock",
    "-0000000000000000005.r." + "a" * 32 + ".lock",
    "00000000000000000005.x." + "a" * 32 + ".lock",
    "00000000000000000005.r." + "a" * 30 + ".lock",   # wrong uuid length
    "00000000000000000005.r." + "a" * 32,             # missing .lock
])
def test_malformed_queue_entry_fail_closed_unit(env, name):
    qdir = _lockdir(env) / "queue"
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / name).write_text("")
    with pytest.raises(rw.QueueStateError):
        rw._max_visible_ticket(_lockdir(env))


def test_process_reader_registry_count(env):
    eid = env["id"]
    assert rw._local_readers.get(eid, 0) == 0
    with rw.env_read_lock(eid):
        assert rw._local_readers.get(eid, 0) == 1          # outer
        with rw.env_read_lock(eid):
            assert rw._local_readers.get(eid, 0) == 1      # nested — still exactly 1
        assert rw._local_readers.get(eid, 0) == 1          # inner release — still 1
    assert rw._local_readers.get(eid, 0) == 0              # outer release — 0


def test_registry_cleaned_after_exception(env, monkeypatch):
    eid = env["id"]
    # force _blocked to raise during admission → registry must not leak
    monkeypatch.setattr(rw, "_reserve_ticket",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(RuntimeError):
        with rw.env_read_lock(eid):
            pass
    assert rw._local_readers.get(eid, 0) == 0
    assert rw._depths().get(eid, 0) == 0


def test_duplicate_ticket_number_fail_closed(env):
    qdir = _lockdir(env) / "queue"
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / f"{5:020d}.r.{'a' * 32}.lock").write_text("")
    (qdir / f"{5:020d}.w.{'b' * 32}.lock").write_text("")   # same numeric ticket
    with pytest.raises(rw.QueueStateError):
        rw._max_visible_ticket(_lockdir(env))
    with pytest.raises(rw.QueueStateError):                 # admission fail-closes too
        with rw.env_read_lock(env["id"]):
            pass


@pytest.mark.parametrize("target,expect_new", [
    ("write", False), ("fsync", False), ("replace", False), ("_fsync_dir", True),
])
def test_durable_counter_fault_matrix(env, monkeypatch, target, expect_new):
    with rw.env_read_lock(env["id"]):                      # counter -> 1
        pass
    import agentnode_sdk._fileutil as fu

    def boom(*a, **k):
        raise OSError("injected durable fault")

    if target == "_fsync_dir":
        monkeypatch.setattr(fu, "_fsync_dir", boom)        # fails AFTER the replace
    else:
        monkeypatch.setattr(fu.os, target, boom)
    with pytest.raises(OSError):
        with rw.env_read_lock(env["id"]):
            pass
    monkeypatch.undo()
    val = (_lockdir(env) / "counter").read_text().strip()
    assert val.isdigit()                                   # never empty/partial/non-canonical
    assert int(val) == (2 if expect_new else 1)            # fully-new or fully-old
    assert [p for p in _lockdir(env).iterdir() if p.name.startswith(".counter")] == []  # no temp


def test_missing_counter_after_init_fail_closed(env):
    with rw.env_read_lock(env["id"]):                      # init: counter + marker
        pass
    (_lockdir(env) / "counter").unlink()                   # deleted AFTER initialization
    with pytest.raises(rw.CounterStateError):
        with rw.env_read_lock(env["id"]):
            pass


def test_never_initialized_starts_at_zero(env):
    with rw.env_read_lock(env["id"]):
        pass
    assert int((_lockdir(env) / "counter").read_text()) == 1
    assert (_lockdir(env) / "initialized").exists()


def test_long_run_success_release_no_orphan(env):
    # A successful context whose protected section outlives the ACQUISITION deadline
    # must still clean up its own ticket (Blocker 5 — short cleanup budget on success),
    # not leave a dead orphan on every long run.
    with rw.env_read_lock(env["id"], timeout=0.5):
        time.sleep(0.9)                     # exceed the acquisition deadline while held
    qdir = _lockdir(env) / "queue"
    assert (list(qdir.iterdir()) if qdir.exists() else []) == []


def test_marker_present_counter_absent_fail_closed(env):
    with rw.env_read_lock(env["id"]):       # init marker + counter
        pass
    (_lockdir(env) / "counter").unlink()     # counter gone, marker remains
    with pytest.raises(rw.CounterStateError):
        with rw.env_read_lock(env["id"]):
            pass


def test_counter_without_marker_fail_closed(env):
    _lockdir(env).mkdir(parents=True, exist_ok=True)
    (_lockdir(env) / "counter").write_text("3")   # counter, no marker
    with pytest.raises(rw.CounterStateError):
        with rw.env_read_lock(env["id"]):
            pass


def test_queue_entry_without_marker_fail_closed(env):
    qdir = _lockdir(env) / "queue"
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / f"{1:020d}.r.{'a' * 32}.lock").write_text("")   # queue entry, no marker
    with pytest.raises(rw.QueueStateError):
        with rw.env_read_lock(env["id"]):
            pass


@pytest.mark.parametrize("kind", ["dir", "badcontent"])
def test_marker_malformed_fail_closed(env, kind):
    _lockdir(env).mkdir(parents=True, exist_ok=True)
    marker = _lockdir(env) / "initialized"
    if kind == "dir":
        marker.mkdir()
    else:
        marker.write_text("2")               # unexpected content
    with pytest.raises((rw.QueueStateError, rw.CounterStateError)):
        with rw.env_read_lock(env["id"]):
            pass


def _boom_on(real, target):
    # fail os.replace only when writing a specific destination file (robust to
    # unrelated os.replace calls elsewhere in the shared test process).
    def f(src, dst, *a, **k):
        if os.path.basename(str(dst)) == target:
            raise OSError(f"{target} write fault")
        return real(src, dst, *a, **k)
    return f


def test_marker_write_fault_leaves_dir_pristine(env):
    import agentnode_sdk._fileutil as fu
    real = fu.os.replace
    fu.os.replace = _boom_on(real, "initialized")
    try:
        with pytest.raises(OSError):
            with rw.env_read_lock(env["id"]):
                pass
    finally:
        fu.os.replace = real                              # restore ONLY os.replace
    assert not (_lockdir(env) / "initialized").exists()   # pristine — nothing published
    assert not (_lockdir(env) / "counter").exists()
    with rw.env_read_lock(env["id"]):                     # clean retry initializes
        pass
    assert int((_lockdir(env) / "counter").read_text()) == 1


def test_marker_then_counter_crash_fail_closed(env):
    import agentnode_sdk._fileutil as fu
    real = fu.os.replace
    fu.os.replace = _boom_on(real, "counter")
    try:
        with pytest.raises(OSError):
            with rw.env_read_lock(env["id"]):
                pass
    finally:
        fu.os.replace = real
    assert (_lockdir(env) / "initialized").exists()       # marker published
    assert not (_lockdir(env) / "counter").exists()       # counter not
    with pytest.raises(rw.CounterStateError):             # fail-closed, no silent reset
        with rw.env_read_lock(env["id"]):
            pass


# ---------------------------------------------------------------------------
# POSIX fork safety (skip on Windows; run in the eventual Linux CI). These prove
# a LIVING forked child does not extend the parent's inherited OS locks.
# ---------------------------------------------------------------------------
def _fork_child_stays_alive_then_parent_releases(env, hold_ctx):
    alive_r, alive_w = os.pipe()
    exit_r, exit_w = os.pipe()
    with hold_ctx:
        pid = os.fork()
        if pid == 0:                                       # child: do NOT acquire; live
            os.close(alive_r)
            os.close(exit_w)
            os.write(alive_w, b"a")
            os.read(exit_r, 1)                             # stay alive until told
            os._exit(0)
        os.close(alive_w)
        os.close(exit_r)
        assert os.read(alive_r, 1) == b"a"                 # child up while we hold
    return pid, alive_r, exit_w


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX only")
def test_fork_living_child_does_not_extend_reader(env):
    pid, alive_r, exit_w = _fork_child_stays_alive_then_parent_releases(
        env, rw.env_read_lock(env["id"]))
    entered = False
    try:
        with rw.env_write_lock(env["id"], timeout=10):     # must enter while child lives
            entered = True
    finally:
        os.write(exit_w, b"x")
        os.waitpid(pid, 0)
        for fd in (alive_r, exit_w):
            try:
                os.close(fd)
            except OSError:
                pass
    assert entered, "living forked child extended the parent's reader lock"


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX only")
def test_fork_living_child_does_not_hold_queue_mutex(env):
    env_dir = rw._env_dir(env["id"])
    pid, alive_r, exit_w = _fork_child_stays_alive_then_parent_releases(
        env, rw._queue_mutex(env_dir, time.monotonic() + 60, "r"))
    try:
        with rw.env_read_lock(env["id"], timeout=10):      # needs the mutex → must work
            pass
    finally:
        os.write(exit_w, b"x")
        os.waitpid(pid, 0)
        for fd in (alive_r, exit_w):
            try:
                os.close(fd)
            except OSError:
                pass


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX only")
def test_fork_child_own_reader_blocks_writer(env):
    ready_r, ready_w = os.pipe()
    exit_r, exit_w = os.pipe()
    pid = os.fork()
    if pid == 0:                                            # child acquires its OWN reader
        os.close(ready_r)
        os.close(exit_w)
        with rw.env_read_lock(env["id"]):
            os.write(ready_w, b"a")
            os.read(exit_r, 1)
        os._exit(0)
    os.close(ready_w)
    os.close(exit_r)
    assert os.read(ready_r, 1) == b"a"
    with pytest.raises(rw.EnvironmentLockTimeout):          # blocked while child reads
        with rw.env_write_lock(env["id"], timeout=1.5):
            pass
    os.write(exit_w, b"x")
    os.waitpid(pid, 0)
    with rw.env_write_lock(env["id"], timeout=10):          # enters after child releases
        pass
    for fd in (ready_r, exit_w):
        try:
            os.close(fd)
        except OSError:
            pass


@contextlib.contextmanager
def _fault_marker_phase(phase):
    # Inject a fault at a specific atomic_write_json phase, but ONLY during the MARKER
    # write (scoped via a flag set around the marker's atomic_write_json call).
    import agentnode_sdk._fileutil as fu
    saved = (fu.atomic_write_json, fu.os.write, fu.os.fsync, fu.os.replace, fu._fsync_dir)
    real_awj = fu.atomic_write_json
    flag = {"on": False}

    def awj(path, data, **k):
        if os.path.basename(str(path)) == "initialized":
            flag["on"] = True
            try:
                return real_awj(path, data, **k)
            finally:
                flag["on"] = False
        return real_awj(path, data, **k)

    def mk(realop, name):
        def op(*a, **k):
            if flag["on"] and name == phase:
                raise OSError(f"{name} fault")
            return realop(*a, **k)
        return op

    fu.atomic_write_json = awj
    fu.os.write = mk(fu.os.write, "write")
    fu.os.fsync = mk(fu.os.fsync, "fsync")
    fu.os.replace = mk(fu.os.replace, "replace")
    fu._fsync_dir = mk(fu._fsync_dir, "dirsync")
    try:
        yield
    finally:
        fu.atomic_write_json, fu.os.write, fu.os.fsync, fu.os.replace, fu._fsync_dir = saved


@pytest.mark.parametrize("phase,marker_after", [
    ("write", False), ("fsync", False), ("replace", False), ("dirsync", True),
])
def test_marker_durable_fault_matrix(env, phase, marker_after):
    with _fault_marker_phase(phase):
        with pytest.raises(OSError):
            with rw.env_read_lock(env["id"]):
                pass
    assert (_lockdir(env) / "initialized").exists() is marker_after
    assert not (_lockdir(env) / "counter").exists()
    assert [p for p in _lockdir(env).iterdir() if p.name.startswith(".initialized")] == []
    if marker_after:                                  # marker published, counter not
        with pytest.raises(rw.CounterStateError):     # → fail-closed, no silent reset
            with rw.env_read_lock(env["id"]):
                pass
    else:                                             # pristine → clean retry inits
        with rw.env_read_lock(env["id"]):
            pass
        assert int((_lockdir(env) / "counter").read_text()) == 1


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
@pytest.mark.parametrize("where", ["env", "queue", "mutex"])
def test_redirected_lock_path_fail_closed(env, tmp_path, where):
    lockdir = _lockdir(env)
    if where == "env":
        lockdir.parent.mkdir(parents=True, exist_ok=True)
        real = tmp_path / "real_env"
        real.mkdir()
        os.symlink(real, lockdir)
    elif where == "queue":
        lockdir.mkdir(parents=True, exist_ok=True)
        real = tmp_path / "real_q"
        real.mkdir()
        os.symlink(real, lockdir / "queue")
    else:
        (lockdir / "queue").mkdir(parents=True, exist_ok=True)
        real = tmp_path / "real_m"
        real.write_text("")
        os.symlink(real, lockdir / "queue-mutex.lock")
    with pytest.raises(rw.QueueStateError):
        with rw.env_read_lock(env["id"]):
            pass


def test_release_short_cleanup_when_mutex_busy(env):
    import threading
    env_dir = rw._env_dir(env["id"])
    stop = threading.Event()
    held = threading.Event()

    def hog():
        with rw._queue_mutex(env_dir, time.monotonic() + 30, "r"):
            held.set()
            stop.wait(10)

    cm = rw.env_read_lock(env["id"])
    cm.__enter__()
    th = threading.Thread(target=hog)
    th.start()
    assert held.wait(10)                              # queue-mutex now busy
    t0 = time.monotonic()
    cm.__exit__(None, None, None)                     # release: cleanup can't get mutex
    elapsed = time.monotonic() - t0
    stop.set()
    th.join(10)
    assert elapsed < 1.0, f"successful release blocked too long ({elapsed:.2f}s)"
    with rw.env_read_lock(env["id"]):                 # next scanner removes any orphan
        pass


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX only")
def test_fork_guard_blocks_fork_during_open_track(env):
    import threading
    in_hook = threading.Event()
    release = threading.Event()
    forked = threading.Event()
    env_dir = rw._env_dir(env["id"])

    def hook():
        in_hook.set()
        release.wait(10)                              # pause INSIDE the fork guard

    def open_section():
        rw._after_track_hook = hook
        try:
            with rw._queue_mutex(env_dir, time.monotonic() + 30, "r"):
                pass
        finally:
            rw._after_track_hook = None

    ta = threading.Thread(target=open_section)
    ta.start()
    assert in_hook.wait(10)                           # thread A paused inside the guard

    def do_fork():
        pid = os.fork()
        if pid == 0:
            os._exit(0)
        os.waitpid(pid, 0)
        forked.set()

    tb = threading.Thread(target=do_fork)
    tb.start()
    assert not forked.wait(1.0)                       # fork BLOCKED by the guard
    release.set()                                     # let A finish open+track
    assert forked.wait(10)                            # fork now proceeds
    ta.join(10)
    tb.join(10)


def test_writer_bounded_admission_under_reader_stream(env):
    # A continuous stream of short readers must not starve a waiting writer.
    stop = env["mk"] / "stop_stream"
    procs = []

    def stream():
        i = 0
        while not stop.exists() and i < 400:
            p = _spawn(env, "read", f"s{i}", hold=0.05)
            procs.append(p)
            (env["mk"] / (f"s{i}" + ".release")).write_text("go")
            i += 1
            time.sleep(0.02)

    import threading
    th = threading.Thread(target=stream)
    th.start()
    time.sleep(0.3)  # stream running
    t0 = time.monotonic()
    with rw.env_write_lock(env["id"]):
        admitted = time.monotonic() - t0
    stop.write_text("x")
    th.join(20)
    for p in procs:
        try:
            p.wait(10)
        except Exception:
            p.kill()
    assert admitted < 30, f"writer starved under reader stream ({admitted:.1f}s)"
