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

import base64
import hashlib
import hmac
import json
import secrets
import threading
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
        from agentnode_sdk.gateway.redaction import scrub

        if refusal not in contract.REFUSALS:
            raise ValueError("%r is not a declared refusal" % refusal)
        if not str(what_to_do or "").strip():
            # A refusal with nothing to do about it leaves somebody stuck, and stuck is
            # indistinguishable from broken to the person it happens to. Refused where the
            # refusal is BUILT, so a path that forgot one cannot reach a caller -- and every
            # refusal in this product goes through here.
            raise ValueError(
                "%r was refused with nothing the refused party can do about it. Every refusal "
                "names one action." % refusal)
        # Scrubbed at construction rather than at each raise site. Refusal text is composed from
        # whatever went wrong, which is exactly where a URL with a code in it, or an exception
        # quoting one, gets in. The structural rules above this are what keep secrets out; this
        # is the second line, and it is in the one place every refusal passes through.
        super().__init__(scrub(because))
        self.refusal = refusal
        self.because = scrub(because)
        self.what_to_do = scrub(what_to_do)

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
    #: WHICH CUSTOMER. Established from the device this credential belongs to, and from nothing
    #: a caller sends. A device is what is holding the credential; an account is who the
    #: credential is FOR, and every question of the form "may I see this / may I change this"
    #: is answered against the account rather than against the device that happens to be asking.
    account_id: str = ""
    #: True when the caller showed it holds the token's SECRET rather than merely a copy of the
    #: token. The older doors have always required that, and an answer to such a caller can be
    #: signed -- it is the only caller able to check the signature.
    proved: bool = False
    #: Set when the caller arrived as a browser session rather than carrying a token itself.
    session_id: str = ""
    #: What a browser sent in the CSRF header. Checked for anything that changes state: the
    #: cookie alone is not enough, because a cookie is attached by the browser and a header is
    #: attached by the page.
    csrf_presented: str = ""
    #: Which door. Written to the audit, and what a compatibility observation is bound to.
    via: str = ""
    device_name: str = ""

    @property
    def authenticated(self) -> bool:
        return bool(self.client_id)


#: Nobody. What an adapter passes when it could not establish a caller at all.
NOBODY = Principal(token="", device_id="", client_id="")


#: The only two operations reachable without a credential, and the whole of that list.
#:
#: They exist because authentication has to start somewhere: one says which gateway you have
#: reached, the other is how you come to hold a credential at all. Everything else goes through
#: `dispatch`, whose first act is to establish who is asking.
#:
#: They are deliberately NOT contract operations. Declaring them would put them in the renderings
#: -- including the ones a model reads -- and would make them addressable at `/v1/op/`, where the
#: dispatcher would demand the credential they exist to obtain. Keeping them out of the contract
#: is what makes "no tool can pair itself" true by construction rather than by a filter.
BOOTSTRAP = ("hello", "pair", "open_session")


def before_anyone(operation: str, params: dict, *, service, via: str = "") -> dict:
    """The one way in for somebody who has no credential yet.

    Every anonymous request in this gateway comes through here. That is the point: a route that
    answered an unauthenticated caller on its own would be a second front door, and the number of
    front doors is the thing worth being able to count.

    Recorded like everything else. An account being probed looks like a run of failed pairings,
    and a log that only kept the successful one would not show it.
    """
    if operation not in BOOTSTRAP:
        _audit(service, operation, NOBODY, "not_authenticated")
        raise Refused("not_authenticated",
                      "That is not something this sandbox will do for somebody it does not "
                      "know yet.",
                      "Pair a device first; everything else needs a credential.")
    try:
        answer = _BOOTSTRAP[operation](service, params or {})
    except Refused as refusal:
        _audit(service, operation, NOBODY, refusal.refusal, refusal.because)
        raise
    _audit(service, operation, NOBODY, "carried_out")
    return answer


#: Everything `hello` will tell somebody it has never met, and the whole of that list.
#:
#: Written out rather than passed through, because this answers anybody who can reach the port
#: and "whatever the gateway happens to return" is not a decision anybody made. A field added to
#: the gateway's own view of itself does not become public by being added; it becomes public by
#: being put here.
#:
#: Every one of these is composed by this gateway from its own state. None of it is caller
#: supplied, and none of it names a path, a file or an account on the machine -- which is the
#: property `test_one_way_in.py` checks rather than trusting this sentence.
#:
#: `reason` and `next_steps` stay. An earlier version cut them on the theory that they were
#: operator-facing, and that was a guess: they are the product's own words for why a gateway is
#: not ready and what to do about it, and a refusal that names no way through is the thing this
#: project has spent months removing everywhere else. `pairing_open` stays too -- the operator
#: opened that window deliberately, and a client that cannot see it is left guessing.
WHAT_A_STRANGER_IS_TOLD = ("protocol", "gateway", "fingerprint", "ready", "reason",
                           "properties", "unproven", "next_steps", "measured_at",
                           "pairing_open")


def _hello(service, params: dict) -> dict:
    """What this gateway will tell somebody it has never met.

    Enough to decide whether to pair with it: which gateway this is, its fingerprint, whether it
    can take work, what it was measured to enforce and what it was not, and whether a pairing
    window is open.
    """
    said = service.hello()
    return {field: said.get(field) for field in WHAT_A_STRANGER_IS_TOLD}


def _pair(service, params: dict) -> dict:
    """Redeem an invitation for a credential.

    Every guard belongs to the pairing itself and is applied by `redeem_pairing`: the code is
    checked in constant time, a wrong one costs the throttle, an expired one is refused, and the
    claim is made and removed in one step so two callers racing on one invitation cannot both be
    told they had it. Nothing here re-implements any of that; it is carried out where it is
    written down, and this records that it happened.
    """
    from agentnode_sdk.gateway.identity import PairingError

    try:
        service.require_private_state()
        # No source address is passed, and that is a decision rather than an omission: behind a
        # reverse proxy every client shares one, and a forwarding header is set by whoever can
        # set one. A limit keyed on either would be a limit on the wrong thing.
        token = service.state.redeem_pairing(
            str(params.get("code", "")), client_name=str(params.get("client_name", "")))
    except PairingError as exc:
        raise Refused("not_authenticated", str(exc),
                      "Ask whoever runs this sandbox for a fresh invitation.") from exc
    identity = service.state.identity
    return {"token": token, "gateway": identity.as_dict(),
            "fingerprint": identity.fingerprint}


