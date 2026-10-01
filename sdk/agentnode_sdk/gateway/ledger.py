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
closed by the next start rather than left saying `running`, because a status that will never change
again is worse than an honest one -- and it is emphatically not re-executed. The client asked once;
the gateway does not get to decide it should happen again.

## THREE FACTS, AND WHICH FIELD HOLDS WHICH

`state-consistency-r1`. This used to be two fields doing three jobs, and two code paths wrote an
outcome into the same one. Measured on two machines, twice: a run whose signed line said
`interrupted` had a ledger entry saying `finished`, because the shutdown path wrote the line and the
run's own handler came back afterwards and wrote the ledger. The word in this file was whichever
path ran last.

So the three facts are kept apart, and only one of them is a word about the ending:

* **`state`** -- this ledger's LIFECYCLE position and nothing else: `accepted`, `running`, `closed`.
  It never carries an outcome. `closed` is absorbing, and `accepted` may go straight to `closed`
  because a run can be refused, cancelled or interrupted without ever leaving the queue.
* **`settled_as`** (with `settled_at`) -- the outcome, COPIED FROM THE SIGNED LINE and written once.
  The signed use log is what a customer would be handed, so it is the authority; this field is a
  cache of it and is never chosen by a code path. A second write of the same word is a no-op; a
  second write of a DIFFERENT word is refused and recorded in `settled_conflicts`, because it means
  two paths read different things out of a log that can only hold one line per run.
* **`cleanup`** -- `None` nobody could ask, `False` something is still there, `True` confirmed gone.
  `True` is absorbing. Before this it was not, so a later start that could not reach the worker
  overwrote an established `True` with "nobody could ask".

**Recovery need is derived from those, never stored.** A run needs attention while `settled_as` is
absent or `cleanup` is not `True`. Nothing writes a field meaning "recovery finished", because a
field like that is one a less-informed write can switch off.

**`ever_ran` is three-valued.** `started_at` is written on a best effort before the container is
asked for, so its presence can overstate execution and its absence cannot establish that nothing
ran. Absence is therefore unknown, and `False` is written only when a worker that keeps a record
says so.

Every one of these moves is a compare-and-set: the file is re-read inside the cross-process lock and
the move is checked against what is actually there. A check against this object's own memory would
hold for one process and not for two sharing a directory, which the rest of this module already
treats as a real situation.

## SCHEMA

The document carries `schema`. A file without one is schema 1, from before `settled_as` existed, and
is read without complaint -- its `state` may hold an outcome word, which is exactly why nothing here
ever infers an outcome from `state`. Writers always stamp the current `SCHEMA`.
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
#:
#: This bounds TWO things and deliberately not a third: contacting a worker, and creating a signed
#: line that is missing. It does NOT bound reconciling a record against a line that already exists.
#: A contradiction between two of this service's own files does not become acceptable because it is
#: a day old, and repairing one needs nobody's cooperation -- it is a read of a file this process
#: already has.
SWEEP_AGAIN_WITHIN_SECONDS = 24 * 60 * 60

#: The shape of the document on disk. 1 is everything written before `settled_as` existed, where
#: `state` could hold an outcome word; 2 keeps the lifecycle and the outcome in separate fields.
SCHEMA = 2

#: The lifecycle, in order. `accepted` may go straight to `closed`: a run can be refused, cancelled
#: or interrupted while it is still in the queue, and a mandatory `running` in between would claim
#: an execution that never happened.
LIFECYCLE = ("accepted", "running", "closed")

#: Where each lifecycle position may go, itself included -- arriving twice is not a move.
_MAY_BECOME = {
    "accepted": ("accepted", "running", "closed"),
    "running": ("running", "closed"),
    "closed": ("closed",),
}

