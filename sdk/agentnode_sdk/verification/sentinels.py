"""Two values, each crossing between the machines, each tied to everything that makes it mean
something.

A sentinel establishes that two machines are two. It is worth exactly as much as the binding
around it, and the previous tool's binding was: a value, and the fact that a grep somewhere had
seen it. `EM3C-E6-RECORD-0001` found that the grep was looking in a file the value could never be
in, so the confirmation had never once succeeded across three external runs.

What a crossing is tied to here:

* the RUN it belongs to -- the signed record's own run id, not the one this tool asked for;
* the PAYLOAD that carried it -- by digest, so a value found in some other job's output is not
  this crossing;
* the RECORD that holds it -- by digest, so what was decided from can be held against later;
* the CHANNEL it arrived on -- taken off the answer, which only a channel can have made.

And two directions, which are not the same claim:

* what the CLIENT made reaches the far side -- established from the gateway's signed record of the
  run, which is where a sandbox job's output actually is;
* what the FAR MACHINE is comes back -- the job prints the machine's own identity from inside the
  sandbox, the signed record carries it, and a second, separate channel asks the machine what its
  identity is. Neither channel vouches for itself, and no log is read.
"""
from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import asdict, dataclass

from agentnode_sdk.gateway import protocol as wire
from agentnode_sdk.verification import channels


class SentinelError(Exception):
    """This crossing cannot be decided, which is not the same as deciding it did not happen."""


def a_fresh_value() -> str:
    """A value nothing else will have. Long enough that finding it anywhere is not a coincidence."""
    return secrets.token_hex(16)


@dataclass(frozen=True)
class Crossing:
    """One value, and everything that makes finding it mean what it is taken to mean."""

    what: str
    #: Where the value was made. Established by construction -- each maker below can only make
    #: the direction it is named for -- rather than passed in as a word.
    made_on: str
    value: str
    run_id: str
    #: What the client sent, as it was sent. The direction a value travelled is DERIVED from
    #: this by whoever reads the record -- a value in the payload is the client's, one the
    #: payload never carried was made where the job ran -- so it is here rather than summarised.
    payload_text: str
    payload_sha256: str
    #: The channel that carried the confirmation, taken from the answer itself.
    confirmed_by: str
    asked: str
    record_sha256: str
    #: True only when a channel answered AND what it answered contains the value.
    holds: bool
    #: True when every channel involved could be reached at all. False means this crossing is
    #: undecided rather than refuted, and the two are never collapsed.
    decidable: bool
    why: str

    def as_dict(self) -> dict:
        return asdict(self)


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def from_the(channel, expected) -> None:
    """Refuse anything that is not the channel this half of a crossing has to come from.

    `EM3C-VERIFY-0002`: these functions took whatever was passed and read the channel's name off
    the answer, so attribution was data. What a half of a crossing is confirmed by is now the type
    of the thing that answered -- an object that merely behaves like a gateway is not one, and
    something that is one cannot be told to call itself otherwise, because `Channel.said` writes
    its own class's name.
    """
    if not isinstance(channel, expected):
        raise SentinelError(
            "this half of a crossing has to be confirmed by " + expected.name + ", and what was "
            "given is a " + type(channel).__name__ + ". A thing that answers like a channel is "
            "not the channel: which one answered is what the crossing rests on")


def named(*answers) -> str:
    """How the channels that confirmed a crossing are written down.

    Built from the answers, never from literals: each name comes off an `Answer`, which only a
    channel can have made. The string is a rendering of what was established, not the record of it
    -- what establishes it is `from_the` above, before anything is asked.
    """
    return " and ".join(dict.fromkeys(a.channel for a in answers))


def _record_of(gateway, run_id: str, payload: bytes, what: str, made_on: str, value: str):
    """The signed record for this run, or a Crossing saying why there is none to read."""
    answer = gateway.record_of(run_id)
    common = dict(what=what, made_on=made_on, value=value, run_id=run_id,
                  payload_text=payload.decode("utf-8", "replace"),
                  payload_sha256=_digest(payload), confirmed_by=named(answer),
                  asked=answer.asked)
    if not answer.answered:
        return None, Crossing(record_sha256="", holds=False, decidable=False,
                              why="the gateway could not be asked: " + answer.trouble, **common)
    record = answer.value or {}
    if str(record.get(wire.RUN_ID_FIELD) or "") != run_id:
        return None, Crossing(
            record_sha256=answer.digest(), holds=False, decidable=False,
            why=("the signed record is about run " + str(record.get(wire.RUN_ID_FIELD))
                 + ", not " + run_id
                 + ". An answer about another run says nothing about this one"), **common)
    return (record, answer), Crossing(record_sha256=answer.digest(), holds=False, decidable=True,
                                      why="", **common)


