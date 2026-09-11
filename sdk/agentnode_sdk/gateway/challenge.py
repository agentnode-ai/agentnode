"""A value this gateway makes for one run, so that what comes back can be shown to have run here.

`EM3C-E7-RECORD-0001`: the seventh external run tried to establish that a job had run on the far
machine by having it print `/etc/machine-id` from inside the sandbox and comparing that with what
the machine said about itself over a second channel. The two cannot agree, and that is right: a
container is deliberately its own environment and does not share that file with its host. The
question was asked of a channel that could answer it -- unlike the sixth run, which searched a log
file the value was never written to -- but it was the wrong question about the wrong thing.

`EM3C-CROSSING-DECISION-0001` chose this instead. When a run is admitted, the gateway makes a value
nothing else has, writes down a small document binding it to that run -- and writes down only its
DIGEST, never the value. The value goes to the process that will run the job, over the one channel
that already carries the job, and comes back inside the answer the gateway signs. Anybody wanting
to know whether the answer's content came from here can compare the value in the answer with the
digest this gateway wrote down before the job started, reading that digest over a channel that is
not the one the value came on.

## What this establishes, and what it does not

It establishes that the code ran somewhere this gateway put the challenge, and that what came back
originated on this side rather than being assembled by whoever asked.

It does NOT identify the physical machine. That is what pairing and the authenticated tunnel are
for, and they are unchanged.

It does NOT protect against a gateway that lies. The signature over the answer is the trust anchor
of the whole arrangement; a gateway willing to sign a false answer can sign one that includes its
own challenge, and nothing on the client's side recovers from that. What this separates is a client
fooling itself from a real remote execution -- which is what six of the seven external runs in this
arc were lost to.

The challenge is NOT a credential. Holding it grants nothing, opens nothing, and authorises
nothing. It is single-use, it expires, and the value is dropped when the run ends.
"""
from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import asdict, dataclass, fields
from typing import Any

#: Long enough that finding it somewhere is not a coincidence, and short enough to read.
CHALLENGE_BYTES = 16

#: How long a binding is worth checking against. A challenge outlives its run by enough that a
#: slow answer still verifies, and not so much that an old one is worth keeping.
LIFETIME_SECONDS = 3600.0

#: How far apart the two clocks may be before an expiry is treated as meaningful.
CLOCK_SLACK_SECONDS = 300.0

#: What the bootstrap calls the value inside the container, once it has read it off stdin. It is
#: an environment variable of the SANDBOX process only -- never of any process on the host, and
#: never an argument to one. `EM3C-CROSSING-DECISION-0001`, F-A-ARGV-EXPOSURE.
INSIDE_THE_SANDBOX = "AGENTNODE_RUN_CHALLENGE"

#: What a job prints to send it back. A marker rather than a bare value, so that finding it in the
#: output is finding something that was put there deliberately.
ECHO = "RETURNED-FROM-THE-FAR-SIDE"

#: Why a run has no challenge to carry. A job that brings its own command is not given one: its
#: standard input is its own, and changing what it reads there to carry this would be altering the
#: job. The document says so rather than the crossing quietly failing.
BROUGHT_ITS_OWN_COMMAND = (
    "this job brought its own command, so its standard input was left as the job's own and no "
    "challenge was put on it")


class ChallengeError(Exception):
    """This binding cannot be read, or does not say what it would have to say."""


@dataclass(frozen=True)
class Binding:
    """What this gateway wrote down about a challenge, before the job it belongs to started.

    The value is not here and never was. What is here is its digest, so that somebody holding the
    value can be told whether it is the one this gateway issued -- and somebody holding only this
    cannot produce the value.
    """

    run_id: str
    gateway_id: str
    #: Which gateway process, and which sandbox behind it, would run this. Two runs of one state
    #: directory across a restart are different instances, and the document says which.
    backend_instance: str
    effective_policy_sha256: str
    issued_at: float
    expires_at: float
    challenge_sha256: str
    #: Whether the value reached the process that would run the job at all.
    delivered: bool
    not_delivered_because: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


FIELD_NAMES = tuple(f.name for f in fields(Binding))


def a_fresh_challenge() -> str:
    """A value nothing else has. From the same source the gateway's own credentials come from."""
    return secrets.token_hex(CHALLENGE_BYTES)


