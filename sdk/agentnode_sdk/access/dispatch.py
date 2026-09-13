"""The one place an operation is carried out, and the one place it is refused.

`MANAGED-ACCESS-DECISION-0001` chose this: every way in ends here. REST routes, the remote MCP
server, the CLI and the SDKs all arrive at `dispatch()`, and nothing else in the product carries
out an operation. A door that could skip a check would make the door the boundary, and there would
then be as many boundaries as doors.

## The order, and why it is this order

    1  the operation exists and this build has it        cheapest, and says so plainly
    2  the caller is who it says it is                   nothing below means anything without it
    3  the device still exists and was not withdrawn     a revoked device is not a caller
    4  the device holds the capability                   authenticated is not the same as allowed
    5  the operator has not stopped the gateway          for anything that would run work
    6  the parameters are exactly what was declared      before anything reads them
    7  the operation runs                                policy, admission, quota inside it
    8  what happened is recorded                         whichever way it went

Steps 6 and 7 are where this delegates rather than decides. The operator's policy, admission,
the quota claim and the meter live in `GatewayService`, are already the only implementation of
themselves, and are reached through it. Copying them here would be a second opinion about the
same question, which is the thing this file exists to prevent.

## What a refusal is

One shape, always: a name from the contract's closed list, what happened in words somebody can
act on, and one thing they could do about it. A client that has to read prose to tell "over a
ceiling" from "malformed" will get it wrong, and a refusal with nothing to do about it leaves
somebody stuck.

## What is NOT here

No transport. No HTTP status codes, no JSON-RPC, no argv. Those belong to the adapters, and an
adapter that needed to make a decision to translate would be a decision made twice.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

from agentnode_sdk.access import contract


#: Every outcome an audit line may carry. Closed, so a caller cannot introduce a new one by
#: arranging to be refused in a way nobody anticipated.
_OUTCOMES = set(contract.REFUSALS) | {"carried_out", "unknown", "too_old", "bad_request",
                                      "wrong_method", "not_a_route"}


class Refused(Exception):
    """A refusal that names itself. Adapters render this; none of them composes one."""

    def __init__(self, refusal: str, because: str, what_to_do: str = "") -> None:
        if refusal not in contract.REFUSALS:
            raise ValueError("%r is not a declared refusal" % refusal)
        super().__init__(because)
        self.refusal = refusal
        self.because = because
        self.what_to_do = what_to_do

    def as_answer(self) -> dict:
        answer = {"refused": self.refusal, "because": self.because}
        if self.what_to_do:
            answer["what_to_do"] = self.what_to_do
        return answer


@dataclass(frozen=True)
class Principal:
    """Who is asking, as the SERVER established it -- never as a caller described itself."""

    token: str
    device_id: str
    client_id: str
    capabilities: tuple = ()
    device_name: str = ""

    @property
    def authenticated(self) -> bool:
        return bool(self.client_id)


#: Nobody. What an adapter passes when it could not establish a caller at all.
NOBODY = Principal(token="", device_id="", client_id="")


def identify(service, token: str) -> Principal:
    """Turn a presented token into a principal, or into nobody.

    The device's capabilities come from what the gateway recorded when it was paired, not from
    anything the caller sends. A caller that could name its own capabilities would be deciding
    what it is allowed to do.
    """
    if not token:
        return NOBODY
    client_id = service.state.client_id_for(token)
    if not client_id:
        return NOBODY
    name = ""
    held = (contract.RUN, contract.READ, contract.MANAGE_DEVICES)
    for device in service.state.paired_clients():
        if device.get("client_id") == client_id:
            name = str(device.get("name", "") or "")
            recorded = device.get("capabilities")
            if recorded:
                held = tuple(c for c in recorded if c in contract.CAPABILITIES)
            break
    return Principal(token=token, device_id=client_id, client_id=client_id,
                     capabilities=held, device_name=name)


def _check_parameters(op, params: dict) -> dict:
    """Exactly what was declared. Anything else is refused rather than ignored."""
    given = dict(params or {})
    unknown = sorted(set(given) - {f.name for f in op.params})
    if unknown:
        raise Refused("malformed",
                      "%s does not take %s." % (op.name, ", ".join(unknown)),
                      "Send only: " + (", ".join(f.name for f in op.params) or "nothing"))
    for f in op.params:
        if f.required and given.get(f.name) in (None, ""):
            raise Refused("malformed",
                          "%s needs %s -- %s." % (op.name, f.name, f.describes),
                          "Add %s and try again." % f.name)
        if f.one_of and f.name in given and given[f.name] not in f.one_of:
            raise Refused("malformed",
                          "%s is not something %s accepts." % (given[f.name], f.name),
                          "Use one of: " + ", ".join(f.one_of))
    return given


#: Nothing caller-influenced is written at all. A character filter was tried first and was
#: not enough: a line of ordinary job output -- "hello world" -- passes any such filter
#: unchanged, so "output cannot survive" was a claim the filter did not support. What is
#: recorded instead is chosen entirely by the server from closed sets.


def _a_name_we_know(op_name: str) -> str:
    """An operation name, only if it is one of ours.

    The name arrives from the caller. A caller that can put arbitrary text in a log line can put
    a token in one -- theirs or, worse, something they are trying to get an operator to read --
    so what is written is either a declared operation or the fact that it was not one.
    """
    return op_name if contract.find(op_name) is not None else "(undeclared)"


def _which_parameters(op_name: str, detail: str) -> list:
    """Which DECLARED parameter names a refusal was about.

    The only thing written about a refusal besides its name. Every value here comes from
    the contract, never from the request, so there is no string a caller can arrange to
    have written -- which is the difference between a log somebody reads and a log
    somebody writes to. It is still enough to tell one malformed request from another.
    """
    op = contract.find(op_name)
    if op is None:
        return []
    said = (detail or "")
    return sorted(f.name for f in op.params if f.name in said)


def _audit(service, op_name: str, principal: Principal, outcome: str, detail: str = "") -> None:
    """One line per attempt, however it went. No token, no artefact, no job output.

    A record of what was refused is as much the point as a record of what was done: an account
    being probed looks like refusals, and a log that only kept successes would not show it.

    Nothing caller-supplied is written. The operation is written only if it is one we declare;
    what the refusal was ABOUT is a list of declared parameter names. A review found both
    fields caller-controlled, and then found that filtering characters was not enough either:
    ordinary text survives a character filter, so a line of job output would have been
    preserved intact. Every value written here now comes from the contract.
    """
    line = {
        "at": round(time.time(), 3),
        "operation": _a_name_we_know(op_name),
        "device": principal.device_id or "(nobody)",
        "outcome": outcome if outcome in _OUTCOMES else "(other)",
        "about": _which_parameters(op_name, detail),
    }
    try:
        path = os.path.join(str(service.state.root), "audit.jsonl")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, sort_keys=True) + "\n")
    except OSError:                                           # pragma: no cover - a full disk
        pass


def _the_operator_has_stopped_it(service) -> str:
    """The kill switch, asked the way the rest of the product asks it."""
    from agentnode_sdk.gateway.allowance import why_it_is_stopped

    try:
        return why_it_is_stopped(service.state.root) or ""
    except Exception as exc:                                  # noqa: BLE001
        # A gateway that cannot tell whether it has been stopped is not one to keep taking work.
        raise Refused("gateway_stopped",
                      "This sandbox cannot tell whether it has been stopped (%s), so it is "
                      "refusing work." % exc,
                      "Ask whoever runs it to look at the gateway's state directory.") from exc


def records_of(service):
    """The sandbox's own account of what it did, for confirming a compatibility claim.

    The only way to build one. Compatibility is a public claim, so what backs it comes
    from the gateway's ledger and its audit rather than from whoever benefits: the run's
    owner says WHO, and the audit says WHAT they carried out.
    """
    from agentnode_sdk.access.compatibility import WhatTheSandboxRecorded

    def who_owns_the_run(run_id):
        record = service.runs.get(str(run_id))
        return getattr(record, "owner_client_id", "") if record is not None else ""

    def what_that_device_did(device_id):
        done = set()
        path = os.path.join(str(service.state.root), "audit.jsonl")
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    entry = json.loads(line)
                    if (entry.get("device") == device_id
                            and entry.get("outcome") == "carried_out"):
                        done.add(entry.get("operation"))
        except (OSError, ValueError):
            return set()
        return done

    return WhatTheSandboxRecorded(who_owns_the_run, what_that_device_did)


def record_a_refusal(service, operation: str, principal: Principal, outcome: str,
                     detail: str = "") -> None:
    """For a refusal a TRANSPORT produced before the dispatcher was reached.

    A request refused at the door -- no such path, the wrong method, a body that is not JSON --
    never reaches `dispatch`, so the audit never saw it. That is the half of the log somebody
    looking for a probe would most want, because a probe rarely gets as far as a real operation.
    """
    _audit(service, operation, principal, outcome, detail)


def dispatch(operation: str, params: dict, principal: Principal, *, service,
             speaks: str = contract.PROTOCOL_VERSION) -> dict:
    """Carry out one operation, or refuse it. The only way in.

    Every refusal is recorded, wherever in the sequence it happened. An earlier version audited at
    each point it refused, and missed one -- the parameter check -- which is exactly the kind of
    gap that makes a log trustworthy right up until the moment somebody relies on it. There is one
    place that records now, and it cannot be skipped by adding a refusal above it.
    """
    try:
        return _carry_out(operation, params, principal, service=service, speaks=speaks)
    except Refused as refusal:
        _audit(service, operation, principal, refusal.refusal, refusal.because)
        raise


def _carry_out(operation: str, params: dict, principal: Principal, *, service,
               speaks: str) -> dict:
    op = contract.find(operation)
    if op is None:
        raise Refused("unknown_operation",
                      "This sandbox has no operation called %r." % operation,
                      "Ask it for `capabilities` to see what it does have.")

    if _older(speaks, op.since):
        raise Refused("unknown_operation",
                      "%s arrived in protocol %s and this client speaks %s."
                      % (op.name, op.since, speaks),
                      "Update the client, or ask `capabilities` for what this version has.")

    if not principal.authenticated:
        raise Refused("not_authenticated",
                      "This request did not come with a credential this sandbox recognises.",
                      "Pair this device again with a fresh invitation.")

    # Who is asking is re-established here, on every dispatch, rather than trusted from the
    # principal that was handed in. A principal is a snapshot of who was asking WHEN IT WAS
    # BUILT, so a caller holding one -- or a long-lived connection reusing one -- would
    # otherwise keep the access it had at that moment for as long as it kept the object. This
    # is what makes a withdrawal take effect at once on every path rather than at the next
    # reconnection.
    #
    # What it raises is the GENERIC refusal, and that is the whole of what
    # `MANAGED-REVOCATION-0001` changed here.
    #
    # Withdrawing one DELETES its token record, so by the time a request arrives there is nothing
    # left that could tell it from a credential this sandbox never issued -- the check could only
    # ever have fired in a race. `MANAGED-REVOCATION-0001` chose to correct the contract rather
    # than keep a tombstone: retaining a record of credentials that no longer exist, so as to
    # tell a caller that its revoked token was once real, buys a diagnostic distinction at the
    # price of both retention and disclosure.
    #
    # What that costs is real and is stated rather than hidden. A client cannot tell "withdrawn"
    # from "wrong", an operator reading the audit sees the generic outcome for both, and both
    # lead a person to the same action: get a new invitation.
    if principal.device_id and service.state.client_id_for(principal.token) != principal.client_id:
        raise Refused("not_authenticated",
                      "This request did not come with a credential this sandbox recognises.",
                      "Pair this device again with a fresh invitation.")

    if op.needs not in principal.capabilities:
        raise Refused("not_permitted",
                      "This device may not %s. It holds: %s."
                      % (op.name, ", ".join(principal.capabilities) or "nothing"),
                      "Ask whoever runs the sandbox to pair a device that may.")

    if op.needs == contract.RUN:
        # Before the parameters, not after. `GatewayService.admit` asks the stop first of all,
        # and the same reason applies here: telling somebody their parameters are wrong while the
        # gateway is stopped sends them off to fix something that was never the obstacle.
        halted = _the_operator_has_stopped_it(service)
        if halted:
            raise Refused("gateway_stopped",
                          "Whoever runs this sandbox has stopped it: " + halted,
                          "Nothing will run until they start it again.")

    given = _check_parameters(op, params)

    handler = HANDLERS.get(op.name)
    if handler is None:                                       # pragma: no cover - see the test
        raise Refused("unknown_operation",
                      "%s is declared but this build cannot carry it out." % op.name,
                      "This is a fault in the sandbox, not in the request.")
    answer = handler(service, principal, given)
    _audit(service, op.name, principal, "carried_out")
    return answer


def _older(speaks: str, since: str) -> bool:
    """Whether a client speaking `speaks` predates an operation introduced at `since`."""
    try:
        return int(speaks) < int(since)
    except (TypeError, ValueError):
        return True


# ------------------------------------------------------------------ the operations themselves
#
# Each is a translation, not a decision. Where something must be decided -- whether this job is
# allowed, whether there is quota for it, what it is metered as -- the call goes to
# `GatewayService`, which is the only implementation of those questions.


#: How long a disclosure a person was shown stays good for. Long enough to read it and decide;
#: short enough that "I agreed to something last week" is not an argument.
DISCLOSURE_GOOD_FOR_SECONDS = 15 * 60


def _what_was_disclosed(answer: dict) -> str:
    """The digest of a disclosure, over the parts that would change what actually happens.

    Taken server-side over the server's own answer, so it names what the person was SHOWN. A
    digest a caller computed would bind whatever the caller decided to hash.
    """
    import hashlib

    material = json.dumps({
        "runs_at": answer.get("runs_at"),
        "transfers": answer.get("transfers"),
        "network": answer.get("network"),
        "limits": answer.get("limits"),
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _remember_the_disclosure(service, principal, digest_of_it: str) -> None:
    """Kept by the SERVER, against this device, with a time on it."""
    shown = getattr(service, "_disclosures_shown", None)
    if shown is None:
        shown = service._disclosures_shown = {}
    shown[(principal.client_id, digest_of_it)] = time.time()


def _take_the_disclosure(service, principal, digest_of_it: str) -> bool:
    """Spend it. One use, by the device it was shown to, within the window.

    Claimed and removed in the same step, so two submissions racing on one disclosure cannot both
    be told they had it -- the same shape as the pairing code's claim, and for the same reason.
    """
    shown = getattr(service, "_disclosures_shown", None) or {}
    when = shown.pop((principal.client_id, digest_of_it), None)
    return bool(when) and (time.time() - when) <= DISCLOSURE_GOOD_FOR_SECONDS


def _capabilities(service, principal, params):
    described = contract.describe()
    # Asked here rather than only at the point of running. `_the_operator_has_stopped_it` raises
    # if it cannot tell, which is right when work is about to start and wrong when somebody is
    # only asking what this sandbox is -- so an unreadable switch is reported as not accepting
    # work, which is the same fail-closed answer without turning a question into an error.
    try:
        halted = _the_operator_has_stopped_it(service)
    except Refused as refusal:
        halted = refusal.because or "this sandbox cannot tell whether it has been stopped"
    return {
        "accepting_work": not halted,
        "not_accepting_because": halted,
        "protocol": described["protocol"],
        "operations": [o for o in described["operations"]
                       if o["needs"] in principal.capabilities],
        "capabilities": list(principal.capabilities),
        "enforces": service.measured_properties(),
        # Every client is told the limits before it does anything, because this is the first
        # thing every client asks and the only place all of them look.
        "what_this_does_not_establish": list(contract.WHAT_THIS_IS_NOT),
    }


def _prepare(service, principal, params):
    """The disclosure, composed server-side so every door shows the same thing."""
    from agentnode_sdk.worker import what_it_does_not_establish

    allowed = service.allowance()
    runs, seconds = service.use.so_far(principal.client_id)
    asked_for = int(params.get("wall_clock_s") or 60)
    network = params.get("network") or "none"
    domains = tuple(params.get("allowed_domains") or ())
    answer = {
        "runs_at": "%s (%s)" % (service.worker.instance_label(), service.worker.topology),
        "transfers": {
            "artifact_sha256": params.get("artifact_sha256", ""),
            "bytes": int(params.get("artifact_bytes") or 0),
            "command": list(params.get("command") or ()),
            "what_else_leaves_this_machine": "nothing",
        },
        "network": {
            "asked_for": network,
            "allowed": list(domains) if network == "allowlist" else [],
            "everything_else": "refused",
        },
        "limits": {
            "wall_clock_s": asked_for,
            "ceilings": allowed.as_dict(),
        },
        "expected_use": {
            "runs_so_far": runs,
            "seconds_so_far": int(seconds),
            "this_would_add_seconds": asked_for,
        },
        "what_this_does_not_establish": what_it_does_not_establish(service.worker.topology),
    }
    answer["accepted_disclosure"] = _what_was_disclosed(answer)
    _remember_the_disclosure(service, principal, answer["accepted_disclosure"])
    return answer


def _submit(service, principal, params):
    import base64

    # Nothing runs that a person was not shown first. `prepare` hands back a digest of what it
    # displayed; this spends it. A review found the field optional and ignored, which made the
    # disclosure a screen rather than a gate -- it could be skipped by simply not sending it.
    presented = str(params.get("accepted_disclosure") or "")
    if not presented:
        raise Refused("malformed",
                      "Nothing runs here that was not disclosed first.",
                      "Call prepare with the same job, show somebody what comes back, and send "
                      "its accepted_disclosure with the submission.")
    if not _take_the_disclosure(service, principal, presented):
        raise Refused("malformed",
                      "That disclosure is not one this sandbox showed this device in the last "
                      "%d minutes, or it has already been used."
                      % (DISCLOSURE_GOOD_FOR_SECONDS // 60),
                      "Call prepare again and submit against what it returns.")

    from agentnode_sdk.gateway.protocol import JobRequest, digest

    artifact = params.get("artifact") or b""
    if isinstance(artifact, str):
        try:
            artifact = base64.b64decode(artifact, validate=True)
        except Exception as exc:                              # noqa: BLE001
            raise Refused("malformed", "The artifact is not valid base64.",
                          "Send the code base64-encoded.") from exc
    # The policy the caller is ASKING for, digested the same way the existing client digests it.
    # The gateway compares this against what it will actually grant, so composing it here rather
    # than letting a caller send a digest is the point: a digest a caller chose would bind
    # nothing.
    from agentnode_sdk.gateway.policy_paths import policy_shape
    from agentnode_sdk.gateway.protocol import canonical_bytes
    from agentnode_sdk.sandbox.contract import Limits, NetworkRules, SandboxPolicy

    network = params.get("network") or "none"
    domains = tuple(params.get("allowed_domains") or ())
    wall_clock = max(1, int(params.get("wall_clock_s") or 60))
    if network == "none":
        rules = NetworkRules(enabled=False, allowed_destinations=frozenset())
    else:
        rules = NetworkRules(enabled=True, allowed_destinations=frozenset(domains))
    asked_for = SandboxPolicy(network=rules, limits=Limits(wall_clock_s=wall_clock))

    request = JobRequest(
        job_id=str(params["run_id"]),
        run_id=str(params["run_id"]),
        artifact_sha256=digest(artifact),
        policy_sha256=digest(canonical_bytes(policy_shape(asked_for))),
        command=tuple(params.get("command") or ()),
        network=network,
        allowed_domains=domains,
        wall_clock_s=wall_clock,
    )
    try:
        record = service.submit(request, artifact, token=principal.token)
    except Exception as exc:                                  # noqa: BLE001
        raise _translate(exc) from exc
    return {"run_id": record.run_id, "state": record.state,
            "admitted_under": dict(getattr(record, "admitted_under_values", {}) or {})}


def _a_run_of_this_caller(service, principal, run_id):
    record = service.runs.get(str(run_id))
    if record is None or (record.owner_client_id and
                          record.owner_client_id != principal.client_id):
        # The same answer either way: telling a stranger that a run exists but is not theirs
        # tells them it exists.
        raise Refused("no_such_run",
                      "This sandbox has no run %r that you submitted." % run_id,
                      "Check the run id, or submit the job again.")
    return record


def _status(service, principal, params):
    """Where a run has got to, including whether it is on its way out.

    `stopping` is reported for as long as a cancellation is being carried out -- INCLUDING once
    the run's own record has gone terminal. That is deliberate, and it is the whole of what makes
    a terminal state worth anything: the record turning "cancelled" or "finished" says the run
    ended and says nothing about whether the sandbox it ran in is gone. Only confirmed cleanup
    says that, and until it is confirmed this reports the run as still stopping.

    An earlier version stopped at `is_terminal(record.state)` and reported the terminal state
    while the teardown was still in progress, so a client polling "until finished" was told the
    run was over while its container might still have been up. A client cannot check what it is
    not told.

    It is bounded: an attempt that fails leaves nothing in flight, and this falls back to what
    the record really says rather than reporting `stopping` for ever.
    """
    record = _a_run_of_this_caller(service, principal, params["run_id"])
    showing = "stopping" if _is_stopping(service, record.run_id) else record.state
    return {"run_id": record.run_id, "state": showing,
            "started_at": int(record.started_at or 0) or None,
            "finished_at": int(record.finished_at or 0) or None}


def _result(service, principal, params):
    from agentnode_sdk.gateway.protocol import is_terminal

    record = _a_run_of_this_caller(service, principal, params["run_id"])
    if not is_terminal(record.state):
        raise Refused("not_finished",
                      "Run %s is still %s." % (record.run_id, record.state),
                      "Ask for its status until it is finished, then ask again.")
    return {"run_id": record.run_id, "state": record.state,
            "exit_code": record.exit_code, "stdout": record.stdout, "stderr": record.stderr,
            "cleanup_verified": record.cleanup_verified}


def _is_stopping(service, run_id: str) -> bool:
    """Whether a cancellation is being carried out for this run right now.

    The gateway owns a bounded pool for this (`access/stopping.py`). Nothing here creates a
    thread and nothing here keeps per-request state, so there is no number of cancel requests
    that produces an unbounded number of anything.
    """
    return bool(service.stopping.in_flight(str(run_id)))


def _cancel(service, principal, params):
    """Ask for a run to be stopped, and come back at once.

    Stopping is not instant and must not pretend to be: the sandbox has to be torn down and
    CONFIRMED gone, which is the only reason a terminal state is worth anything, and that can
    take the gateway's whole settle window. Doing it on the caller's thread meant the caller, and
    the person watching them, waited that long with nothing to look at.

    What carries it out is a fixed pool the gateway owns. An earlier version started a thread per
    request, which moved the waiting off the caller and turned "ask to cancel" into "ask for a
    thread" -- a worse arrangement in better clothes.

    Idempotent in every direction that matters:

    * a run that has already ended is not ended again;
    * a second request while one is in flight JOINS it, returns the same state, and costs no
      budget -- polling your own cancellation must not be rationed;
    * a request after an attempt that failed starts a new attempt, because that is a retry
      rather than a duplicate, and `attempts` says how many there have been.

    Cleanup is still a precondition for the terminal state. Nothing here shortens that; it only
    stops the caller being held while it happens.
    """
    from agentnode_sdk.access import stopping as pool
    from agentnode_sdk.gateway.protocol import is_terminal

    record = _a_run_of_this_caller(service, principal, params["run_id"])
    standing = service.stopping.about(record.run_id)
    if is_terminal(record.state) and not (standing and standing.in_flight):
        return {"run_id": record.run_id, "state": record.state, "accepted": False,
                "attempts": standing.attempts if standing else 0,
                "cleanup_verified": record.cleanup_verified,
                "problem": standing.problem if standing else ""}

    joined = bool(standing is not None and standing.in_flight)
    # Set before anything is queued, so nothing can read this flag in the gap between deciding
    # to stop and the stopping beginning.
    record.cancel_requested.set()
    try:
        stop = service.stopping.ask(record.run_id, by=principal.client_id)
    except pool.TooManyStops as too_many:
        raise Refused("over_a_ceiling", too_many.because, too_many.what_to_do) from too_many
    return {"run_id": record.run_id, "state": "stopping", "accepted": not joined,
            "attempts": stop.attempts, "cleanup_verified": stop.settled,
            "problem": stop.problem}


def _usage(service, principal, params):
    allowed = service.allowance()
    runs, seconds = service.use.so_far(principal.client_id)
    return {"runs": int(runs), "seconds": int(seconds),
            "ceilings": allowed.as_dict(), "clears_at": None}


def _devices_list(service, principal, params):
    return {"devices": [
        {"device_id": d.get("client_id", ""), "name": d.get("client_name", ""),
         "last_used": d.get("last_used"), "paired_at": d.get("issued_at")}
        for d in service.state.paired_clients()
    ]}


def _devices_revoke(service, principal, params):
    wanted = str(params["device_id"])
    return {"device_id": wanted, "withdrawn": bool(service.state.revoke_client(wanted))}


def _translate(exc: Exception) -> Refused:
    """Turn what the gateway raises into the contract's own words. One place, so every door
    refuses the same thing the same way."""
    from agentnode_sdk.gateway.allowance import OverTheCeiling
    from agentnode_sdk.gateway.protocol import ProtocolError

    if isinstance(exc, Refused):
        return exc
    if isinstance(exc, OverTheCeiling):
        return Refused("over_a_ceiling", str(exc),
                       "Wait until the window clears, or ask for a higher ceiling.")
    if isinstance(exc, ProtocolError):
        return Refused("malformed", str(exc), "Correct the request and send it again.")
    return Refused("sandbox_unavailable",
                   "This sandbox could not carry that out: %s" % exc,
                   "Try again; if it keeps happening, tell whoever runs it.")


HANDLERS = {
    "capabilities": _capabilities,
    "prepare": _prepare,
    "submit": _submit,
    "status": _status,
    "result": _result,
    "cancel": _cancel,
    "usage": _usage,
    "devices.list": _devices_list,
    "devices.revoke": _devices_revoke,
}
