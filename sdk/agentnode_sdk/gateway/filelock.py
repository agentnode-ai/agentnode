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


#: How long to keep trying to put a file in place when the platform says somebody else has it.
#: Bounded: a replace that cannot happen in two seconds is a problem to report, not to wait out.
REPLACE_SECONDS = 2.0


def replace_with_retry(tmp, path) -> None:
    """`os.replace`, retried briefly while Windows says the target is in use.

    On Windows a rename over a file FAILS with `PermissionError` for as long as any other handle
    has the target open -- so an operator changing something while the gateway reads it gets an
    error, and a concurrent test silently changes nothing. Neither is a race in the DATA: the
    rename either happened or did not. Retrying briefly is what makes "it happened" the usual
    answer.

    On POSIX a rename over an open file always succeeds, so the loop runs once and this costs
    nothing there.

    Here rather than copied into each writer: it was copied into `atomically` only, and the three
    in `activation.py` -- which write the snapshot that decides whether this gateway may take
    work at all -- did not have it. A concurrent-change drill found that as a `PermissionError`
    out of a measurement that had simply not been written.
    """
    deadline = time.monotonic() + REPLACE_SECONDS
    while True:
        try:
            os.replace(tmp, path)
            return
        except PermissionError:                               # pragma: no cover - Windows only
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.02)


def read_text_with_retry(path, encoding: str = "utf-8") -> str:
    """Read a file, retrying briefly while Windows says it is in use.

    The other side of `replace_with_retry`. A rename over a file is indivisible for a reader that
    already HOLDS a handle; a reader that tries to OPEN one during the rename gets a sharing
    violation, which Python raises as `PermissionError`. So a file that is written while it is
    read -- the cancellation journal is exactly that -- fails to be read for reasons that have
    nothing to do with its contents.

    UNREADABLE IS STILL NOT EMPTY. This retries and then RAISES; it never answers "" or "{}". A
    reader that treated a busy file as an absent one would be the fail-open this product spends
    its time removing, and the journal's own comment says why: "nothing was being stopped"
    because the file could not be parsed is how a container gets left running with nobody
    accounting for it.

    On POSIX a reader is never refused for this reason, so the loop runs once and this costs
    nothing there.
    """
    deadline = time.monotonic() + REPLACE_SECONDS
    while True:
        try:
            with open(path, encoding=encoding) as fh:
                return fh.read()
        except PermissionError:                               # pragma: no cover - Windows only
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.02)


def atomically(path, text: str, mode: int = 0o600) -> None:
    """Write beside, then rename over, so a reader never sees half a file.

    The retry is the part that is not obvious, and it is here rather than copied into five
    modules. On Windows `os.replace` FAILS with PermissionError while any other handle has the
    target open -- so an operator lowering a ceiling while the gateway is reading it gets an
    error, and a test that changes something under load silently changes nothing. Neither is a
    race in the data: the rename either happens or does not, and retrying briefly is what makes
    "it happens" the usual answer.

    On POSIX a rename over an open file always succeeds, so the loop runs once and this costs
    nothing there.
    """
    import tempfile as _tempfile

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = _tempfile.mkstemp(dir=str(path.parent), prefix="." + path.name + "-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        replace_with_retry(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        os.chmod(path, mode)
    except OSError:                                           # pragma: no cover - advisory here
        pass


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