class Ledger:
    """A small durable record of what this gateway has already accepted."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data: dict = {"schema": SCHEMA, "nonces": {}, "runs": {}}
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
                # A document with no `schema` is one written before `settled_as` existed. It is read
                # without complaint and WITHOUT being rewritten: a load that writes is a load that
                # can corrupt, and the repair of an old entry belongs to the reconciliation that
                # reads the signed log, not to opening the file.
                "schema": int(loaded.get("schema") or 1),
                "nonces": dict(loaded.get("nonces") or {}),
                "runs": dict(loaded.get("runs") or {}),
            }

    def _write_locked(self) -> None:
        """Atomic: write beside the target, then rename over it."""
        # Stamped on every write, so a document that has been touched by this version says so and a
        # reader never has to guess which shape it is holding.
        self._data["schema"] = SCHEMA
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
              now: float | None = None, owner_account_id: str = "",
              admitted: dict | None = None) -> bool:
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
                # Which customer, so that a run rebuilt from this ledger after a restart is
                # still attributable to one. Without it a restarted gateway held runs whose
                # account was blank, and a blank account matches nothing -- which is the safe
                # direction, but it also means the customer cannot see their own interrupted
                # run, so the answer is to record it rather than to relax the comparison.
                "owner_account_id": str(owner_account_id),
                "state": "accepted",
                # WHAT A CLOSING LINE NEEDS AND A RESTART CANNOT RECOVER.
                #
                # A gateway that restarts must write the usage line for the run it interrupted,
                # and that line states the limits the run was admitted under and which policies
                # applied. Those live in memory on the record and die with the process. Taking
                # "whatever is configured now" instead would put a figure on a customer's
                # record that describes a different run, so they are written down here, once,
                # at the moment they are settled.
                #
                # Empty when the caller did not supply them: then the closing line says
                # UNATTRIBUTED rather than guessing, which is the same word this gateway
                # already uses for a policy it cannot name.
                "admitted": dict(admitted or {}),
            }
            if nonce:
                self._data["nonces"][str(nonce)] = now
            self._write_locked()
            return True

    def forget_runs(self, matches) -> int:
        """Remove every run entry `matches` selects. Returns how many. For deletion.

        What this costs, stated rather than discovered: the ledger is also what makes a run id
        unrepeatable, so a forgotten run id could be claimed again. That is acceptable HERE and
        only here -- the account those runs belonged to no longer exists, so there is nobody to
        replay a request as. It would not be acceptable as a general tidying operation, which is
        why this takes a predicate from one caller rather than an age.

        The nonces are deliberately NOT removed. A nonce is not personal data -- it is a random
        value a client chose -- and dropping them would turn deletion into a way to make old
        signed requests replayable.
        """
        with self._lock, ProcessLock(self.path):
            self._load()
            going = [run_id for run_id, entry in self._data["runs"].items()
                     if matches(run_id, entry)]
            for run_id in going:
                del self._data["runs"][run_id]
            if going:
                self._write_locked()
            return len(going)

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

    def note_lifecycle(self, run_id: str, state: str, at: float | None = None) -> str:
        """Move a run's LIFECYCLE position forward, and -- for `running` -- record WHEN.

        Returns where the run now is, which is not always where the caller asked for: a move that
        is not allowed leaves the file alone and returns what is actually there. A caller that
        needs to know whether its move was the one that happened compares.

        The time matters for exactly one reason and it is not bookkeeping: a gateway that restarts
        has to write a closing line for the run it interrupted, and a closing line whose billed
        figure was computed from times this process invented is a false statement about a
        customer's bill. `first_seen` already says when the job arrived; this says when it started,
        and the difference between them is what was waited rather than billed.

        Written only for `running`, because that is the only transition whose time is not
        recoverable from somewhere else: arrival is `first_seen`, and an ending is whenever the
        gateway is writing the line.

        THIS FIELD IS NOT AN OUTCOME. It was, and that is the defect this module was rewritten for:
        two paths put ending-words here and the last one to run decided what the file said. An
        ending lives in `settled_as`, copied from the signed line.
        """
        want = str(state)
        with self._lock, ProcessLock(self.path):
            # RE-READ INSIDE THE LOCK. The comparison that decides this move has to be against
            # what is on disk, not against what this object loaded at construction -- otherwise
            # two gateways sharing a directory each compare against their own stale copy and the
            # monotonicity holds in neither.
            self._load()
            entry = self._data["runs"].get(str(run_id))
            if entry is None:
                return ""
            here = str(entry.get("state") or "accepted")
            # A document from before this change can hold an outcome word here. It is not a
            # lifecycle position, so it is not compared as one: such an entry is treated as
            # `closed`, which refuses every further move and is the conservative reading.
            if here not in LIFECYCLE:
                here = "closed"
            if want not in _MAY_BECOME.get(here, ()):
                return here
            entry["state"] = want
            if want == "running" and not entry.get("started_at"):
                entry["started_at"] = float(time.time() if at is None else at)
            self._write_locked()
            return want

    def note_it_settled(self, run_id: str, settled_as: str,
                        at: float | None = None) -> tuple[str, bool]:
        """Record WHAT THE SIGNED LINE SAYS this run became. Once. Returns (what it says, wrote).

        The word must come from the line, never from the path that is calling. That is the whole
        repair: the shutdown path and the run's own handler both reach here, and on the parent they
        each wrote their own idea of the ending, so the file said whichever ran last.

        Writing the same word again is not a conflict -- two paths that both read the one line will
        both pass the same word, and that has to be allowed or an idempotent reconciliation could
        not run twice. A DIFFERENT word is refused and recorded: the log holds one line per run, so
        two different words mean somebody read something else, and that is worth keeping rather
        than resolving by whoever arrived second.

        The log's five words are `finished`, `refused`, `cancelled`, `unverified` and `interrupted`.
        That list is documentation and NOT a check, deliberately: the signed line is the authority and
        this field mirrors it, so refusing a word the log actually carries would be the original defect
        again -- a durable record disagreeing with the line because a code path preferred its own idea
        of the vocabulary.
        """
        word = str(settled_as)
        when = float(time.time() if at is None else at)
        with self._lock, ProcessLock(self.path):
            self._load()
            entry = self._data["runs"].get(str(run_id))
            if entry is None:
                return "", False
            already = str(entry.get("settled_as") or "")
            if already:
                if already != word:
                    # DURABLY, and without touching the value. A conflict that is only logged is a
                    # conflict nobody finds; one that overwrites is the defect again.
                    conflicts = entry.setdefault("settled_conflicts", [])
                    if isinstance(conflicts, list):
                        conflicts.append({"at": when, "already": already, "offered": word})
                        self._write_locked()
                return already, False
            entry["settled_as"] = word
            entry["settled_at"] = when
            # An ending is also the end of the lifecycle. Written here rather than by a second call,
            # so there is no window in which a run has an outcome and is still selectable as
            # mid-flight.
            entry["state"] = "closed"
            self._write_locked()
            return word, True

    def settled_as(self, run_id: str) -> str:
        """What the signed line says this run became, or "" if nothing has established it yet."""
        with self._lock, ProcessLock(self.path):
            self._load()
            entry = self._data["runs"].get(str(run_id))
            return str((entry or {}).get("settled_as") or "")

    def note_execution(self, run_id: str, ever_ran: bool) -> bool | None:
        """Record, from an authority, whether this run ever began. Returns what is now established.

        Only a worker that keeps a record can establish `False`. Nothing derives it from a missing
        timestamp, because `started_at` is written on a best effort: its absence means nobody wrote
        it, which is not the same as nothing having run.
        """
        with self._lock, ProcessLock(self.path):
            self._load()
            entry = self._data["runs"].get(str(run_id))
            if entry is None:
                return None
            known = entry.get("ever_ran")
            if isinstance(known, bool):
                # Established already. A second, contradicting answer is kept rather than applied,
                # for the same reason a contradicting outcome is.
                if known is not bool(ever_ran):
                    conflicts = entry.setdefault("execution_conflicts", [])
                    if isinstance(conflicts, list):
                        conflicts.append({"at": time.time(), "already": known,
                                          "offered": bool(ever_ran)})
                        self._write_locked()
                return known
            entry["ever_ran"] = bool(ever_ran)
            self._write_locked()
            return bool(ever_ran)

    def did_it_ever_run(self, run_id: str) -> bool | None:
        """True, False, or None for unknown -- and unknown is a real answer here.

        `True` when a slot was held, which is what `started_at` records. `False` only when a worker
        said so. `None` otherwise, including for every run whose best-effort `started_at` simply was
        not written: telling somebody their job never started because a timestamp is missing is a
        false statement in the one place they go to find out.
        """
        with self._lock, ProcessLock(self.path):
            self._load()
            entry = self._data["runs"].get(str(run_id))
            if entry is None:
                return None
            if entry.get("started_at"):
                return True
            known = entry.get("ever_ran")
            return known if isinstance(known, bool) else None

    def note_quota_repair(self, run_id: str, was, now: float) -> None:
        """Say that a quota figure was corrected from the signed line, and what it was.

        Recorded so a repair is auditable without writing a second usage line -- the log holds one
        line per run and this is not one. Written once per correction, so a reconciliation that has
        nothing left to correct writes nothing at all and is byte-stable on repetition.
        """
        with self._lock, ProcessLock(self.path):
            self._load()
            entry = self._data["runs"].get(str(run_id))
            if entry is None:
                return
            repairs = entry.setdefault("quota_repairs", [])
            if isinstance(repairs, list):
                repairs.append({"at": time.time(), "was": was, "now": float(now)})
                self._write_locked()

    def unfinished_runs(self, now: float | None = None) -> list[str]:
        """Runs with no established ending, and recent enough that a line may still be written.

        Selected on the ABSENCE OF A FACT rather than on a word. After reconciliation every run that
        has a signed line has `settled_as`, so what is left here is exactly the set that has no line
        -- which is the set a start owes one. A legacy entry whose old `state` happens to read
        `finished` is in this set if nothing signed ever said so, and that is deliberate: the word
        in that field was never evidence.

        Age-bounded, because writing a line is work with a worker in it.
        """
        now = time.time() if now is None else now
        with self._lock, ProcessLock(self.path):
            self._load()
            return sorted(
                run_id for run_id, entry in self._data["runs"].items()
                if not str(entry.get("settled_as") or "")
                and float(entry.get("first_seen", 0)) > now - SWEEP_AGAIN_WITHIN_SECONDS
            )

    def note_a_sandbox_was_asked_for(self, run_id: str) -> None:
        """This run got as far as asking the worker for a container.

        Kept apart from `running`, which is written when a SLOT is taken -- earlier, and before
        anything has been asked of a worker. The difference is one a closing line has to be able
        to state: a run interrupted between the two held a slot and never had a sandbox, and
        saying its sandbox was confirmed gone would claim one had existed.
        """
        with self._lock, ProcessLock(self.path):
            self._load()
            entry = self._data["runs"].get(str(run_id))
            if entry is None:
                return
            entry["asked_for_a_sandbox"] = True
            self._write_locked()

    def note_cleanup(self, run_id: str, verified: bool | None) -> bool | None:
        """Whether what a run left behind was confirmed gone. Returns what is now recorded.

        `True` IS ABSORBING, and it was not. This wrote whatever it was handed, and what it is
        handed on a later start is `record.cleanup_verified`, which is `None` when the worker could
        not be reached. So a start that could not ask overwrote an answer an earlier start HAD got
        -- replacing "confirmed gone" with "nobody could ask", and losing the only fact that stops
        anyone asking again.

        `None` and `False` may still move between themselves: neither ends the search, so neither
        can lose anything by being replaced by the other.
        """
        with self._lock, ProcessLock(self.path):
            self._load()
            entry = self._data["runs"].get(str(run_id))
            if entry is None:
                return None
            if entry.get("cleanup") is True:
                return True
            entry["cleanup"] = verified
            self._write_locked()
            return verified

    def runs_left_unswept(self, now: float | None = None) -> list[str]:
        """Runs whose sandbox nobody has confirmed is gone. Selected on that fact and nothing else.

        It used to also require `state == "interrupted"`, and that is how this very set could be
        escaped: the word was overwritable, so a run whose ending was rewritten by a later path fell
        out of the only place anything would have looked at it again -- whatever its cleanup said.
        The condition is now the fact the search is actually about.

        Only True stops the asking. False is "something is still there" and None is "nobody could
        ask"; neither is an answer that should end the search, and neither can be reached from True
        any more. Nothing here is gated on `asked_for_a_sandbox` or on `started_at` either: both are
        best-effort writes, and this module's own comments say a run can have a live container and
        still read as having never started. Using either as negative proof loses containers.

        Bounded by age, because asking is work with a worker in it, and a run old enough that its
        host has been rebooted since is not one to keep questioning a runtime about on every start.

        Runs whose ending is not established yet are NOT here: they are `unfinished_runs`, and that
        path sweeps them as part of closing them. The two sets are kept disjoint on purpose -- their
        union is `runs_needing_attention`, which is the predicate that matters -- because a start
        works through this one against a budget. Overlapping sets spend that budget twice on the same
        run and can leave the second half of recovery with none.
        """
        now = time.time() if now is None else now
        with self._lock, ProcessLock(self.path):
            self._load()
            return sorted(
                run_id for run_id, entry in self._data["runs"].items()
                if entry.get("cleanup") is not True
                and str(entry.get("settled_as") or "")
                and float(entry.get("first_seen", 0)) > now - SWEEP_AGAIN_WITHIN_SECONDS
            )

    def runs_needing_attention(self, now: float | None = None) -> list[str]:
        """THE predicate, in one place: an ending that is not established, or a cleanup that is not.

        `unfinished_runs` and `runs_left_unswept` are the two disjoint halves of this, and a start
        does different work for each. This exists so that what recovery is responsible for can be
        read as one sentence rather than inferred from two methods -- and so a test can check the
        predicate itself instead of checking one of its halves.
        """
        now = time.time() if now is None else now
        with self._lock, ProcessLock(self.path):
            self._load()
            return sorted(
                run_id for run_id, entry in self._data["runs"].items()
                if (not str(entry.get("settled_as") or "") or entry.get("cleanup") is not True)
                and float(entry.get("first_seen", 0)) > now - SWEEP_AGAIN_WITHIN_SECONDS
            )

    def snapshot(self) -> dict:
        """A copy of every run entry, taken under one lock and one read.

        For the reconciliation, which has to look at every run. Asking the ledger run by run means a
        file lock and a full parse per run, which on a month of history is a start that spends
        minutes deciding it has nothing to do.
        """
        with self._lock, ProcessLock(self.path):
            self._load()
            return {run_id: dict(entry) for run_id, entry in self._data["runs"].items()}


class LedgerUnreadable(Exception):
    """The durable record could not be read, so replays could not be recognised."""
