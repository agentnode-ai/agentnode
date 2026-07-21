"""FIFO-ticket inter-process reader/writer lock for the shared Python environment
(Agent-Exec A1-E-Lock).

Readers = host-agent dispatch/execution; the single writer = an install into the
shared interpreter. Keyed by ``env_id`` (the identity M1 uses).

Design — a crash-safe FIFO ticket queue with provable bounded writer progress:

* A monotone **ticket** is allocated under a short exclusive ``queue-mutex``; the
  counter is durable (atomic + fsync via ``_fileutil.atomic_write_json``) and
  **fail-closed** on any corruption or **rollback** below the highest visible ticket
  (never reset, never reused).
* Each participant creates ``queue/<020d ticket>.<r|w>.<uuid>.lock`` and holds an
  exclusive OS lock on it — that held lock *is* the liveness proof.
* Service order is strictly ascending ticket. A **reader** proceeds when no lower
  LIVE writer exists (readers run concurrently); a **writer** proceeds when no lower
  live participant exists. Once a writer holds ticket N, later readers (> N) cannot
  overtake it → the writer waits only for the finite set of live tickets < N.
* **Orphans** (crashed holders) are detected purely by lock-acquirability — no PID,
  no time heuristic. Registration, scans and ALL cleanup run under the queue-mutex,
  so a scanned ticket is always locked-live or a genuine orphan, and a malformed /
  non-regular queue entry is **fail-closed** (never silently skipped).
* A **single monotone total deadline** bounds the entire acquisition (mutex waits +
  admission); no sub-step starts a fresh deadline.
* **Same-process read→write is refused** (``ReadToWriteUpgradeForbidden``) before any
  blocking OS-lock operation.

Advisory OS locks auto-release on process exit, so a crash never leaks the lock —
only harmless orphan files, cleaned on the next scan.
"""
from __future__ import annotations

import os
import re
import stat
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

_POLL = 0.005
_ADMIT_POLL = 0.01
_CLEANUP_MUTEX_TIMEOUT = 10.0
DEFAULT_ACQUIRE_TIMEOUT = 300.0

_TICKET_RE = re.compile(r"^(\d{20})\.([rw])\.([0-9a-f]{32})\.lock$")
_COUNTER_RE = re.compile(r"^(0|[1-9][0-9]{0,17})\n?$")  # canonical, bounded, no leading zeros

# ---------------------------------------------------------------------------
# Platform per-file exclusive OS locks: non-blocking try-lock + unlock.
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass


class ReadToWriteUpgradeForbidden(RuntimeError):
    """A writer was requested from a process that already holds a reader for the
    same environment — refused (never a blocking upgrade / deadlock)."""

    code = "read_to_write_upgrade_forbidden"


class EnvironmentLockTimeout(RuntimeError):
    """A read/write acquisition exceeded its single monotone total deadline."""

    def __init__(self, ttype: str):
        self.code = (
            "environment_write_lock_timeout" if ttype == "w"
            else "environment_read_lock_timeout"
        )
        super().__init__(self.code)


class CounterStateError(RuntimeError):
    """The ticket counter is corrupted/tampered — fail-closed (never reused)."""

    code = "environment_lock_counter_corrupt"


class CounterRollbackError(RuntimeError):
    """The counter is below the highest visible ticket (rollback) — fail-closed."""

    code = "environment_lock_counter_rollback"


class QueueStateError(RuntimeError):
    """A malformed / non-regular entry exists in the controlled queue dir —
    fail-closed (never silently skipped, which could hide an earlier participant)."""

    code = "environment_lock_queue_corrupt"


# ---------------------------------------------------------------------------
# Process-wide reader registry (with the interprocess lock this also covers the
# same-process cross-thread upgrade case).
# ---------------------------------------------------------------------------
_local_lock = threading.Lock()
_local_readers: dict[str, int] = {}


