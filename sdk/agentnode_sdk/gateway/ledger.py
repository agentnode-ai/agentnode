"""What the gateway must still know after it is restarted.

Replay protection that lives only in process memory is replay protection with a documented way
around it: restart the gateway -- or wait for it to be restarted, which on a server happens on its
own -- and every captured request becomes usable again. The window is not wide, because a request
older than the clock-skew allowance is refused by its timestamp before anything else looks at it.
But "narrow" is not "closed", and the thing on the other side of it is running somebody's code a
second time.

So two facts outlive the process:

* **nonces**, for the acceptance window plus a margin. This is the one that actually stops a
  captured request being re-sent across a restart.
* **run ids**, for much longer. A run id is how a client asks about its job, and re-sending a
  submission must stay a replay rather than becoming a second execution just because the record of
  the first one was in memory that has since been reused.

Writes are atomic -- a temporary file in the same directory, then a rename over the target. A
half-written ledger read back after a crash would be a ledger that has forgotten things, and a
ledger that has forgotten things says yes to a replay. The rename is the point: it either happened
or it did not.

The claim is recorded **before the job starts**, not after it finishes. A crash between the two is
the case this exists for, and a ledger written afterwards would have nothing to say about exactly
the run that was interrupted.

## In-flight runs at restart

A run that was executing when the process died is not running any more and never will be. It is
marked `interrupted` on load rather than left saying `running`, because a status that will never
change again is worse than an honest one -- and it is emphatically not re-executed. The client
asked once; the gateway does not get to decide it should happen again.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path

from agentnode_sdk.gateway.filelock import ProcessLock

#: Nonces are kept a good deal longer than the freshness window. The window is what makes a
#: captured request stale; this margin is what covers a clock that moved.
NONCE_RETENTION_SECONDS = 60 * 60

#: Run ids are kept long enough that "ask again" keeps working across a restart and a night.
RUN_RETENTION_SECONDS = 30 * 24 * 60 * 60

#: How long an interrupted run whose sandbox was never confirmed gone keeps being asked about on
#: every start. Long enough to cover a worker that was down for a while; short enough that a start
#: does not interrogate a runtime about a month of history.
SWEEP_AGAIN_WITHIN_SECONDS = 24 * 60 * 60


class Ledger:
    """A small durable record of what this gateway has already accepted."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {"nonces": {}, "runs": {}}
        self._load()

    # ------------------------------------------------------------------ storage

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # An unreadable ledger is not an empty one. Starting with a blank ledger here would
            # silently re-open every replay the file existed to prevent, so the gateway refuses
            # to start instead and a person decides what to do about the file.
            raise LedgerUnreadable(
                f"the gateway's ledger at {self.path} could not be read. It records which jobs "
                "have already been accepted, so starting without it would let a captured request "
                "be replayed. Move the file aside to start with an empty ledger, understanding "
                "that any job accepted before now could then be submitted again."
            ) from None
        if isinstance(loaded, dict):
            self._data = {
                "nonces": dict(loaded.get("nonces") or {}),
                "runs": dict(loaded.get("runs") or {}),
            }

    def _write_locked(self) -> None:
        """Atomic: write beside the target, then rename over it."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".ledger-")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _prune_locked(self, now: float) -> None:
        self._data["nonces"] = {
            n: t for n, t in self._data["nonces"].items()
            if float(t) > now - NONCE_RETENTION_SECONDS
        }
        self._data["runs"] = {
            r: e for r, e in self._data["runs"].items()
            if float(e.get("first_seen", 0)) > now - RUN_RETENTION_SECONDS
        }

    # ------------------------------------------------------------------ questions

    def knows_nonce(self, nonce: str) -> bool:
        with self._lock, ProcessLock(self.path):
            self._load()
            return str(nonce) in self._data["nonces"]

    def knows_run(self, run_id: str) -> bool:
        with self._lock, ProcessLock(self.path):
            self._load()
            return str(run_id) in self._data["runs"]

    def run_entry(self, run_id: str) -> dict | None:
        with self._lock, ProcessLock(self.path):
            self._load()
            entry = self._data["runs"].get(str(run_id))
            return dict(entry) if entry else None

    def claim(self, run_id: str, nonce: str, request_sha256: str, owner_client_id: str,
              now: float | None = None) -> bool:
        """Record this run and its nonce, if neither has been seen. True when newly claimed.

        One critical section covers both the look and the write, so two identical requests
        arriving together cannot both be told they are the first -- which is the same shape of
        defect as reading a pairing code and clearing it in two steps.
        """
        now = time.time() if now is None else now
        # The process lock matters more here than anywhere else: without it two gateways sharing
        # a directory can each find the same nonce absent and each accept the same job, which is
        # the replay this file exists to prevent. Re-read INSIDE the lock, because whatever was
        # loaded at construction may be stale by now.
        with self._lock, ProcessLock(self.path):
            self._load()
            self._prune_locked(now)
            if str(run_id) in self._data["runs"] or str(nonce) in self._data["nonces"]:
                return False
            self._data["runs"][str(run_id)] = {
                "first_seen": now,
                "request_sha256": str(request_sha256),
                "owner_client_id": str(owner_client_id),
                "state": "accepted",
            }
            if nonce:
                self._data["nonces"][str(nonce)] = now
            self._write_locked()
            return True

    def note_challenge(self, run_id: str, binding: dict) -> None:
        """Write down what this gateway issued for a run, before the job starts.

        Durable and on disk before the container is, so that what it says predates anything the
        run could produce. The document holds the challenge's DIGEST; the value is not here and
        this method has no way to be given it -- `challenge.bind` does not put it in one.
        """
        with self._lock, ProcessLock(self.path):
            self._load()
            entry = self._data["runs"].get(str(run_id))
            if entry is None:
                return
            entry["challenge"] = dict(binding)
            self._write_locked()

    def challenge_for(self, run_id: str) -> dict | None:
        """What was written down for one run, or nothing. One run: never a listing."""
        with self._lock:
            entry = self._data["runs"].get(str(run_id))
            if not isinstance(entry, dict):
                return None
            binding = entry.get("challenge")
            return dict(binding) if isinstance(binding, dict) else None

    def note_state(self, run_id: str, state: str) -> None:
        with self._lock, ProcessLock(self.path):
            self._load()
            entry = self._data["runs"].get(str(run_id))
            if entry is None:
                return
            entry["state"] = str(state)
            self._write_locked()

    def unfinished_runs(self) -> list[str]:
        """Run ids the ledger last saw mid-flight -- interrupted, not running."""
        with self._lock:
            return sorted(
                run_id for run_id, entry in self._data["runs"].items()
                if str(entry.get("state")) in ("accepted", "running")
            )

    def note_cleanup(self, run_id: str, verified: bool | None) -> None:
        """Whether what a run left behind was confirmed gone. Durable, because the answer
        decides whether anyone ever asks again."""
        with self._lock, ProcessLock(self.path):
            self._load()
            entry = self._data["runs"].get(str(run_id))
            if entry is None:
                return
            entry["cleanup"] = verified
            self._write_locked()

    def runs_left_unswept(self, now: float | None = None) -> list[str]:
        """Interrupted runs whose sandbox nobody has confirmed is gone.

        A run is marked interrupted by the restart that cut it short, which is also when its
        container is asked about. If the worker could not be reached at that moment -- a gateway
        coming up before its worker is exactly when that happens -- the container is still there
        and the run is no longer mid-flight, so nothing would ever look at it again. This is what
        the next restart looks at.

        Only True stops the asking. False is "something is still there" and None is "nobody could
        ask"; neither is an answer that should end the search. Bounded by age, because a run old
        enough that its host has been rebooted since is not one to keep questioning a runtime
        about on every start.
        """
        now = time.time() if now is None else now
        with self._lock:
            return sorted(
                run_id for run_id, entry in self._data["runs"].items()
                if str(entry.get("state")) == "interrupted"
                and entry.get("cleanup") is not True
                and float(entry.get("first_seen", 0)) > now - SWEEP_AGAIN_WITHIN_SECONDS
            )


class LedgerUnreadable(Exception):
    """The durable record could not be read, so replays could not be recognised."""
