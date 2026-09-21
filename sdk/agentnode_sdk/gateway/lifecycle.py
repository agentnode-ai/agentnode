"""Which gateway is serving a directory, and whether the last one got to stop.

Two questions, one file, because they are the same question asked at two moments.

## Why a closing line needs this

A run interrupted by a restart is owed a line saying WHY, and "the gateway went away" is not
specific enough to act on: a customer whose job was cut short by a planned stop and one whose
gateway was killed are in different situations, and only the second has reason to suspect the
machine. Nothing in the ledger distinguishes them -- a ledger entry left at `running` looks the
same either way.

What does distinguish them is whether a shutdown was ever BEGUN. A gateway that is stopping can
say so before it goes; one that is killed cannot say anything, and the absence of that statement
is itself the evidence. So this writes one small file:

    serving.json   {"since": <when it took over>, "pid": <its pid>, "stopping": <bool>,
                    "stopping_since": <when it began to stop, if it did>}

A later gateway reads it before it recovers anything, and the answer decides the reason on every
line it writes for a run it found mid-flight.

**`gateway_lost` claims only what that absence supports.** A process that was killed, one that
crashed on an unhandled error, and a machine that lost power leave the same file behind, which is
one that still says `"stopping": false`. Naming any one of the three would be inventing a fact.
What is established is that no shutdown was begun, and that is what the word says.

A directory with no `serving.json` at all -- an installation from before this file existed --
reads the same way, and for the same reason: there is no record of a shutdown having begun.

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
WAS_SERVING = "serving"
NOTHING_RECORDED = "nothing recorded"


def _path(root) -> Path:
    return Path(root) / SERVING_NAME


def what_the_last_gateway_did(root) -> str:
    """`BEGAN_TO_STOP`, `WAS_SERVING`, or `NOTHING_RECORDED`. Never raises.

    A file that cannot be read or does not parse reads as `NOTHING_RECORDED`, which is the same
    answer as no file: in both cases there is no record of a shutdown having begun, and that is
    the only thing this function is entitled to report.
    """
    try:
        said = json.loads(_path(root).read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return NOTHING_RECORDED
    if not isinstance(said, dict):
        return NOTHING_RECORDED
    return BEGAN_TO_STOP if bool(said.get("stopping")) else WAS_SERVING


def why_a_run_was_interrupted(root) -> str:
    """The termination reason for runs this gateway finds mid-flight, from the file above."""
    from agentnode_sdk.gateway.protocol import GATEWAY_LOST, GATEWAY_STOPPED

    return (GATEWAY_STOPPED if what_the_last_gateway_did(root) == BEGAN_TO_STOP
            else GATEWAY_LOST)


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
    """This process has taken the directory over. Written AFTER recovery has read the old value."""
    _write(root, {"since": time.time(), "pid": os.getpid(), "stopping": False})


def say_it_is_stopping(root) -> None:
    """A shutdown has begun. Written before anything else about stopping is done."""
    said = {"since": time.time(), "pid": os.getpid(), "stopping": True,
            "stopping_since": time.time()}
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
