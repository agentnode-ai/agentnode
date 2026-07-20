"""Internal file utilities — atomic writes and cross-platform file locking."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any


def _fsync_dir(dirpath: Path) -> None:
    """fsync a directory so a rename inside it is power-loss durable (POSIX).

    Windows has no directory fsync (``os.open`` on a directory raises
    ``PermissionError``); there the atomic ``os.replace`` relies on NTFS metadata
    journaling, which is weaker than an explicit dir-fsync. We do NOT fake a
    Windows dir-fsync — we skip it, and the residual power-loss window is
    documented (A1-M1 durability contract)."""
    if os.name == "nt":
        return
    dfd = os.open(str(dirpath), os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def atomic_write_json(
    path: Path, data: Any, *, mode: int | None = None, durable: bool = False
) -> None:
    """Write JSON to *path* atomically (temp file + os.replace).

    Guarantees either the old content or the new content is on disk —
    never a partial write.

    ``durable=True`` (used only by the A1-M1 lock transaction; default ``False``
    keeps every existing caller unchanged) additionally fsyncs the file BEFORE the
    replace and the parent directory AFTER it, so a committed write survives power
    loss. A durability failure RAISES: before the replace the old content is
    intact; a POSIX dir-fsync failure can raise AFTER the replace has landed, so a
    ``durable=True`` caller must treat an exception as "state may have changed" and
    re-read rather than assume the old content survived.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(data, indent=2) + "\n"

    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name}_",
        suffix=".tmp",
    )
    try:
        os.write(fd, content.encode("utf-8"))
        if durable:
            os.fsync(fd)                       # file bytes durable before replace
        os.close(fd)
        fd = -1

        if mode is not None and os.name != "nt":
            os.chmod(tmp_path, mode)

        os.replace(tmp_path, str(path))
        if durable:
            _fsync_dir(path.parent)            # rename durable (POSIX; no-op on Windows)
    except Exception:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


@contextmanager
def file_lock(path: Path):
    """Cross-platform exclusive file lock using a sidecar .lk file.

    Blocks until the lock is acquired. Released automatically on context
    exit or process crash (OS reclaims the file descriptor).
    """
    lock_path = str(path) + ".lk"
    os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
    fp = open(lock_path, "w")
    try:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(fp.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(fp.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if sys.platform == "win32":
            import msvcrt
            try:
                msvcrt.locking(fp.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        else:
            import fcntl
            fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
        fp.close()
