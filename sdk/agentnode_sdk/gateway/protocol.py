"""EM-3C wire protocol: what a client and a self-hosted gateway say to each other.

`EM3C-ARCHITECTURE-0001` chose **T-C + S-B**, and both halves show up here.

**S-B — the client sends the work *and the properties it requires*, and the gateway refuses the
job server-side when it cannot satisfy them.** So a job carries its requirements explicitly, and a
refusal is a normal, structured answer rather than an error. The gateway never silently downgrades
a job to something it *can* do.

**T-C — the gateway is measured, and a report is bound to an identity and a version.** Every
response the gateway sends -- hello, pairing, job submission, status, cancellation, and the
not-found answers -- carries its identity, its version and a fingerprint over both, so a client can
tie any answer to the build that produced it. A gateway that changes either invalidates what the
client knew about it. `GatewayService.stamp` is the single place that adds them, so an endpoint
added later cannot quietly omit them.

What this module is *not*: it is not transport. It defines the bytes that are signed and the rules
for accepting them. HTTP, TLS and framing live elsewhere, and TLS in particular is deliberately not
this module's business — on loopback there is nothing to encrypt, and for a real deployment the
transport is a separate decision that must not be pre-empted by baking it in here.

Three properties the wire format has to carry, because each of them is something an attacker gets
for free otherwise:

* **integrity of what runs.** The artifact is identified by its sha256, and that digest is inside
  the signed envelope. A gateway that receives a different artifact than the one the client signed
  for refuses before anything starts.
* **integrity of what is allowed.** The composed policy is hashed the same way and also signed, so
  a job cannot be replayed against a laxer policy than the one it was authorised under.
* **freshness.** Every request carries a nonce and a timestamp. The gateway remembers nonces for
  the acceptance window and refuses a repeat, so a captured request cannot be replayed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

#: Bumped when the meaning of a field changes. A gateway refuses a version it does not implement
#: rather than guessing, because guessing is how a client ends up believing a property holds.
PROTOCOL_VERSION = "em3c/2"

#: What this build used to speak. Kept so a refusal can say what changed rather than only that
#: something did. `em3c/1` carried a timeout as the exit code -1, and a client speaking it would
#: read a killed run as one that exited -- so this version is refused rather than accommodated,
#: which is the safe direction when the difference is "did this finish or was it stopped".
SUPERSEDED_VERSIONS = ("em3c/1",)

#: Why a run stopped, as a meaning rather than as a number. `EM3C-E4-CLASSIFY-0001`: a run ended
#: by its own wall clock was reported as exit code -1, which a Windows client then observed as
#: 4294967295 -- so the client could not tell a timeout from an ordinary failure, and two numbers
#: had to be treated as the same thing to make it work. A process that was killed did not exit,
#: and has no exit code; what it has is a reason.
EXITED = "exited"
TIMED_OUT = "timeout"
CANCELLED = "cancelled"

#: THE THREE ABOVE WERE ALL THERE WAS, and a run that was killed fell into `EXITED` -- because
#: `why_it_stopped` reads a backend that says nothing as an ordinary exit, and a backend returning
#: a bare `(137, out, err)` says nothing. `EXITED` plus a finished state then meant `succeeded`.
#: So a container the kernel had killed for running out of memory, or one an operator's command
#: had destroyed, was signed into the usage log as a success.
#:
#: These are the endings that were missing. Each names ONE thing:
#:
#:   out_of_memory  -- the runtime reports the container was killed for exceeding its memory
#:                     ceiling. Not inferred from an exit status: the runtime answers this
#:                     directly, and 137 alone cannot tell it from any other SIGKILL
#:   runtime_lost   -- the container runtime could not be asked what became of the container, or
#:                     answered that it no longer knows it. NOBODY ESTABLISHED what the payload
#:                     did, which is not the same as the payload failing
#:   transport_lost -- the connection carrying the run ended before an answer came back. Same
#:                     shape as the one above: unestablished, not failed
#:
#: There is deliberately NO "killed by a signal" among these. A container runtime reports an exit
#: status and, separately, whether it killed the container for memory; it does not report who
#: sent a signal. A status of 137 is 128+9 both for a container something killed AND for a
#: program that chose to exit 137, and nothing available here tells the two apart. Naming an
#: ending this code cannot establish is exactly the claim E5 exists to refuse.
OUT_OF_MEMORY = "out_of_memory"
RUNTIME_LOST = "runtime_lost"
TRANSPORT_LOST = "transport_lost"

TERMINATION_REASONS = (EXITED, TIMED_OUT, CANCELLED,
                       OUT_OF_MEMORY, RUNTIME_LOST, TRANSPORT_LOST)

#: The reasons under which nobody established what the payload did. They are NOT failures: a run
#: reported as failed when nobody knows is a false statement about a customer's job in the same
#: way a run reported as succeeded is.
NOTHING_WAS_ESTABLISHED = (RUNTIME_LOST, TRANSPORT_LOST)

#: What a run that has not stopped says about why it stopped: nothing.
#:
#: `EM3C-E8-RECORD-0001`: the field defaulted to `EXITED`, so a queued run and a running run both
#: reported a reason for stopping, and a cancelled run kept whatever the destroyed container's
#: exit had looked like. A default that names one of the things a field can mean is a claim
#: nobody made. This one names none of them.
NOT_STOPPED = ""

#: The CLI's own status when a run ended on its limit. 124 is what `timeout(1)` uses, it fits in
#: the range every platform can carry, and it is a documented constant rather than a sentinel
#: that happens to survive the trip.
TIMEOUT_EXIT_STATUS = 124


#: Every state from which a run will never move again. One list, shared, because a client that
#: does not recognise a terminal state waits for it forever -- which is how "unverified" was
#: first met: the run had ended and the client polled until it timed out.
TERMINAL_STATES = ("finished", "refused", "cancelled", "unverified", "interrupted")

#: Where a run starts, and the one place it can be before it is terminal.
QUEUED = "accepted"
RUNNING = "running"
STATES = (QUEUED, RUNNING) + TERMINAL_STATES

#: How far along a state is. `EM3C-E6-RECORD-0001` found a client showing a run as running after
#: the gateway had already cancelled it and removed its container. Nothing was stopping a state
#: from going backwards, because nothing had ever been asked to: every place that set one just
#: assigned it. A state may raise this number and may never lower it, and nothing leaves the top.
_STAGE = {QUEUED: 0, RUNNING: 1}
_STAGE.update({state: 2 for state in TERMINAL_STATES})


def stage_of(state: str) -> int:
    """How far along a state is, or a refusal. An unknown state is not quietly ranked lowest."""
    if state not in _STAGE:
        raise ProtocolError(
            f"{state!r} is not a state this build knows, and a state it cannot place is one it "
            "cannot say anything about the order of")
    return _STAGE[state]


def is_terminal(state: str) -> bool:
    return stage_of(state) == 2


def may_move(old: str, new: str) -> bool:
    """Whether a run in `old` may become `new`. Forward only, and never out of a terminal state.

    Staying put is allowed: the same state arriving twice is not a move. What is refused is going
    backwards -- and going anywhere at all once a run is terminal, because a terminal state that
    can be replaced is not one a reader can act on.
    """
    before, after = stage_of(old), stage_of(new)
    if before == 2:
        return old == new
    return after >= before


def refuse_move(old: str, new: str) -> None:
    """Raise unless this move is allowed. Fails closed: nothing is silently kept or dropped."""
    if not may_move(old, new):
        raise ProtocolError(
            f"a run in {old!r} cannot become {new!r}. A state moves forward or stays where it is, "
            "and a terminal state is where it stops; anything else means two answers about the "
            "same run disagree and a reader cannot tell which one is now")


#: What a run's end amounts to, said the way a person says it. The wire carries a state and a
#: reason; these four are what those two together mean, and they exist so the distinction is
#: answerable from the record rather than reconstructed by whoever is reading it.
SUCCEEDED = "succeeded"
CANCELLED_OUTCOME = "cancelled"
TIMED_OUT_OUTCOME = "timed_out"
FAILED = "failed"

#: THE FIFTH, and it is not a kind of failure. `unverified` was already a terminal STATE -- "the
#: gateway could not establish what happened" -- and `outcome_of` mapped it to `failed`, which
#: rounds an open question into an answer. A customer told their job failed will not resubmit it;
#: a customer told nobody could establish what it did will ask. Those are different, and the
#: record has to be able to say the second one.
UNVERIFIED_OUTCOME = "unverified"
OUTCOMES = (SUCCEEDED, CANCELLED_OUTCOME, TIMED_OUT_OUTCOME, FAILED, UNVERIFIED_OUTCOME)


def what_disagrees(state: str, termination_reason: str, exit_code, native_status,
                   native_platform: str) -> str:
    """Empty when these say one thing about how a run ended. Otherwise, what does not fit.

    One rule, in the place that defines the words, so that the gateway writing a record and the
    reader judging one cannot come to different conclusions about the same five fields.
    """
    try:
        stopped = is_terminal(state)
    except ProtocolError:
        # A state this build cannot place is refused where states are read, and that refusal is
        # not this function's to make twice. What IS this function's is the agreement between
        # these fields, and there is no agreement to judge against a state nobody can place.
        return ""
    if not stopped:
        if termination_reason != NOT_STOPPED:
            return ("this run is " + state + " and says it stopped because "
                    + str(termination_reason) + ". A run that has not stopped has no reason for "
                    "having stopped")
        if exit_code is not None:
            return ("this run is " + state + " and carries an exit status. Nothing that is still "
                    "running has exited")
        return ""
    if termination_reason not in TERMINATION_REASONS:
        return ("this run stopped for " + repr(str(termination_reason)[:24]) + ", which is not a "
                "reason this build knows, so what happened to it cannot be read")
    if state == "cancelled" and termination_reason != CANCELLED:
        return ("this run is cancelled and says it stopped because " + termination_reason
                + ". A run that was cancelled stopped because it was cancelled, whatever the "
                "runtime made of the container it was in")
    if termination_reason != EXITED and exit_code is not None:
        return ("this run is recorded as " + termination_reason + " AND as having exited "
                + repr(exit_code) + ". Nothing that was stopped chose a status, so one of the two "
                "is not what happened")
    if native_status is not None and not str(native_platform or ""):
        return ("a native status was recorded without saying which platform produced it, so the "
                "number cannot be read as anything")
    return ""


def outcome_of(state: str, termination_reason: str = NOT_STOPPED, exit_code=None) -> str:
    """The outcome of a run in this state, or "" while it still has none.

    It used to be about the RUN and not the program it carried: "a run that completed and
    delivered a result succeeded, whatever number the program returned". That reading is gone,
    and deliberately. This record exists so a customer can be told what a service did for them,
    and a line saying `succeeded` about a job that exited 3 is not something they can use. The
    exit status is now part of the signed line, so the claim can be checked against it.

    A state that is not terminal maps to no outcome at all.
    """
    if not is_terminal(state):
        return ""
    # THE ORDER IS DELIBERATE, and it is stated here because two of these can be true at once.
    #
    # 1. A cancellation outranks everything the container did afterwards. The customer asked for
    #    it to stop; whatever the runtime made of a container this gateway then destroyed is a
    #    consequence of that request and not a second, competing reason. (`EM3C-E8-RECORD-0001`
    #    is the same point, made about the exit status.)
    # 2. The operator's wall clock next: it ended the run on purpose, and the record should say
    #    that rather than that the payload was killed -- which is true but less useful.
    # 3. Then the endings where NOBODY ESTABLISHED what the payload did. These must not fall
    #    through to `failed`, which is the whole of what `UNVERIFIED_OUTCOME` exists for.
    # 4. Only then: a run whose state says finished AND whose reason says it exited. Both, not
    #    either. This is the line the old version got wrong -- it asked only about the state, so
    #    a killed container that the gateway had recorded as finished came out `succeeded`.
    # 5. Everything else is a failure, and `termination_reason` beside it says which kind.
    if state == "cancelled":
        return CANCELLED_OUTCOME
    if termination_reason == TIMED_OUT:
        return TIMED_OUT_OUTCOME
    if (state in ("unverified", "interrupted")
            or termination_reason in NOTHING_WAS_ESTABLISHED):
        # `interrupted` belongs here and not under `failed`. The gateway went away; nobody
        # established what the payload did, and the job certainly did not fail at anything it
        # was asked to do. Whether it had started at all is in the same line -- `started_at` is
        # 0.0 and `seconds` is 0.0 for one that never left the queue -- so the distinction is
        # answerable without making it a second outcome. Saying it in the REASON is
        # `interrupted-audit-record-r1`'s question (I5), not this one's.
        #
        # It used to be written as `outcome="interrupted"`, which is not one of the five at all:
        # a state's name standing in an outcome's field, so a reader branching on OUTCOMES saw a
        # value that could not occur.
        return UNVERIFIED_OUTCOME
    if state == "finished" and termination_reason in (EXITED, NOT_STOPPED):
        # 4a. AND THE PROGRAM HAS TO HAVE SAID IT WORKED. This used to end here: a run that
        #     reached the far end was `succeeded` "whatever number the program returned". For a
        #     record whose job is to say what a service did, that reads a job which exited 3 as
        #     a success, and the signed line carried no exit status for a reader to check it
        #     against. The status is now in the line, and it decides.
        # 4b. AND A MISSING STATUS IS NOT A ZERO. `exited` with nothing to show for it is the
        #     original defect in miniature -- absence read as success. Nobody established that
        #     it worked, so the line says exactly that rather than guessing either way.
        if exit_code is None:
            return UNVERIFIED_OUTCOME
        try:
            return SUCCEEDED if int(exit_code) == 0 else FAILED
        except (TypeError, ValueError):
            return UNVERIFIED_OUTCOME
    return FAILED

#: How far apart the two clocks may be before a request is refused as stale. Wide enough for an
#: ordinary skew, narrow enough that a captured request stops being useful quickly.
CLOCK_SKEW_SECONDS = 120

#: Nonces are remembered for at least the acceptance window, so a replay inside the window is
#: caught by the cache and one outside it is caught by the timestamp.
NONCE_TTL_SECONDS = CLOCK_SKEW_SECONDS * 2


class ProtocolError(Exception):
    """The message is not one this build can act on. Always refuse rather than interpret."""


def canonical_bytes(payload: dict[str, Any]) -> bytes:
    """The exact bytes that get signed.

    Sorted keys and no incidental whitespace, so two encoders of the same dict produce the same
    signature. Without this the signature would depend on how the sender happened to serialise.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def policy_digest(granted: Any) -> str:
    """A digest of a policy, over the one canonical shape both sides use.

    EM3C-DIGEST-DECISION-0001 chose B1: the requested and the effective policy are digested over
    the SAME shape, which is what makes them comparable -- a delta needs two values of one thing,
    not two different summaries.

    What it deliberately does not cover, named because a reader would otherwise assume it does:
    retention and the assurance floor. No path enforces them yet, so a digest change there would
    mean less than it appears to.
    """
    from agentnode_sdk.gateway.policy_paths import policy_shape

    return digest(canonical_bytes(policy_shape(granted)))


