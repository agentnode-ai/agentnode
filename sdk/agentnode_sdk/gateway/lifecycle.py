"""Which gateway is serving a directory, and whether the last one got to stop.

Two questions, one file, because they are the same question asked at two moments.

## Why a closing line needs this

A run interrupted by a restart is owed a line saying WHY, and "the gateway went away" is not
specific enough to act on: a customer whose job was cut short by a planned stop and one whose
gateway was killed are in different situations, and only the second has reason to suspect the
machine. Nothing in the ledger distinguishes them -- a ledger entry left at `running` looks the
same either way.

What distinguishes them is what the previous gateway managed to leave behind. So this writes one
small file, and a later gateway reads it before it recovers anything:

    serving.json   {"since": <when it took over>, "pid": <its pid>, "stopping": <bool>,
                    "crashed": <bool>, "boot": <the kernel's identity for this boot>,
                    "stopping_since": <when it began to stop, if it did>}

Three of the four answers come out of that file directly, and the fourth comes out of comparing
one of its fields with the machine:

    it began to stop                  somebody stopped this gateway
    it recorded a failure             an unhandled error reached the top of the process, and the
                                      process said so before it went
    neither, and the SAME boot        the process ended without stopping and without failing, on
                                      a machine that kept running -- so something outside it
                                      ended it
    neither, and another boot,        the machine restarted, or there is no boot identity to
    or nothing to read                compare. Why is not established

**An earlier version of this had only two answers**, on the reasoning that a killed process, a
crashed one and a machine that lost power leave the same trace, which is none. Two of the three
turned out to leave something:

* a crash RUNS CODE. There is a moment, however short, in which the process can write down that
  it is ending badly;
* a machine that restarted says so, because the kernel's boot identity changes across a reboot
  and not otherwise.

What stays in `gateway_lost` is the case where the boot identity cannot be had or where it
changed -- which says the machine restarted, and not why. Naming that one `killed` would be
inventing a fact, which is what the earlier version was right to refuse.

## Why one gateway at a time

Found on the closed alpha while taking stock, not reasoned about in advance: a gateway left over
from an exercise four days earlier was still serving `/var/lib/agentnode/state` on a second port,
alongside the one systemd had started on the first. Two processes, one directory, one signed log,
one ledger.

Nothing had stopped it, and nothing would have. The run ledger is transactional per write, so the
two did not corrupt it -- but both would select the same run as needing recovery, and both would
answer for it.

So a gateway that SERVES a directory holds an exclusive lock on it for as long as it serves, and a
second one refuses to start and says which process has it. A lock the kernel owns, so a gateway
that dies releases it without anybody having to decide whether a lock file is stale.

The lock is taken by the one command that takes a directory over, and by nothing else. Every
operator command builds a gateway object to read something; those are visitors, they recover
nothing, and locking them out would mean an operator could not look at a running gateway -- which
is the defect this project already fixed once, from the other direction.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

#: The file. Beside the state it describes, not inside the ledger: the ledger is about runs, and
#: a gateway's own comings and goings are not a run.
SERVING_NAME = "serving.json"

#: What the last gateway on this directory was doing when it was last heard from.
BEGAN_TO_STOP = "stopping"
RECORDED_A_FAILURE = "crashed"
WAS_SERVING = "serving"
NOTHING_RECORDED = "nothing recorded"


def this_boot() -> str:
    """The kernel's identity for the current boot, or "" where there is none to be had.

    It changes across a reboot and not otherwise, which is the whole of its use here: a marker
    written under one boot and read under another says the MACHINE went, while the same value on
    both sides says only the process did. That is the difference between a gateway somebody
    killed and a host that restarted, and without it both are "we cannot tell".

    Linux publishes it. A platform that does not gets "", and the reason that is honest rather
    than a gap is that every use of this treats "" as "cannot be established" and says so.
    """
    try:
        with open("/proc/sys/kernel/random/boot_id", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _path(root) -> Path:
    return Path(root) / SERVING_NAME


def what_the_last_gateway_did(root) -> str:
    """`BEGAN_TO_STOP`, `RECORDED_A_FAILURE`, `WAS_SERVING` or `NOTHING_RECORDED`. Never raises.

    A file that cannot be read or does not parse reads as `NOTHING_RECORDED`, which is the same
    answer as no file: in both cases there is nothing the previous gateway left to read, and
    that is the only thing this function is entitled to report.

    Stopping is checked before crashing: a gateway that began to stop and then failed on the way
    out was stopped, and the failure is what happened while it did.
    """
    try:
        said = json.loads(_path(root).read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return NOTHING_RECORDED
    if not isinstance(said, dict):
        return NOTHING_RECORDED
    if bool(said.get("stopping")):
        return BEGAN_TO_STOP
    if bool(said.get("crashed")):
        return RECORDED_A_FAILURE
    return WAS_SERVING


def why_a_run_was_interrupted(root) -> str:
    """The termination reason for runs this gateway finds mid-flight, from the file above.

    Four answers, and each says only what the file supports:

      it began to stop             -> gateway_stopped
      it recorded a failure        -> gateway_crashed
      neither, and the SAME boot   -> gateway_killed. The process ended without stopping and
                                      without failing, on a machine that kept running, so
                                      something outside it ended it
      neither, and another boot,   -> gateway_lost. The machine restarted, or there is no boot
      or no boot identity             identity to compare, and why is not established
    """
    from agentnode_sdk.gateway.protocol import (GATEWAY_CRASHED, GATEWAY_KILLED, GATEWAY_LOST,
                                                GATEWAY_STOPPED)

    was = what_the_last_gateway_did(root)
    if was == BEGAN_TO_STOP:
        return GATEWAY_STOPPED
    if was == RECORDED_A_FAILURE:
        return GATEWAY_CRASHED
    if was == NOTHING_RECORDED:
        return GATEWAY_LOST
    now, before = this_boot(), _boot_it_was_written_under(root)
    if now and before and now == before:
        return GATEWAY_KILLED
    return GATEWAY_LOST


def _boot_it_was_written_under(root) -> str:
    try:
        said = json.loads(_path(root).read_text(encoding="utf-8"))
        return str(said.get("boot") or "") if isinstance(said, dict) else ""
    except Exception:                                          # noqa: BLE001
        return ""


def _write(root, said: dict) -> None:
    """Atomically, and never fatally: a marker that could not be written must not stop a gateway.

    The cost of failing to write it is one closing line that says `gateway_lost` when a stop was
    in fact begun -- a reason that is less specific than the truth. The cost of refusing to start
    over it would be a gateway that will not serve because it could not write a note about
    itself.
    """
    try:
        path = _path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".new")
        handle = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(said, sort_keys=True, separators=(",", ":")))
        os.replace(tmp, path)
    except Exception:                                          # noqa: BLE001
        pass


def say_it_is_serving(root) -> None:
    """This process has taken the directory over. Written AFTER recovery has read the old value.

    The boot identity goes in because a later start has to be able to tell a machine that
    restarted from a process that was ended while the machine kept running.
    """
    _write(root, {"since": time.time(), "pid": os.getpid(), "stopping": False,
                  "crashed": False, "boot": this_boot()})


def say_it_crashed(root, what: str = "") -> None:
    """An unhandled failure reached the top of this process.

    A crash RUNS CODE, which is what makes it different from being killed: there is a moment,
    however short, in which the process can say that it is ending badly. Written before the
    failure is re-raised, so that a gateway which then dies has still left the statement.

    `what` is the exception's type name and nothing else. A message can carry anything a
    traceback touched, and this file is not a place to put it.
    """
    said = {"since": time.time(), "pid": os.getpid(), "stopping": False, "crashed": True,
            "boot": this_boot(), "failed_with": str(what or "")[:80]}
    try:
        was = json.loads(_path(root).read_text(encoding="utf-8"))
        if isinstance(was, dict) and was.get("since"):
            said["since"] = was["since"]
    except Exception:                                          # noqa: BLE001
        pass
    _write(root, said)


def say_it_is_stopping(root) -> None:
    """A shutdown has begun. Written before anything else about stopping is done."""
    said = {"since": time.time(), "pid": os.getpid(), "stopping": True, "crashed": False,
            "boot": this_boot(), "stopping_since": time.time()}
    try:
        was = json.loads(_path(root).read_text(encoding="utf-8"))
        if isinstance(was, dict) and was.get("since"):
            said["since"] = was["since"]
    except Exception:                                          # noqa: BLE001
        pass
    _write(root, said)


class AnotherGatewayHasIt(Exception):
    """A second gateway tried to serve a directory one is already serving."""


class OnlyOneGateway:
    """The exclusive lock a serving gateway holds for as long as it serves.

    Held, not taken and released: the point is the whole lifetime. Acquired by entering it and
    released by closing it or by the process ending, because the kernel owns the lifetime of the
    underlying lock and hands it back when the descriptor goes -- including when the process is
    killed, which is exactly the case a lock file with a staleness rule gets wrong.
    """

    def __init__(self, root) -> None:
        from agentnode_sdk.gateway.filelock import ProcessLock

        self.root = Path(root)
        # A short timeout on purpose. If another gateway holds this, waiting does not help: it
        # intends to hold it for as long as it runs.
        self._lock = ProcessLock(self.root / "serving", timeout=1.0)
        self._held = False

    def take(self) -> "OnlyOneGateway":
        from agentnode_sdk.gateway.filelock import LockUnavailable

        try:
            self._lock.__enter__()
        except LockUnavailable:
            raise AnotherGatewayHasIt(
                "another gateway is already serving %s. Two gateways on one directory share one "
                "ledger and one signed log, and both would answer for the same interrupted run. "
                "Stop the one that is running, or start this one on a directory of its own."
                % self.root) from None
        self._held = True
        return self

    def close(self) -> None:
        if not self._held:
            return
        self._held = False
        try:
            self._lock.__exit__(None, None, None)
        except Exception:                                      # noqa: BLE001
            pass

    def __enter__(self) -> "OnlyOneGateway":
        return self.take()

    def __exit__(self, *_exc) -> None:
        self.close()
