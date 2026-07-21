"""FIFO-ticket inter-process reader/writer lock for the shared Python environment
(Agent-Exec A1-E-Lock).

Readers = host-agent dispatch/execution; the single writer = an install into the
shared interpreter. Keyed by ``env_id`` (the same identity M1 uses,
``_env_lock.resolve_env_identity``), so every participant resolving one interpreter
serializes correctly regardless of which CWD-relative lockfile drove it.

Design — a crash-safe FIFO ticket queue (provable bounded writer progress):

* A monotone **ticket** is allocated under a short exclusive ``queue-mutex``.
* Each participant creates ``queue/<ticket>.<r|w>.<uuid>.lock`` and holds an
  **exclusive OS lock on it** — that held lock *is* the liveness proof.
* Service order is strictly ascending ticket. A **reader** proceeds when no
  **writer** with a lower live ticket exists (readers with consecutive tickets run
  concurrently). A **writer** proceeds when **no** participant with a lower live
  ticket exists. So once a writer holds ticket N, every later reader takes a ticket
  > N and cannot overtake it — the writer waits only for the finite set of live
  tickets < N. No starvation.
* **Orphans** (crashed participants) are detected *purely by lock-acquirability*: a
  ticket whose exclusive lock can be taken has no live holder → it is removed. No
  PID and no time heuristic. Registration (create+lock) and every scan/cleanup run
  under the ``queue-mutex``, so a ticket seen during a scan is always either locked
  (live) or a genuine orphan.
* **Same-process read→write is refused** (``read_to_write_upgrade_forbidden``): if any
  thread in this process holds a reader for the env, acquiring a writer raises rather
  than deadlocking.

The lock is advisory and released automatically by the OS on process exit, so a
crash never leaks the lock — only (harmless) orphan files, cleaned on the next scan.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

_POLL = 0.005          # queue-mutex acquire backoff
_ADMIT_POLL = 0.01     # admission re-scan backoff

# ---------------------------------------------------------------------------
# Platform per-file exclusive OS locks: blocking(loop) + non-blocking try-lock.
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


# ---------------------------------------------------------------------------
# Process-wide reader registry (interprocess lock + this in-process guard together
# cover the same-process cross-thread upgrade case).
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
# Paths
# ---------------------------------------------------------------------------
def _env_dir(env_id: str) -> Path:
    from agentnode_sdk.config import config_dir
    d = config_dir() / "locks" / f"rw-{env_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


@contextmanager
def _queue_mutex(env_dir: Path):
    """Short exclusive mutex guarding ticket allocation + queue scans/cleanup."""
    path = env_dir / "queue-mutex.lock"
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        while not _try_lock(fd):
            time.sleep(_POLL)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


def _next_ticket(env_dir: Path) -> int:
    cpath = env_dir / "counter"
    try:
        n = int(cpath.read_text() or "0")
    except (FileNotFoundError, ValueError):
        n = 0
    n += 1
    cpath.write_text(str(n))
    return n


def _parse_ticket(name: str) -> tuple[int, str] | None:
    # "<020d ticket>.<r|w>.<uuid>.lock"
    parts = name.split(".")
    if len(parts) < 4 or parts[-1] != "lock" or parts[1] not in ("r", "w"):
        return None
    try:
        return int(parts[0]), parts[1]
    except ValueError:
        return None


def _is_live(tpath: Path) -> bool:
    """Under the queue-mutex: True if the ticket has a live holder. A dead ticket's
    lock is acquirable → we remove it and report not-live. Fail-safe: on any error,
    treat as live (never wrongly admit past a possibly-live lower ticket)."""
    try:
        fd = os.open(str(tpath), os.O_RDWR)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    try:
        if _try_lock(fd):          # acquired → no live holder → orphan
            _unlock(fd)
            os.close(fd)
            try:
                os.unlink(str(tpath))
            except OSError:
                pass
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
    qdir = env_dir / "queue"
    for name in os.listdir(qdir):
        parsed = _parse_ticket(name)
        if parsed is None:
            continue
        num, otype = parsed
        if num >= my_n:
            continue
        if not _is_live(qdir / name):
            continue
        if ttype == "w" or otype == "w":
            return True
    return False


@contextmanager
def _acquire(env_id: str, ttype: str):
    if ttype == "w" and _local_reader_active(env_id):
        raise ReadToWriteUpgradeForbidden(
            "cannot acquire the environment write-lock while this process holds a reader"
        )
    env_dir = _env_dir(env_id)
    (env_dir / "queue").mkdir(parents=True, exist_ok=True)
    tok = uuid.uuid4().hex
    fd = -1
    ticket_path: Path | None = None
    registered_local = False
    try:
        with _queue_mutex(env_dir):
            n = _next_ticket(env_dir)
            ticket_path = env_dir / "queue" / f"{n:020d}.{ttype}.{tok}.lock"
            fd = os.open(str(ticket_path), os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            if not _try_lock(fd):  # a fresh exclusive file must be lockable
                raise RuntimeError("could not lock a freshly created ticket")
        if ttype == "r":
            _register_local_reader(env_id)
            registered_local = True
        # Admission: wait until no blocking lower live ticket remains.
        while True:
            with _queue_mutex(env_dir):
                if not _blocked(env_dir, n, ttype):
                    break
            time.sleep(_ADMIT_POLL)
        yield
    finally:
        if registered_local:
            _unregister_local_reader(env_id)
        if fd >= 0:
            _unlock(fd)
            try:
                os.close(fd)
            except OSError:
                pass
        if ticket_path is not None:
            try:
                os.unlink(str(ticket_path))
            except OSError:
                pass


@contextmanager
def env_read_lock(env_id: str):
    """Acquire the shared (reader) side for one host-agent execution. Held for the
    entire protected section; released on exit. Reentrant across readers."""
    with _acquire(env_id, "r"):
        yield


@contextmanager
def env_write_lock(env_id: str):
    """Acquire the exclusive (writer) side for one install into the environment.
    Refused (``ReadToWriteUpgradeForbidden``) if this process holds a reader."""
    with _acquire(env_id, "w"):
        yield