def _open_session(service, params: dict) -> dict:
    """Redeem an invitation for a BROWSER SESSION rather than for a token.

    Same invitation, same single-use claim, same throttle -- and the credential never leaves this
    gateway. A browser is handed a session identifier in a cookie its own scripts cannot read,
    and a CSRF token it is expected to keep in memory and nowhere else.

    A device is still created, because a session belongs to a device and withdrawing that device
    has to end the session. What is different is only that nobody is given its token: there is no
    durable bearer credential in the browser to steal, because none was ever sent there.
    """
    from agentnode_sdk.access import sessions as store

    paired = _pair(service, params)
    client_id = service.state.client_id_for(paired["token"])
    try:
        session_id, csrf = service.sessions.open(client_id, label=str(params.get(
            "client_name", "")))
    except store.TooManySessions as too_many:
        raise Refused("over_a_ceiling", str(too_many),
                      "End a session you are no longer using, then sign in again.") from too_many
    # The token is deliberately dropped on the floor. It exists -- the device is real and its
    # secret is what signs its answers -- and this is the one caller that is never told it.
    return {"session": session_id, "csrf": csrf, "device": client_id,
            "device_name": str(params.get("client_name", "")),
            "gateway": paired["gateway"], "fingerprint": paired["fingerprint"]}


_BOOTSTRAP = {"hello": _hello, "pair": _pair, "open_session": _open_session}


def identify(service, token: str, proof=None, via: str = "") -> Principal:
    """Turn a presented token into a principal, or into nobody.

    The device's capabilities come from what the gateway recorded when it was paired, not from
    anything the caller sends. A caller that could name its own capabilities would be deciding
    what it is allowed to do.

    `proof` is an optional `(payload, signature)` showing the caller holds the token's SECRET
    rather than merely a copy of the token. The older doors have always required it, and when
    they became translators onto this dispatcher that requirement had to come WITH them: a
    translator that dropped it would have quietly turned a signed request into a bearer one,
    which is a weaker thing wearing the same name.

    Verifying it here rather than at the door is the point. Checking a signature is transport
    work, but deciding WHO IS ASKING is not, and there is one place that decides. A proof that
    does not verify makes the caller nobody, not a partly-trusted somebody.
    """
    if not token:
        return NOBODY
    proved = False
    if proof is not None:
        payload, signature = proof
        try:
            service.authenticate(token, payload or {}, signature or "")
        except Exception:                                     # noqa: BLE001
            return NOBODY
        proved = True
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
                     account_id=service.state.account_id_for(token),
                     capabilities=held, device_name=name, proved=proved, via=via)


def identify_session(service, session_id: str, csrf: str = "", via: str = "browser"):
    """Turn a browser session into a principal, or into nobody.

    The session says which device this is; everything after that -- what it may do, what it may
    see -- comes from the device, exactly as it would for a caller holding that device's token.
    A session is a way of PRESENTING an identity, not a different kind of identity, and keeping
    it that way is what stops the browser becoming a second permission system.
    """
    if not session_id:
        return NOBODY
    found = service.sessions.whose(session_id)
    if not found:
        return NOBODY
    known = identify_client(service, str(found["client_id"]), via=via)
    if known is NOBODY:
        # The session outlived the device it belongs to. Withdrawing a device ends its sessions,
        # so reaching here means something went the other way round; either way, nobody.
        return NOBODY
    return Principal(token=known.token, device_id=known.device_id, client_id=known.client_id,
                     account_id=known.account_id,
                     capabilities=known.capabilities, device_name=known.device_name,
                     proved=False, session_id=session_id, csrf_presented=csrf, via=via)


def a_confirmed_session(service, session_id: str, csrf: str):
    """A principal for a browser that presented BOTH its cookie and its confirmation value.

    For the two console addresses that answer with something other than an operation's result --
    a file, and a fresh confirmation value. They still must not decide anything themselves, and
    "is this really that session, and did the page itself ask" is a decision.
    """
    who = identify_session(service, session_id, csrf, via="browser")
    if not who.authenticated:
        return NOBODY
    if not service.sessions.csrf_matches(session_id, csrf):
        return NOBODY
    return who


def fresh_confirmation(service, session_id: str) -> str:
    """A new confirmation value for a session that still exists, or "" for one that does not."""
    who = identify_session(service, session_id, via="browser")
    if not who.authenticated:
        return ""
    return service.sessions.new_confirmation(session_id)


def identify_client(service, client_id: str, via: str = ""):
    """A principal for a device this gateway already knows, named by its identity.

    Used where a credential is not what was presented -- a browser session -- so the capabilities
    still come from what the gateway recorded at pairing and from nothing a caller sends.
    """
    for device in service.state.paired_clients():
        if device.get("client_id") != str(client_id):
            continue
        held = tuple(c for c in (device.get("capabilities") or contract.CAPABILITIES)
                     if c in contract.CAPABILITIES)
        from agentnode_sdk.gateway import accounts as _accounts

        return Principal(token="", device_id=str(client_id), client_id=str(client_id),
                         account_id=str(device.get("account_id")
                                        or _accounts.solo_account_for(str(client_id))),
                         capabilities=held or contract.CAPABILITIES,
                         device_name=str(device.get("client_name")
                                         or device.get("name") or ""), via=via)
    return NOBODY


def rendered_record(service, principal: Principal, run_id: str) -> dict:
    """The whole run record, for a door whose wire shape predates the contract.

    Those doors answer with the entire signed record, and their clients read fields the narrower
    `status` and `result` deliberately do not carry. They are translators now, so they must not
    do their own ownership check: that is a decision, and decisions live here. This performs
    exactly the checks the contract's own operations perform, in the same order, and renders.
    """
    # Authentication FIRST, as everywhere else. Without this line an unauthenticated caller
    # reached a record lookup and was saved only by the ownership test that follows -- safe by
    # accident rather than by order, which is the arrangement this whole layer exists to end.
    if not principal.authenticated:
        raise Refused("not_authenticated",
                      "This request did not come with a credential this sandbox recognises.",
                      "Pair this device again with a fresh invitation.")
    return _a_run_of_this_caller(service, principal, run_id).public()


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
    if op_name in BOOTSTRAP:
        return op_name
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


