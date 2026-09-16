"""What an operator needs to see, and where it goes when nobody has chosen a provider.

Choosing where metrics and alerts are sent is a decision with a bill attached, so it is not made
here. What IS made here is the shape: a small interface with one method, and a default that writes
to a file beside the gateway's other state. An operator who later picks a provider writes one
adapter; nothing that produces an event has to change, and nothing was built around an assumption
about which provider it would be.

## What is worth watching, and why each one

    states            how many runs are accepted, running, stopping, finished, refused. The
                      shape of a gateway in trouble is visible here before it is visible
                      anywhere else: stopping climbing and finished flat is a teardown that is
                      not completing
    capacity          how much of the ceiling is in use. An operator asked to raise a limit
                      needs to know whether the limit is what is being hit
    error rates       refusals per reason. Not one number: "over a ceiling" rising is a
                      customer needing more, and "not authenticated" rising is somebody
                      guessing, and a single error rate hides the difference
    abuse signals     refusals concentrated on one account, and unknown-tool or unknown-route
                      probing. Patterns, NOT intent -- see `admission` for why that distinction
                      is load-bearing rather than a nicety
    aborted cleanups  a sandbox that was not confirmed gone. The one number that means somebody
                      has to look now rather than tomorrow, because it is the property the whole
                      product rests on

## Alert rules

Evaluated here against those counts, so a gateway with no provider still has alerting rather than
having "alerting, once you configure something". A rule produces an event like any other; what a
provider does with an event marked as an alert is the provider's business.

## What never reaches an event

The same rule as everywhere else: no credential, no artifact, no job output, no customer-supplied
text. Events carry counts, names from closed lists, and account identifiers -- and everything is
scrubbed on the way out regardless, because this is the surface most likely to grow a field in a
hurry during an incident.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

#: Where events go when nobody has chosen anywhere else.
EVENTS_NAME = "events.jsonl"

#: What an event can be. Closed, so a dashboard can be written against it.
KINDS = ("counts", "alert", "incident")

#: How serious. Three, because more than three means nobody agrees what the middle ones mean.
INFO, WARNING, CRITICAL = "info", "warning", "critical"


class Sink:
    """Where events go. One method, on purpose.

    A provider adapter implements `emit` and nothing else. Anything richer -- batching, retries,
    a session, a schema -- belongs inside an adapter rather than in the interface every producer
    has to satisfy.
    """

    def emit(self, event: dict) -> None:                      # pragma: no cover - interface
        raise NotImplementedError


class LocalFileSink(Sink):
    """The default. A JSON line per event, beside the gateway's other state.

    Chosen rather than "no sink" because a gateway that only counts things when somebody has
    bought a monitoring product is a gateway with no history of the incident that made them buy
    one.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def emit(self, event: dict) -> None:
        from agentnode_sdk.gateway.redaction import scrub_everything

        line = json.dumps(scrub_everything(event), sort_keys=True)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                handle = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(handle, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
        except OSError:                                       # pragma: no cover - a full disk
            # Losing a metric must never lose a run. This is the one place in the gateway where
            # swallowing an error is right, and it is written down as a decision rather than
            # left as a bare except.
            pass


class NowhereSink(Sink):
    """For a test, and for an operator who has deliberately turned this off."""

    def __init__(self) -> None:
        self.seen: list = []

    def emit(self, event: dict) -> None:
        self.seen.append(event)


# --------------------------------------------------------------------------- what is counted


@dataclass
class Counts:
    """One snapshot. Every field is a number this gateway can actually produce."""

    at: float = 0.0
    runs_by_state: dict = field(default_factory=dict)
    #: How much of each ceiling is in use, as (in use, ceiling). A ceiling of 0 is not reported:
    #: there is nothing to be at a fraction of.
    capacity: dict = field(default_factory=dict)
    refusals_by_reason: dict = field(default_factory=dict)
    #: Accounts with the most refusals lately, and how many. A PATTERN, and nothing more.
    refusals_by_account: dict = field(default_factory=dict)
    probes: int = 0
    cleanups_not_confirmed: int = 0
    accounts: int = 0
    devices: int = 0
    stopped_because: str = ""

    def as_event(self) -> dict:
        return {"kind": "counts", "at": round(self.at, 3),
                "runs_by_state": dict(self.runs_by_state),
                "capacity": {k: list(v) for k, v in self.capacity.items()},
                "refusals_by_reason": dict(self.refusals_by_reason),
                "refusals_by_account": dict(self.refusals_by_account),
                "probes": self.probes,
                "cleanups_not_confirmed": self.cleanups_not_confirmed,
                "accounts": self.accounts, "devices": self.devices,
                "stopped_because": self.stopped_because}


#: How far back the refusal counts look. Short enough that a burst is visible as a burst.
RECENT_SECONDS = 15 * 60.0


def look(service, now: float | None = None, since: float = RECENT_SECONDS) -> Counts:
    """Take a snapshot. Reads what the gateway already holds; changes nothing."""
    from agentnode_sdk.gateway import accounts as _accounts
    from agentnode_sdk.gateway.allowance import why_it_is_stopped

    at = time.time() if now is None else now
    counts = Counts(at=at)

    for record in list(service.runs.values()):
        state = str(getattr(record, "state", "") or "unknown")
        counts.runs_by_state[state] = counts.runs_by_state.get(state, 0) + 1
        if getattr(record, "finished_at", None) and getattr(
                record, "cleanup_verified", None) is not True:
            counts.cleanups_not_confirmed += 1

    try:
        allowed = service.allowance()
        from agentnode_sdk.gateway.protocol import is_terminal

        going = sum(1 for r in service.runs.values() if not is_terminal(r.state))
        if allowed.concurrent_runs:
            counts.capacity["concurrent_runs"] = (going, allowed.concurrent_runs)
        if allowed.account_concurrent_runs:
            counts.capacity["account_concurrent_runs"] = (
                going, allowed.account_concurrent_runs)
    except Exception:                                         # noqa: BLE001
        # A gateway that cannot read its ceilings is already refusing work and saying why.
        # Reporting no capacity figure is honest; inventing one would not be.
        pass

    for line in _audit_since(service, at - since):
        outcome = str(line.get("outcome") or "")
        if outcome in ("carried_out", ""):
            continue
        counts.refusals_by_reason[outcome] = counts.refusals_by_reason.get(outcome, 0) + 1
        who = str(line.get("account") or "(nobody)")
        counts.refusals_by_account[who] = counts.refusals_by_account.get(who, 0) + 1
        if outcome in ("unknown_operation", "not_a_route", "not_authenticated"):
            counts.probes += 1

    devices = service.state.paired_clients()
    counts.devices = len(devices)
    counts.accounts = len({str(d.get("account_id")
                               or _accounts.solo_account_for(str(d.get("client_id") or "")))
                           for d in devices})
    try:
        counts.stopped_because = why_it_is_stopped(service.state.root) or ""
    except Exception as exc:                                  # noqa: BLE001
        counts.stopped_because = "cannot tell (%s)" % str(exc)[:80]
    return counts


def _audit_since(service, cutoff: float):
    path = Path(service.state.root) / "audit.jsonl"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        if not raw.strip():
            continue
        try:
            line = json.loads(raw)
        except ValueError:
            continue
        if float(line.get("at") or 0.0) >= cutoff:
            yield line


# --------------------------------------------------------------------------- alert rules


@dataclass(frozen=True)
class Rule:
    """One thing worth waking somebody for, and the words they will read at 3am."""

    name: str
    severity: str
    says: str
    #: Given `Counts`, returns None or the reason this fired.
    fires: object

    def check(self, counts: Counts):
        try:
            why = self.fires(counts)
        except Exception:                                     # noqa: BLE001 - a rule must not
            return None                                       # be able to take the gateway down
        if not why:
            return None
        return {"kind": "alert", "at": round(counts.at, 3), "rule": self.name,
                "severity": self.severity, "because": why, "what_it_means": self.says}


def _a_sandbox_was_not_confirmed_gone(counts: Counts):
    if counts.cleanups_not_confirmed:
        return "%d finished run(s) whose sandbox was not confirmed gone" % (
            counts.cleanups_not_confirmed)
    return None


def _the_gateway_is_stopped(counts: Counts):
    return ("this gateway is not taking work: " + counts.stopped_because
            if counts.stopped_because else None)


def _capacity_is_nearly_gone(counts: Counts):
    tight = [name for name, (used, ceiling) in counts.capacity.items()
             if ceiling and used >= ceiling * 0.9]
    return "at or near the ceiling on: " + ", ".join(sorted(tight)) if tight else None


def _somebody_is_probing(counts: Counts):
    if counts.probes >= 20:
        return ("%d request(s) in the last 15 minutes named an operation, a route or a "
                "credential this gateway does not have" % counts.probes)
    return None


def _one_account_is_being_refused_a_lot(counts: Counts):
    loudest = sorted(counts.refusals_by_account.items(), key=lambda kv: -kv[1])[:1]
    if loudest and loudest[0][1] >= 50 and loudest[0][0] != "(nobody)":
        return ("%s has been refused %d time(s) in the last 15 minutes. That is a PATTERN and "
                "not a finding about what they were trying to do." % loudest[0])
    return None


#: The rules this gateway ships with. An operator can add to them; none of them needs a provider.
RULES = (
    Rule("a sandbox was not confirmed gone", CRITICAL,
         "The one property everything else rests on. Look now.",
         _a_sandbox_was_not_confirmed_gone),
    Rule("somebody is probing", WARNING,
         "Names that do not exist are being tried. Bounded by the rate limit; worth seeing.",
         _somebody_is_probing),
    Rule("one account is being refused a lot", WARNING,
         "Could be abuse, could be a broken script. It is a reason to look, not a verdict.",
         _one_account_is_being_refused_a_lot),
    Rule("capacity is nearly gone", WARNING,
         "Jobs are about to start being refused for want of room.",
         _capacity_is_nearly_gone),
    Rule("the gateway is stopped", INFO,
         "Deliberate, if somebody did it deliberately.",
         _the_gateway_is_stopped),
)


def observe(service, sink: Sink, now: float | None = None) -> dict:
    """One pass: take the counts, emit them, evaluate every rule, emit what fired.

    Returns what was emitted, so a caller -- a timer, a health endpoint, a test -- can act on it
    without reading the file back.
    """
    counts = look(service, now=now)
    sink.emit(counts.as_event())
    fired = [alert for alert in (rule.check(counts) for rule in RULES) if alert]
    for alert in fired:
        sink.emit(alert)
    return {"counts": counts.as_event(), "alerts": fired}


def health(service, now: float | None = None) -> dict:
    """What a load balancer or an operator's own check asks for.

    Deliberately narrow: whether this gateway is serving, whether it is taking work, and whether
    it has been measured. No counts, no account, no configuration -- a health endpoint is the one
    thing most likely to be left reachable by accident, so it is built to be safe when it is.
    """
    ready, because = False, "not measured"
    try:
        readiness = service.readiness_now()
        ready, because = bool(readiness.ready), str(readiness.reason or "")
    except Exception as exc:                                  # noqa: BLE001
        because = "could not be determined (%s)" % type(exc).__name__

    taking_work = False
    try:
        from agentnode_sdk.gateway.allowance import why_it_is_stopped

        taking_work = not (why_it_is_stopped(service.state.root) or "")
    except Exception:                                         # noqa: BLE001
        taking_work = False
    return {"serving": True, "measured": ready, "taking_work": taking_work,
            "because": because if not ready else ""}