def _register_local_reader(env_id: str) -> None:
    with _local_lock:
        _local_readers[env_id] = _local_readers.get(env_id, 0) + 1


def _unregister_local_reader(env_id: str) -> None:
    with _local_lock:
        n = _local_readers.get(env_id, 0) - 1
        if n <= 0:
            _local_readers.pop(env_id, None)
        else:
            _local_readers[env_id] = n


def _local_reader_active(env_id: str) -> bool:
    with _local_lock:
        return _local_readers.get(env_id, 0) > 0


# ---------------------------------------------------------------------------
# Paths + queue-mutex (bounded by the caller's single total deadline)
# ---------------------------------------------------------------------------
def _env_dir(env_id: str) -> Path:
    from agentnode_sdk.config import config_dir
    d = config_dir() / "locks" / f"rw-{env_id}"
    (d / "queue").mkdir(parents=True, exist_ok=True)
    return d


@contextmanager
def _queue_mutex(env_dir: Path, deadline: float, ttype: str):
    path = env_dir / "queue-mutex.lock"
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise EnvironmentLockTimeout(ttype)
            time.sleep(_POLL)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# Counter (strict + durable + rollback-checked) — all under the queue-mutex.
# ---------------------------------------------------------------------------
def _read_counter(env_dir: Path) -> int:
    cpath = env_dir / "counter"
    try:
        raw = cpath.read_text(encoding="ascii")
    except FileNotFoundError:
        return 0
    except (OSError, UnicodeDecodeError) as exc:
        raise CounterStateError("counter unreadable / not ASCII") from exc
    if len(raw) > 24 or _COUNTER_RE.match(raw) is None:
        raise CounterStateError("counter is not a canonical non-negative integer")
    return int(raw.strip())


def _write_counter_durable(env_dir: Path, value: int) -> None:
    from agentnode_sdk._fileutil import atomic_write_json
    atomic_write_json(env_dir / "counter", value, durable=True)  # json.dumps(int) == the digits


def _iter_tickets(qdir: Path):
    """Yield ``(num, type, path)`` for every queue entry, FAIL-CLOSED on a malformed
    name or a non-regular file. Runs only under the queue-mutex."""
    for name in os.listdir(qdir):
        m = _TICKET_RE.match(name)
        if m is None:
            raise QueueStateError("malformed queue entry name")
        p = qdir / name
        try:
            st = os.lstat(str(p))
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(st.st_mode):        # reject symlink/junction/dir/etc.
            raise QueueStateError("non-regular queue entry")
        yield int(m.group(1)), m.group(2), p


def _max_visible_ticket(env_dir: Path) -> int:
    hi = 0
    for num, _t, _p in _iter_tickets(env_dir / "queue"):
        if num > hi:
            hi = num
    return hi


def _reserve_ticket(env_dir: Path, ttype: str) -> int:
    """Under the queue-mutex: strict counter read → rollback check vs the highest
    visible ticket → durable counter write → return the new ticket number."""
    counter = _read_counter(env_dir)
    if counter < _max_visible_ticket(env_dir):
        raise CounterRollbackError("counter is below the highest visible ticket")
    nxt = counter + 1
    _write_counter_durable(env_dir, nxt)
    return nxt


# ---------------------------------------------------------------------------
# Liveness / orphan classification (under the queue-mutex)
# ---------------------------------------------------------------------------
def _safe_unlink(p: Path) -> None:
    try:
        os.unlink(str(p))
    except OSError:
        pass


def _is_live(tpath: Path) -> bool:
    """Under the queue-mutex: True if the ticket has a live holder. A dead ticket's
    lock is acquirable → remove it (close before unlink for Windows; the queue-mutex
    guarantees no concurrent scanner in the close→unlink window). Fail-safe: on any
    unexpected error, treat as live (never wrongly admit past a possibly-live lower
    ticket)."""
    try:
        fd = os.open(str(tpath), os.O_RDWR)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    try:
        if _try_lock(fd):
            _unlock(fd)
            os.close(fd)
            _safe_unlink(tpath)     # under the mutex → no concurrent observer
            return False
        os.close(fd)
        return True
    except OSError:
        try:
            os.close(fd)
        except OSError:
            pass
        return True