#: The doors this gateway has. An adapter names itself with one of these when it dispatches.
WAYS_IN = contract.CHANNELS


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
        # WHICH CUSTOMER this line is about. Not caller-supplied: it comes from the device
        # record. Without it the audit could only ever be read per device, which means the one
        # question an operator actually asks of it -- what has this customer been doing -- could
        # not be answered without first reconstructing who owned which credential.
        "account": principal.account_id or "(nobody)",
        # WHICH DOOR. Not caller-supplied: the adapter that calls `dispatch` names itself, and
        # anything that is not one of the ways in we declare is written as "(other)". A
        # compatibility observation is bound to this, so a value a caller could choose would let
        # it claim to have arrived somewhere it never did.
        "via": principal.via if principal.via in WAYS_IN else "(other)",
        "outcome": outcome if outcome in _OUTCOMES else "(other)",
        "about": _which_parameters(op_name, detail),
    }
    from agentnode_sdk.gateway.redaction import scrub_everything

    try:
        path = os.path.join(str(service.state.root), "audit.jsonl")
        with open(path, "a", encoding="utf-8") as fh:
            # Every value above already comes from the contract rather than from a caller, which
            # is what actually keeps this file clean. The scrub is the second line: it costs one
            # pass over a five-key object and it is what catches the field somebody adds next
            # year without reading why the others are shaped the way they are.
            fh.write(json.dumps(scrub_everything(line), sort_keys=True) + "\n")
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
        answer = _carry_out(operation, params, principal, service=service, speaks=speaks)
    except Refused as refusal:
        _audit(service, operation, principal, refusal.refusal, refusal.because)
        raise
    return _bound_to_this_gateway(service, principal, operation, answer)


def _bound_to_this_gateway(service, principal: Principal, operation: str, answer: dict) -> dict:
    """Sign an answer for a caller that can check the signature, and for nobody else.

    The older doors have always answered with the record bound to the gateway that produced it,
    and `EM3C-EVIDENCE-0020` is why: an answer's outcome could be changed on the way to a client
    and the binding still recomputed to what had been signed, so the binding covers everything
    the answer says happened. Making those doors translators onto this dispatcher must not lose
    that, which means it has to be expressible HERE.

    Only for a caller that PROVED it holds the token's secret. Anyone else could not verify a
    signature, so attaching one would be decoration -- and decoration that looks like evidence is
    worse than none. It goes in a field of its own rather than at the top level, so the answer
    still contains exactly what the operation declares.
    """
    op = contract.find(operation)
    if not principal.proved or op is None or not isinstance(answer, dict):
        return answer
    if not any(f.name == "answer_binding" for f in op.returns):
        return answer
    try:
        signed = service.sign_answer(service.stamp(dict(answer)), principal.token)
    except Exception:                                         # noqa: BLE001
        # A gateway that cannot sign says so by not signing. It does not invent a binding, and
        # it does not fail an operation that otherwise succeeded.
        return answer
    carried = {k: signed[k] for k in ("gateway", "protocol", "binding", "signature")
               if k in signed}
    return {**answer, "answer_binding": carried} if carried else answer


def _carry_out(operation: str, params: dict, principal: Principal, *, service,
               speaks: str) -> dict:
    op = contract.find(operation)
    if op is None or not contract.usable(op):
        # An unclassified operation is not a quieter operation -- it is unreachable. Answered as
        # "no such operation", because from a caller's side that is exactly what it is: nothing
        # here will carry it out, over this transport or any other.
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
    if principal.session_id:
        # A session is re-established on every request too, for the same reason and with the
        # same effect: ending one takes hold on the very next thing it tries to do.
        if not service.sessions.whose(principal.session_id):
            raise Refused("not_authenticated",
                          "This session has ended.",
                          "Sign in again from an invitation.")
        if op.changes and not service.sessions.csrf_matches(principal.session_id,
                                                            principal.csrf_presented):
            # SameSite=Strict already means another origin's request carries no cookie. This is
            # the second lock: a request that changes something must also carry a value only the
            # page itself has, which a cross-site request cannot obtain and an injected script
            # cannot read out of a cookie.
            raise Refused("not_authenticated",
                          "That request did not carry this session's confirmation value.",
                          "Reload the page and try again.")
    elif principal.device_id and (service.state.client_id_for(principal.token)
                                  != principal.client_id):
        raise Refused("not_authenticated",
                      "This request did not come with a credential this sandbox recognises.",
                      "Pair this device again with a fresh invitation.")

    if op.needs not in principal.capabilities:
        raise Refused("not_permitted",
                      "This device may not %s. It holds: %s."
                      % (op.name, ", ".join(principal.capabilities) or "nothing"),
                      "Ask whoever runs the sandbox to pair a device that may.")

    # Before the parameters, not after, and for EVERY operation rather than only the ones that
    # run something. Telling somebody their parameters are wrong while the gateway is stopped or
    # their account is suspended sends them off to fix something that was never the obstacle --
    # and a gateway that bounds only the expensive operation has not bounded anything, because a
    # probe uses the cheap ones.
    #
    # It is one call. What it asks -- the operator's stop, this account's standing, the request
    # rate -- lives in `GatewayService`, which is the only implementation of each; this is the
    # one place that asks, and there is no second opinion here about any of them.
    #
    # `op.needs == RUN` is what "would run work" means in this contract, and it is the same test
    # the stop applied before admission existed. It is NOT `op.changes`: `usage` changes nothing
    # and is exactly what somebody needs while the gateway is stopped, while `cancel` changes
    # something and was always refused by the stop.
    from agentnode_sdk.gateway import admission as _admission

    try:
        service.may_this_caller_proceed(principal.account_id, principal.device_id,
                                        op.needs == contract.RUN)
    except _admission.NotAdmitted as refused:
        raise Refused(refused.refusal, refused.because, refused.what_to_do) from refused

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


