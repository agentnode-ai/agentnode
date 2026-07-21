"""A1-E-Lock: FIFO-ticket inter-process reader/writer lock."""
from __future__ import annotations

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
    # our reader ticket was cleaned up on timeout (no leftover .r. ticket leaks)
    qdir = env["mk"] / "locks" / f"rw-{env['id']}" / "queue"
    readers = [p for p in qdir.iterdir() if ".r." in p.name] if qdir.exists() else []
    assert readers == []
    _release(env, "tw")
    w.wait(10)


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
