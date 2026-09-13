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
from dataclasses import dataclass, field

from agentnode_sdk.access import contract


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


def _audit(service, op_name: str, principal: Principal, outcome: str, detail: str = "") -> None:
    """One line per attempt, however it went. No token, no artefact, no job output.

    A record of what was refused is as much the point as a record of what was done: an account
    being probed looks like refusals, and a log that only kept successes would not show it.
    """
    line = {
        "at": round(time.time(), 3),
        "operation": op_name,
        "device": principal.device_id or "(nobody)",
        "outcome": outcome,
        "detail": detail[:200],
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

    if principal.device_id and service.state.client_id_for(principal.token) != principal.client_id:
        raise Refused("device_revoked",
                      "This device has been withdrawn from this sandbox.",
                      "Ask whoever runs it for a new invitation.")

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


def _capabilities(service, principal, params):
    described = contract.describe()
    return {
        "protocol": described["protocol"],
        "operations": [o for o in described["operations"]
                       if o["needs"] in principal.capabilities],
        "capabilities": list(principal.capabilities),
        "enforces": service.measured_properties(),
    }


def _prepare(service, principal, params):
    """The disclosure, composed server-side so every door shows the same thing."""
    from agentnode_sdk.worker import what_it_does_not_establish

    allowed = service.allowance()
    runs, seconds = service.use.so_far(principal.client_id)
    asked_for = int(params.get("wall_clock_s") or 60)
    network = params.get("network") or "none"
    domains = tuple(params.get("allowed_domains") or ())
    return {
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


def _submit(service, principal, params):
    import base64

    from agentnode_sdk.gateway.protocol import JobRequest, digest

    artifact = params.get("artifact") or b""
    if isinstance(artifact, str):
        try:
            artifact = base64.b64decode(artifact, validate=True)
        except Exception as exc:                              # noqa: BLE001
            raise Refused("malformed", "The artifact is not valid base64.",
                          "Send the code base64-encoded.") from exc
    request = JobRequest(
        job_id=str(params["run_id"]),
        run_id=str(params["run_id"]),
        artifact_sha256=digest(artifact),
        command=tuple(params.get("command") or ()),
        wall_clock_s=int(params.get("wall_clock_s") or 60),
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
    record = _a_run_of_this_caller(service, principal, params["run_id"])
    return {"run_id": record.run_id, "state": record.state,
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


def _cancel(service, principal, params):
    record = _a_run_of_this_caller(service, principal, params["run_id"])
    service.cancel(record.run_id)
    return {"run_id": record.run_id, "state": record.state,
            "cleanup_verified": record.cleanup_verified}


def _usage(service, principal, params):
    allowed = service.allowance()
    runs, seconds = service.use.so_far(principal.client_id)
    return {"runs": int(runs), "seconds": int(seconds),
            "ceilings": allowed.as_dict(), "clears_at": None}


def _devices_list(service, principal, params):
    return {"devices": [
        {"device_id": d.get("client_id", ""), "name": d.get("name", ""),
         "last_used": d.get("last_used"), "paired_at": d.get("issued_at")}
        for d in service.state.paired_clients()
    ]}


def _devices_revoke(service, principal, params):
    wanted = str(params["device_id"])
    for device in service.state.paired_clients():
        if device.get("client_id") == wanted:
            token = device.get("token") or device.get("token_sha256")
            withdrawn = bool(service.state.revoke(token)) if token else False
            return {"device_id": wanted, "withdrawn": withdrawn}
    return {"device_id": wanted, "withdrawn": False}


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
