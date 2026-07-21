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

THREAT MODEL (symlinks / reparse points): this primitive fail-closes on a STATICALLY
present symlink/reparse point at the env dir, queue dir, queue-mutex, counter, and
marker (a redirected mutex could otherwise split participants into different sync
domains). It does NOT claim to be race-safe against *deliberate same-user* filesystem
manipulation: a check→open TOCTOU window remains, and defending a same-OS-user
attacker is out of scope for A1-E-Lock (that is the sandbox's job). Ordinary
non-regular files are rejected; a hostile same-user swap between check and open is a
documented, accepted limitation.
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
# On a SUCCESSFUL release the acquisition deadline is typically long past (the
# protected section ran a while), so cleanup uses a VERY short internal budget to
# unlink our own ticket — avoiding a dead-orphan on every long run — without adding a
# multi-second tail latency to a normal agent end. If the queue-mutex is not available
# within this budget, the caller returns immediately and leaves a dead orphan for the
# next scanner.
_CLEANUP_BUDGET = 0.2

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
_all_lock_fds: set[int] = set()

# Fork barrier: every open→track and untrack→close transition of a lock FD runs under
# this guard, and the fork ``before`` handler acquires it — so a fork can never
# interleave inside those transitions (which would leave the child a shared,
# untracked open-file-description its handler could not close, defeating crash-safety).
# An RLock (not a plain Lock) is REQUIRED: a fork raised in the SAME thread while it
# holds the guard (a signal handler / hook / future callback) would otherwise make the
# ``before`` handler re-acquire and self-deadlock.
_fork_guard = threading.RLock()


def _track_fd(fd: int) -> None:
    with _fd_lock:
        _all_lock_fds.add(fd)


def _untrack_fd(fd: int) -> None:
    with _fd_lock:
        _all_lock_fds.discard(fd)


# Optional test hooks: called (if set) INSIDE the fork guard — after open+track, and
# after untrack (before close) — so a test can attempt a concurrent fork and prove it
# blocks during both the open-side and release-side transitions.
_after_track_hook = None
_before_close_hook = None


def _open_tracked(path: Path, flags: int, mode: int = 0o600) -> int:
    """Open a lock file and register its FD ATOMICALLY w.r.t. fork (under the fork
    guard), so the child's after-fork handler is guaranteed to know — and close —
    every inherited lock FD. The blocking lock attempt happens AFTER this returns."""
    with _fork_guard:
        fd = os.open(str(path), flags, mode)
        try:
            _track_fd(fd)
        except BaseException:
            os.close(fd)
            raise
        if _after_track_hook is not None:
            _after_track_hook()
        return fd


def _close_tracked_lock_fd(fd: int, *, unlock: bool) -> None:
    """Untrack → (optionally) unlock → close a lock FD ATOMICALLY w.r.t. fork (under
    the fork guard). This closes the release-side window: a fork must never interleave
    between untrack and close, which would leave the child a still-open, no-longer-
    tracked (thus un-closable) inherited lock FD. This is the ONLY place a tracked lock
    FD is untracked+closed — no direct _untrack_fd()+os.close() elsewhere."""
    if fd < 0:
        return
    with _fork_guard:
        _untrack_fd(fd)          # untrack before close is safe: the guard blocks forks
        if _before_close_hook is not None:
            _before_close_hook()
        if unlock:
            _unlock(fd)
        try:
            os.close(fd)
        except OSError:
            pass


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
def _reject_redirected(path: Path) -> None:
    """Fail-closed if *path* is a statically-present symlink / reparse point. A
    redirected env dir, queue dir, or queue-mutex could split participants into
    different synchronisation domains. THREAT-MODEL LIMIT: this is a STATIC check; the
    check→open window is NOT race-safe against deliberate same-user filesystem
    manipulation (see the module docstring)."""
    try:
        st = os.lstat(str(path))
    except FileNotFoundError:
        return
    if stat.S_ISLNK(st.st_mode):
        raise QueueStateError("lock path is a symlink")
    if os.name == "nt" and (
        getattr(st, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    ):
        raise QueueStateError("lock path is a reparse point")


def _env_dir(env_id: str) -> Path:
    from agentnode_sdk.config import config_dir
    d = config_dir() / "locks" / f"rw-{env_id}"
    _reject_redirected(d)                       # a redirected env lock dir → fail-closed
    (d / "queue").mkdir(parents=True, exist_ok=True)
    _reject_redirected(d / "queue")
    return d


@contextmanager
def _queue_mutex(env_dir: Path, deadline: float, ttype: str):
    path = env_dir / "queue-mutex.lock"
    _reject_redirected(path)   # static symlink/reparse on the mutex would split domains
    fd = _open_tracked(path, os.O_RDWR | os.O_CREAT)   # open+track atomic w.r.t. fork
    locked = False
    try:
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise EnvironmentLockTimeout(ttype)
            time.sleep(_POLL)
        locked = True
        yield
    finally:
        _close_tracked_lock_fd(fd, unlock=locked)      # untrack→unlock→close atomically


# ---------------------------------------------------------------------------
# Counter (strict + durable + rollback-checked) — all under the queue-mutex.
# ---------------------------------------------------------------------------
_MARKER_CONTENT = "1"


def _valid_marker(env_dir: Path) -> bool:
    """True if a VALID ``initialized`` marker exists, False if absent, FAIL-CLOSED on a
    non-regular/symlinked/malformed marker (checked without following a symlink)."""
    mpath = env_dir / "initialized"
    try:
        st = os.lstat(str(mpath))
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(st.st_mode):
        raise QueueStateError("initialized marker is not a regular file")
    try:
        raw = mpath.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise CounterStateError("initialized marker unreadable") from exc
    if raw != _MARKER_CONTENT:
        raise CounterStateError("initialized marker has unexpected content")
    return True


def _counter_exists_regular(env_dir: Path) -> bool:
    try:
        st = os.lstat(str(env_dir / "counter"))
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(st.st_mode):
        raise CounterStateError("counter is not a regular file")
    return True


def _read_counter_value(env_dir: Path) -> int:
    try:
        raw = (env_dir / "counter").read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError) as exc:
        raise CounterStateError("counter unreadable / not ASCII") from exc
    if len(raw) > 24 or _COUNTER_RE.match(raw) is None:
        raise CounterStateError("counter is not a canonical non-negative integer")
    return int(raw.strip())


def _write_counter_durable(env_dir: Path, value: int) -> None:
    from agentnode_sdk._fileutil import atomic_write_json
    atomic_write_json(env_dir / "counter", value, durable=True)  # json.dumps(int) == the digits


def _write_marker_durable(env_dir: Path) -> None:
    from agentnode_sdk._fileutil import atomic_write_json
    atomic_write_json(env_dir / "initialized", int(_MARKER_CONTENT), durable=True)


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
    """Under the queue-mutex: MARKER-FIRST init → strict counter read → rollback check
    → exhaustion check → durable counter write → new ticket number.

    Init contract: a valid ``initialized`` marker means a counter MUST exist (missing →
    fail-closed, never a silent reset). If no marker, the dir must be pristine (no
    counter, empty queue) and we write the marker DURABLE *before* the counter — a
    crash between the two leaves a marker-without-counter → fail-closed on the next
    acquire (availability loss is safer than ticket reuse)."""
    if _valid_marker(env_dir):
        if not _counter_exists_regular(env_dir):
            raise CounterStateError("counter missing after initialization")
        counter = _read_counter_value(env_dir)
    else:
        if _counter_exists_regular(env_dir):
            raise CounterStateError("counter present without an initialization marker")
        if _max_visible_ticket(env_dir) != 0:
            raise QueueStateError("queue entries without an initialization marker")
        _write_marker_durable(env_dir)               # marker FIRST
        counter = 0
    if counter < _max_visible_ticket(env_dir):
        raise CounterRollbackError("counter is below the highest visible ticket")
    if counter >= _MAX_TICKET:                       # next would exceed the range
        raise TicketExhausted("ticket space exhausted")  # no counter change, no token
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
        fd = _open_tracked(tpath, os.O_RDWR)   # open+track atomic w.r.t. fork
    except FileNotFoundError:
        return False
    except OSError:
        return True
    try:
        if _try_lock(fd):
            _close_tracked_lock_fd(fd, unlock=True)   # dead → untrack→unlock→close atomically
            _safe_unlink(tpath)     # under the mutex → no concurrent observer
            return False
        _close_tracked_lock_fd(fd, unlock=False)      # live → not ours to unlock
        return True
    except OSError:
        _close_tracked_lock_fd(fd, unlock=False)
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
def _cleanup_ticket(
    env_dir: Path, fd: int, ticket_path: Path | None, deadline: float, acquired: bool
) -> None:
    """Release + remove OUR OWN ticket. Always leaves the token DEAD (unlocked) first;
    unlinks only under the queue-mutex.

    - On a SUCCESSFUL release (``acquired``) the original deadline is typically past,
      so use a SHORT internal cleanup budget to unlink (avoids leaving a dead orphan on
      every long run) without blocking the caller for long.
    - On a FAILED/timed-out acquisition, use the ORIGINAL deadline: if it is already
      past, do NOT acquire another blocking mutex — leave the DEAD token as a
      safely-cleanable orphan for the next scanner."""
    if fd >= 0:
        _close_tracked_lock_fd(fd, unlock=True)   # untrack→unlock→close atomically; token dead
    if ticket_path is None:
        return
    cleanup_deadline = time.monotonic() + _CLEANUP_BUDGET if acquired else deadline
    if time.monotonic() >= cleanup_deadline:
        return                        # leave the DEAD token for the next scanner
    try:
        with _queue_mutex(env_dir, cleanup_deadline, "r"):
            _safe_unlink(ticket_path)
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
    acquired = False
    try:
        with _queue_mutex(env_dir, deadline, ttype):
            n = _reserve_ticket(env_dir, ttype)
            ticket_path = env_dir / "queue" / f"{n:020d}.{ttype}.{uuid.uuid4().hex}.lock"
            fd = _open_tracked(ticket_path, os.O_RDWR | os.O_CREAT | os.O_EXCL)
            if not _try_lock(fd):
                raise RuntimeError("could not lock a freshly created ticket")
        while True:
            with _queue_mutex(env_dir, deadline, ttype):
                if not _blocked(env_dir, n, ttype):
                    break
            if time.monotonic() >= deadline:
                raise EnvironmentLockTimeout(ttype)
            time.sleep(_ADMIT_POLL)
        acquired = True
        yield
    finally:
        _cleanup_ticket(env_dir, fd, ticket_path, deadline, acquired)


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


def _before_fork() -> None:
    # Block the fork until any in-progress open→track transition completes, so the
    # child sees a fully-tracked FD set (no locked-but-untracked inherited FD).
    _fork_guard.acquire()


def _after_fork_parent() -> None:
    _fork_guard.release()


def _after_fork_child() -> None:
    # Runs single-threaded in the child. The inherited _fork_guard is LOCKED (held by
    # _before_fork); never try to release/acquire it — just replace it. Same for the
    # other process-local mutexes (a mutex held by another thread at fork time would
    # stay locked forever here). CLOSE every inherited lock FD (queue-mutex, ticket,
    # orphan-probe are all tracked) so the child stops extending any parent lock via a
    # shared open-file-description; do NOT unlink queue entries (they are the parent's).
    global _local_lock, _fd_lock, _fork_guard
    for fd in list(_all_lock_fds):
        try:
            os.close(fd)
        except OSError:
            pass
    _all_lock_fds.clear()
    _local_readers.clear()
    _thread_local.depths = {}
    _local_lock = threading.Lock()
    _fd_lock = threading.Lock()
    _fork_guard = threading.RLock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(
        before=_before_fork,
        after_in_parent=_after_fork_parent,
        after_in_child=_after_fork_child,
    )


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
