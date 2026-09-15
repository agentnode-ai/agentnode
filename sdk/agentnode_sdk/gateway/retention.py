"""How long this gateway keeps things, how a customer gets their data out, and how it goes.

## What is actually kept

Most of what this gateway holds already forgets by itself, and saying so precisely matters more
than adding a policy on top of it:

    sessions          expire, and are removed when they do
    enrolments        expire, and are tidied on the next read
    use counters      a rolling window; entries outside it are dropped on every write
    rate counters     the same, over a minute
    ledger nonces     pruned by age, which is what makes replay protection bounded
    a job's output    held in memory for the run's lifetime and never written to disk

Two things do not, and they are the two with a reason to persist:

    audit.jsonl       every operation attempted, so a probe is visible afterwards
    use-log.jsonl     what each run used, which is what a bill is eventually made from

So there are exactly two configurable periods, and pretending there are twelve would be a
configuration surface that mostly does nothing.

## The default is not "for ever"

    audit             90 days
    metering          400 days

Neither is a legal opinion and both are stated where somebody can change them. 400 rather than 365
because a yearly reconciliation happens after the year ends.

An unreadable retention file does **not** silently keep everything. It refuses to sweep and says
so: deleting on a guess is worse than not deleting, and a sweep that quietly did nothing is how a
retention policy becomes a document rather than a behaviour.

## Deletion is not retention

Retention is time passing. Deletion is somebody asking. They are separate calls, they leave
different traces, and only one of them is allowed to break the shape of the metering chain --
see `meter.erase`, which is how erasure and a hash chain are reconciled.

## What survives a deletion, and why

* **The fact that lines existed**, as signed tombstones: their number, when they were erased and
  why, and nothing about who or what. Removing them outright would mean anybody who can delete
  can also remove a line invisibly, which is the property the chain exists to have.
* **Nothing else.** Credentials, sessions, enrolments, counters, the account record, the audit
  lines and the metering contents all go.
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

#: The two classes that persist. Everything else forgets by itself; see the module docstring.
CLASSES = ("audit", "metering")

#: What each class is kept for when nobody has said otherwise.
DEFAULT_AUDIT_DAYS = 90
DEFAULT_METERING_DAYS = 400

DAY = 24 * 60 * 60.0


class RetentionUnreadable(OSError):
    """The periods cannot be read, so nothing is swept.

    Its own type because the caller must tell it from an absent file: absent means the defaults,
    and unreadable means an operator set something this gateway cannot see. Sweeping on a guess
    could delete a record somebody is required to keep.
    """


@dataclass(frozen=True)
class Retention:
    """How long each class is kept. Zero means indefinitely, and has to be written on purpose."""

    audit_days: int = DEFAULT_AUDIT_DAYS
    metering_days: int = DEFAULT_METERING_DAYS

    def as_dict(self) -> dict:
        return asdict(self)


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
    return Retention(
        audit_days=max(0, int(body.get("audit_days", DEFAULT_AUDIT_DAYS))),
        metering_days=max(0, int(body.get("metering_days", DEFAULT_METERING_DAYS))),
    )


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
    """Whether a sweep is owed. A gateway that has never swept owes one."""
    at = time.time() if now is None else now
    path = Path(root) / LAST_SWEEP_NAME
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
        return (at - float(body.get("at") or 0.0)) >= SWEEP_EVERY_SECONDS
    except (OSError, ValueError):
        return True


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
    _atomically(Path(root) / LAST_SWEEP_NAME,
                json.dumps({"at": at, "removed": done}, sort_keys=True) + "\n")
    return done


def sweep(root: str | os.PathLike[str], now: float | None = None) -> dict:
    """Drop what is past its period. Idempotent, and safe to interrupt.

    Each file is rewritten beside itself and renamed over, so an interrupted sweep leaves the
    original intact rather than half a file. Running it twice does the same thing as running it
    once, which is what lets it be a timer rather than a ceremony.
    """
    at = time.time() if now is None else now
    keep = read_retention(root)
    done = {"audit_removed": 0, "metering_erased": 0, "problems": []}

    if keep.audit_days:
        try:
            done["audit_removed"] = _sweep_audit(root, at - keep.audit_days * DAY)
        except OSError as exc:
            done["problems"].append("the audit could not be swept: %s" % str(exc)[:160])

    if keep.metering_days:
        try:
            from agentnode_sdk.gateway import meter

            cutoff = at - keep.metering_days * DAY
            done["metering_erased"] = meter.erase(
                root, "past this gateway's metering retention period",
                lambda line: float(line.get("finished_at") or 0.0) < cutoff)
        except (OSError, ValueError) as exc:
            done["problems"].append("the metering record could not be swept: %s"
                                    % str(exc)[:160])
    return done


def _sweep_audit(root, cutoff: float) -> int:
    path = Path(root) / "audit.jsonl"
    if not path.exists():
        return 0
    kept, dropped = [], 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            line = json.loads(raw)
        except ValueError:
            # A torn line is kept rather than dropped: sweeping is about age, and a line this
            # cannot read is not a line it knows the age of.
            kept.append(raw)
            continue
        if float(line.get("at") or 0.0) < cutoff:
            dropped += 1
            continue
        kept.append(raw)
    if dropped:
        _atomically(path, "\n".join(kept) + ("\n" if kept else ""))
    return dropped


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
    went = {"devices": 0, "sessions": 0, "enrolments": 0, "audit_lines": 0,
            "metering_erased": 0, "counters": 0, "ledger_runs": 0, "account_record": False}

    devices = [str(d.get("client_id") or "") for d in state.devices_in(wanted)]

    # 1. Access. Sessions and enrolments before credentials, because both are asked ABOUT a
    #    device and a device that is already gone cannot be asked about.
    from agentnode_sdk.access.enrolment import Connections
    from agentnode_sdk.access.sessions import Sessions

    sessions, enrolling = Sessions(root), Connections(root)
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
    except (OSError, ValueError):
        went["ledger_runs"] = 0

    # 5. The metering. Erased rather than removed: see `meter.erase` for how that is reconciled
    #    with a chain whose whole purpose is to show that nothing was removed.
    try:
        went["metering_erased"] = meter.erase(
            root, because,
            lambda line: (str(line.get("account_id") or "") == wanted
                          or str(line.get("client_id") or "") in set(devices)))
    except (OSError, ValueError):
        went["metering_erased"] = 0

    # 6. The account itself, last.
    went["account_record"] = bool(state.accounts.forget(wanted))
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
    went = {"metering_erased": 0, "ledger_runs": 0, "audit_lines": 0}
    try:
        went["metering_erased"] = meter.erase(
            root, because, lambda line: str(line.get("run_id") or "") == wanted)
    except (OSError, ValueError):
        pass
    try:
        went["ledger_runs"] = Ledger(root / "ledger.json").forget_runs(
            lambda run, _entry: str(run) == wanted)
    except (OSError, ValueError):
        pass
    return went


def _drop_audit_lines(root: Path, matches) -> int:
    path = Path(root) / "audit.jsonl"
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