#: Every part of a disclosure that is BOUND. Changing any of these between prepare and submit
#: produces a different digest, and a different digest is not the approval that was given.
#:
#: What is deliberately NOT here is as considered as what is. `expected_use.runs_so_far` and
#: `seconds_so_far` are counters that move on their own; binding them would invalidate every
#: disclosure the moment anything else ran, which is not a security property, it is a bug that
#: looks like one. What is bound from that section is the part the person was actually told
#: about THIS job: what it would add.
BOUND_BY_THE_DISCLOSURE = (
    ("approved_by",),                      # who was shown it, and where they confirmed it
    ("will_run_as",),                      # the one connection it is an approval FOR
    ("runs_at",),                          # the backend and where it runs
    ("transfers",),                        # artifact digest, size, command
    ("network",),                          # mode and the destination allowlist
    ("limits",),                           # resources asked for, and the ceilings in force
    ("requested_policy_sha256",),          # the policy being asked for
    ("operator_policy_sha256",),           # the policy in force when it was shown
    ("secrets",),                          # which named secrets would be released
    ("expected_use", "this_would_add_seconds"),   # the basis it is counted and charged against
    ("good_for_seconds",),                 # how long the approval was said to last
)


def _what_was_disclosed(answer: dict) -> str:
    """The digest of a disclosure, over everything that would change what happens or what a
    person was told about the decision.

    Taken server-side over the server's own answer, so it names what the person was SHOWN. A
    digest a caller computed would bind whatever the caller decided to hash.
    """
    import hashlib

    picked = {}
    for path in BOUND_BY_THE_DISCLOSURE:
        here = answer
        for step in path:
            here = (here or {}).get(step) if isinstance(here, dict) else None
        picked[".".join(path)] = here
    material = json.dumps(picked, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _which_part_drifted(was: dict | None, now: dict) -> str:
    """Name the fields that differ, for a caller that can act on knowing.

    Found by giving the tools to a real model with a job to do. It changed the network setting
    between being shown the job and submitting it -- which this refuses, correctly -- but the
    refusal said only that SOMETHING had changed. It guessed, guessed again, and gave up. A
    caller supplied both sides of this comparison, so naming which one moved tells it nothing it
    did not already have, and turns a dead end into a thing it can fix.

    Nothing is echoed back: the field NAMES, never the values. A refusal is not a place to
    reflect a caller's payload.
    """
    if not isinstance(was, dict):
        return ""
    # BOUND_BY_THE_DISCLOSURE is a tuple of PATHS, not of names: the head of each is the field
    # at the top of the answer. Comparing against the paths themselves would match nothing and
    # this would silently always say "nothing moved", which is worse than not saying it.
    bound = [path[0] for path in BOUND_BY_THE_DISCLOSURE]
    moved = sorted({name for name in bound if was.get(name) != now.get(name)})
    if not moved:
        return ""
    return ("\n\nWhat is different: " + ", ".join(moved)
            + ". Everything else matches what was shown.")


def _spend_the_disclosure(service, principal, presented: str, about: dict) -> None:
    """Claim a disclosure for THIS submission, from THIS connection, or refuse.

    `presented` is `<nonce>.<digest>`. The nonce says which approval; the digest says what was
    approved.

    Two checks, and they establish different things.

    The first is WHO IS SUBMITTING. The approval named one connection -- a device and a channel
    -- and that is compared against what this gateway worked out from the request in hand, never
    against anything the request claimed. A submission from another device, or over another
    channel, is refused here with a sentence that says which connection it was for, because that
    is a thing a person can act on.

    The second is WHAT IS BEING SUBMITTED. The digest is recomputed from the approval side as it
    was recorded, the execution side as it actually is, and the job now in hand. Anything that
    drifted -- the code, the command, the network, the limits, the policy -- produces a different
    number, and no amount of holding the first approval produces it.
    """
    nonce, _, carried = presented.partition(".")
    if not nonce or not carried:
        raise Refused("disclosure_required",
                      "That is not a disclosure this sandbox issued.",
                      "Call prepare for this job and send back what it returns.")
    shown = getattr(service, "_disclosures_shown", None) or {}
    # LOOKED AT first, spent last. An earlier version popped it here and checked afterwards, so
    # a submission that was going to be refused consumed the approval anyway -- one wrong-channel
    # attempt, or one stolen approval string used from the wrong place, and the person had to go
    # and agree to everything again. A refusal must not cost the thing being protected.
    kept = shown.get(nonce)
    if not kept:
        raise Refused("disclosure_required",
                      "That approval has already been used, or this sandbox never issued it.",
                      "Call prepare again and submit against what it returns.")
    if (time.time() - kept["when"]) > DISCLOSURE_GOOD_FOR_SECONDS:
        raise Refused("disclosure_required",
                      "That approval is older than %d minutes, so what it described may no "
                      "longer be what would happen."
                      % (DISCLOSURE_GOOD_FOR_SECONDS // 60),
                      "Call prepare again and submit against what it returns.")

    meant_for = kept["will_run_as"]
    arrived_as = {"device": principal.client_id,
                  "channel": principal.via or "(unrecorded)",
                  "shown_as": meant_for.get("shown_as", "")}
    if (arrived_as["device"] != meant_for.get("device")
            or arrived_as["channel"] != meant_for.get("channel")):
        raise Refused("disclosure_required",
                      "That approval was given for %s over %s, and this submission arrived from "
                      "somewhere else. An approval is for one connection."
                      % (meant_for.get("shown_as") or meant_for.get("device"),
                         meant_for.get("channel")),
                      "Submit it from the connection that was approved, or have somebody "
                      "approve this job for the connection you are using.")

    would_be = _what_would_happen(service, principal, about,
                                  approved_by=kept["approved_by"], will_run_as=meant_for)
    expected = _what_was_disclosed(would_be)
    if not hmac.compare_digest(carried, expected):
        raise Refused("disclosure_required",
                      "This submission is not the job that was disclosed. Something that "
                      "changes what would actually happen -- the code, the command, the network "
                      "it may reach, or how long it may run -- is different from what a person "
                      "was shown."
                      + _which_part_drifted(kept.get("disclosed"), would_be),
                      "Call prepare again with exactly this job, show a person the answer, and "
                      "submit against that.")

    # Everything holds, so now it is spent -- and spent by exactly one caller. Two submissions
    # racing on one approval both reach here; only the one whose `pop` returns the record goes
    # on, which is the same claim-in-one-step the pairing code makes, moved to the end where it
    # costs nothing to a caller who was going to be refused anyway.
    if shown.pop(nonce, None) is None:
        raise Refused("disclosure_required",
                      "That approval has just been used by something else.",
                      "Call prepare again and submit against what it returns.")


def _remember_the_disclosure(service, nonce: str, answer: dict) -> None:
    """Kept by the SERVER: what was approved, for which connection, and when.

    Keyed by the nonce alone rather than by nonce-and-device, because the device that SUBMITS is
    not always the device that approved -- that is the whole point of separating the two. The
    nonce is 128 bits this gateway chose; who may spend it is decided by what is stored here,
    not by being able to guess where it is filed.
    """
    shown = getattr(service, "_disclosures_shown", None)
    if shown is None:
        shown = service._disclosures_shown = {}
    shown[nonce] = {"when": time.time(),
                    "approved_by": answer["approved_by"],
                    "will_run_as": answer["will_run_as"],
                    # Kept so a refusal can say WHICH bound part moved. The bound fields only --
                    # this is not a copy of the job.
                    "disclosed": {path[0]: answer.get(path[0])
                                  for path in BOUND_BY_THE_DISCLOSURE}}


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


def _the_connection_this_is_for(service, principal, params) -> dict:
    """Which paired connection an approval is being given for. Defaults to the one asking.

    Nominating somebody else's connection is a device-management act, not sandbox use, so it
    needs the capability a person's own client holds. Without that rule anything holding a
    device token could have a person approve a job "for" a connection of its choosing.
    """
    channel = str(params.get("execution_channel") or "") or (principal.via or "(unrecorded)")
    if channel not in contract.CHANNELS and channel != "(unrecorded)":
        raise Refused("malformed",
                      "This sandbox has no channel called %r." % channel,
                      "Choose one of: " + ", ".join(contract.CHANNELS))
    wanted = str(params.get("execution_device") or "") or principal.client_id
    if wanted != principal.client_id and contract.MANAGE_DEVICES not in principal.capabilities:
        raise Refused("not_permitted",
                      "This device may not approve a job on behalf of another connection.",
                      "Approve it from the connection that will run it, or use a device that "
                      "manages this account's devices.")
    named = ""
    for device in service.state.paired_clients():
        if device.get("client_id") == wanted:
            named = str(device.get("client_name") or device.get("name") or "")
            break
    else:
        raise Refused("malformed",
                      "This sandbox has no paired connection to run that.",
                      "Pair the connection first, then approve a job for it.")
    # Stable server-side identifiers only. No token and no session id reaches a disclosure or
    # its digest: a person is shown a name, and what is bound is an id this gateway assigned.
    return {"device": wanted, "channel": channel, "shown_as": named or wanted}


def _what_would_happen(service, principal, params, *, approved_by=None, will_run_as=None):
    """What this job would actually do, composed server-side so every door shows the same thing.

    Separated from `prepare` so that `submit` can compose it again for the job it has in hand
    and compare. One function, so the thing a person is shown and the thing a submission is
    measured against cannot drift apart -- if they could, the check would pass while meaning
    nothing.
    """
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
            # SORTED. A set of destinations has no order, and two callers asking for the same
            # two hosts in different orders are asking for the same thing -- so binding the
            # order would bind something that is not a policy fact and refuse a submission that
            # matched its approval in every way that matters. Found exactly that way: the older
            # door sorts its allowlist on the wire and prepare did not.
            "allowed": (["anywhere this machine can reach"] if network == "unrestricted"
                        else sorted(domains) if network == "allowlist" else []),
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
    # --- who approved it, and what they approved it FOR -------------------------------------
    #
    # These are two different facts and an earlier version had them as one. Binding a single
    # "channel" meant prepare and submit had to arrive the same way, which forbids the ordinary
    # arrangement this product exists for: a person confirms comfortably in a browser, and the
    # AI they set up runs the job over MCP afterwards.
    #
    # So the side that is OBSERVED and the side that is CHOSEN are kept apart. Who approved it
    # and where they were when they did is established by this gateway from the request itself.
    # What they approved it for is chosen at this call, shown to them in words, and bound -- so
    # approving in a browser approves one named connection rather than approving in general.
    answer["approved_by"] = approved_by or {
        "account": principal.client_id,
        "channel": principal.via or "(unrecorded)",
        "shown_as": principal.device_name or principal.client_id,
    }
    answer["will_run_as"] = will_run_as or _the_connection_this_is_for(service, principal, params)
    answer["requested_policy_sha256"] = _digest_of_the_policy(asked_for)
    answer["operator_policy_sha256"] = _digest_of_the_policy(
        getattr(service, "operator_policy", None))
    # Names only, and there are none: this sandbox does not release named secrets into a job at
    # all. Said rather than omitted, because an empty section a person can see is an answer and
    # a missing section is a guess.
    answer["secrets"] = {"names_released": [], "values": "never disclosed, and never sent"}
    answer["good_for_seconds"] = DISCLOSURE_GOOD_FOR_SECONDS
    # The honest remainder. A disclosure of this kind is often expected to cover these, and this
    # sandbox does not model them -- so it says so here rather than leaving a person to assume
    # the silence means "none" or "handled".
    answer["not_modelled"] = [
        "Data classes: this sandbox does not classify what a job processes, so nothing here "
        "says what kind of data is involved.",
        "Region and retention: it runs where its worker runs, and keeps what its ledger keeps. "
        "There is no per-job region or retention setting to disclose.",
        "Price: this is a closed test service with no billing, so what is shown is time and "
        "runs against the ceilings, not money.",
        "The EFFECTIVE policy is the composition of what is asked for and what the operator "
        "allows, and is only settled at admission. What is bound here is what was ASKED for "
        "and the operator policy in force; what was granted comes back with the submission.",
    ]
    return answer


def _digest_of_the_policy(policy) -> str:
    """One digest, computed the way the rest of the product computes policy digests."""
    import hashlib

    if policy is None:
        return ""
    try:
        from agentnode_sdk.gateway.policy_paths import policy_shape
        from agentnode_sdk.gateway.protocol import canonical_bytes

        return hashlib.sha256(canonical_bytes(policy_shape(policy))).hexdigest()
    except Exception:                                         # noqa: BLE001
        # A policy this build cannot digest is reported as one it cannot digest. Returning ""
        # here would make two different policies look alike, which is the one thing a digest
        # must never do, so it returns something that cannot collide with a real digest.
        return "(this gateway could not digest the policy in force)"


def _with_the_digest_worked_out(params: dict) -> dict:
    """Fill in the artifact's digest and size from the artifact, when it was supplied.

    A caller that already knows them keeps sending them and nothing changes. A caller that does
    not -- which is any AI holding only these tools, because none of them can hash anything --
    sends the code instead and is told what it is about to agree to.

    When both are given they must AGREE. Trusting the stated digest over the bytes in hand would
    let a caller have one thing described and another thing bound; recomputing silently would
    hide that they disagreed. So it is a refusal, and it says which is which.
    """
    artifact = params.get("artifact")
    if not artifact:
        return params
    try:
        raw = base64.b64decode(str(artifact), validate=True)
    except Exception as broke:                                # noqa: BLE001
        raise Refused("malformed", "The artifact is not valid base64: %s" % broke,
                      "Send the code base64-encoded, or leave it out and send the digest.")             from None
    worked_out = hashlib.sha256(raw).hexdigest()
    stated = str(params.get("artifact_sha256") or "")
    if stated and stated != worked_out:
        # `malformed` rather than a new name: the declared vocabulary of refusals is small on
        # purpose, and "the parameters disagree with each other" is exactly what it covers.
        raise Refused(
            "malformed",
            "The digest you gave does not match the code you sent: you said %s, the code is %s."
            % (stated[:16], worked_out[:16]),
            "Send the code without a digest and this gateway will work it out, or send the "
            "digest of the code you actually mean.")
    said_bytes = params.get("artifact_bytes")
    if said_bytes not in (None, "") and int(said_bytes) != len(raw):
        raise Refused(
            "malformed",
            "The size you gave does not match the code you sent: you said %s bytes, it is %d."
            % (said_bytes, len(raw)),
            "Leave the size out and this gateway will work it out.")
    filled = dict(params)
    filled["artifact_sha256"] = worked_out
    filled["artifact_bytes"] = len(raw)
    # Not part of what is described, and not carried into the disclosure: `prepare` says what
    # WOULD happen. The code travels with `submit`.
    filled.pop("artifact", None)
    return filled


def _prepare(service, principal, params):
    """Show what would happen, and issue a single-use proof that it was shown.

    The proof is `<nonce>.<digest>`. The digest is over the parts that would change what
    actually happens, taken server-side over the server's own answer -- a digest a caller
    computed would bind whatever the caller decided to hash. The nonce makes each disclosure its
    own: without it two identical jobs would produce the same proof, so preparing twice and
    submitting twice would work off one consent, and spending one would silently spend the
    other.
    """
    params = _with_the_digest_worked_out(params)
    answer = _what_would_happen(service, principal, params)
    nonce = secrets.token_hex(16)
    answer["accepted_disclosure"] = "%s.%s" % (nonce, _what_was_disclosed(answer))
    _remember_the_disclosure(service, nonce, answer)
    return answer


#: Where a submission leaves the record it produced, for `submitted_record` to collect one line
#: later.
#:
#: Thread-local, and emphatically not a dict keyed by the principal. The first version used
#: `id(principal)`, which is a reused address rather than an identity: once a principal is
#: collected the next object can be handed the same id, and a later submission would then pick
#: up a record belonging to somebody else's request. It survived every test run in isolation and
#: came apart in a full one, which is what that class of bug does.
_handoff = threading.local()


def submitted_record(service, principal: Principal, params: dict) -> dict:
    """Submit, and render the whole record, for a door whose wire shape predates the contract.

    Goes through `dispatch` like everything else -- every check, in the same order, in the same
    place -- and differs only in what it renders afterwards. The older doors answer with the
    entire signed record and their clients read fields the narrower `submit` answer does not
    carry; translating them must not lose that.
    """
    answer = dispatch("submit", params, principal, service=service)
    record = getattr(_handoff, "record", None)
    _handoff.record = None
    if record is None:                                        # pragma: no cover - defensive
        return dict(answer)
    return record.public()


def _submit(service, principal, params):
    """Run something, having first established that this exact thing was disclosed.

    The order matters and is not the order it was in. What is being submitted has to be worked
    out BEFORE the disclosure can be judged, because judging it means recomputing what a
    disclosure for THIS submission would look like and requiring the presented one to match.

    Without that, the gate was a formality: a caller could call `prepare` for one job, be shown
    what that job would do, and then spend the same disclosure on a different job entirely. The
    digest was checked for existence, never against the submission it arrived with. So "nothing
    runs that a person was not shown" held only for callers who were not trying.

    Five bindings, and all five are checked here:

    * the DEVICE -- the disclosure is stored under the client it was shown to;
    * the CONTENT -- artifact digest, command, network, limits and ceilings, recomputed and
      compared, so any change after acceptance invalidates it;
    * the EXPIRY -- older than the window and it is not spendable;
    * the NONCE -- each disclosure is its own, so two identical jobs do not share one;
    * ONE USE -- claimed and removed in the same step, so two submissions racing on one cannot
      both be told they had it.
    """
    import base64

    from agentnode_sdk.gateway.policy_paths import policy_shape
    from agentnode_sdk.gateway.protocol import JobRequest, canonical_bytes, digest
    from agentnode_sdk.sandbox.contract import Limits, NetworkRules, SandboxPolicy

    artifact = params.get("artifact") or b""
    if isinstance(artifact, str):
        try:
            artifact = base64.b64decode(artifact, validate=True)
        except Exception as exc:                              # noqa: BLE001
            raise Refused("malformed", "The artifact is not valid base64.",
                          "Send the code base64-encoded.") from exc

    claimed = str(params.get("artifact_sha256") or "")
    if claimed and not hmac.compare_digest(claimed, digest(artifact)):
        raise Refused("malformed",
                      "The artifact does not match the digest this request is signed for.",
                      "Send the artifact this request describes, or sign a request for the one "
                      "you are sending.")
    when = params.get("issued_at")
    if when:
        from agentnode_sdk.gateway.protocol import check_freshness

        try:
            check_freshness(float(when))
        except Exception as exc:                              # noqa: BLE001
            raise _translate(exc) from exc

    network = params.get("network") or "none"
    domains = tuple(params.get("allowed_domains") or ())
    wall_clock = max(1, int(params.get("wall_clock_s") or 60))

    # The policy the caller is ASKING for, digested the same way the existing client digests it.
    # Composed here rather than accepted from the caller: a digest a caller chose would bind
    # whatever the caller decided to hash.
    if network == "none":
        rules = NetworkRules(enabled=False, allowed_destinations=frozenset())
    elif network == "unrestricted":
        # None is not the empty set here, and collapsing them would digest the widest and the
        # narrowest policy to the same value. The older door could ask for this; so can this one.
        rules = NetworkRules(enabled=True, allowed_destinations=None)
    else:
        rules = NetworkRules(enabled=True, allowed_destinations=frozenset(domains))
    asked_for = SandboxPolicy(network=rules, limits=Limits(wall_clock_s=wall_clock))
    composed = digest(canonical_bytes(policy_shape(asked_for)))
    said_policy = str(params.get("policy_sha256") or "")
    if said_policy and not hmac.compare_digest(said_policy, composed):
        raise Refused("malformed",
                      "This request is signed for a different policy than the one it asks for.",
                      "Compose the digest from the policy you are actually requesting.")

    # A replay is refused HERE, before consent is even looked at. It has to be: after a
    # restart the approvals this gateway was holding are gone, so a captured request re-sent
    # from outside would be answered "nobody agreed to this" -- true, and the wrong thing to
    # say about a request that is being replayed at you. The durable ledger is the same source
    # `admit` uses; asking it early only changes which refusal arrives first.
    #
    # Safe to refuse early because reconnecting is NOT a re-POST: a client that lost the answer
    # asks `status`, which needs no approval and carries no risk of running anything twice.
    said_nonce = str(params.get("nonce") or "")
    if said_nonce:
        try:
            already = service.ledger.knows_nonce(said_nonce)
        except Exception:                                     # noqa: BLE001
            already = False
        if already:
            raise Refused("malformed",
                          "this request has already been used (replay)",
                          "Ask `status` about the run it started; a dropped answer is read "
                          "again, not sent again.")

    # AFTER everything that establishes the request is what it says it is, and before anything
    # runs. A request signed for another artifact, or another policy, or issued an hour ago is
    # WRONG, and telling its sender "nobody agreed to this" would send them to fix the one thing
    # that was not the problem. Consent is the last gate, not the first.
    presented = str(params.get("accepted_disclosure") or "")
    if not presented:
        # Deliberately NOT "call prepare for them and carry on". A gateway that obtains the
        # consent it requires, on behalf of the party it is protecting the person from, has not
        # obtained consent. It says what is missing and what to do, and runs nothing.
        raise Refused("disclosure_required",
                      "Nothing runs here that was not disclosed to a person first, and this "
                      "submission carried no proof that anything was.",
                      "Call prepare with exactly this job, show a person what it returns, and "
                      "send back the accepted_disclosure it gave you once they have agreed.")
    _spend_the_disclosure(service, principal, presented, {
        "command": list(params.get("command") or ()),
        "artifact_sha256": digest(artifact),
        "artifact_bytes": len(artifact),
        "network": network,
        "allowed_domains": list(domains),
        "wall_clock_s": wall_clock,
    })


    # Everything the caller asked for, carried through rather than summarised. What a job
    # REQUIRES of the sandbox is the part that must never soften in passing: dropping a required
    # property would run the job with less than was asked for and call that success.
    request = JobRequest(
        job_id=str(params.get("job_id") or params["run_id"]),
        run_id=str(params["run_id"]),
        artifact_sha256=digest(artifact),
        policy_sha256=composed,
        required_properties=tuple(params.get("required_properties") or ()),
        mandatory=tuple(params.get("mandatory") or ()),
        optional=tuple(params.get("optional") or ()),
        command=tuple(params.get("command") or ()),
        network=network,
        allowed_domains=domains,
        wall_clock_s=wall_clock,
        **({"nonce": str(params["nonce"])} if params.get("nonce") else {}),
        **({"issued_at": float(params["issued_at"])} if params.get("issued_at") else {}),
    )
    try:
        record = service.submit(request, artifact, token=principal.token)
    except Exception as exc:                                  # noqa: BLE001
        raise _translate(exc) from exc
    # Handed back with the answer, not looked up afterwards. A submission that is REFUSED -- a
    # second request claiming a run id that already exists, say -- produces a record that is not
    # the run filed under that id, so `service.runs[run_id]` would hand back the earlier run and
    # report somebody else's success as this submission's outcome.
    # A refusal names itself, wherever it was decided. `GatewayService.submit` answers a
    # refused job with a RECORD rather than by raising, because the older doors hand that whole
    # record back and their clients read it. The contract's door does not: it refuses, by name,
    # with something to do about it -- the same shape as every other refusal here.
    if record.state == "refused" and getattr(record, "refused_as", ""):
        raise Refused(record.refused_as, record.refusal,
                      getattr(record, "refusal_remedy", "")
                      or "Ask whoever runs this sandbox.")

    _handoff.record = record
    told = record.public()
    return {"run_id": record.run_id, "state": record.state,
            "admitted_under": dict(getattr(record, "admitted_under_values", {}) or {}),
            "request_policy_sha256": told.get("request_policy_sha256", ""),
            "effective_policy_sha256": told.get("effective_policy_sha256", "")}


def _a_run_of_this_caller(service, principal, run_id):
    """A run belongs to the device that submitted it, inside the account that device is in.

    Deliberately BOTH, and deliberately still per device. Per device is the narrower rule and
    keeping it means accounts did not quietly widen what one credential can reach; the account
    test is a second, independent condition, so a device id that somehow appeared in two
    accounts still could not reach across. Neither test is load-bearing alone.
    """
    record = service.runs.get(str(run_id))
    owning_account = getattr(record, "owner_account_id", "") if record is not None else ""
    if record is None or (record.owner_client_id and
                          record.owner_client_id != principal.client_id) or (
                              owning_account and owning_account != principal.account_id):
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
    """The devices of the account that asked, and no others.

    This used to be `paired_clients()`, which is everything this gateway holds. On a gateway
    with one customer those are the same list and the defect is invisible; on a gateway with two
    it is a customer list handed to whoever asks.
    """
    return {"devices": [
        {"device_id": d.get("client_id", ""), "name": d.get("client_name", ""),
         "last_used": d.get("last_used"), "paired_at": d.get("issued_at")}
        for d in service.state.devices_in(principal.account_id)
    ]}


def _connections_enrol(service, principal, params):
    """Start setting up a connection, and issue what it will have to answer."""
    from agentnode_sdk.access.enrolment import NoSuchChallenge

    try:
        # Scoped to the ACCOUNT, not to the device that happened to click. A person setting up
        # their AI from their laptop and finishing on their desktop is one customer doing one
        # thing; a person in another account is not, and that is the line this draws.
        begun = service.connections.begin(principal.account_id, str(params["way_in"]),
                                          str(params["label"]))
    except NoSuchChallenge as exc:                            # pragma: no cover - defensive
        raise Refused("malformed", str(exc), "Start the setup again.") from exc
    return {"challenge": begun["challenge"], "ticket": begun["ticket"],
            "expires_at": int(begun["expires_at"])}


def _connections_check(service, principal, params):
    """Whether that connection has done the thing. Read from this gateway's own audit."""
    from agentnode_sdk.access.enrolment import NoSuchChallenge

    try:
        found = service.connections.about(str(params["challenge"]))
    except NoSuchChallenge as exc:
        raise Refused("no_such_run", str(exc),
                      "Start setting the connection up again.") from exc
    if found["account"] != principal.account_id:
        # The same answer as one that does not exist. A challenge is not a thing to enumerate.
        raise Refused("no_such_run",
                      "this sandbox is not setting up a connection under that name",
                      "Start setting the connection up again.")
    said = service.connections.satisfied_by(str(params["challenge"]), lambda: _audit_lines(service))
    return {"satisfied": bool(said.get("satisfied")), "label": found["label"],
            "way_in": found["channel"], "operation": found["operation"],
            "why": said.get("why", "")}


def _audit_lines(service):
    """This gateway's own record of what it carried out. Nothing else takes part in a verdict."""
    path = os.path.join(str(service.state.root), "audit.jsonl")
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue
    except OSError:
        return


def _sessions_list(service, principal, params):
    return {"sessions": service.sessions.belonging_to(principal.client_id)}


def _sessions_end(service, principal, params):
    """End a session. Yours by default, another of your own by name.

    Named by what the list shows and never by the identifier itself -- a caller ending a session
    is looking at a list, and a revoke that needed the identifier could only ever be performed by
    the session being revoked, which is exactly backwards.
    """
    wanted = str(params.get("session") or "")
    if not wanted:
        if not principal.session_id:
            raise Refused("malformed",
                          "There is no session making this request, so there is no this one "
                          "to end.",
                          "Name the session to end, or sign out from the browser.")
        return {"session": "", "ended": bool(service.sessions.end(principal.session_id)),
                "this_one": True}
    mine = {s["session"] for s in service.sessions.belonging_to(principal.client_id)}
    if wanted not in mine:
        # The same answer as one that does not exist. Telling somebody that a session exists
        # but is not theirs tells them it exists.
        return {"session": wanted, "ended": False, "this_one": False}
    ended = service.sessions.end_named(wanted)
    return {"session": wanted, "ended": bool(ended),
            "this_one": bool(principal.session_id
                             and wanted == fingerprint_of(principal.session_id))}


def fingerprint_of(session_id: str) -> str:
    from agentnode_sdk.access.sessions import fingerprint

    return fingerprint(session_id)


def _devices_rotate(service, principal, params):
    """Hand back a replacement credential for the identity already asking.

    Client-initiated on purpose: rotating only from the server side would mean an operator
    conveying a new secret by hand, which is the moment secrets get pasted into chat windows.
    """
    replacement = service.state.rotate_token(principal.token)
    if replacement is None:
        raise Refused("not_authenticated",
                      "This request did not come with a credential this sandbox recognises.",
                      "Pair this device again with a fresh invitation.")
    return {"token": replacement, "device_id": principal.client_id}


def _devices_revoke(service, principal, params):
    """Withdraw a device, and take back everything it had already been given.

    Removing the credential stops the NEXT request. That is not the whole of revocation, and a
    review was right to refuse it as such: what a device already holds keeps working unless
    something reaches for it.

    Three things are already-issued authority, and all three go:

    * its sessions -- otherwise a browser signed in as that device carries on;
    * its runs -- a job it started is its work, still executing, in a sandbox nobody may now
      ask about; leaving it would mean a withdrawn device's code kept running to completion;
    * its enrolments -- an unspent download ticket MINTS A FRESH CREDENTIAL when collected, so
      one left standing is a way to walk straight back in.

    Order matters. The credential is removed LAST, so that everything above is done on behalf of
    a device this gateway still recognises; doing it the other way round means asking questions
    about somebody who is already nobody.
    """
    from agentnode_sdk.gateway.protocol import is_terminal

    wanted = str(params["device_id"])
    # WHOSE device, before anything is done to it. Everything below this line takes something
    # away, and doing any of it to a device belonging to somebody else is the same breach
    # whether or not the credential removal at the end would have been refused.
    #
    # The answer for another account's device is the answer for a device that does not exist:
    # `withdrawn: false`, nothing stopped, nothing ended. Telling somebody that a device exists
    # but is not theirs tells them it exists.
    if wanted not in {str(d.get("client_id") or "")
                      for d in service.state.devices_in(principal.account_id)}:
        return {"device_id": wanted, "withdrawn": False, "runs_stopping": []}
    service.sessions.end_every(wanted)
    service.connections.drop_everything_touching(wanted)

    stopped = []
    for run_id, record in list(service.runs.items()):
        if getattr(record, "owner_client_id", "") != wanted or is_terminal(record.state):
            continue
        record.cancel_requested.set()
        try:
            service.stopping.ask(run_id, by="(a withdrawal)")
            stopped.append(run_id)
        except Exception:                                     # noqa: BLE001
            # A run that could not be queued is not a reason to leave the credential in place.
            # The withdrawal still happens, and the run is reported as one that was not stopped
            # rather than quietly counted as stopped.
            pass

    return {"device_id": wanted,
            "withdrawn": bool(service.state.revoke_client(
                wanted, within_account=principal.account_id)),
            "runs_stopping": stopped}


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
    "connections.enrol": _connections_enrol,
    "connections.check": _connections_check,
    "sessions.list": _sessions_list,
    "sessions.end": _sessions_end,
    "devices.list": _devices_list,
    "devices.rotate": _devices_rotate,
    "devices.revoke": _devices_revoke,
}