#: What every answer carries because the gateway stamped it, and what it carries in addition
#: when it belongs to a paired client. Named here, once, because two things need to agree about
#: it: the gateway that adds them and anything that later reads an answer back. `EM3C-E3-CLASSIFY-0001`
#: found an evidence reader that had been given its own list of an INNER object's fields while
#: the client receives this envelope, so the reader refused every real answer.
STAMP_FIELDS = ("gateway", "fingerprint", "protocol")
SIGNATURE_FIELDS = ("binding", "signature")


#: What an answer carries INSTEAD of a run when there is no run to describe. Named here for the
#: same reason as the stamp: the gateway writes it and something else reads it back, and two
#: lists of one thing drift. `EM3C-EVIDENCE-0014`: the reader had this one of its own.
ERROR_FIELDS = ("error",)


def refusal(reason: str) -> dict[str, Any]:
    """The body of an answer that has no run to describe. The only place it is built."""
    return {"error": str(reason)}


def stamp_fields(identity) -> dict[str, Any]:
    """The three fields `GatewayService.stamp` adds. The only place they are built."""
    return {"gateway": identity.as_dict(),
            "fingerprint": identity.fingerprint,
            "protocol": PROTOCOL_VERSION}


def binding_fields() -> tuple[str, ...]:
    """The keys of a response binding, from the function that builds one."""
    return tuple(response_binding(
        gateway_id="", version="", job_id="", run_id="", artifact_sha256="",
        request_policy_sha256="", effective_policy_sha256="", result=""))


