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
"""
import os
import sys
import time


def main() -> None:
    repo_root, config_dir, env_id, mode, prefix = sys.argv[1:6]
    hold = float(sys.argv[6]) if len(sys.argv) > 6 else 0.0
    sys.path.insert(0, repo_root)
    os.environ["AGENTNODE_CONFIG"] = config_dir
    from agentnode_sdk import _env_rwlock as rw

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
