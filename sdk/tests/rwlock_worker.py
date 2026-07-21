"""Standalone subprocess worker for the FIFO-ticket RW-lock tests.

Usage: python rwlock_worker.py <repo_root> <config_dir> <env_id> <mode> <marker_prefix>
       [hold_seconds]

modes:
  read           acquire read-lock, write "<prefix>.acq" with a monotonic stamp,
                 hold until "<prefix>.release" appears (or hold_seconds), then write
                 "<prefix>.rel" and exit.
  write          same, but the write-lock.
  crash-read     acquire read-lock, write "<prefix>.acq", then os._exit(1) WHILE
                 holding — leaves an orphan ticket file (lock freed by the OS).
  crash-write    same, write-lock.
  closefail-read acquire read-lock with an INDETERMINATE os.close simulated for a class
                 of lock FD (env RWLOCK_CLOSEFAIL = mutex|ticket|probe). Writes
                 "<prefix>.acq" once the body is entered and "<prefix>.rel" after a
                 clean release — neither should appear once the fault fires, because the
                 primitive must os._exit(138) at the failing close.
"""
import os
import sys
import time


def _install_close_fault(rw, pattern: str) -> None:
    """Simulate an indeterminate os.close() failure for one class of lock FD. Records
    each _open_tracked fd→path, then fails os.close for the matching class."""
    fdpath: dict[int, str] = {}
    real_open_tracked = rw._open_tracked

    def rec(path, *a, **k):
        fd = real_open_tracked(path, *a, **k)
        fdpath[fd] = str(path)
        return fd

    rw._open_tracked = rec

    if os.environ.get("RWLOCK_UNLOCKFAIL"):
        def _boom_unlock(fd):
            raise OSError("simulated unlock failure")
        rw._unlock = _boom_unlock

    real_close = rw.os.close

    def close_op(fd):
        p = fdpath.get(fd)
        if p is not None:
            base = os.path.basename(p)
            parent = os.path.basename(os.path.dirname(p))
            if pattern == "mutex" and base == "queue-mutex.lock":
                raise OSError("simulated indeterminate close (mutex)")
            if pattern in ("ticket", "probe") and parent == "queue":
                raise OSError(f"simulated indeterminate close ({pattern})")
        return real_close(fd)

    rw.os.close = close_op


def main() -> None:
    repo_root, config_dir, env_id, mode, prefix = sys.argv[1:6]
    hold = float(sys.argv[6]) if len(sys.argv) > 6 else 0.0
    sys.path.insert(0, repo_root)
    os.environ["AGENTNODE_CONFIG"] = config_dir
    from agentnode_sdk import _env_rwlock as rw

    if mode == "closefail-read":
        _install_close_fault(rw, os.environ.get("RWLOCK_CLOSEFAIL", "ticket"))
        with rw.env_read_lock(env_id):
            with open(prefix + ".acq", "w") as f:      # reached only if acquire survived
                f.write(str(time.monotonic()))
        with open(prefix + ".rel", "w") as f:          # reached only on a CLEAN release
            f.write(str(time.monotonic()))
        return

    lock = rw.env_write_lock if mode.endswith("write") else rw.env_read_lock
    if mode.startswith("crash"):
        cm = lock(env_id)
        cm.__enter__()
        with open(prefix + ".acq", "w") as f:
            f.write(str(time.monotonic()))
        sys.stdout.flush()
        os._exit(1)  # crash while holding — no finally, OS frees the lock
    else:
        with lock(env_id):
            with open(prefix + ".acq", "w") as f:
                f.write(str(time.monotonic()))
            deadline = time.monotonic() + hold if hold else None
            while True:
                if os.path.exists(prefix + ".release"):
                    break
                if deadline is not None and time.monotonic() >= deadline:
                    break
                time.sleep(0.01)
        with open(prefix + ".rel", "w") as f:
            f.write(str(time.monotonic()))


if __name__ == "__main__":
    main()