#: Everything an answer says about what HAPPENED, as opposed to which job it was about. One
#: list, because the signature covers it and the reader recomputes it, and two lists of the same
#: thing drift. `EM3C-EVIDENCE-0020`: the signature covered the identifiers, both policy digests
#: and a digest of stdout -- so the state, the exit code, the reason a run stopped, the native
#: status, the cleanup and the timestamps could all be changed on the way to the client and the
#: binding still recomputed to what was signed.
OUTCOME_FIELDS = ("state", "exit_code", "termination_reason", "native_status", "native_platform",
                  "cleanup_verified", "refusal", "stdout", "stderr", "policy_deltas",
                  "started_at", "finished_at")

#: Which run an answer is about, and where what it ran printed. Named here because this is where
#: the format is defined: anything that needs to reach into an answer for one of these asks for
#: the name rather than spelling it, so a rename here breaks its callers instead of leaving them
#: quietly reading a field that no longer exists.
RUN_ID_FIELD = "run_id"
STDOUT_FIELD = "stdout"
assert STDOUT_FIELD in OUTCOME_FIELDS


def outcome_digest(body: dict[str, Any]) -> str:
    """A digest over what an answer says happened.

    Absent is not the same as empty: a field the answer does not carry is recorded as absent, so
    removing one changes the digest rather than looking like a field that was there and blank.
    """
    return digest(canonical_bytes(
        {name: body[name] if name in body else None for name in OUTCOME_FIELDS}))