def _blocked(env_dir: Path, my_n: int, ttype: str) -> bool:
    """Under the queue-mutex: is any lower LIVE ticket a blocker for me?
    writer → any lower live participant blocks; reader → only a lower live writer."""
    for num, otype, path in _iter_tickets(env_dir / "queue"):
        if num >= my_n:
            continue
        if not _is_live(path):
            continue
        if ttype == "w" or otype == "w":
            return True
    return False


# ---------------------------------------------------------------------------
# Acquire / release
# ---------------------------------------------------------------------------
def _cleanup_ticket(env_dir: Path, fd: int, ticket_path: Path | None) -> None:
    """Release + remove OUR OWN ticket (unique uuid path). Always leaves the token
    DEAD (unlocked) first, then unlinks under the queue-mutex (best-effort, short
    bound). A leftover DEAD token is harmless — the next scanner removes it under its
    own mutex — so a failed unlink is never a safety problem."""
    if fd >= 0:
        _unlock(fd)
        try:
            os.close(fd)             # token now dead (a scanner would classify it dead)
        except OSError:
            pass
    if ticket_path is None:
        return
    try:
        with _queue_mutex(env_dir, time.monotonic() + _CLEANUP_MUTEX_TIMEOUT, "r"):
            _safe_unlink(ticket_path)     # unlink only under the mutex
    except EnvironmentLockTimeout:
        pass  # leave the DEAD token for the next scanner (safe)


@contextmanager
def _acquire(env_id: str, ttype: str, timeout: float):
    # Same-process read->write is refused BEFORE any blocking OS-lock operation.
    if ttype == "w" and _local_reader_active(env_id):
        raise ReadToWriteUpgradeForbidden(
            "cannot acquire the environment write-lock while this process holds a reader"
        )
    deadline = time.monotonic() + timeout   # single monotone total deadline
    env_dir = _env_dir(env_id)
    fd = -1
    ticket_path: Path | None = None
    registered_local = False
    try:
        with _queue_mutex(env_dir, deadline, ttype):
            n = _reserve_ticket(env_dir, ttype)
            ticket_path = env_dir / "queue" / f"{n:020d}.{ttype}.{uuid.uuid4().hex}.lock"
            fd = os.open(str(ticket_path), os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            if not _try_lock(fd):
                raise RuntimeError("could not lock a freshly created ticket")
        if ttype == "r":
            _register_local_reader(env_id)
            registered_local = True
        while True:
            with _queue_mutex(env_dir, deadline, ttype):
                if not _blocked(env_dir, n, ttype):
                    break
            if time.monotonic() >= deadline:
                raise EnvironmentLockTimeout(ttype)
            time.sleep(_ADMIT_POLL)
        yield
    finally:
        if registered_local:
            _unregister_local_reader(env_id)
        _cleanup_ticket(env_dir, fd, ticket_path)


@contextmanager
def env_read_lock(env_id: str, timeout: float = DEFAULT_ACQUIRE_TIMEOUT):
    """Acquire the shared (reader) side for one host-agent execution. Held for the
    entire protected section; released on exit. Reentrant across readers. Raises
    :class:`EnvironmentLockTimeout` if acquisition exceeds *timeout*."""
    with _acquire(env_id, "r", timeout):
        yield


@contextmanager
def env_write_lock(env_id: str, timeout: float = DEFAULT_ACQUIRE_TIMEOUT):
    """Acquire the exclusive (writer) side for one install into the environment.
    Refused (``ReadToWriteUpgradeForbidden``) if this process holds a reader. Raises
    :class:`EnvironmentLockTimeout` if acquisition exceeds *timeout*."""
    with _acquire(env_id, "w", timeout):
        yield
