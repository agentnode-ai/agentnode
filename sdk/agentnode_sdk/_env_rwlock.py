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
DEFAULT_ACQUIRE_TIMEOUT = 300.0

# One consistent value range for the counter regex, the ticket-name width, and the
# exhaustion bound: max allocatable ticket = 10**18 - 1 (18 nines), which always fits
# the 20-digit zero-padded ticket name, so the primitive never writes a counter it
# would later reject as corrupt.
_MAX_TICKET = 10**18 - 1
_TICKET_RE = re.compile(r"^(\d{20})\.([rw])\.([0-9a-f]{32})\.lock$")
_COUNTER_RE = re.compile(r"^(0|[1-9][0-9]{0,17})\n?$")  # 0 .. 10**18-1, no leading zeros

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


class TicketExhausted(RuntimeError):
    """The ticket space (``_MAX_TICKET``) is exhausted — fail-closed before any
    counter change or token creation."""

    code = "environment_lock_ticket_exhausted"


# ---------------------------------------------------------------------------
# Process-wide reader registry (with the interprocess lock this also covers the
# same-process cross-thread upgrade case).
# ---------------------------------------------------------------------------
_local_lock = threading.Lock()
_local_readers: dict[str, int] = {}

# All ticket FDs currently held by THIS process. On POSIX ``fork`` these FDs are
# duplicated into the child (a shared open-file-description), so a child that inherits
# a parent's ticket lock would keep the interprocess lock alive after the parent
# releases → a writer would block forever. The fork handler closes them in the child.
_fd_lock = threading.Lock()
_open_ticket_fds: set[int] = set()


def _track_fd(fd: int) -> None:
    with _fd_lock:
        _open_ticket_fds.add(fd)


def _untrack_fd(fd: int) -> None:
    with _fd_lock:
        _open_ticket_fds.discard(fd)


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
        # Distinguish a never-initialized dir from a counter DELETED after init: an
        # ``initialized`` marker (written on first reservation) means a counter once
        # existed, so a missing counter is corruption — never a silent reset to 0.
        if (env_dir / "initialized").exists():
            raise CounterStateError("counter missing after initialization")
        return 0
    except (OSError, UnicodeDecodeError) as exc:
        raise CounterStateError("counter unreadable / not ASCII") from exc
    if len(raw) > 24 or _COUNTER_RE.match(raw) is None:
        raise CounterStateError("counter is not a canonical non-negative integer")
    return int(raw.strip())


def _write_counter_durable(env_dir: Path, value: int) -> None:
    from agentnode_sdk._fileutil import atomic_write_json
    atomic_write_json(env_dir / "counter", value, durable=True)  # json.dumps(int) == the digits


def _ensure_initialized(env_dir: Path) -> None:
    marker = env_dir / "initialized"
    if not marker.exists():
        from agentnode_sdk._fileutil import atomic_write_json
        atomic_write_json(marker, 1, durable=True)


def _scan_tickets(qdir: Path) -> list[tuple[int, str, Path]]:
    """Return ``(num, type, path)`` for every queue entry, FAIL-CLOSED on a malformed
    name, a non-regular file, OR a DUPLICATE numeric ticket number. A duplicate number
    (any type/uuid, live or orphan) would let equal-numbered participants ignore each
    other and break FIFO exclusivity — so it is refused, never resolved by picking a
    winner. Runs only under the queue-mutex."""
    out: list[tuple[int, str, Path]] = []
    seen: set[int] = set()
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
        num = int(m.group(1))
        if num in seen:
            raise QueueStateError("duplicate ticket number")
        seen.add(num)
        out.append((num, m.group(2), p))
    return out


def _max_visible_ticket(env_dir: Path) -> int:
    return max((num for num, _t, _p in _scan_tickets(env_dir / "queue")), default=0)