def response_binding(*, gateway_id: str, version: str, job_id: str, run_id: str,
                     artifact_sha256: str, request_policy_sha256: str,
                     effective_policy_sha256: str, result: Any,
                     outcome: dict[str, Any] | None = None) -> dict[str, Any]:
    """The exact tuple a response is authenticated over.

    Everything a client needs in order to know that THIS answer belongs to THIS job on THIS
    gateway under THIS policy. Leaving any of it out would let an answer be lifted from one
    context into another -- a result from a laxer policy replayed against a stricter request, or
    a result from another gateway entirely.
    """
    return {
        "protocol": PROTOCOL_VERSION,
        "gateway_id": gateway_id,
        "version": version,
        "job_id": job_id,
        "run_id": run_id,
        "artifact_sha256": artifact_sha256,
        "request_policy_sha256": request_policy_sha256,
        "effective_policy_sha256": effective_policy_sha256,
        "result_sha256": digest(canonical_bytes({"result": result})),
        # What happened, not only which job it was. Without this, an answer's outcome is
        # unauthenticated and the binding recomputes to what was signed anyway.
        "outcome_sha256": outcome_digest(outcome or {}),
    }


def new_nonce() -> str:
    return secrets.token_hex(16)


def sign(secret: bytes, payload: dict[str, Any]) -> str:
    return hmac.new(secret, canonical_bytes(payload), hashlib.sha256).hexdigest()