def digest_of(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def bind(*, run_id: str, gateway_id: str, backend_instance: str, effective_policy_sha256: str,
         value: str, delivered: bool, because: str = "", now: float | None = None,
         lifetime: float = LIFETIME_SECONDS) -> Binding:
    """Write down what this challenge belongs to. The value goes no further than its digest."""
    at = time.time() if now is None else now
    return Binding(
        run_id=str(run_id), gateway_id=str(gateway_id), backend_instance=str(backend_instance),
        effective_policy_sha256=str(effective_policy_sha256),
        issued_at=at, expires_at=at + float(lifetime),
        challenge_sha256=digest_of(value) if value else "",
        delivered=bool(delivered), not_delivered_because=str(because))


def read(document: Any) -> Binding:
    """One binding document, or a refusal. Nothing is defaulted and nothing is ignored."""
    if not isinstance(document, dict):
        raise ChallengeError("a challenge binding is not an object")
    unknown = sorted(set(document) - set(FIELD_NAMES))
    if unknown:
        raise ChallengeError(
            "a challenge binding carries " + ", ".join(repr(x) for x in unknown)
            + ", which this build does not describe")
    missing = [name for name in FIELD_NAMES
               if name not in document and name != "not_delivered_because"]
    if missing:
        raise ChallengeError("a challenge binding has no " + ", ".join(missing))
    return Binding(**{name: document.get(name, "") for name in FIELD_NAMES})


def why_it_does_not_hold(binding: Binding, *, run_id: str, gateway_id: str,
                         effective_policy_sha256: str, value: str,
                         now: float | None = None) -> str:
    """Empty when this value is the one that gateway issued for that run. Otherwise, why not.

    Everything on the right of each comparison is something the asker establishes for itself --
    the run it submitted, the gateway it paired with, the policy digest the SIGNED ANSWER carries
    -- rather than something read from the same place as the binding. A document that agrees only
    with itself would establish nothing.
    """
    at = time.time() if now is None else now
    # Before asking what came back: a run that was never given a challenge has nothing to have
    # sent back, and saying "nothing came back" about it would describe the symptom instead of
    # the reason.
    if not binding.delivered:
        return (binding.not_delivered_because
                or "the challenge never reached the process that would run the job")
    if not value:
        return "nothing came back to check against it"
    if binding.run_id != str(run_id):
        return ("this binding is about run " + binding.run_id + ", not " + str(run_id)
                + ". A document about another run says nothing about this one")
    if binding.gateway_id != str(gateway_id):
        return ("this binding names gateway " + (binding.gateway_id or "nobody") + ", and the one "
                "that was paired with is " + str(gateway_id))
    if binding.effective_policy_sha256 != str(effective_policy_sha256):
        return ("this binding was issued under policy " + binding.effective_policy_sha256[:16]
                + "... and the answer says the run happened under "
                + str(effective_policy_sha256)[:16] + "...")
    if not binding.challenge_sha256:
        return "this binding records no digest, so there is nothing to hold the value against"
    if binding.expires_at and at > binding.expires_at + CLOCK_SLACK_SECONDS:
        return ("this binding expired at %.0f and it is now %.0f. A challenge that outlives its "
                "run is one somebody kept" % (binding.expires_at, at))
    if digest_of(value) != binding.challenge_sha256:
        return ("what came back is not what this gateway issued: it hashes to "
                + digest_of(value)[:16] + "... and the binding records "
                + binding.challenge_sha256[:16] + "...")
    return ""


def bootstrap(command_was_given: bool) -> list[str]:
    """The command the sandbox runs when the client did not bring one of its own.

    It reads the challenge off the FIRST LINE of standard input and puts it in its own process
    environment, then runs the job exactly as before. Standard input rather than an argument
    because `EM3C-CROSSING-DECISION-0001`, F-A-ARGV-EXPOSURE: a value on the container runtime's
    command line is one anybody listing processes on the host can read, and this one has no
    business being visible there. Nothing about the sandbox changes; the value exists inside it
    and nowhere else.
    """
    if command_was_given:
        return []
    return ["python", "-c",
            "import base64,os,sys;"
            "os.environ[" + repr(INSIDE_THE_SANDBOX) + "]=sys.stdin.readline().strip();"
            "exec(base64.b64decode(sys.stdin.read()).decode())"]


def on_stdin(value: str, payload: str) -> str:
    """What the sandbox process reads: the challenge, then the job, and nothing else."""
    return value + "\n" + payload
