"""A lock whose holder is gone is not a lock.

The activation lock is a file created with O_EXCL, holding the pid of whoever took it. It used to
be honoured until it was half an hour old, whatever had become of that process. So a policy change
that ended without its `__exit__` -- killed, or interrupted by a restart -- refused every later
change for the whole window.

Found on the closed alpha while switching it to mutual TLS: the file was left behind, the unit
that measures what the sandbox enforces failed ten times and gave up, and the gateway went on
refusing every job that asks for a container until somebody deleted the file by hand. On a machine
that is supposed to come back by itself after a restart, that is the difference.
"""
from __future__ import annotations

import gc
import os

import pytest

from agentnode_sdk.gateway.activation import ActivationError, ActivationLock


def a_lock_left_behind(root, pid) -> None:
    """The file exactly as an interrupted activation leaves it: the pid, and nothing else."""
    (root / "activation.lock").write_text(str(pid), encoding="ascii")


def _a_pid_that_is_not_running() -> int:
    """A pid nobody holds. Taken by starting a process and letting it end."""
    import subprocess
    import sys

    # The handle is released as well, not only waited on: on Windows a pid whose handle is still
    # open is still reported as a process, and the test would then be asking the wrong question.
    with subprocess.Popen([sys.executable, "-c", "pass"]) as started:
        started.wait(timeout=60)
        pid = started.pid
    gc.collect()
    return pid


class TestALockIsHonouredWhileItsHolderRuns:

    def test_a_second_activation_is_refused(self, tmp_path):
        with ActivationLock(tmp_path):
            with pytest.raises(ActivationError) as refused:
                with ActivationLock(tmp_path):
                    raise AssertionError("two activations were allowed at once")
        assert "already running" in str(refused.value)

    def test_the_lock_records_the_pid_of_whoever_holds_it(self, tmp_path):
        with ActivationLock(tmp_path):
            said = (tmp_path / "activation.lock").read_text(encoding="ascii").strip()
        assert said == str(os.getpid())

    def test_and_it_is_gone_afterwards(self, tmp_path):
        with ActivationLock(tmp_path):
            pass
        assert not (tmp_path / "activation.lock").exists()


class TestALockLeftBehindIsBroken:

    def test_a_lock_naming_a_process_that_ended_does_not_block(self, tmp_path):
        """The case the alpha hit: the file is there, the process is not."""
        a_lock_left_behind(tmp_path, _a_pid_that_is_not_running())
        lock = ActivationLock(tmp_path)
        with lock:
            assert (tmp_path / "activation.lock").read_text(encoding="ascii").strip() \
                == str(os.getpid())
        assert "no longer running" in lock.broke_a_lock

    def test_and_it_says_so_rather_than_breaking_it_silently(self, tmp_path):
        a_lock_left_behind(tmp_path, _a_pid_that_is_not_running())
        lock = ActivationLock(tmp_path)
        with lock:
            pass
        assert lock.broke_a_lock, "a lock was broken and nothing said so"

    def test_a_lock_that_cannot_be_read_as_a_pid_is_still_honoured(self, tmp_path):
        """Not a number, so nothing can be established: the age rule decides, not a guess."""
        (tmp_path / "activation.lock").write_text("not a pid", encoding="ascii")
        with pytest.raises(ActivationError):
            with ActivationLock(tmp_path):
                raise AssertionError("a lock that could not be read was broken on a guess")

    def test_an_empty_lock_is_still_honoured(self, tmp_path):
        (tmp_path / "activation.lock").write_text("", encoding="ascii")
        with pytest.raises(ActivationError):
            with ActivationLock(tmp_path):
                raise AssertionError("an unreadable lock was broken on a guess")

    def test_the_age_rule_still_breaks_one_nobody_can_account_for(self, tmp_path):
        """A live pid that is somebody else's: honoured until it is older than any activation."""
        a_lock_left_behind(tmp_path, os.getpid())
        lock = ActivationLock(tmp_path, stale_after=0.0)
        with lock:
            pass
        assert "more than" in lock.broke_a_lock