def verify_signature(secret: bytes, payload: dict[str, Any], signature: str) -> bool:
    """Constant-time comparison. A timing-variable check leaks the signature one byte at a time."""
    return hmac.compare_digest(sign(secret, payload), str(signature or ""))


@dataclass(frozen=True)
class JobRequest:
    """One unit of work, and everything the gateway needs to refuse it for the right reason."""

    job_id: str
    run_id: str
    artifact_sha256: str
    policy_sha256: str
    #: What the client REQUIRES of the backend. S-B: the gateway refuses if it cannot satisfy
    #: these, rather than running the job with less.
    required_properties: tuple[str, ...] = ()
    #: Policy fields this job REQUIRES to survive composition. Narrowing one of these is refused
    #: before the container starts.
    mandatory: tuple[str, ...] = ()
    #: Policy fields the job would like but can run without. Narrowing one of these is allowed and
    #: is reported back as a delta rather than silently applied.
    optional: tuple[str, ...] = ()
    command: tuple[str, ...] = ()
    network: str = "none"
    allowed_domains: tuple[str, ...] = ()
    wall_clock_s: int = 60
    nonce: str = field(default_factory=new_nonce)
    issued_at: float = field(default_factory=time.time)

    def to_payload(self) -> dict[str, Any]:
        return {
            "protocol": PROTOCOL_VERSION,
            "job_id": self.job_id,
            "run_id": self.run_id,
            "artifact_sha256": self.artifact_sha256,
            "policy_sha256": self.policy_sha256,
            "required_properties": sorted(self.required_properties),
            "mandatory": sorted(self.mandatory),
            "optional": sorted(self.optional),
            "command": list(self.command),
            "network": self.network,
            "allowed_domains": sorted(self.allowed_domains),
            "wall_clock_s": int(self.wall_clock_s),
            "nonce": self.nonce,
            "issued_at": float(self.issued_at),
        }

    @staticmethod
    def from_payload(payload: dict[str, Any]) -> JobRequest:
        if payload.get("protocol") != PROTOCOL_VERSION:
            raise ProtocolError(
                f"this gateway speaks {PROTOCOL_VERSION}, the request says "
                f"{payload.get('protocol')!r}"
            )
        try:
            return JobRequest(
                job_id=str(payload["job_id"]),
                run_id=str(payload["run_id"]),
                artifact_sha256=str(payload["artifact_sha256"]),
                policy_sha256=str(payload["policy_sha256"]),
                required_properties=tuple(payload.get("required_properties") or ()),
                mandatory=tuple(payload.get("mandatory") or ()),
                optional=tuple(payload.get("optional") or ()),
                command=tuple(payload.get("command") or ()),
                network=str(payload.get("network", "none")),
                allowed_domains=tuple(payload.get("allowed_domains") or ()),
                wall_clock_s=int(payload.get("wall_clock_s", 60)),
                nonce=str(payload["nonce"]),
                issued_at=float(payload["issued_at"]),
            )
        except KeyError as exc:
            raise ProtocolError(f"the request is missing {exc.args[0]!r}") from exc
        except (TypeError, ValueError) as exc:
            raise ProtocolError(f"the request is malformed: {exc}") from exc


