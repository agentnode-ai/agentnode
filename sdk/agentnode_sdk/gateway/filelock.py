"""Mutual exclusion between processes, for the state two processes really do share.

`EM3C-GATEWAY-0009` named the gap precisely: an atomic rename publishes a new value atomically, but
it does not make read-check-write a transaction. Two processes can each read the same state, each
decide on it, and each write back — and the second write erases the first. For the pairing throttle
that loses failed attempts, which is the count an attacker wants lost. For the run ledger it is
worse: two processes can both find a nonce absent and both accept the same job.

`threading.Lock` does not help. It excludes threads inside one interpreter, and the whole reason
this state is on disk is that `agentnode gateway pair` and `agentnode gateway start` are different
interpreters.

So this takes a real lock, from the operating system:

* **POSIX** — `fcntl.flock`, an advisory exclusive lock on a dedicated `.lock` file.
* **Windows** — `msvcrt.locking`, a mandatory byte-range lock on the same.

Both are released by the kernel when the file descriptor closes, including when the process dies.
That is the property worth paying for: a lock file whose staleness has to be *detected* needs a
rule for when to break it, and every such rule is either too eager (breaking a lock a live process
holds) or too patient (a crash blocks the gateway until someone notices). Letting the kernel own
the lifetime removes the question.

Acquisition is bounded. A lock that waits forever turns a stuck process into a hung gateway, so
after `timeout` seconds this raises rather than blocking — and the caller decides what a refusal
means, which for both current callers is to fail closed.

## Threads as well as processes

The OS primitives exclude *processes*, and on Windows a byte-range lock is owned by the process, so
a second thread in the same process is granted it immediately. Relying on the file lock alone
therefore leaves threads inside one interpreter unsynchronised — which is not a theoretical gap: it
made the pairing race test fail intermittently, because the test simulates separate gateways with
threads, exactly as a threaded server does. So a per-path lock inside this interpreter is taken
first, and the OS lock second. Same order everywhere, so the pair cannot deadlock.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

try:                                                          # POSIX
    import fcntl

    _HAVE_FCNTL = True
except ImportError:                                           # pragma: no cover - Windows
    _HAVE_FCNTL = False

try:                                                          # Windows
    import msvcrt

    _HAVE_MSVCRT = True
except ImportError:                                           # pragma: no cover - POSIX
    _HAVE_MSVCRT = False


#: One lock per path, for this interpreter. Keyed by the resolved path so two spellings of the
#: same file cannot each get their own.
_LOCAL: dict[str, threading.Lock] = {}
_LOCAL_GUARD = threading.Lock()


def _local_lock(path: Path) -> threading.Lock:
    key = str(path.resolve() if path.parent.exists() else path)
    with _LOCAL_GUARD:
        lock = _LOCAL.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCAL[key] = lock
        return lock


class LockUnavailable(Exception):
    """The lock could not be taken in time. Callers fail closed rather than proceeding."""


class ProcessLock:
    """An exclusive lock on `<path>.lock`, held for the duration of a `with` block."""

    def __init__(self, path: str | os.PathLike[str], timeout: float = 10.0,
                 poll: float = 0.02) -> None:
        self.path = Path(str(path) + ".lock")
        self.timeout = timeout
        self.poll = poll
        self._fd: int | None = None
        self._local = _local_lock(self.path)
        self._held_local = False

    def _try_acquire(self, fd: int) -> bool:
        if _HAVE_FCNTL:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except OSError:
                return False
        if _HAVE_MSVCRT:                                      # pragma: no cover - Windows
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return True
            except OSError:
                return False
        # No locking primitive at all. Say so rather than pretending to hold a lock: a lock that
        # silently does nothing is worse than no lock, because the code above it stops checking.
        raise LockUnavailable(
            "this platform offers no file locking, so shared gateway state cannot be updated safely"
        )

    def __enter__(self) -> "ProcessLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self._local.acquire(timeout=self.timeout):
            raise LockUnavailable(
                f"another thread has held {self.path.name} for more than {self.timeout:.0f}s"
            )
        self._held_local = True
        try:
            fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o600)
        except BaseException:
            self._release_local()
            raise
        deadline = time.monotonic() + self.timeout
        while True:
            if self._try_acquire(fd):
                self._fd = fd
                return self
            if time.monotonic() >= deadline:
                os.close(fd)
                self._release_local()
                raise LockUnavailable(
                    f"another process has held {self.path.name} for more than "
                    f"{self.timeout:.0f}s; refusing rather than proceeding on stale state"
                )
            time.sleep(self.poll)

    def _release_local(self) -> None:
        if self._held_local:
            self._held_local = False
            self._local.release()

    def __exit__(self, *exc) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            self._release_local()
            return
        try:
            if _HAVE_FCNTL:
                fcntl.flock(fd, fcntl.LOCK_UN)
            elif _HAVE_MSVCRT:                                # pragma: no cover - Windows
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        finally:
            os.close(fd)
            self._release_local()
