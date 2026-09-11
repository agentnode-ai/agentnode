"""What a client may consume, and the one thing that stops everything.

A gateway anybody can reach is a gateway anybody can exhaust. Until this existed the only thing
bounding a paired client was the wall clock of a single job: nothing counted how many it had sent,
how much it had already used, or how many were running at once. And there was nothing an operator
could do to stop all of it at once except kill the process, which loses the runs in flight rather
than ending them.

## Who decides

The operator, in a file in the gateway's own directory, which nothing a client sends can influence.
An allowance is read at admission rather than held from construction, so lowering a ceiling takes
effect on the next job rather than on the next restart -- and a run already in flight is left to
finish, because stopping it is what the kill switch is for and a quota is not a cancellation.

## What is counted, and where

Against the client id the ledger already records as a run's owner, in a file beside the ledger,
under the same process lock -- so two submissions arriving together cannot both be admitted past a
ceiling, and a restart does not forget what a client used a minute ago.

Use is forgotten by TIME and never by count. A counter that dropped its oldest entry when it filled
would be one an attacker empties by sending enough, and then walks through.

## The stop

One file. While it exists, nothing is admitted and the reason the operator gave is what every
client is told. If it cannot be read -- not absent, but unreadable -- the gateway treats itself as
stopped, because a gateway that cannot tell whether it has been stopped is not one to keep taking
work.

## What this does not establish

Counting use is not billing. Nothing here prices anything, and nothing here is charged.

A ceiling bounds a PAIRED client. An unpaired stranger is bounded by pairing, which counts its own
attempts and locks out on failures -- a different mechanism for a different problem.

And on one host these counts protect the MACHINE from a client. They do not protect clients from
each other: two clients' jobs still run on one kernel, which is what `ALPHA-BOUNDARY-0001` decided
against and what the second machine fixes.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from agentnode_sdk.gateway.filelock import ProcessLock

#: What the operator's limits are called in the gateway's own directory.
ALLOWANCE_NAME = "allowance.json"

#: Where use is counted. Beside the ledger, under its own lock.
USE_NAME = "use.json"

#: The file whose existence stops everything.
STOP_NAME = "stopped.json"

#: How long use is remembered. A window rather than a running total: an alpha that counted for
#: ever would refuse a client that behaved perfectly a month ago.
WINDOW_SECONDS = 24 * 60 * 60.0


#: What every refusal of these two kinds begins with. A client is told in prose, like every
#: other refusal, but the prose starts with something stable -- so "I am over a limit" can be
#: told from "my job was malformed" without anybody parsing a sentence.
STOPPED_SAYS = "this gateway is not taking work: "
CEILING_SAYS = "over the ceiling"


class Stopped(Exception):
    """This gateway is not taking work. Carries what the operator said about it."""

    def __init__(self, reason: str) -> None:
        super().__init__(STOPPED_SAYS + reason)
        self.reason = reason


class OverTheCeiling(Exception):
    """A paired client has reached one of its limits. Carries which, and when it lifts."""

    def __init__(self, which: str, message: str, lifts_at: float = 0.0) -> None:
        super().__init__(CEILING_SAYS + " (" + which + "): " + message)
        #: Which ceiling, by name, so a client can tell one from another and from every other
        #: kind of refusal.
        self.which = which
        self.lifts_at = lifts_at


@dataclass(frozen=True)
class Allowance:
    """What one client may use. Every field is a number an operator chose.

    Zero means no ceiling of that kind, which is what an alpha with one operator and one client
    starts as. It is spelled out rather than left to the absence of a field, so that a file
    somebody wrote by hand says what it means.
    """

    #: How many runs may be going at once.
    concurrent_runs: int = 0
    #: How many may be started in the window.
    runs_per_window: int = 0
    #: How many seconds of wall clock they may consume in the window, added up.
    seconds_per_window: int = 0
    window_seconds: float = WINDOW_SECONDS

    def as_dict(self) -> dict:
        return asdict(self)

    def digest(self) -> str:
        """What was in force, for a record to bind."""
        import hashlib

        return hashlib.sha256(
            json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


class CannotReadWhatWasUsed(OSError):
    """Raised instead of reading a damaged ledger as an empty one.

    Its own type because the caller must tell it from an absent file: absent means nothing has
    run yet, and unreadable means things have run and this gateway cannot see how many.
    """


class CannotReadTheCeilings(OSError):
    """Raised instead of guessing what an operator meant to allow.

    Its own type because the caller has to tell it from an absent file: absent is a gateway that
    has not been given limits, and unreadable is a gateway that HAS and cannot see them.
    """


def read_allowance(root: str | os.PathLike[str]) -> Allowance:
    """The operator's limits, or the ones an alpha starts with.

    Absent means no limits have been set, and the defaults apply. PRESENT AND UNREADABLE raises.

    An earlier version returned the defaults for both, with a docstring arguing that the defaults
    are not "no limits". They are: every ceiling defaults to 0, and 0 means unlimited. So a
    corrupt or truncated file silently removed every ceiling the operator had set, on a gateway
    that went on looking configured -- the failure and the thing it was protecting against wear
    the same face. That is the same mistake as a replay floor that starts again from zero, and it
    is wrong for the same reason: the permissive state is never the one to fall back to.
    """
    path = Path(root) / ALLOWANCE_NAME
    if not path.exists():
        return Allowance()
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CannotReadTheCeilings(
            "this gateway cannot read the limits it is supposed to be applying ("
            + str(exc)[:120] + "). It will not take work until it can: carrying on would mean "
            "applying no ceiling at all to a client the operator meant to bound. The file is "
            + str(path) + "; removing it restores the defaults deliberately.") from exc
    if not isinstance(body, dict):
        raise CannotReadTheCeilings(
            "the limits in " + str(path) + " are not an object, so this gateway cannot tell what "
            "it is supposed to allow. It will not take work until that is fixed.")
    return Allowance(
        concurrent_runs=int(body.get("concurrent_runs") or 0),
        runs_per_window=int(body.get("runs_per_window") or 0),
        seconds_per_window=int(body.get("seconds_per_window") or 0),
        window_seconds=float(body.get("window_seconds") or WINDOW_SECONDS),
    )


def write_allowance(root: str | os.PathLike[str], allowance: Allowance) -> Path:
    path = Path(root) / ALLOWANCE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomically(path, json.dumps(allowance.as_dict(), indent=2, sort_keys=True) + "\n")
    return path


# ------------------------------------------------------------------------------- the stop

def stop_everything(root: str | os.PathLike[str], reason: str, by: str = "") -> Path:
    """Refuse all work until somebody deliberately lifts it."""
    path = Path(root) / STOP_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomically(path, json.dumps(
        {"reason": reason or "the operator stopped this gateway", "at": time.time(), "by": by},
        indent=2, sort_keys=True) + "\n")
    return path


def start_again(root: str | os.PathLike[str]) -> bool:
    """Lift it. True when something was lifted."""
    path = Path(root) / STOP_NAME
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def why_it_is_stopped(root: str | os.PathLike[str]) -> str:
    """The operator's reason, or "" when this gateway is taking work.

    Unreadable is stopped. A file that exists and cannot be parsed, or a directory that cannot be
    listed, is a gateway that cannot tell whether it has been stopped -- and one that answered
    "not stopped" to that question would be answering a question nobody asked it.
    """
    path = Path(root) / STOP_NAME
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except OSError as exc:
        return ("this gateway cannot tell whether it has been stopped (" + str(exc) + "), so it "
                "is not taking work until somebody looks at it")
    try:
        body = json.loads(raw)
        said = str(body.get("reason") or "")
    except ValueError:
        said = ""
    return said or "this gateway has been stopped by its operator"


# ------------------------------------------------------------------------------- the counting

class Use:
    """What each client has used lately. Durable, locked, and forgotten by time."""

    def __init__(self, path: str | os.PathLike[str], window: float = WINDOW_SECONDS) -> None:
        self.path = Path(path)
        self.window = window
        self._lock = threading.Lock()

    def _load(self) -> dict:
        """What has been used. An unreadable file RAISES rather than reading as nothing.

        This is the third place in this gateway where the permissive fallback was the bug, and it
        is the same bug each time: an empty ledger means "this client has used nothing", which is
        exactly the state somebody who had exhausted their allowance would like it to be in.
        Damaging one file would have restored every client's full window, on a gateway that went
        on looking like it was counting.

        Absent is the only case that starts empty, and it is the honest one: nothing has run yet.
        """
        if not self.path.exists():
            return {}
        try:
            body = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CannotReadWhatWasUsed(
                "this gateway cannot read what its clients have already used ("
                + str(exc)[:120] + "), so it cannot tell whether the next job is within anyone's "
                "allowance. It will not take work until it can. The file is " + str(self.path)
                + "; removing it starts the counting again deliberately rather than by accident."
            ) from exc
        if not isinstance(body, dict):
            raise CannotReadWhatWasUsed(
                "what this gateway has recorded about use is not an object, so it cannot be read "
                "as a count of anything. " + str(self.path))
        return body

    def _forget(self, body: dict, now: float) -> dict:
        kept = {}
        for client, runs in body.items():
            fresh = [r for r in runs if float(r.get("at", 0)) > now - self.window]
            if fresh:
                kept[str(client)] = fresh
        return kept

    def note(self, client_id: str, run_id: str, now: float | None = None) -> None:
        """One run started. Written before it runs, so a crash counts it rather than losing it."""
        at = time.time() if now is None else now
        with self._lock, ProcessLock(self.path):
            body = self._forget(self._load(), at)
            body.setdefault(str(client_id), []).append(
                {"run_id": str(run_id), "at": at, "seconds": 0.0})
            _atomically(self.path, json.dumps(body, sort_keys=True))

    def claim(self, client_id: str, run_id: str, judge, now: float | None = None) -> None:
        """Look at what this client has used and write down another run, as ONE transaction.

        `judge(runs, seconds, oldest)` raises if this run may not start. It is called while the
        lock is held, and the claim is written before the lock is released.

        Reading and then writing as two steps is the bug this exists to remove: two requests
        arriving together both read "one run used, one allowed" and both then wrote, so a ceiling
        of one admitted two. The window between the look and the claim is exactly as long as the
        work in between, and there is no amount of care at the call site that closes it -- only
        holding the lock across both does.
        """
        at = time.time() if now is None else now
        with self._lock, ProcessLock(self.path):
            body = self._forget(self._load(), at)
            mine = body.get(str(client_id), [])
            judge(len(mine), sum(float(e.get("seconds", 0.0)) for e in mine),
                  min((float(e.get("at", at)) for e in mine), default=at))
            body.setdefault(str(client_id), []).append(
                {"run_id": str(run_id), "at": at, "seconds": 0.0})
            _atomically(self.path, json.dumps(body, sort_keys=True))

    def finished(self, client_id: str, run_id: str, seconds: float,
                 now: float | None = None) -> None:
        """How long it took, added to what that client has used."""
        at = time.time() if now is None else now
        with self._lock, ProcessLock(self.path):
            body = self._forget(self._load(), at)
            for entry in body.get(str(client_id), []):
                if entry.get("run_id") == str(run_id):
                    entry["seconds"] = float(seconds)
            _atomically(self.path, json.dumps(body, sort_keys=True))

    def so_far(self, client_id: str, now: float | None = None) -> tuple[int, float]:
        """(runs, seconds) this client has used inside the window."""
        at = time.time() if now is None else now
        with self._lock, ProcessLock(self.path):
            runs = self._forget(self._load(), at).get(str(client_id), [])
        return len(runs), sum(float(r.get("seconds", 0.0)) for r in runs)

    def oldest(self, client_id: str, now: float | None = None) -> float:
        at = time.time() if now is None else now
        with self._lock, ProcessLock(self.path):
            runs = self._forget(self._load(), at).get(str(client_id), [])
        return min((float(r.get("at", at)) for r in runs), default=at)


def _atomically(path: Path, text: str) -> None:
    """Written beside and renamed over, so a reader never sees half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=str(path.parent), prefix="." + path.name + "-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        os.chmod(path, 0o600)
    except OSError:                                           # pragma: no cover - advisory here
        pass