def what_the_client_made(gateway, run_id: str, payload: bytes, value: str) -> Crossing:
    """The client made this value and put it in the payload. Did the far side's record carry it?

    Read out of the gateway's own signed record of the run, which is where a sandbox job's output
    is. Nothing greps anything, and nothing asks a shell about a run.
    """
    from_the(gateway, channels.TheGatewayItself)
    if value.encode("utf-8") not in payload:
        raise SentinelError(
            "this value is not in the payload it is said to have travelled in, so whatever finding "
            "it would establish, it would not be that this payload carried it")
    got, unfinished = _record_of(gateway, run_id, payload, "a value made on the client",
                                 "the client", value)
    if got is None:
        return unfinished
    record, answer = got
    where = str(record.get(wire.STDOUT_FIELD) or "")
    found = value in where
    return Crossing(
        **{k: v for k, v in unfinished.as_dict().items()
           if k not in ("holds", "why", "decidable")},
        holds=found, decidable=True,
        why=("the gateway's signed record of this run carries it in the output of the job the "
             "client sent" if found else
             "the gateway's signed record of this run does not carry it, and that record is where "
             "the output of the job the client sent is"))


def what_the_gateway_issued(gateway, ledger, run_id: str, payload: bytes,
                            gateway_id: str, now=None) -> Crossing:
    """The gateway made a value for this run. Did what came back turn out to be it?

    `EM3C-E7-RECORD-0001` killed the attempt before this one: the job printed the identity of the
    machine it was running on, and a container does not share that with its host, so two correct
    answers disagreed. `EM3C-CROSSING-DECISION-0001` chose this instead.

    Two channels and neither vouches for itself. The VALUE arrives only inside the gateway's signed
    record of the run, because the job echoed it. The DIGEST arrives only from the gateway's own
    durable record, read over ssh through its read-only command -- and that record never held the
    value, so this channel could not have supplied it even if it wanted to.

    Everything the binding is checked against is something the asker establishes for itself: the
    run it submitted, the gateway it paired with, and the policy digest the SIGNED ANSWER carries.
    A document agreeing only with itself would establish nothing.
    """
    from agentnode_sdk.gateway import challenge as ch

    from_the(gateway, channels.TheGatewayItself)
    from_the(ledger, channels.TheGatewaysOwnRecord)

    said = ledger.for_run(run_id)
    got, unfinished = _record_of(gateway, run_id, payload, "the challenge this gateway issued",
                                 "the far machine", "")
    if got is None:
        return unfinished
    record, answer = got
    base = {k: v for k, v in unfinished.as_dict().items()
            if k not in ("holds", "why", "decidable", "confirmed_by", "value")}
    both = named(answer, said)
    if not said.answered:
        return Crossing(**base, value="", confirmed_by=both, holds=False, decidable=False,
                        why="the gateway's own record could not be read: " + said.trouble)

    # What came back, taken out of the job's own output. A marked line rather than a bare value,
    # so that finding it is finding something the job put there on purpose.
    carried = str(record.get(wire.STDOUT_FIELD) or "")
    found = re.search(ch.ECHO + r"\s+([0-9a-f]{8,})", carried)
    value = found.group(1) if found else ""
    # And where it found itself. `EM3C-CROSSING-0001`, F-C2-INSTANCE-NOT-VERIFIED: the binding
    # named an executing instance that nothing compared with anything. What it is compared with
    # is this -- which came back on the SIGNED ANSWER, not from where the binding came from.
    ran_in = re.search(ch.ECHO_INSTANCE + r"\s+(\S+)", carried)
    instance = ran_in.group(1) if ran_in else ""

    # The half that makes it a crossing at all: a value the client could have written into its own
    # payload establishes nothing about the far side. This one must NOT be in what was sent.
    if value and value.encode("utf-8") in payload:
        return Crossing(**base, value=value, confirmed_by=both, holds=False, decidable=True,
                        why=("this value is in the payload the client sent, so the client could "
                             "have produced it and it says nothing about where the job ran"))
    try:
        binding = ch.read(said.value)
    except ch.ChallengeError as exc:
        return Crossing(**base, value=value, confirmed_by=both, holds=False, decidable=False,
                        why="the gateway's own record could not be read: " + str(exc))
    why = ch.why_it_does_not_hold(
        binding, run_id=run_id, gateway_id=gateway_id,
        effective_policy_sha256=str(record.get("effective_policy_sha256") or ""),
        value=value, backend_instance=instance, now=now)
    return Crossing(**base, value=value, confirmed_by=both, holds=not why, decidable=True,
                    why=(why or ("what came back is the value this gateway wrote down the digest "
                                 "of before the job started, and the client never had it")))


def both_ways(client_side: Crossing, machine_side: Crossing) -> tuple[bool, str]:
    """Two machines, or a reason it cannot be said. Undecided is never rounded to no."""
    if not (client_side.decidable and machine_side.decidable):
        undecided = [c for c in (client_side, machine_side) if not c.decidable]
        return False, ("this cannot be decided: "
                       + "; ".join(c.why for c in undecided))
    if client_side.holds and machine_side.holds:
        return True, ("a value made here was found in the far side's signed record of the run, and "
                      "what ran there printed an identity a separate channel confirms is that "
                      "machine's")
    missing = [c.what for c in (client_side, machine_side) if not c.holds]
    return False, "these did not cross: " + ", ".join(missing)
