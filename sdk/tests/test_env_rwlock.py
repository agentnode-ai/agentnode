"""A1-E-Lock: FIFO-ticket inter-process reader/writer lock."""
from __future__ import annotations

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
    # counter=5, no ticket files → the next participant gets 6 and is not blocked.
    _lockdir(env).mkdir(parents=True, exist_ok=True)
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
    _lockdir(env).mkdir(parents=True, exist_ok=True)
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


@pytest.mark.skipif(not hasattr(os, "fork"), reason="fork is POSIX-only")
def test_fork_child_drops_inherited_ticket_fd(env):
    # Parent holds a reader (an open, tracked ticket FD). After fork the child's
    # after_in_child handler must have CLOSED the inherited FD and cleared the
    # registries so the child never keeps the parent's interprocess lock alive.
    with rw.env_read_lock(env["id"]):
        assert len(rw._open_ticket_fds) >= 1
        r, w = os.pipe()
        pid = os.fork()
        if pid == 0:                                       # child
            ok = (len(rw._open_ticket_fds) == 0 and rw._local_readers == {}
                  and rw._depths() == {})
            os.write(w, b"1" if ok else b"0")
            os._exit(0)
        os.close(w)
        seen = os.read(r, 1)
        os.waitpid(pid, 0)
        os.close(r)
        assert seen == b"1"


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
