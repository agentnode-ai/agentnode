"""How long this gateway keeps things, how a customer gets their data out, and how it goes.

## Every class, and a period for each

`CLASSES` is the table: what the file is, what it holds, how long it is kept by default, and what
a customer loses when it expires. An operator sets a period per class; the sweep enforces all of
them; `describe()` renders the table for a command or a page.

An earlier version had periods for the two classes that persist by default and said the rest
forget by themselves. That was true and it was not the point. A class that expires on a schedule
nobody chose has a retention period the operator cannot see or change, and "it is already short"
is not a policy. The classes that DO expire on their own still do -- the period here is a ceiling
over that, so a session that ends after twelve hours never reaches a seven-day period, and the
period is still the operator's to lower.

**A job's code and a job's output are in no class**, because they are never written to disk. They
are held in memory for the run and handed to the caller who submitted it.

## The default is not "for ever"

Zero means indefinitely and has to be written on purpose; a sweep reports which classes are being
kept that way. Nothing here is a legal opinion.

An unreadable retention file does **not** silently keep everything. It refuses to sweep and says
so: deleting on a guess is worse than not deleting, and a sweep that quietly did nothing is how a
retention policy becomes a document rather than a behaviour. A class that cannot be swept is
named in `problems` rather than counted as swept.

## Deletion is not retention

Retention is time passing. Deletion is somebody asking. They are separate calls, they leave
different traces, and only one of them is allowed to break the shape of the metering chain --
see `meter.erase`, which is how erasure and a hash chain are reconciled.

## What survives a deletion, and why

* **The fact that lines existed**, as signed tombstones: their number, when they were erased and
  why, and nothing about who or what. Removing them outright would mean anybody who can delete
  can also remove a line invisibly, which is the property the chain exists to have.
* **Nothing else** on this gateway. Credentials, sessions, enrolments, counters, ledger entries,
  the account record, the audit lines and the metering contents all go.
* **Backups and exports taken before the deletion**, which it cannot reach. See
  `deploy/backup-and-restore.sh` and `delete_account`, both of which say so rather than leaving
  it to be assumed.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

#: Where the operator's periods live.
RETENTION_NAME = "retention.json"

DAY = 24 * 60 * 60.0

#: EVERY class this gateway stores, with what it is, what it defaults to, and what a customer
#: loses when it expires. One table, so a class added later that is not in it is a class the
#: sweep does not know about -- and a test reads this against the files a real gateway writes.
#:
#: Several of these ALSO expire on their own, and keep doing so. The period here is a ceiling
#: over that rather than a replacement for it: a session that ends after twelve hours never
#: reaches a seven-day retention period, and the period is still the operator's to lower.
CLASSES = {
    "backups": {
        "file": "(sealed archives, outside the state directory)",
        "days": 35,
        "is": "the sealed copies a restore is made from",
        "expiring_means": "a customer deleted before this is gone from every copy this gateway "
                          "made, which is what the deletion promise rests on",
        "also_expires_on_its_own": False,
    },
    "audit": {
        "file": "audit.jsonl",
        "days": 90,
        "is": "every operation attempted, so a probe is visible afterwards",
        "expiring_means": "nobody can look back further than this at who did what",
        "also_expires_on_its_own": False,
    },
    "metering": {
        "file": "use-log.jsonl",
        "days": 400,
        "is": "what each run used, which is what a bill is made from",
        "expiring_means": "a run older than this can no longer be billed or disputed",
        "also_expires_on_its_own": False,
    },
    "sessions": {
        "file": "sessions.json",
        "days": 7,
        "is": "browser sign-ins",
        "expiring_means": "a person has to sign in again",
        "also_expires_on_its_own": True,
    },
    "enrolments": {
        "file": "enrolling.json",
        "days": 1,
        "is": "connections being set up, and their one-time download tickets",
        "expiring_means": "an unfinished setup has to be started again",
        "also_expires_on_its_own": True,
    },
    "ledger": {
        "file": "ledger.json",
        "days": 30,
        "is": "which runs were accepted, and the nonces that make a replay visible",
        "expiring_means": "a signed request older than this could be sent again",
        "also_expires_on_its_own": True,
    },
    "counters": {
        "file": "use.json",
        "days": 2,
        "is": "what each device and account has used inside the quota window",
        "expiring_means": "nothing: the window itself is shorter than this",
        "also_expires_on_its_own": True,
    },
    "rate": {
        "file": "rate.json",
        "days": 1,
        "is": "how many requests each caller made in the last minute",
        "expiring_means": "nothing: the window itself is one minute",
        "also_expires_on_its_own": True,
    },
    "events": {
        "file": "events.jsonl",
        "days": 30,
        "is": "what an operator watched: counts, capacity, refusals, alerts",
        "expiring_means": "an incident older than this has no counts behind it",
        "also_expires_on_its_own": False,
    },
    "invitations": {
        "file": "joining.json",
        "days": 1,
        "is": "invitations to join an account that a customer has open",
        "expiring_means": "an unused invitation has to be made again",
        "also_expires_on_its_own": True,
    },
    "exports": {
        "file": "exports.jsonl",
        "days": 400,
        "is": "who took a copy of an account's data, and when",
        "expiring_means": "'who has a copy of this' becomes unanswerable for older copies",
        "also_expires_on_its_own": False,
    },
}

#: Kept as names for the two that had them before, so nothing that imported them breaks.
DEFAULT_AUDIT_DAYS = CLASSES["audit"]["days"]
DEFAULT_METERING_DAYS = CLASSES["metering"]["days"]


class RetentionUnreadable(OSError):
    """The periods cannot be read, so nothing is swept.

    Its own type because the caller must tell it from an absent file: absent means the defaults,
    and unreadable means an operator set something this gateway cannot see. Sweeping on a guess
    could delete a record somebody is required to keep.
    """


@dataclass(frozen=True)
class Retention:
    """How long each class is kept. Zero means indefinitely, and has to be written on purpose.

    One field per entry in `CLASSES`, named `<class>_days`, so the two cannot drift: a test
    compares the fields of this dataclass against the keys of that table and fails if either
    grows without the other.
    """

    backups_days: int = CLASSES["backups"]["days"]
    audit_days: int = CLASSES["audit"]["days"]
    metering_days: int = CLASSES["metering"]["days"]
    sessions_days: int = CLASSES["sessions"]["days"]
    enrolments_days: int = CLASSES["enrolments"]["days"]
    ledger_days: int = CLASSES["ledger"]["days"]
    counters_days: int = CLASSES["counters"]["days"]
    rate_days: int = CLASSES["rate"]["days"]
    events_days: int = CLASSES["events"]["days"]
    exports_days: int = CLASSES["exports"]["days"]
    invitations_days: int = CLASSES["invitations"]["days"]

    def as_dict(self) -> dict:
        return asdict(self)

    def days_for(self, class_name: str) -> int:
        return int(getattr(self, "%s_days" % class_name))


def describe() -> list:
    """Every class, for an operator command and for a customer-facing page.

    A list rather than prose, because "what do you keep and for how long" is a question with a
    table for an answer, and a paragraph is how a class quietly stops being in it.
    """
    return [dict(name=name, **what) for name, what in sorted(CLASSES.items())]


def read_retention(root: str | os.PathLike[str]) -> Retention:
    path = Path(root) / RETENTION_NAME
    if not path.exists():
        return Retention()
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RetentionUnreadable(
            "this gateway cannot read how long it is supposed to keep things (%s), so it is "
            "sweeping nothing. The file is %s; removing it restores the defaults deliberately."
            % (str(exc)[:120], path)) from exc
    if not isinstance(body, dict):
        raise RetentionUnreadable("the retention periods in %s are not an object" % path)
    unknown = sorted(set(body) - set(Retention().as_dict()))
    if unknown:
        raise RetentionUnreadable(
            "the retention periods in %s name %s, which this gateway does not understand. A "
            "period that reads as configured and is not applied is worse than none."
            % (path, ", ".join(repr(u) for u in unknown)))
    return Retention(**{
        "%s_days" % name: max(0, int(body.get("%s_days" % name, what["days"])))
        for name, what in CLASSES.items()
    })


def write_retention(root: str | os.PathLike[str], retention: Retention) -> Path:
    path = Path(root) / RETENTION_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomically(path, json.dumps(retention.as_dict(), indent=2, sort_keys=True) + "\n")
    return path


# --------------------------------------------------------------------------- the sweep


#: How often a running gateway sweeps. Hourly rather than daily: a sweep that only happens at
#: some particular time of day never happens on a gateway that is restarted before it.
SWEEP_EVERY_SECONDS = 60 * 60.0

#: Where the last sweep is recorded, so a restart does not mean starting the clock again and a
#: gateway that has never swept can be told from one whose sweep is failing.
LAST_SWEEP_NAME = "retention-last-swept.json"


def due(root: str | os.PathLike[str], now: float | None = None) -> bool:
    """Whether a sweep is owed. A gateway that has never swept owes one.

    Reads `at`, which is when a sweep last finished with NOTHING outstanding. A sweep that could
    not do part of its job does not advance it -- see `sweep_if_due` -- so one that fails keeps
    being owed instead of being put off for an hour.
    """
    at = time.time() if now is None else now
    path = Path(root) / LAST_SWEEP_NAME
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
        return (at - float(body.get("at") or 0.0)) >= SWEEP_EVERY_SECONDS
    except (OSError, ValueError):
        return True


def last_sweep(root: str | os.PathLike[str]) -> dict:
    """What the last attempt did, including what it could not do. For an operator to read.

    `{}` when there has never been one. Deliberately not an exception: "this gateway has never
    swept" is an answer a command should print, not an error it should raise.
    """
    try:
        body = json.loads((Path(root) / LAST_SWEEP_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return body if isinstance(body, dict) else {}


def sweep_if_due(root: str | os.PathLike[str], now: float | None = None) -> dict | None:
    """The call a running gateway makes. Returns what was swept, or None if nothing was owed.

    This exists because a review was right that an invocable function is not enforcement. The
    criterion says retention must be enforced by something that RUNS, and until this was wired
    into the gateway's own timer the periods in `retention.json` described an intention.
    """
    at = time.time() if now is None else now
    if not due(root, at):
        return None
    done = sweep(root, now=at)

    # A SWEEP WITH PROBLEMS IS NOT A SWEEP THAT HAPPENED. What stood here recorded the attempt
    # either way, so a class that could not be swept -- a file gone read-only, a store that will
    # not parse -- put the next attempt off for an hour and left a gateway reporting that it had
    # swept. Two things follow from that being wrong, and both are here:
    #
    #   * `at` advances only when nothing was outstanding, so `due` keeps saying yes and the
    #     gateway's own timer tries again on its next tick rather than in an hour;
    #   * what could not be done is WRITTEN DOWN, with when it was last tried, so an operator
    #     reading `agentnode gateway keeps` is told rather than having to notice.
    problems = list(done.get("problems") or ())
    before = last_sweep(root)
    _atomically(Path(root) / LAST_SWEEP_NAME, json.dumps({
        # When a sweep last finished CLEAN. This is the only field `due` reads.
        "at": float(before.get("at") or 0.0) if problems else at,
        "last_tried": at,
        "problems": problems,
        "removed": done,
    }, sort_keys=True) + "\n")
    return done


def sweep(root: str | os.PathLike[str], now: float | None = None) -> dict:
    """Drop what is past its period. Idempotent, and safe to interrupt.

    Each file is rewritten beside itself and renamed over, so an interrupted sweep leaves the
    original intact rather than half a file. Running it twice does the same thing as running it
    once, which is what lets it be a timer rather than a ceremony.
    """
    at = time.time() if now is None else now
    keep = read_retention(root)
    done = {"problems": [], "swept": {}}

    for name in sorted(CLASSES):
        days = keep.days_for(name)
        if not days:
            # Zero means indefinitely, and has to have been written on purpose. Reported so an
            # operator reading a sweep can see which classes are being kept for ever.
            done["swept"][name] = "kept indefinitely"
            continue
        try:
            done["swept"][name] = _SWEEPERS[name](Path(root), at - days * DAY)
        except Exception as exc:                              # noqa: BLE001
            done["problems"].append("%s could not be swept: %s" % (name, str(exc)[:160]))
            done["swept"][name] = "FAILED"

    # The two names the earlier shape used, kept so nothing that read them breaks. They are the
    # same numbers, said twice, rather than a second source of truth.
    done["audit_removed"] = done["swept"].get("audit", 0)
    done["metering_erased"] = done["swept"].get("metering", 0)
    return done


def _sweep_jsonl(path: Path, cutoff: float, when) -> int:
    """Drop lines older than the cutoff from a JSON-lines file. Returns how many went.

    `when(line)` says what a line's age is. A line this cannot read is KEPT: sweeping is about
    age, and a line whose age cannot be established is not a line known to be old.
    """
    if not path.exists():
        return 0
    kept, dropped = [], 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            line = json.loads(raw)
        except ValueError:
            kept.append(raw)
            continue
        try:
            age = float(when(line) or 0.0)
        except (TypeError, ValueError):
            kept.append(raw)
            continue
        if age < cutoff:
            dropped += 1
            continue
        kept.append(raw)
    if dropped:
        _atomically(path, "\n".join(kept) + ("\n" if kept else ""))
    return dropped


def _sweep_map(path: Path, cutoff: float, when, inside: str = "") -> int:
    """The same, for a file holding one JSON object keyed by id."""
    if not path.exists():
        return 0
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Unreadable is not empty. Rewriting it would destroy whatever is there; the sweep says
        # it could not do this one and the caller reports it.
        raise
    if not isinstance(body, dict):
        return 0
    target = body.get(inside) if inside else body
    if not isinstance(target, dict):
        return 0
    going = [key for key, value in target.items()
             if isinstance(value, dict) and float(when(value) or 0.0) < cutoff]
    for key in going:
        del target[key]
    if going:
        _atomically(path, json.dumps(body, sort_keys=True))
    return len(going)


def _sweep_ledger(root: Path, cutoff: float) -> int:
    """Runs, nonces and challenges together -- they are one file and one decision."""
    path = root / CLASSES["ledger"]["file"]
    if not path.exists():
        return 0
    body = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(body, dict):
        return 0
    went = 0
    runs = body.get("runs")
    if isinstance(runs, dict):
        going = [k for k, v in runs.items()
                 if isinstance(v, dict) and float(v.get("first_seen") or 0.0) < cutoff]
        for k in going:
            runs.pop(k, None)
            if isinstance(body.get("challenges"), dict):
                body["challenges"].pop(k, None)
        went += len(going)
    nonces = body.get("nonces")
    if isinstance(nonces, dict):
        going = [k for k, t in nonces.items() if float(t or 0.0) < cutoff]
        for k in going:
            del nonces[k]
        went += len(going)
    if went:
        _atomically(path, json.dumps(body, sort_keys=True))
    return went


def _sweep_counters(root: Path, cutoff: float) -> int:
    """`use.json` is {key: [entry, ...]}. A key whose entries all go, goes."""
    path = root / CLASSES["counters"]["file"]
    if not path.exists():
        return 0
    body = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(body, dict):
        return 0
    went, kept = 0, {}
    for key, entries in body.items():
        if not isinstance(entries, list):
            kept[key] = entries
            continue
        fresh = [e for e in entries
                 if isinstance(e, dict) and float(e.get("at") or 0.0) >= cutoff]
        went += len(entries) - len(fresh)
        if fresh:
            kept[key] = fresh
    if went:
        _atomically(path, json.dumps(kept, sort_keys=True))
    return went


def _sweep_rate(root: Path, cutoff: float) -> int:
    """`rate.json` is {key: [timestamp, ...]}."""
    path = root / CLASSES["rate"]["file"]
    if not path.exists():
        return 0
    body = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(body, dict):
        return 0
    went, kept = 0, {}
    for key, stamps in body.items():
        if not isinstance(stamps, list):
            kept[key] = stamps
            continue
        fresh = [t for t in stamps if float(t or 0.0) >= cutoff]
        went += len(stamps) - len(fresh)
        if fresh:
            kept[key] = fresh
    if went:
        _atomically(path, json.dumps(kept, sort_keys=True))
    return went


def _sweep_metering(root: Path, cutoff: float) -> int:
    """Erased to signed tombstones rather than removed. See `meter.erase`."""
    from agentnode_sdk.gateway import meter

    return meter.erase(root, "past this gateway's metering retention period",
                       lambda line: float(line.get("finished_at") or 0.0) < cutoff)


def _sweep_backups(root: Path, cutoff: float) -> int:
    """Delete sealed archives older than the cutoff. Returns how many went.

    THE PERIOD IS AN OPERATIONAL CHOICE AND IS WRITTEN DOWN AS ONE. 35 days is a month with a
    margin, chosen so a monthly restore rehearsal always has something to rehearse with; it is
    not derived from anything and is not presented as if it were. Like every other period in this
    table it is the operator's to change, and lowering it shortens the deletion promise rather
    than breaking it.

    Backups do not live in the state directory -- keeping them there would put the copy and the
    thing it is a copy of on the same disk -- so the place to sweep is read from
    `backups.json` beside the retention table. A gateway that has not been told where its backups
    are sweeps nothing and SAYS so, rather than reporting a confident zero: "nowhere to look" and
    "nothing was old enough" are different answers and only one of them means the promise holds.
    """
    where = _where_the_backups_are(root)
    if where is None:
        # ABSENT IS NOT UNREADABLE, and neither is an error. A gateway that was never told where
        # its backups are is not broken -- most are not the machine that holds them. It reports a
        # NON-ANSWER, the same shape as "kept indefinitely", so a sweep cannot read as "the
        # backups were checked and none were old" when nothing was looked at. `backups.json`
        # beside `retention.json` is what turns this into a real sweep.
        return "no backup directory is configured, so none were checked"
    if not where.is_dir():
        # Told where, and it is not there: that IS a problem, because somebody wrote down an
        # answer this gateway cannot honour and the deletion promise rests on it.
        raise RuntimeError("the backup directory %s is not there" % where)
    gone = 0
    for archive in sorted(where.glob("*.sealed")):
        try:
            if archive.stat().st_mtime < cutoff:
                archive.unlink()
                gone += 1
        except OSError as exc:
            raise RuntimeError("%s could not be removed: %s" % (archive.name, exc)) from exc
    return gone


def _where_the_backups_are(root: Path):
    """The directory this gateway writes its sealed archives to, or None if it was never told."""
    import json as _json

    try:
        said = _json.loads((Path(root) / "backups.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        # Unreadable is not absent. A gateway that cannot read where its backups are must not
        # report that it swept them.
        raise RuntimeError("backups.json could not be read: %s" % exc) from exc
    where = str(said.get("directory") or "").strip() if isinstance(said, dict) else ""
    return Path(where) if where else None


#: One sweeper per class. A class in `CLASSES` with no sweeper here is a KeyError at sweep time
#: rather than a class quietly kept for ever, and a test asserts the two sets match.
_SWEEPERS = {
    "backups": _sweep_backups,
    "audit": lambda root, cutoff: _sweep_jsonl(
        root / CLASSES["audit"]["file"], cutoff, lambda line: line.get("at")),
    "metering": _sweep_metering,
    "sessions": lambda root, cutoff: _sweep_map(
        root / CLASSES["sessions"]["file"], cutoff, lambda v: v.get("opened_at")),
    "enrolments": lambda root, cutoff: _sweep_map(
        root / CLASSES["enrolments"]["file"], cutoff, lambda v: v.get("began_at")),
    "ledger": _sweep_ledger,
    "counters": _sweep_counters,
    "rate": _sweep_rate,
    "events": lambda root, cutoff: _sweep_jsonl(
        root / CLASSES["events"]["file"], cutoff, lambda line: line.get("at")),
    "exports": lambda root, cutoff: _sweep_jsonl(
        root / CLASSES["exports"]["file"], cutoff, lambda line: line.get("at")),
    "invitations": lambda root, cutoff: _sweep_map(
        root / CLASSES["invitations"]["file"], cutoff, lambda v: v.get("made_at")),
}


# --------------------------------------------------------------------------- deletion


def _the_state(given):
    """Accept the gateway's state or the service holding it.

    Both are natural things for a caller to have -- an operator command holds a state, a request
    path holds a service -- and making the caller remember which one this wanted is exactly the
    kind of friction that ends in somebody passing the wrong one and getting a confusing
    attribute error rather than a deletion.
    """
    return given if hasattr(given, "root") else given.state


def delete_account(state, account_id: str, because: str = "the customer asked") -> dict:
    """Remove a customer from this gateway. Returns what went, by class.

    Ordered so that access stops FIRST and the record goes last: a deletion that erased the
    metering and then failed while removing credentials would leave a customer who can still
    send work and whose use is no longer recorded.
    """
    from agentnode_sdk.gateway import accounts as _accounts
    from agentnode_sdk.gateway import admission, allowance, meter

    state = _the_state(state)
    wanted = str(account_id or "")
    if not _accounts.well_formed(wanted):
        raise _accounts.NoSuchAccount(wanted)
    root = Path(state.root)
    # `problems` is not decoration. Every step below used to be wrapped in a bare `except:
    # pass`, so a deletion that failed halfway reported the same shape of success as one that
    # worked -- and "we deleted your data" is the one claim that must never be made on a guess.
    # Anything that could not be done is named here, and `complete` is False when the list is
    # not empty.
    went = {"devices": 0, "sessions": 0, "enrolments": 0, "audit_lines": 0,
            "metering_erased": 0, "counters": 0, "ledger_runs": 0, "export_records": 0,
            "account_record": False, "problems": [], "complete": True}

    devices = [str(d.get("client_id") or "") for d in state.devices_in(wanted)]

    # 1. Access. Sessions and enrolments before credentials, because both are asked ABOUT a
    #    device and a device that is already gone cannot be asked about.
    from agentnode_sdk.access.enrolment import Connections
    from agentnode_sdk.access.sessions import Sessions

    from agentnode_sdk.gateway.joining import Joining

    sessions, enrolling = Sessions(root), Connections(root)
    try:
        went["invitations"] = Joining(root).drop_everything_of(wanted)
    except (OSError, ValueError) as exc:
        went["problems"].append("this account's open invitations could not be withdrawn: %s"
                                % str(exc)[:160])
    for device in devices:
        went["sessions"] += sessions.end_every(device)
        went["enrolments"] += enrolling.drop_everything_touching(device)
    for device in devices:
        if state.revoke_client(device, within_account=wanted):
            went["devices"] += 1

    # 2. Counters, which are keyed by account and by device.
    use = allowance.Use(root / allowance.USE_NAME)
    rate = admission.RateLimit(root / admission.RATE_NAME)
    for key in [wanted, *devices]:
        went["counters"] += int(bool(use.forget_everything_about(key)))
        went["counters"] += int(bool(rate.forget(key)))

    # 3. The audit. Lines name the account and the device and nothing else about a person, and
    #    both of those identify them, so both go.
    went["audit_lines"] = _drop_audit_lines(
        root, lambda line: (str(line.get("account")) == wanted
                            or str(line.get("device")) in set(devices)))

    # 4. The ledger, which names the account and the device against every run it claimed.
    #    A test found this missing: everything else was gone and the account id was still
    #    sitting in the file that survives a restart, which is the one that matters most.
    try:
        from agentnode_sdk.gateway.ledger import Ledger

        book = Ledger(root / "ledger.json")
        went["ledger_runs"] = book.forget_runs(
            lambda _run_id, entry: (str(entry.get("owner_account_id") or "") == wanted
                                    or str(entry.get("owner_client_id") or "")
                                    in set(devices)))
    except (OSError, ValueError) as exc:
        went["problems"].append(
            "the ledger still names this account and its runs: %s" % str(exc)[:160])

    # 5. The metering. Erased rather than removed: see `meter.erase` for how that is reconciled
    #    with a chain whose whole purpose is to show that nothing was removed.
    try:
        went["metering_erased"] = meter.erase(
            root, because,
            lambda line: (str(line.get("account_id") or "") == wanted
                          or str(line.get("client_id") or "") in set(devices)))
    except (OSError, ValueError) as exc:
        went["problems"].append(
            "this account's metered use could NOT be erased and is still readable: %s"
            % str(exc)[:160])

    # 6. The record of who took a COPY of this account. Every line names the account, so a
    #    deletion that left them behind left the identifier in a file nobody was looking at --
    #    and then reported itself complete. A review found it, and nothing here would have: the
    #    test that checked the identifier was gone never made an export first.
    #
    #    What this CANNOT reach is said rather than implied. The copies themselves: an export
    #    handed to somebody is in their hands, and a backup taken before the deletion still
    #    contains the customer. Deletion cannot reach a file it does not have. Removing the
    #    RECORD of those copies stops this gateway holding the identifier, and that is not the
    #    same as the copies being gone.
    try:
        went["export_records"] = _drop_export_lines(
            root, lambda line: str(line.get("account_id") or "") == wanted)
    except (OSError, ValueError) as exc:
        went["problems"].append(
            "the record of who took a copy of this account is still there: %s" % str(exc)[:160])

    # 7. The account itself, last.
    try:
        went["account_record"] = bool(state.accounts.forget(wanted))
    except Exception as exc:                                  # noqa: BLE001
        went["problems"].append("the account record itself could not be removed: %s"
                                % str(exc)[:160])
    went["complete"] = not went["problems"]
    return went


def delete_run(state, run_id: str, because: str = "the customer asked") -> dict:
    """Remove one run. Everything the gateway holds about it, by the same rules.

    Three stores, and one of them is deliberately empty:

    * the metering line, erased to a signed tombstone like any other;
    * the ledger entry, which names the run, the device that submitted it and the account;
    * the audit -- which never recorded a run id at all. It records WHICH operation and which
      of its declared parameter NAMES a refusal was about, never a value. Saying so is more
      useful than a line of code that removes nothing.

    The NONCE is kept. A nonce is a random value a client chose, it identifies nobody, and
    dropping it would turn deleting a run into a way to make the signed request that started it
    replayable. So the run id becomes free again and the request that used it does not.
    """
    from agentnode_sdk.gateway import meter
    from agentnode_sdk.gateway.ledger import Ledger

    state = _the_state(state)
    root = Path(state.root)
    wanted = str(run_id or "")
    went = {"metering_erased": 0, "ledger_runs": 0, "audit_lines": 0,
            "problems": [], "complete": True}
    try:
        went["metering_erased"] = meter.erase(
            root, because, lambda line: str(line.get("run_id") or "") == wanted)
    except (OSError, ValueError) as exc:
        went["problems"].append("this run's metered line could NOT be erased: %s"
                                % str(exc)[:160])
    try:
        went["ledger_runs"] = Ledger(root / "ledger.json").forget_runs(
            lambda run, _entry: str(run) == wanted)
    except (OSError, ValueError) as exc:
        went["problems"].append("the ledger still names this run: %s" % str(exc)[:160])
    went["complete"] = not went["problems"]
    return went


def _drop_export_lines(root: Path, matches) -> int:
    """The record of who took a COPY. Kept separate from the audit's reader on purpose: two
    different files with two different reasons, and one function wearing both names would read
    as though removing from them were one decision."""
    return _drop_lines(Path(root) / EXPORTS_NAME, matches)


def _drop_audit_lines(root: Path, matches) -> int:
    return _drop_lines(Path(root) / "audit.jsonl", matches)


def _drop_lines(path: Path, matches) -> int:
    """Rewrite a JSON-lines file without the matching lines. Returns how many went.

    A line that does not parse is KEPT. It cannot be shown to be this customer's, and throwing
    away what cannot be read would turn a damaged file into a deletion nobody asked for.
    """
    try:
        written = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return 0
    kept, dropped = [], 0
    for raw in written.splitlines():
        if not raw.strip():
            continue
        try:
            line = json.loads(raw)
        except ValueError:
            kept.append(raw)
            continue
        if matches(line):
            dropped += 1
            continue
        kept.append(raw)
    if dropped:
        _atomically(path, "\n".join(kept) + ("\n" if kept else ""))
    return dropped


# --------------------------------------------------------------------------- export


#: Where the fact that an export happened is recorded. Beside the audit, not in it: the audit is
#: per-operation and an export is an operator act, so folding one into the other would mean an
#: operator action appearing as though a customer had performed it.
EXPORTS_NAME = "exports.jsonl"


def note_an_export(root: str | os.PathLike[str], account_id: str, by: str,
                   how_many_bytes: int, now: float | None = None) -> None:
    """Write down that somebody took a copy of an account's data.

    An export hands over everything the service holds about a person. Producing one without a
    record means nobody can answer "who has a copy of this, and since when" -- which is the
    first question asked when a copy turns up somewhere it should not be.
    """
    at = time.time() if now is None else now
    path = Path(root) / EXPORTS_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(handle, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": round(at, 3), "account_id": str(account_id),
                             "by": str(by or "")[:64], "bytes": int(how_many_bytes)},
                            sort_keys=True) + "\n")


def exports_of(root: str | os.PathLike[str], account_id: str = "") -> list:
    """Every export taken, or every export of one account."""
    path = Path(root) / EXPORTS_NAME
    out = []
    try:
        written = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for raw in written.splitlines():
        if not raw.strip():
            continue
        try:
            line = json.loads(raw)
        except ValueError:
            continue
        if not account_id or str(line.get("account_id")) == str(account_id):
            out.append(line)
    return out


def export_account(state, account_id: str) -> dict:
    """Everything this gateway holds about one customer, as plain JSON.

    Three rules, and each is the answer to a way this could go wrong:

    * **one account.** Nothing here reads anything belonging to anybody else, so an export
      handed to the wrong person is still only that person's own data;
    * **no secret.** Tokens are stored hashed and the hashes are not included either -- a hash
      of a credential is still a thing to check guesses against. What is included is what the
      customer would see in their own device list;
    * **readable without this software.** JSON with spelled-out keys, not a pickle and not a
      database file, because "you can have your data" means nothing if it needs our code to open.
    """
    from agentnode_sdk.gateway import accounts as _accounts
    from agentnode_sdk.gateway import allowance, meter
    from agentnode_sdk.gateway.redaction import scrub_everything

    state = _the_state(state)
    wanted = str(account_id or "")
    if not _accounts.well_formed(wanted):
        raise _accounts.NoSuchAccount(wanted)
    root = Path(state.root)
    devices = state.devices_in(wanted)
    device_ids = {str(d.get("client_id") or "") for d in devices}

    lines = []
    try:
        written = (root / "audit.jsonl").read_text(encoding="utf-8")
    except OSError:
        # Absent, or unreadable. Two states with one reading here, and that is acceptable only
        # because an export is a copy rather than a decision: nothing is refused or permitted on
        # the strength of it. `what_is_not_here` below says what an export leaves out.
        written = ""
    for raw in written.splitlines():
        if not raw.strip():
            continue
        try:
            line = json.loads(raw)
        except ValueError:
            continue
        if str(line.get("account")) == wanted:
            lines.append(line)

    use = allowance.Use(root / allowance.USE_NAME)
    runs, seconds = use.so_far(wanted)

    try:
        account = state.accounts.get(wanted).as_dict()
    except (_accounts.NoSuchAccount, _accounts.AccountsUnreadable):
        account = {"account_id": wanted, "state": "could not be read"}

    return scrub_everything({
        "about": "everything AgentNode holds about one account on this gateway",
        # What an export CANNOT do, said in the export itself rather than in a document the
        # person holding it will not have. A copy taken before a deletion is still a copy.
        "what_this_copy_means": (
            "This is a copy taken at one moment. Deleting this account later removes it from "
            "the gateway and cannot remove it from this file or from any backup taken before "
            "the deletion. Whoever holds a copy holds it until they delete it."),
        "account": account,
        "devices": [{"device_id": str(d.get("client_id") or ""),
                     "name": str(d.get("client_name") or ""),
                     "paired_at": d.get("issued_at")}
                    for d in devices],
        "what_has_been_used_in_the_current_window": {"runs": runs, "seconds": seconds},
        "metered_use": [line for line in meter.read(root)
                        if not meter.is_a_tombstone(line)
                        and (str(line.get("account_id") or "") == wanted
                             or str(line.get("client_id") or "") in device_ids)],
        "operations_attempted": lines,
        "what_is_not_here": [
            "credentials, in any form, including the hashes this gateway stores",
            "anything belonging to another account",
            "the contents of jobs, which this gateway never writes down",
            "lines erased earlier at this account's own request",
        ],
    })


def _atomically(path: Path, text: str) -> None:
    """Beside and renamed over, with the bounded retry Windows needs. One implementation."""
    from agentnode_sdk.gateway.filelock import atomically

    atomically(path, text)
