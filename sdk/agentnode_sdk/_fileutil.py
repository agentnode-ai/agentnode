"""Internal file utilities — atomic writes and cross-platform file locking."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path, data: Any, *, mode: int | None = None) -> None:
    """Write JSON to *path* atomically (temp file + os.replace).

    Guarantees either the old content or the new content is on disk —
    never a partial write.
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
        os.close(fd)
        fd = -1

        if mode is not None and os.name != "nt":
            os.chmod(tmp_path, mode)

        os.replace(tmp_path, str(path))
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