def _reserve_ticket(env_dir: Path, ttype: str) -> int:
    """Under the queue-mutex: strict counter read → rollback check vs the highest
    visible ticket → exhaustion check → durable counter write → new ticket number."""
    counter = _read_counter(env_dir)
    if counter < _max_visible_ticket(env_dir):
        raise CounterRollbackError("counter is below the highest visible ticket")
    if counter >= _MAX_TICKET:                       # next would exceed the range
        raise TicketExhausted("ticket space exhausted")  # no counter change, no token
    nxt = counter + 1
    _write_counter_durable(env_dir, nxt)
    _ensure_initialized(env_dir)     # mark the dir initialized (counter loss → fail-closed)
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
    """Under the queue-mutex: True if the ticket has a live holder.

    Honest orphan contract (a lock-through-unlink is not achievable on Windows, where
    an open+locked file cannot be unlinked): classification happens ONLY under the
    queue-mutex; a successful non-blocking try-lock proves no protocol-conformant
    participant holds the token and — because the mutex means no other scanner runs
    concurrently — we then unlock, close, and unlink under the same mutex. Ticket
    names are never reused (unique uuid). On an unlink failure a DEAD orphan remains,
    safely removed by the next scanner. No claim is made against deliberate external
    filesystem manipulation outside the lock protocol. Fail-safe: on any unexpected
    error, treat as live (never wrongly admit past a possibly-live lower ticket)."""
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
    for num, otype, path in _scan_tickets(env_dir / "queue"):
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
def _cleanup_ticket(env_dir: Path, fd: int, ticket_path: Path | None, deadline: float) -> None:
    """Release + remove OUR OWN ticket, bounded by the ORIGINAL total deadline (never
    a fresh budget). Always leaves the token DEAD (unlocked) first; unlinks only under
    the queue-mutex. If the deadline is already past, do NOT acquire another blocking
    mutex — leave the DEAD token as a safely-cleanable orphan for the next scanner."""
    if fd >= 0:
        _untrack_fd(fd)
        _unlock(fd)
        try:
            os.close(fd)             # token now dead (a scanner would classify it dead)
        except OSError:
            pass
    if ticket_path is None or time.monotonic() >= deadline:
        return                        # no new time budget after the total deadline
    try:
        with _queue_mutex(env_dir, deadline, "r"):
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
    try:
        with _queue_mutex(env_dir, deadline, ttype):
            n = _reserve_ticket(env_dir, ttype)
            ticket_path = env_dir / "queue" / f"{n:020d}.{ttype}.{uuid.uuid4().hex}.lock"
            fd = os.open(str(ticket_path), os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            if not _try_lock(fd):
                raise RuntimeError("could not lock a freshly created ticket")
            _track_fd(fd)   # so a forked child can close this inherited lock FD
        while True:
            with _queue_mutex(env_dir, deadline, ttype):
                if not _blocked(env_dir, n, ttype):
                    break
            if time.monotonic() >= deadline:
                raise EnvironmentLockTimeout(ttype)
            time.sleep(_ADMIT_POLL)
        yield
    finally:
        _cleanup_ticket(env_dir, fd, ticket_path, deadline)


# ---------------------------------------------------------------------------
# Same-thread reader reentrancy (avoids the nested-reader-behind-a-writer deadlock:
# a nested reader in the same thread reuses the outer interprocess ticket — it never
# takes a NEW ticket that would queue behind a waiting writer while the outer ticket
# is still held by this very thread).
# ---------------------------------------------------------------------------
_thread_local = threading.local()


def _depths() -> dict[str, int]:
    d = getattr(_thread_local, "depths", None)
    if d is None:
        d = {}
        _thread_local.depths = d
    return d


def _reset_after_fork() -> None:
    # A forked child must not inherit the parent's ticket LOCKS or its in-memory
    # registries (the design spawns; this is belt-and-suspenders). We CLOSE every
    # inherited ticket FD so the child stops extending the parent's shared open-file-
    # description locks (else a writer would block after the parent releases). We do
    # NOT unlink queue entries — they belong to the parent. Runs single-threaded in
    # the child; access the sets WITHOUT the (possibly fork-inherited-locked) mutexes.
    for fd in list(_open_ticket_fds):
        try:
            os.close(fd)
        except OSError:
            pass
    _open_ticket_fds.clear()
    _local_readers.clear()
    _thread_local.depths = {}


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_after_fork)


@contextmanager
def env_read_lock(env_id: str, timeout: float = DEFAULT_ACQUIRE_TIMEOUT):
    """Acquire the shared (reader) side for one host-agent execution. Held for the
    entire protected section; released on exit.

    Same-thread nesting is reentrant WITHOUT a new interprocess ticket (only the
    outermost reader owns the ticket; only the outermost release frees it), so a
    nested reader can never deadlock behind a writer waiting on this thread's outer
    reader. A different thread in the same process is an independent reader. Raises
    :class:`EnvironmentLockTimeout` if acquisition exceeds *timeout*."""
    depths = _depths()
    if depths.get(env_id, 0) > 0:          # nested same-thread reader — reuse the ticket
        depths[env_id] += 1
        try:
            yield
        finally:
            depths[env_id] -= 1
            if depths[env_id] <= 0:
                depths.pop(env_id, None)
        return
    with _acquire(env_id, "r", timeout):   # outermost reader — take the interprocess ticket
        depths[env_id] = 1
        _register_local_reader(env_id)     # process-wide (for the write refusal)
        try:
            yield
        finally:
            _unregister_local_reader(env_id)
            depths.pop(env_id, None)


@contextmanager
def env_write_lock(env_id: str, timeout: float = DEFAULT_ACQUIRE_TIMEOUT):
    """Acquire the exclusive (writer) side for one install into the environment.
    Refused (``ReadToWriteUpgradeForbidden``) if this process holds a reader. Raises
    :class:`EnvironmentLockTimeout` if acquisition exceeds *timeout*."""
    with _acquire(env_id, "w", timeout):
        yield