class NonceCache:
    """Remembers nonces for the acceptance window. A repeat inside it is a replay.

    Deliberately not a set that grows forever: entries expire, and a request older than the window
    is refused by its timestamp before the cache is even consulted. The two together mean a
    captured request is useless in both directions -- too old, or already seen.
    """

    def __init__(self, ttl_seconds: int = NONCE_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._seen: dict[str, float] = {}

    def _expire(self, now: float) -> None:
        cutoff = now - self._ttl
        for nonce in [n for n, t in self._seen.items() if t < cutoff]:
            del self._seen[nonce]

    def check_and_remember(self, nonce: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self._expire(now)
        if nonce in self._seen:
            raise ProtocolError("this request has already been used (replay)")
        self._seen[nonce] = now

    def __len__(self) -> int:                                    # pragma: no cover - diagnostics
        return len(self._seen)


def check_freshness(issued_at: float, now: float | None = None,
                    skew: int = CLOCK_SKEW_SECONDS) -> None:
    """Refuse a request that is too old, and one dated too far in the future.

    The future case matters: without it, a captured request could be given a timestamp far ahead
    and stay valid indefinitely.
    """
    now = time.time() if now is None else now
    delta = now - float(issued_at)
    if delta > skew:
        raise ProtocolError(f"this request is {int(delta)}s old; the limit is {skew}s")
    if delta < -skew:
        raise ProtocolError(
            f"this request is dated {int(-delta)}s in the future; the two clocks disagree"
        )
