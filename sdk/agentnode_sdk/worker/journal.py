"""What this worker has already been asked to do, written down before it does it.

The transport refuses a replayed MESSAGE: every frame carries a nonce, the nonce is remembered,
and a frame seen twice is refused (`worker/protocol.py`). That is not the same as refusing a
replayed JOB, and the difference is the whole reason this file exists. A retry carries a FRESH
nonce -- it has to, or it would be refused as a replay -- so the same `run_id` delivered twice is
two perfectly legitimate messages, and until now `Bench.answer` would have run the job twice.

On one machine that never happened, because the gateway opens one connection per request, never
retries, and never re-issues a `run` for a run id. A network between the two removes all three of
those accidents at once.

## The rule

`run_id` is the execution identity. For one run id this worker executes AT MOST ONCE, ever --
across a retry, a reconnect, a gateway restart and its own restart. The second delivery does not
execute; it gets what the first one produced, or an honest "still running", or an honest
"nobody knows".

Same run id with DIFFERENT work is not a retry, it is a collision or an attack, and it is refused
rather than resolved. What counts as "different" is a digest over the parts of a job that decide
what actually runs: the command, the artefact, what goes in on stdin, the network mode and its
allowed destinations, and the ceilings. Two deliveries that disagree on any of those are two
different jobs wearing one name.

## At most once, not exactly once

A crash between starting a container and recording that it started leaves a run whose outcome
nobody can establish. The honest answer is `unknown`, and `unknown` is a state this journal can
represent. It is NEVER resolved by running the job again: running it again is the one thing that
could turn "we do not know" into "it happened twice", and of the two, twice is worse. Exactly-once
delivery is not available over a network; exactly-once EXECUTION is, and that is what this gives.

## What is written down, and what is not

The digest of the job, never the job. No artefact, no stdin, no token, no policy document -- a
worker host is the machine most likely to be compromised and its disk should hold as little as
possible. The outcome IS kept, because answering "what happened to run X" after a lost connection
is the entire point of keeping anything, and it is kept bounded and only until it is either
acknowledged or too old to be wanted.

## Crash safety

One file per run, written to a temporary name, fsynced, and renamed. A rename is atomic, so a
reader sees either the previous record or the new one and never half of one. Every record also
carries a checksum over its own contents, so a record damaged by something other than this code
is refused rather than believed -- and a record that cannot be read is a refusal to execute, not
permission. A journal that cannot be written is the same: nothing runs. A worker that executed
foreign code without being able to write down that it had would be a worker that can run the same
job any number of times.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any

#: The on-disk shape.
FORMAT = 1

#: Records are readable by the worker account and nothing else.
FILE_MODE = 0o600
DIR_MODE = 0o700

#: How long a finished record is kept when nobody has acknowledged it. Long enough that a control
#: plane which was down for a night can still come back and ask what happened; short enough that
#: a worker's disk is not an archive. Codex's condition: never evict while the gateway may
#: legitimately still retry.
KEEP_UNACKNOWLEDGED_SECONDS = 24 * 3600

#: And the ceiling on how many records are kept at all, oldest acknowledged ones first.
KEEP_AT_MOST = 10_000

#: How much of an outcome's text is worth keeping to answer with later. A cap, because this is a
#: worker's disk and the text came from somebody else's program.
MOST_TEXT = 64 * 1024

# ------------------------------------------------------------------------------- the states

#: Written before anything is started. A record in this state means the decision to run was
#: taken and the container may or may not exist yet.
ACCEPTED = "accepted"
#: The container was started. Between here and `FINISHED` an outcome exists in the world that
#: this worker may not have written down yet.
STARTED = "started"
#: It ended, and the outcome is in the record.
FINISHED = "finished"
#: It ended and cleanup has not been established yet.
CLEANUP_PENDING = "cleanup_pending"
#: Cleanup was established.
CLEANED = "cleaned"
#: Cleanup could not be established, and that is recorded as itself rather than as either
#: of the other two.
CLEANUP_UNPROVEN = "cleanup_unproven"

STATES = (ACCEPTED, STARTED, FINISHED, CLEANUP_PENDING, CLEANED, CLEANUP_UNPROVEN)

#: States from which nothing more will happen on its own.
SETTLED = (FINISHED, CLEANED, CLEANUP_UNPROVEN)

# ------------------------------------------------------------- what a claim comes back as

#: Nobody has asked for this run before. The caller -- and only this caller -- may execute it.
FRESH = "fresh"
#: Asked for before and not finished. Whoever asked first owns it; this delivery does not run.
IN_FLIGHT = "in_flight"
#: Asked for before and finished. The recorded outcome is the answer.
DONE = "done"
#: It was started and this worker cannot say how it ended. Never resolved by running it again.
UNKNOWN = "unknown"
#: The same run id carrying different work.
CONFLICT = "conflict"
#: A record that exists and cannot be believed. Refuses, like every other unreadable thing here.
UNREADABLE = "unreadable"


class JournalRefused(Exception):
    """The journal will not let this run proceed, and says why."""

    def __init__(self, cause: str, because: str, what_to_do: str) -> None:
        super().__init__(because)
        self.cause = cause
        self.because = because
        self.what_to_do = what_to_do


@dataclass(frozen=True)
class Claim:
    """The answer to "may I run this, and if not, why not"."""

    verdict: str
    run_id: str
    state: str = ""
    outcome: dict | None = None
    cleanup: Any = None
    recorded_digest: str = ""

    @property
    def may_execute(self) -> bool:
        """Exactly one verdict permits execution, and it is the one that created the record."""
        return self.verdict == FRESH


def digest_of_job(job: Any) -> str:
    """What makes this job THIS job.

    Over the parts that decide what actually runs, and not over the message that carried them:
    two deliveries of the same work differ in their nonce, their request id and their deadline,
    and none of those changes what would execute. The artefact is hashed rather than included --
    the digest goes in the record, the artefact never does.
    """
    limits = getattr(job, "limits", None)
    body = {
        "command": list(getattr(job, "command", ()) or ()),
        "artifact": hashlib.sha256(
            (getattr(job, "artifact", b"") or b"")
            if isinstance(getattr(job, "artifact", b""), (bytes, bytearray))
            else str(getattr(job, "artifact", "")).encode("utf-8")).hexdigest(),
        "stdin": hashlib.sha256(
            (getattr(job, "stdin", "") or "").encode("utf-8")).hexdigest(),
        "network": str(getattr(job, "network", "") or ""),
        "allowed_domains": sorted(getattr(job, "allowed_domains", ()) or ()),
        "container_name": str(getattr(job, "container_name", "") or ""),
        "limits": {
            "cpu": getattr(limits, "cpu", None),
            "memory_mb": getattr(limits, "memory_mb", None),
            "processes": getattr(limits, "processes", None),
            "wall_clock_s": getattr(limits, "wall_clock_s", None),
            "storage_mb": getattr(limits, "storage_mb", None),
        },
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _checksum(record: dict) -> str:
    body = {k: v for k, v in record.items() if k != "checksum"}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"),
                   default=str).encode("utf-8")).hexdigest()


def _trim(outcome: dict | None) -> dict | None:
    """An outcome small enough to keep. Text is capped; nothing else is touched."""
    if outcome is None:
        return None
    kept = dict(outcome)
    for field in ("stdout", "stderr"):
        text = kept.get(field)
        if isinstance(text, str) and len(text) > MOST_TEXT:
            kept[field] = text[:MOST_TEXT] + "\n[... kept short by the worker's journal]"
    return kept


class Journal:
    """One directory, one file per run."""

    def __init__(self, at: str | os.PathLike[str], *, now=time.time) -> None:
        self.at = str(at)
        self._now = now
        try:
            os.makedirs(self.at, mode=DIR_MODE, exist_ok=True)
        except OSError as exc:
            raise JournalRefused(
                "journal_unwritable",
                "this worker cannot create its journal at %s (%s), and a worker that cannot "
                "write down what it has been asked to do could run the same job any number of "
                "times." % (self.at, exc),
                "Give the worker account a writable directory and point --journal at it.") from exc

    # ------------------------------------------------------------------ where a record lives

    def _path(self, run_id: str) -> str:
        """Named by a digest of the run id, not by the run id.

        A run id comes from another machine. Using it as a filename would make the shape of this
        directory something the other machine decides, and "../" is a run id too.
        """
        return os.path.join(self.at, hashlib.sha256(
            str(run_id).encode("utf-8")).hexdigest() + ".json")

    # ------------------------------------------------------------------ reading and writing

    def _read(self, run_id: str) -> dict | None:
        try:
            with open(self._path(run_id), "rb") as handle:
                raw = handle.read()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise JournalRefused(
                "journal_unreadable",
                "the journal record for %s could not be read (%s)." % (run_id, exc),
                "Look at the worker's journal directory. Nothing will run for this run id "
                "until its record can be read, because running it might run it twice.") from exc
        try:
            record = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return {"__damaged__": "not json"}
        if not isinstance(record, dict):
            return {"__damaged__": "not an object"}
        if record.get("checksum") != _checksum(record):
            # A record this code wrote always matches. One that does not was damaged by
            # something else, and a damaged record is not evidence of anything -- least of all
            # of a job not having run.
            return {"__damaged__": "checksum"}
        if int(record.get("format") or 0) != FORMAT:
            return {"__damaged__": "format"}
        return record

    def _write(self, record: dict, *, only_if_new: bool = False) -> None:
        record["checksum"] = _checksum(record)
        path = self._path(record["run_id"])
        # UNIQUE PER CALL, not per process: several threads claiming the same run id at
        # the same instant share a pid, and a shared temporary name means one of them
        # writes while another unlinks. The concurrency test found exactly that.
        temporary = "%s.new.%s" % (path, secrets.token_hex(8))
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        try:
            handle = os.open(temporary, flags, FILE_MODE)
            try:
                with os.fdopen(handle, "wb") as fh:
                    fh.write(json.dumps(record, sort_keys=True).encode("utf-8"))
                    fh.flush()
                    os.fsync(fh.fileno())
            except BaseException:
                os.unlink(temporary)
                raise
            if only_if_new:
                # THE ONE IRREVERSIBLE DECISION, and it is a link() rather than a rename: link
                # fails if the name already exists, atomically, so exactly one of any number of
                # simultaneous deliveries creates the record and the rest are told it existed.
                # A rename would have silently overwritten whichever one got there first.
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    os.unlink(temporary)
                    raise
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
            else:
                os.replace(temporary, path)
        except FileExistsError:
            raise
        except OSError as exc:
            raise JournalRefused(
                "journal_unwritable",
                "this worker could not write its journal record for %s (%s), so it will not "
                "run it: a job it cannot write down is a job it could run again."
                % (record.get("run_id"), exc),
                "Look at the worker's journal directory and its free space.") from exc

    # ------------------------------------------------------------------ the decision

    def claim(self, run_id: str, request_digest: str) -> Claim:
        """May this delivery execute? The ONLY place that answer is produced.

        Creating the record and deciding to run are the same act -- they are not two steps with
        a window between them -- because any window is a window in which a second delivery of
        the same job also decides to run.
        """
        run_id = str(run_id)
        existing = self._read(run_id)
        if existing is not None:
            return self._about(run_id, request_digest, existing)

        record = {
            "format": FORMAT,
            "run_id": run_id,
            "request_digest": str(request_digest),
            "state": ACCEPTED,
            "accepted_at": self._now(),
            "started_at": None,
            "finished_at": None,
            "outcome": None,
            "cleanup": None,
            "acknowledged": False,
        }
        try:
            self._write(record, only_if_new=True)
        except FileExistsError:
            # Somebody else created it between the read and the link. They own the run.
            again = self._read(run_id)
            if again is None:                                 # pragma: no cover - vanished
                raise JournalRefused(
                    "journal_raced",
                    "the record for %s appeared and disappeared while it was being claimed."
                    % run_id,
                    "Ask again. Nothing was run.") from None
            return self._about(run_id, request_digest, again)
        return Claim(FRESH, run_id, state=ACCEPTED, recorded_digest=str(request_digest))

    def _about(self, run_id: str, request_digest: str, record: dict) -> Claim:
        """What an existing record means for a delivery that has just arrived."""
        if record.get("__damaged__"):
            raise JournalRefused(
                "journal_damaged",
                "the journal record for %s is damaged (%s), so this worker cannot tell whether "
                "that run has already happened. It will not run it: running it might run it a "
                "second time." % (run_id, record["__damaged__"]),
                "Take the damaged record out of the way deliberately, after establishing what "
                "happened to that run. This worker will not decide it for you.")

        recorded = str(record.get("request_digest") or "")
        if recorded != str(request_digest):
            # NOT RESOLVED, REFUSED. One of the two deliveries is wrong, and picking either
            # would be choosing which of two callers to be wrong about.
            raise JournalRefused(
                "run_id_reused_for_different_work",
                "run %s has already been accepted for different work. A run id names one piece "
                "of work; this delivery carries another. Nothing was run for it." % run_id,
                "Use a fresh run id. If two callers are producing the same ids, that is the "
                "thing to fix -- this worker will not choose between them.")

        state = str(record.get("state") or "")
        if state in (FINISHED, CLEANUP_PENDING, CLEANED, CLEANUP_UNPROVEN):
            return Claim(DONE, run_id, state=state, outcome=record.get("outcome"),
                         cleanup=record.get("cleanup"), recorded_digest=recorded)
        if state == STARTED:
            # It was started and no outcome was ever written. Something happened in the world
            # and this worker cannot say what. That is `unknown`, and it stays unknown.
            return Claim(UNKNOWN, run_id, state=state, recorded_digest=recorded)
        return Claim(IN_FLIGHT, run_id, state=state or ACCEPTED, recorded_digest=recorded)

    def look(self, run_id: str) -> Claim | None:
        """What is known about a run, without claiming anything. `None` if nothing is."""
        record = self._read(str(run_id))
        if record is None:
            return None
        return self._about(str(run_id), str(record.get("request_digest") or ""), record)

    # ------------------------------------------------------------------ moving a run along

    def _amend(self, run_id: str, **fields) -> None:
        record = self._read(str(run_id))
        if record is None or record.get("__damaged__"):
            raise JournalRefused(
                "journal_damaged" if record else "journal_missing",
                "the journal record for %s is not there to be updated." % run_id,
                "This is a programming error: a run is claimed before it is noted.")
        record.update(fields)
        self._write(record)

    def note_started(self, run_id: str) -> None:
        """Written BEFORE the container is started, so that a crash during the start leaves a
        record saying something was begun rather than a record saying nothing was."""
        self._amend(run_id, state=STARTED, started_at=self._now())

    def note_finished(self, run_id: str, outcome: dict | None) -> None:
        self._amend(run_id, state=FINISHED, finished_at=self._now(),
                    outcome=_trim(outcome))

    def note_cleanup(self, run_id: str, verified: Any) -> None:
        """`True`, `False` or `None`, and the three are kept apart. `None` is "nobody could
        establish it", which is not the same as "it is still there"."""
        state = (CLEANED if verified is True
                 else CLEANUP_UNPROVEN if verified is None else CLEANUP_PENDING)
        self._amend(run_id, state=state, cleanup=verified)

    def acknowledge(self, run_id: str) -> None:
        """The control plane has the outcome and has written its own line. After this the
        record is only kept for as long as the retention rule keeps it."""
        self._amend(run_id, acknowledged=True, acknowledged_at=self._now())

    # ------------------------------------------------------------------ retention

    def sweep(self, *, now: float | None = None) -> int:
        """Drop what is no longer needed. Returns how many records went.

        Never drops a record that is not settled -- an unfinished run is the one thing nobody
        may forget -- and never drops an unacknowledged settled one until the recovery window
        has passed, because until then the control plane may still legitimately come back and
        ask what happened.
        """
        at = self._now() if now is None else now
        settled: list[tuple[float, str, bool]] = []
        gone = 0
        for name in os.listdir(self.at):
            if not name.endswith(".json") or ".new." in name:
                continue
            path = os.path.join(self.at, name)
            try:
                record = json.loads(open(path, "rb").read().decode("utf-8"))
            except (OSError, UnicodeDecodeError, ValueError):
                continue                                      # damaged: kept, never guessed at
            if str(record.get("state")) not in SETTLED:
                continue
            when = float(record.get("finished_at") or record.get("accepted_at") or 0.0)
            acknowledged = bool(record.get("acknowledged"))
            if acknowledged or at - when > KEEP_UNACKNOWLEDGED_SECONDS:
                try:
                    os.unlink(path)
                    gone += 1
                except OSError:                               # pragma: no cover
                    pass
            else:
                settled.append((when, path, acknowledged))
        # And a ceiling on the rest, oldest first, so a worker nobody ever acknowledges still
        # has a bounded directory.
        if len(settled) > KEEP_AT_MOST:
            for _when, path, _ack in sorted(settled)[:len(settled) - KEEP_AT_MOST]:
                try:
                    os.unlink(path)
                    gone += 1
                except OSError:                               # pragma: no cover
                    pass
        return gone

    def count(self) -> int:
        return len([n for n in os.listdir(self.at)
                    if n.endswith(".json") and ".new." not in n])


__all__ = ["ACCEPTED", "CLEANED", "CLEANUP_PENDING", "CLEANUP_UNPROVEN", "CONFLICT", "Claim",
           "DONE", "FINISHED", "FORMAT", "FRESH", "IN_FLIGHT", "Journal", "JournalRefused",
           "KEEP_AT_MOST", "KEEP_UNACKNOWLEDGED_SECONDS", "MOST_TEXT", "SETTLED", "STARTED",
           "STATES", "UNKNOWN", "UNREADABLE", "digest_of_job"]
