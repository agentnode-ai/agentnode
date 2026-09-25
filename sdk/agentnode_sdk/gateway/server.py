"""The gateway itself: it accepts a job, refuses it, or runs it in a container it owns.

This is where **S-B** stops being a diagram. The client says what it requires; this decides, on the
server, whether those requirements can be met, and refuses when they cannot. It never runs a job
with less than was asked for and reports success.

It is also where an **operator policy** exists for the first time. The person running this server
is not the person sending the work, and the fold reflects that:

    granted = merge_policies({
        Scope.ORGANISATION: what the server operator allows,   # highest -- the machine's owner
        Scope.USER:         what the submitting client allows,
        Scope.PACKAGE:      what the job asks for,             # lowest -- untrusted
    })

`merge_policies` already guarantees a lower scope can only narrow, so a client cannot talk the
gateway into more than its operator permitted, and a job cannot talk the client into more than it
asked for. That property is not re-implemented here; it is used.

Transport is deliberately plain HTTP from the standard library. On loopback there is nothing to
encrypt, and choosing a TLS story for a real deployment is a decision this module must not
pre-empt by baking one in. Every request is signed regardless of transport, so the authenticity of
a job does not depend on the channel.
"""
from __future__ import annotations

import base64
import hmac
import urllib.parse
import json
import os
import pathlib
import secrets
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from agentnode_sdk.worker import what_it_does_not_establish
from agentnode_sdk.access import rest as _rest
from agentnode_sdk.access import sessions as _sessions
from agentnode_sdk.access.enrolment import Connections
from agentnode_sdk.access.sessions import Sessions
from agentnode_sdk.access.stopping import Stopping
from agentnode_sdk.access import dispatch as _dispatch
from agentnode_sdk.gateway import client as _gc
from agentnode_sdk.gateway.identity import GatewayState, PairingError
from agentnode_sdk.gateway import health
from agentnode_sdk.gateway import challenge as ch
from agentnode_sdk.gateway.ledger import Ledger
from agentnode_sdk.gateway.runs import Runs
from agentnode_sdk.gateway.readiness import (
    Readiness,
    ReadinessGate,
    ReportBinding,
    describe_missing,
)
from agentnode_sdk.gateway.transport import TlsFiles, check_bind_address
from agentnode_sdk.gateway.allowance import OverTheCeiling, Stopped, why_it_is_stopped
from agentnode_sdk.worker import CouldNotRestrictTheNetwork, JobFailed, Job as WorkerJob
from agentnode_sdk.worker import Limits as WorkerLimits
from agentnode_sdk.worker import WorkerUnreachable
from agentnode_sdk.gateway.protocol import (
    refusal,
    PROTOCOL_VERSION,
    JobRequest,
    NonceCache,
    ProtocolError,
    canonical_bytes,
    check_freshness,
    digest,
    response_binding,
    sign,
    verify_signature,
)


#: The one command that closes every refusal below. A gate that names no way through is a
#: wall, so the remediation is a command that really runs and really changes the answer.
_MEASURE = "agentnode gateway doctor --measure"

MAX_BODY_BYTES = 32 * 1024 * 1024


@dataclass
class RunRecord:
    """One run, and enough about it to answer the same question twice with the same answer."""

    run_id: str
    job_id: str
    request_sha256: str = ""     # the signed request this run belongs to
    requested_policy: dict = field(default_factory=dict)
    effective_policy: dict = field(default_factory=dict)
    request_policy_sha256: str = ""
    effective_policy_sha256: str = ""
    deltas: list = field(default_factory=list)
    artifact_sha256: str = ""
    #: What the job said it required. Kept so the end of the run can check that what it
    #: asked for actually held, rather than only that the job finished.
    required_properties: tuple = ()
    #: Which paired client this run belongs to. A run is readable and cancellable by its
    #: owner and by nobody else. This is the client's identity, NOT its token: rotating a
    #: credential must not orphan the runs the client already submitted.
    owner_client_id: str = ""
    #: Which CUSTOMER this run belongs to. Not derivable from the owning device later: a device
    #: can be withdrawn, and a run that outlived its device would then have no account at all --
    #: which is exactly when somebody is trying to find out what happened.
    owner_account_id: str = ""
    binding: dict = field(default_factory=dict)
    signature: str = ""
    state: str = "accepted"          # accepted | running | finished | refused | cancelled
    exit_code: int | None = None
    #: WHY it stopped, not what number came back. `EM3C-E4-CLASSIFY-0001`: a run ended by its own
    #: wall clock was reported as exit code -1, a Windows client read 4294967295, and the two had
    #: to be called equal for anything to work. A killed process has no exit code; it has a
    #: reason. The runtime's own number is kept beside it, with the platform it belongs to.
    #:
    #: `EM3C-E8-RECORD-0001`: this defaulted to `"exited"`, so a run that had not stopped said why
    #: it had stopped, and a cancelled run kept whatever the destroyed container's exit looked
    #: like. Nothing has to set it for it to be right now, because empty claims nothing.
    termination_reason: str = ""
    #: Set when the operator's kill switch is what ended this run, and carried into the answer so
    #: a client is told that rather than being left to read a bare "cancelled" as its own doing.
    halted_by: str = ""
    #: Where this run was measured and what the thing measuring it was configured as, taken at
    #: admission like everything else that must not change underneath a finished run. The
    #: conformance report has bound these for a while; a JOB's own evidence did not, so a reader
    #: holding one run's record could not tell which arrangement produced it without going to
    #: find a separate document and hoping it was the same one.
    worker_topology: str = ""
    worker_configuration_sha256: str = ""
    backend_version: str = ""
    #: The ceilings this run was ADMITTED under, taken at admission. Sampling it at the end would
    #: mean a limit changed while a run was going rewrote what that run is recorded as having been
    #: allowed -- which is the one thing a record of what was allowed must not do.
    admitted_under: str = ""
    #: The ceilings themselves, not only their digest. A digest binds a record
    #: to a configuration; it cannot be turned back into the numbers, and the
    #: file it stood for is exactly the thing that gets edited afterwards.
    admitted_under_values: dict = field(default_factory=dict)
    native_status: int | None = None
    native_platform: str = ""
    stdout: str = ""
    stderr: str = ""
    refusal: str = ""
    #: WHICH refusal, by the contract's name, when this record is one. A record carrying only
    #: prose meant the contract door answered "it is refused, here is a sentence" -- so a client
    #: that branches on the closed list of refusals could not tell "over a ceiling" from
    #: "malformed" without reading English, which is exactly what the closed list exists to
    #: avoid. Empty means this record is not a refusal.
    refused_as: str = ""
    #: What the refused party can actually do. Empty for a record that is not a refusal.
    refusal_remedy: str = ""
    #: WHICH situation, inside a refusal name that covers more than one. See `Refused.cause` in
    #: `access/dispatch.py`. Empty when the name already says everything.
    refusal_cause: str = ""
    container_name: str = ""
    cleanup_verified: bool | None = None
    #: WHEN THIS JOB ARRIVED. Not when it started: a job may wait for a slot, and the wait is
    #: this gateway's doing rather than the customer's, so it is kept apart from anything that
    #: is charged for.
    queued_at: float = field(default_factory=time.time)
    #: WHEN THE BILLED CLOCK STARTED, which is the moment a worker slot was actually held.
    #:
    #: **0.0 means it never started**, and that is load-bearing rather than a default: a job
    #: cancelled while it waited must be billed nothing, and the way that is guaranteed is that
    #: there is no start time to subtract from. This used to be `default_factory=time.time`,
    #: set when the record was CONSTRUCTED -- so the moment a queue existed in front of the
    #: worker, every second of waiting would have been billed as execution.
    started_at: float = 0.0
    finished_at: float | None = None
    cancel_requested: threading.Event = field(default_factory=threading.Event)
    #: The place in the queue, while this run has one. None once it holds a slot, and None for
    #: every run on a gateway with no machine ceiling. Not in `public`: it is an object, and what
    #: a caller may know about waiting is `waiting_for_a_slot` and their own `queued_at`.
    slot_ticket: object | None = None
    #: The value this gateway issued for this run, while the run is alive. It is NOT in `public`,
    #: it is not in the ledger, and it is dropped when the run reaches a terminal state -- what
    #: survives is its digest, in the binding the ledger holds.
    challenge: str = ""

    def move_to(self, new_state: str) -> None:
        """Change this run's state, or refuse to.

        `EM3C-E6-RECORD-0001`: a client was shown a run as running after the gateway had cancelled
        it and removed its container. Nothing had ever stopped a state going backwards, because
        every place that set one simply assigned the field. There is one place now, and it fails
        closed: a move that is not forward raises rather than being ignored, so a caller that
        would have written the wrong state finds out instead of the reader finding out later.
        """
        from agentnode_sdk.gateway.protocol import refuse_move

        refuse_move(self.state, new_state)
        self.state = new_state

    @property
    def outcome(self) -> str:
        """What this run's end amounts to, or "" while it has none."""
        from agentnode_sdk.gateway.protocol import outcome_of

        return outcome_of(self.state, self.termination_reason, self.exit_code)

    def public(self) -> dict[str, Any]:
        """What a client may see. No secrets, and no fields that only mean something inside."""
        return {
            "run_id": self.run_id,
            "job_id": self.job_id,
            "state": self.state,
            "exit_code": self.exit_code,
            "termination_reason": self.termination_reason,
            "halted_by": self.halted_by,
            "worker_topology": self.worker_topology,
            "worker_topology_means": what_it_does_not_establish(self.worker_topology),
            "worker_configuration_sha256": self.worker_configuration_sha256,
            "backend_version": self.backend_version,
            "native_status": self.native_status,
            "native_platform": self.native_platform,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "refusal": self.refusal,
            "artifact_sha256": self.artifact_sha256,
            "cleanup_verified": self.cleanup_verified,
            # Invariant 5: a narrowing may run, but the answer has to SAY what changed.
            "requested_policy": self.requested_policy,
            "effective_policy": self.effective_policy,
            "request_policy_sha256": self.request_policy_sha256,
            "effective_policy_sha256": self.effective_policy_sha256,
            "policy_deltas": self.deltas,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            # What the caller may know about its OWN wait. Not a position and not a count:
            # a position is by construction a tally of other people's jobs.
            "queued_at": self.queued_at,
            "waiting_for_a_slot": bool(self.state == "accepted" and not self.started_at),
        }


#: How long recovery waits for an interrupted run's container to be listed. A container that was
#: going to appear did so before the restart, so this is one or two listings rather than the
#: window a live cancel needs against a runtime that is still creating one.
RECOVERY_APPEAR_SECONDS = 1.0

#: A ceiling on the whole recovery sweep. Starting up must not be held open by a runtime that is
#: slow to answer about every run in a long ledger. Runs not reached keep `cleanup_verified` at
#: None, which reads as "nobody could ask" rather than "nothing was left behind".
RECOVERY_BUDGET_SECONDS = 30.0


#: The first release whose client calls prepare and carries back what a person agreed to.
#: Named in the refusal an older client gets, so "update" is an instruction rather than advice.
MIGRATED_CLIENT = "0.25.0"


def name_the_refusal(exc: Exception) -> tuple:
    """What to call a refusal raised during admission, and what to tell the refused party.

    One classifier, so the name a record carries and the name a door renders are the same name.
    Two copies of this would eventually disagree, and the door's copy would be the one a client
    saw.
    """
    from agentnode_sdk.gateway import admission
    from agentnode_sdk.gateway.accounts import AccountsUnreadable
    from agentnode_sdk.gateway.allowance import (
        CannotReadTheCeilings,
        CannotReadWhatWasUsed,
        OverTheCeiling,
        Stopped,
    )
    from agentnode_sdk.gateway.protocol import ProtocolError

    if isinstance(exc, admission.NotAdmitted):
        return exc.refusal, exc.what_to_do
    if isinstance(exc, Stopped):
        return "gateway_stopped", (
            "Nothing will run until whoever runs it starts it again. You can still ask what "
            "happened to runs you already submitted.")
    if isinstance(exc, OverTheCeiling):
        return "over_a_ceiling", (
            "Wait until the window clears, or ask whoever runs this sandbox for a higher "
            "ceiling. Nothing was started and nothing was counted.")
    if isinstance(exc, (CannotReadTheCeilings, CannotReadWhatWasUsed, AccountsUnreadable)):
        return "gateway_stopped", (
            "Ask whoever runs this sandbox to look at its state directory. It is refusing work "
            "rather than applying a limit it cannot read.")
    if isinstance(exc, ProtocolError):
        return "malformed", "Correct the request and send it again."
    return "sandbox_unavailable", (
        "Try again; if it keeps happening, tell whoever runs this sandbox.")


def container_name_for(run_id: str) -> str:
    """The name this gateway gives a run's container.

    Derived from the run id and nothing else, so a gateway that has lost its memory can still
    name what it started. The admission path and the recovery path both call THIS: a recovery
    that spelled the name even slightly differently would address nothing, find nothing, and
    report that as nothing having been left behind.
    """
    return "agentnode-em3c-" + str(run_id)[:16]


class GatewayService:
    """The decisions. Kept apart from HTTP so they can be tested without a socket."""

    def sign_answer(self, body: dict[str, Any], token: str) -> dict[str, Any]:
        """Authenticate an answer with the paired client's own secret (C1).

        EM3C-DIGEST-DECISION-0001 chose the per-token HMAC: both sides already derive it at
        pairing, so no new key material is introduced. Its limit is named rather than left to be
        discovered -- a result authenticated this way is verifiable by the paired client and by
        nobody else, so it is not evidence a third party can check.

        The binding covers the whole tuple: gateway identity and version, protocol, job and run
        id, artifact digest, both policy digests, the result, and a digest over everything the
        answer says HAPPENED -- state, exit code, why it stopped, the native status and its
        platform, cleanup, refusal, both streams, the narrowing and the timestamps.
        `EM3C-EVIDENCE-0020` found that last part missing: an answer's outcome could be changed
        on the way to the client and the binding still recomputed to what had been signed. An answer lifted out of
        its context fails verification because the context is what is signed.
        """
        secret = self.state.token_secret(token)
        if secret is None:
            return body
        identity = self.state.identity
        binding = response_binding(
            gateway_id=identity.gateway_id, version=identity.version,
            job_id=body.get("job_id", ""), run_id=body.get("run_id", ""),
            artifact_sha256=body.get("artifact_sha256", ""),
            request_policy_sha256=body.get("request_policy_sha256", ""),
            effective_policy_sha256=body.get("effective_policy_sha256", ""),
            result=body.get("stdout", ""),
            outcome=body,
        )
        return {**body, "binding": binding, "signature": sign(secret, binding)}

    def stamp(self, body: dict[str, Any]) -> dict[str, Any]:
        """Bind an answer to the gateway that produced it.

        T-C only means something if a client can tell WHICH build answered. An earlier version
        stamped only /v1/hello and /v1/pair while the protocol claimed every response carried it,
        so a job result could not be tied to the gateway that produced it -- the review was right
        that the claim was broader than the code. Every answer carries it now.
        """
        # Built by `protocol.stamp_fields`, which is also what anything reading an answer back
        # is told an answer carries. Two lists of the same thing drift; one does not.
        from agentnode_sdk.gateway.protocol import stamp_fields

        return {**body, **stamp_fields(self.state.identity)}

    def __init__(self, state: GatewayState, backend=None, operator_policy=None,
                 worker=None, recover: bool = True) -> None:
        self.state = state
        self._backend = backend
        #: What runs foreign code. `ALPHA-BOUNDARY-0001`: this is the only way anything here
        #: reaches a container runtime, so that moving the worker to another machine is a
        #: deployment change and not a rewrite. A caller that supplies a `backend` gets a worker
        #: on this machine wrapped around it, which is what every caller did before there was a
        #: word for it.
        self._worker = worker
        self._operator_policy = operator_policy
        self.nonces = NonceCache()
        # Every run, AND the same runs indexed by who owns them. A customer's path resolves a
        # run inside its own namespace, so a foreign identifier is not found and refused -- it
        # is not found. See `gateway/runs.py` and EXISTENCE-ISOLATION-DECISION-0001.
        self.runs: Runs = Runs()
        # What must survive this process. In-memory replay protection has a documented way
        # around it: restart the gateway, which on a server happens on its own.
        self.ledger = Ledger(self.state.root / "ledger.json")
        #: WHAT THIS MACHINE RUNS AT ONCE, and who waits for it. Built from the operator's own
        #: ceilings, so the number is theirs and appears in no source file here. Rebuilt on
        #: demand by `slots` below when those ceilings change, because an operator who lowers the
        #: ceiling should not have to restart the gateway to be obeyed.
        self._slots = None
        self._slots_for = None
        self._slots_lock = threading.Lock()
        #: Which gateway process, and which sandbox behind it. A restart is a different instance,
        #: and a challenge says which one issued it.
        self.instance = "%s:%s" % (self.worker.instance_label(), secrets.token_hex(8))
        #: The run threads this service has started and not yet seen finish. Held so that close()
        #: can wait for them: a thread nobody is keeping is a thread nobody can give back.
        self._running: set = set()
        self._running_lock = threading.Lock()
        #: Run threads that were still alive when close() stopped waiting. Set here as well so
        #: that reading it before close() says "nothing was left behind" rather than raising.
        self.left_running: list = []
        # A gateway does not start on a contract that does not describe itself. Checked here
        # as well as in the generators, so a build where somebody half-declared an operation
        # fails at the start rather than at the first request for a schema.
        from agentnode_sdk.access import contract as _contract

        _contract.check_classifications()
        self.readiness = ReadinessGate(self.state.root)
        #: Whether the worker is there NOW, as opposed to what was once measured about it.
        #: `gateway/health.py` says why those are different questions and why the difference
        #: needed an object of its own. Built for every gateway and STARTED only by the service
        #: that serves requests: a gateway built in a test, or one around an in-process worker,
        #: has no worker that can be lost independently of itself, so it stays in `starting`,
        #: which is what those did before this existed.
        self.health = health.HealthWatch(
            reach=lambda budget: self.worker.confirm_reachable(budget),
            # `measure` and not `_transact` directly, so the watch takes the same lock, writes
            # the same activation and is judged by the same gate as an operator's measurement.
            # `.ready` rather than the verdict object: the watch decides one thing, and
            # interpreting a verdict is not its job.
            measure=lambda: self.measure().ready,
            say=lambda line: print("gateway: " + line, flush=True),
            # The operator-visible copy. `gateway status` and `gateway watch` run in a different
            # process from the gateway and cannot see the object above, so without this they
            # would have to open their own connection to a worker that may be running foreign
            # code -- which is how the present command turns an unreachable worker into a
            # command that errors, indistinguishable from a broken command.
            publish_to=self.state.root / health.HEALTH_FILE)
        #: Who carries out cancellations. Bounded, owned, and durable across a restart -- see
        #: `access/stopping.py`. Nothing is started until the first cancellation is asked for,
        #: so a gateway that never cancels anything has no threads for it.
        #: The browser sessions this gateway has open. A browser is never given a token; it
        #: is given one of these, in a cookie its own scripts cannot read.
        self.sessions = Sessions(self.state.root)
        #: Connections being set up, and the challenge each has to answer before this gateway
        #: will call it compatible.
        self.connections = Connections(self.state.root)
        #: Invitations into an account that already exists -- "add my other laptop". Separate
        #: from the operator's single live pairing code, which creates a NEW customer; see
        #: `gateway/joining.py` for why one slot is right there and wrong here.
        from agentnode_sdk.gateway.joining import Joining

        self.joining = Joining(self.state.root)
        self.stopping = Stopping(self.state.root, self._stop_it_and_confirm)
        # RECOVERY IS WHAT A GATEWAY DOES WHEN IT TAKES OVER A DIRECTORY, not what happens
        # whenever this object is constructed -- and the difference is not academic.
        #
        # Eleven operator commands build one of these to reach a method or, in four cases, only
        # to get at the state beside it. Each of those was therefore running crash recovery
        # against a directory a LIVE gateway was serving from: marking its running jobs as
        # interrupted, asking the worker to remove their containers -- which kills them -- and
        # then writing a signed usage line saying the job was interrupted.
        #
        # Measured, not reasoned about: `agentnode gateway accounts`, which only lists
        # customers, took 10.6 s and ended a job that had been running for 8. The client got
        # -9. This is what was behind `EARLY-ENDING-HOLDERS.md` -- holders dying at ~11 s
        # whenever an exercise ran an operator command, and surviving whenever it did not.
        #
        # So callers that are not starting a gateway pass `recover=False`. The default stays
        # True because every other construction site -- the serving path, and tests that mean
        # to simulate a restart -- is one where recovering IS the right thing, and a default
        # that quietly stopped recovering would lose interrupted runs instead of killing live
        # ones. Which of the two defaults is right in the long run belongs to
        # `early-ending-success-r1`; this is the part that must not wait for it.
        # WHY the runs it is about to find were interrupted, read BEFORE anything is written.
        #
        # The answer is in a file the previous gateway left: it says whether a shutdown was ever
        # begun. Read first and overwritten after, because overwriting it first would erase the
        # only evidence of what happened to the process that wrote it.
        from agentnode_sdk.gateway import lifecycle as _lifecycle

        self._interrupted_by = _lifecycle.why_a_run_was_interrupted(self.state.root)
        # The closing lines this gateway could not write, and what says it is going away.
        self._owed: dict = {}
        self._owed_lock = threading.Lock()
        self._owed_thread = None
        self._closing = threading.Event()
        if recover:
            self._restore_interrupted()
            # And now this process is the one serving. After recovery, so that a gateway which
            # dies during recovery is still read as having been lost rather than as having
            # served and stopped.
            _lifecycle.say_it_is_serving(self.state.root)
        # A container being torn down does not disappear because the process did. Anything the
        # journal still remembers is picked up here, on the way back up.
        self.stopping.pick_up_where_it_left_off()
        self._lock = threading.Lock()

    def _how_it_was_interrupted(self) -> str:
        """The reason for a run this gateway found mid-flight when it took the directory over."""
        return getattr(self, "_interrupted_by", "") or ""

    # ------------------------------------------------------------------ backend

    @property
    def backend(self):
        """The sandbox backend, for a caller that is building a worker around it.

        Nothing in this class may use it. Every question about a runtime goes through `worker`,
        because a gateway that could ask one directly would be a gateway that has to be where it
        is -- which is the thing `ALPHA-BOUNDARY-0001` decided against.
        """
        if self._backend is None:
            from agentnode_sdk.sandbox.container_backend import ContainerBackend

            self._backend = ContainerBackend()
        return self._backend

    @property
    def worker(self):
        """Whatever runs foreign code. Here for now; elsewhere later, without this class caring.

        Where it is comes from the gateway's configuration and from nowhere else: `worker_address`
        and `worker_key`, which are a deployment's business. With neither, the worker is in this
        process -- which is what every caller had before there was a word for it, and what a test
        with a stand-in backend still wants.

        `ALPHA-BOUNDARY-0001`: this property is the ONLY place in the control plane that names
        where the worker is. Nothing else in the product names a socket, a path, an account or a
        host.

        That is not the same as saying the worker can be moved, and this docstring used to say it
        was. The address this reads is handed to `from_address`, which speaks unix sockets and
        mutual TLS on loopback (`worker/tls.py`) and refuses everything else. A `tcps://` address
        takes its certificate settings from `worker_tls` in the same configuration; an address
        of that kind without them is refused there, not reached some other way. A worker on
        another machine is therefore still a change to the product, not to a deployment: the
        transport refuses every address that is not loopback, in code.
        """
        if self._worker is None:
            address = str(self.config.get("worker_address") or "")
            if address:
                from agentnode_sdk.worker import protocol as wire
                from agentnode_sdk.worker.remote import from_address

                self._worker = from_address(
                    address, wire.read_key(str(self.config.get("worker_key") or "")),
                    tls=self._worker_tls())
            else:
                from agentnode_sdk.worker.local import LocalWorker

                self._worker = LocalWorker(self.backend)
        return self._worker

    def _who_ran(self, run_id: str) -> tuple[str, str, str]:
        """(transport, identity, worker id) for the line about this run.

        Over mutual TLS all three come from the connection that carried the run, checked in its
        handshake -- never from what the worker said about itself. If no such connection was made
        by this process the line says UNATTRIBUTED rather than naming anybody. Over the socket
        the id is the worker's label, read exactly as it always was.
        """
        from agentnode_sdk.gateway import meter

        transport, identity, worker_id = self.worker.who_ran(run_id)
        if transport == "mtls" and not identity:
            identity = meter.UNATTRIBUTED
        return transport, identity, worker_id or meter.UNATTRIBUTED

    def _worker_tls(self):
        """The certificate settings for a `tcps://` worker, or None when none are configured.

        `worker_tls` in the configuration: this gateway's own certificate and key, the one trust
        anchor, the deployment, and the worker instance(s) it accepts. All or nothing -- a
        partial set is refused rather than completed with a default, because a default here would
        be a check nobody decided on.
        """
        said = self.config.get("worker_tls")
        if not said:
            return None
        from agentnode_sdk.worker.tls import (DEFAULT_REEVALUATE_SECONDS, DEFAULT_RELOAD_SECONDS,
                                              TlsSettings)

        # From stage 5 the revocation list and the floor belong to the whole: a TLS side without
        # them could not tell a revoked worker from a valid one, or a clock set back from the
        # right time. The two intervals have defaults, and their sum is the promised delay.
        needed = ("certificate", "key", "anchor", "deployment", "accept", "revocation_list",
                  "floor")
        missing = [k for k in needed if not said.get(k)]
        if missing:
            raise ValueError("worker_tls is missing %s; it is used whole or not at all"
                             % ", ".join(missing))
        return TlsSettings(certificate=str(said["certificate"]), key=str(said["key"]),
                           anchor=str(said["anchor"]), deployment=str(said["deployment"]),
                           accept=frozenset(str(a) for a in said["accept"]),
                           revocation_list=str(said["revocation_list"]),
                           floor=str(said["floor"]),
                           reload_seconds=float(said.get("reload_seconds")
                                                or DEFAULT_RELOAD_SECONDS),
                           reevaluate_seconds=float(said.get("reevaluate_seconds")
                                                    or DEFAULT_REEVALUATE_SECONDS))

    @property
    def config(self) -> dict:
        """What this gateway was configured with, read from its own directory.

        Read rather than held: `agentnode gateway start` and everything else are separate
        processes, and a configuration held from construction would be the one that was there when
        whichever process happened to start first came up.
        """
        import json as _json

        path = self.state.root / "config.json"
        try:
            loaded = _json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def active_state(self):
        """The authenticated policy-and-report pair currently in force, or None.

        Read on every admission rather than held from construction (`EM3C-Y6-DECISION-0001`,
        `D4`): a gateway that cached the policy it started with would keep admitting jobs under
        it after the operator changed it, which is the same class of staleness the digest exists
        to catch, just moved into memory.

        A snapshot that fails any of its checks raises, and the caller treats that as not ready.
        It is never downgraded to "no policy", because "no policy" is a *valid* closed state and
        a tampered one must not be able to impersonate it.
        """
        from agentnode_sdk.gateway.activation import ActivationStore

        return ActivationStore(self.state.root).load_active()

    def configured_envelope(self):
        """What the operator has ASKED for, read from the config file.

        Deliberately not the same thing as what is in force. The config file is an input; the
        authenticated snapshot is the decision. Editing the file by hand therefore changes what
        this returns and does NOT change what runs -- it makes the two disagree, and a
        disagreement is refused rather than resolved in the file's favour.
        """
        from agentnode_sdk.gateway import operator_policy as opol

        if self._operator_policy is not None:
            return self._explicit_envelope()
        path = self.state.root / "config.json"
        if not path.is_file():
            return opol.build(opol.NONE)
        try:
            config = opol.loads_strict(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise opol.OperatorPolicyError(f"the gateway config cannot be read: {exc}") from None
        return opol.from_config(config)

    def _explicit_envelope(self):
        """A policy handed in at construction, described in the same envelope as a configured one."""
        from agentnode_sdk.gateway import operator_policy as opol

        net = getattr(self._operator_policy, "network", None)
        enabled = bool(getattr(net, "enabled", False))
        dests = getattr(net, "allowed_destinations", None)
        if not enabled:
            return opol.build(opol.NONE)
        if dests is None:
            return opol.build(opol.UNRESTRICTED)
        return opol.build(opol.RESTRICTED, tuple(dests))

    def operator_envelope(self):
        """The operator policy actually IN FORCE -- from the authenticated snapshot."""
        from agentnode_sdk.gateway import operator_policy as opol

        if self._operator_policy is not None:
            return self._explicit_envelope()
        state = self.active_state()
        if state is None:
            return opol.build(opol.NONE)
        return state.policy

    def operator_policy(self):
        """What this machine's owner allows. The highest scope in the fold."""
        if self._operator_policy is not None:
            return self._operator_policy
        from agentnode_sdk.sandbox.contract import NetworkRules, SandboxPolicy
        from agentnode_sdk.gateway import operator_policy as opol

        envelope = self.operator_envelope()
        if envelope.mode == opol.NONE:
            # A gateway defaults to no network for foreign code. The operator opens it
            # deliberately, and only after the opening has been measured.
            return SandboxPolicy(network=NetworkRules(enabled=False,
                                                      allowed_destinations=frozenset()))
        if envelope.mode == opol.UNRESTRICTED:
            return SandboxPolicy(network=NetworkRules(enabled=True, allowed_destinations=None))
        return SandboxPolicy(network=NetworkRules(
            enabled=True, allowed_destinations=frozenset(envelope.allowed_destinations)))

    # ------------------------------------------------------------------ capabilities

    def report_binding(self, policy_digest: str = "") -> ReportBinding:
        """What a conformance report about this gateway would have to be about.

        `policy_digest` is supplied while a *pending* policy is being measured, so the report is
        stamped with the policy it was taken for rather than with the one still in force.
        """
        from agentnode_sdk.gateway.boot import boot_identity

        from agentnode_sdk.conformance.report import SUITE_VERSION

        from agentnode_sdk.gateway import policy_version as _versions

        identity = self.state.identity
        isolation = self.worker.can_it_isolate()
        boot_value, _method = boot_identity()
        # The ordinal for whichever policy this report is ABOUT -- the pending one while it is
        # being measured, the one in force otherwise. Assigned here rather than read, because
        # measuring a policy is exactly the moment it becomes one of this gateway's policies.
        wanted = policy_digest or ""
        if not wanted:
            try:
                wanted = self.operator_envelope().digest()
            except Exception:                                 # noqa: BLE001
                wanted = ""
        try:
            ordinal = _versions.version_for(self.state.root, wanted) if wanted else 0
        except OSError:
            # An ordering this gateway cannot read leaves the field EMPTY. A version that was
            # guessed would make a report look checked against something nobody checked.
            ordinal = 0
        return ReportBinding(
            operator_policy_version=str(ordinal) if ordinal > 0 else "",
            gateway_id=identity.gateway_id,
            gateway_version=identity.version,
            backend=isolation.backend if isolation.backend != "none" else "",
            image_digest=self.worker.image_digest(),
            boot_id=boot_value,
            backend_version=self.runtime_version(),
            conformance_schema=str(SUITE_VERSION),
            operator_policy_digest=policy_digest or self.operator_envelope().digest(),
            # Where this was measured, and what the worker was configured as when it was.
            # `ALPHA-BOUNDARY-0001`: a report that does not say which of those two it describes is
            # a report somebody will read as describing the other.
            worker_topology=self.worker.topology,
            worker_configuration_sha256=self.worker.configuration_sha256(),
            # WHAT THIS GATEWAY IS RUNNING AS. Read from the running process and from the pin
            # written by the deployment, never from a constant in the source: a field that says
            # what somebody intended rather than what is true is a field that keeps saying it
            # after the intention stops matching.
            **self._what_this_is_running_as(),
        )

    def _what_this_is_running_as(self) -> dict:
        """The interpreter, artefact, commit and build identity, as facts about this process.

        Empty strings where this gateway genuinely cannot tell -- an installation made before
        the pin existed has no commit to report, and inventing one would be worse than the gap.
        """
        from agentnode_sdk.gateway import runtime_pin

        said = {"python_version": runtime_pin.running_python(),
                "artefact_sha256": runtime_pin.installed_artefact_digest(),
                "commit": "", "build_id": ""}
        try:
            pinned = runtime_pin.read_pin(runtime_pin.pin_dir())
        except Exception:                                     # noqa: BLE001
            return said
        said["commit"] = str(pinned.get("commit") or "")
        said["artefact_sha256"] = said["artefact_sha256"] or str(
            pinned.get("artefact_sha256") or "")
        said["build_id"] = str(pinned.get("build_id") or "")
        return said

    def runtime_version(self) -> str:
        """The container runtime's own version, asked once per process.

        Process-lifetime rather than per-admission, and deliberately the same lifetime as
        `check_available`, which this gateway has always cached the same way: asking a runtime
        for its version is a subprocess, and doing that on every job would be a real cost for a
        value that changes when a daemon is upgraded -- which restarts the daemon and, in
        practice, the gateway with it. A runtime upgraded underneath a still-running gateway is
        caught at its next start or measurement, not mid-process. That is a stated limit, not an
        assumption that it cannot happen.
        """
        return self.worker.runtime_version()

    def measure(self, options=None, now: float | None = None):
        """Re-measure the policy currently configured, and put it in force if it holds.

        This is what closes the loop. A gate that can refuse but offers no way through is not a
        gate, it is a wall -- and the remediation the refusal names has to be a command that
        really runs and really changes the answer.
        """
        return self._transact(None, options=options, now=now)

    def activate(self, proposed, options=None, now: float | None = None):
        """Propose a policy, measure THAT policy, and put it in force only if it holds.

        `EM3C-FINAL-0001` found the earlier arrangement writing the proposal to the config file
        before the lock was held and restoring it after the lock was released, with the envelope
        captured earlier still. Two operators changing the policy at once could therefore measure
        one proposal and activate another, or restore a config belonging to the other command.
        Everything that reads or writes the operator's intent now happens inside one lock, and
        the policy that is measured is the object passed in rather than whatever the file says by
        the time the measurement starts.
        """
        return self._transact(proposed, options=options, now=now)

    def _config_path(self):
        return self.state.root / "config.json"

    def _write_config_for(self, envelope) -> None:
        """Record the operator's intent, keeping every setting that is not about egress."""
        from agentnode_sdk.gateway import operator_policy as opol

        path = self._config_path()
        config = {}
        if path.is_file():
            try:
                config = opol.loads_strict(path.read_text(encoding="utf-8"))
            except (OSError, opol.OperatorPolicyError):
                config = {}
        if envelope.mode == opol.RESTRICTED:
            config["egress_allowed"] = list(envelope.allowed_destinations)
        else:
            config.pop("egress_allowed", None)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config, indent=2, sort_keys=True), encoding="utf-8")

    def _restore_config(self, previous: str | None) -> None:
        path = self._config_path()
        if previous is None:
            if path.is_file():
                path.unlink()
        else:
            path.write_text(previous, encoding="utf-8")

    def _transact(self, proposed, options=None, now: float | None = None):
        """One lock around the whole change: intent, measurement, and what becomes of both."""
        from datetime import datetime, timezone

        from agentnode_sdk.conformance.runner import run_conformance
        from agentnode_sdk.gateway.activation import ActivationLock, ActivationStore

        store = ActivationStore(self.state.root)
        stamp = datetime.fromtimestamp(now or time.time(), tz=timezone.utc).isoformat()
        path = self._config_path()

        with ActivationLock(self.state.root):
            previous = path.read_text(encoding="utf-8") if path.is_file() else None
            try:
                if proposed is not None:
                    self._write_config_for(proposed)
                    envelope = proposed
                else:
                    envelope = self.configured_envelope()

                # Written first, and never consulted by admission. Its only job is to say what
                # was being measured if the process dies before it finishes.
                store.write_pending(envelope)

                report = self.worker.measure(
                    generated_at=stamp, options=options,
                    egress_matrix=self._egress_matrix_for(envelope),
                    # The policy's own destinations, so the check compares the matrix against
                    # what is being permitted rather than against what the run happened to do.
                    egress_expected=(envelope.allowed_destinations or None))
                binding = self.report_binding(envelope.digest())
                document = {"measured_at": now if now is not None else time.time(),
                            "binding": binding.as_dict(), "report": report}

                # Kept as a diagnostic copy. Readiness does not read it -- it reads the snapshot
                # -- so a stale file here can never make a gateway look ready.
                self.readiness.store(report, binding, now)

                verdict = self.readiness.evaluate_document(document, binding,
                                                           envelope.required_properties)
                if not verdict.ready:
                    self._restore_config(previous)
                    store.clear_pending()
                    return verdict
                # Past this call the change is committed: `activate` treats its own rename as
                # the commit point and cannot raise after it. So anything that reaches the
                # handler below happened BEFORE the commit, and restoring the intent is right.
                store.activate(envelope, report, binding.as_dict(), now)
            except BaseException:
                self._restore_config(previous)
                store.clear_pending()
                raise
        # What the measurement proved, not whether the gateway may take work. The health watch
        # calls this to decide whether the worker it just measured is good again, and a gated
        # answer would tell it "no" because the gate is held by this very measurement.
        return self._what_the_measurement_proves()

    def _egress_matrix_for(self, envelope):
        """Measure the allowlist this policy actually names, or return nothing measured.

        `EM3C-Y6-DECISION-0001`, `D3-a`. The suite reports `egress-allowlist` as `not_checked`
        when no matrix reaches it, and the gateway supplied none -- so a gateway that permitted
        egress was ready on a report that had never tried to leave it. The matrix is now built
        from the policy's own destinations, with a control that is deliberately NOT among them,
        so the run distinguishes "the allowlist works" from "nothing has a route anywhere".

        Returns None for a closed policy: there is no allowlist to measure, and inventing a
        passing matrix for one would be the failure this exists to prevent.
        """
        from agentnode_sdk.gateway import operator_policy as opol

        if envelope.mode != opol.RESTRICTED or not envelope.allowed_destinations:
            return None
        from agentnode_sdk.conformance.runner import measure_egress

        denied = next((c for c in ("example.org", "example.net", "iana.org")
                       if c not in envelope.allowed_destinations), None)
        if denied is None:
            # Every control this build knows is on the allowlist, so a denial could not be told
            # apart from a failure to reach anything. Unmeasured, and therefore not ready.
            return None
        # ALL of them. Measuring the first and permitting the rest was what EM3C-FINAL-0001
        # found: the policy was reported as measured while every destination after the first
        # was an open path nobody had tried.
        return self.worker.measure_egress(allowed=envelope.allowed_destinations, denied=denied)

    def readiness_now(self):
        """The current answer to whether this gateway may take work, with its reason.

        Two questions, and both have to hold. What the last measurement PROVES -- recomputed
        below, never remembered from when the process started -- and whether the worker it was
        about is reachable NOW. The second used to be missing, and that is how the closed alpha
        came to report that it was protecting a job it could not have run: the proof was valid
        and the machine it described was not answering.

        The live answer is checked first when it is bad, because it is the more recent fact. A
        stale report and an absent worker are both refusals, and a reader is better served by
        the one that changed than by the one that has been true all along.
        """
        live = self.health.now()
        measured = self._what_the_measurement_proves()
        if live.may_admit:
            return measured
        return Readiness(
            False, live.reason, {}, tuple(measured.unproven),
            # No "measure it again" here: while the worker is unreachable that is not a step
            # anybody can carry out, and a remedy that cannot be followed is worse than none.
            ("Wait for the sandbox worker to come back; nothing will run until it has.",)
            if live.state == health.UNAVAILABLE else (),
            measured.measured_at,
        )

    #: The structured live health of this gateway, for anything that reports rather than admits.
    #: A caller that needs to tell `unavailable` from `measuring` reads this; `readiness_now`
    #: deliberately flattens both to "no".
    def health_now(self):
        return self.health.now()

    def published_health(self):
        """The same question, asked from a process that may not be the one serving.

        `gateway status`, `gateway watch` and an operator's own check each run in their own
        process, where the watch above has never probed anything: asking it there would get
        `starting` for a machine whose worker has been gone for two minutes. They read what the
        serving gateway wrote instead, aged, so an old permissive statement cannot be carried
        forward -- `health.read_published` is where that ageing lives and why.

        Nothing is ADMITTED on the strength of this. Admission reads the object in the process
        that is doing the admitting; this is what is reported.
        """
        if getattr(self.worker, "transport", "in-process") == "in-process":
            # There is no worker here that can be lost without this process going with it, so
            # there is nothing published and nothing to age. Saying "no statement, assume the
            # worst" about that would report every in-process gateway as broken.
            return health.starting()
        return health.read_published(self.state.root / health.HEALTH_FILE)

    def _what_the_measurement_proves(self):
        """What the last measurement establishes, with no regard for whether the worker is there.

        Every part of this is recomputed: the policy is re-read and re-digested, and the report
        is judged against the properties THAT policy requires. Nothing here is remembered from
        when the process started.

        Separate from `readiness_now` so that the health watch's own measurement can be judged
        without consulting the state that measurement is about to settle -- during it the live
        state is `measuring`, and asking the gated answer would have it conclude that its own
        measurement had failed.
        """
        from agentnode_sdk.gateway.activation import SnapshotUnusable
        from agentnode_sdk.gateway.operator_policy import OperatorPolicyError

        try:
            state = self.active_state()
            configured = self.configured_envelope()
        except (SnapshotUnusable, OperatorPolicyError) as exc:
            # A policy that cannot be read is not a closed policy. It is an unknown one, and an
            # unknown policy is not something to run foreign code under.
            return Readiness(
                False,
                "this gateway cannot read the policy it is supposed to be enforcing: "
                + str(exc) + " Nothing will be run until that is resolved.",
                {}, (), (_MEASURE,),
            )

        if state is None:
            return Readiness(
                False,
                "this gateway has not been measured yet, so it cannot say what it enforces. "
                "Nothing will be run until it has been.",
                {}, tuple(configured.required_properties), (_MEASURE,),
            )

        if configured.digest() != state.policy_digest:
            # The config file was changed without going through an activation. The file is an
            # input, not the decision -- so this is refused rather than obeyed.
            return Readiness(
                False,
                "what this gateway is configured to allow is not what was measured and put into "
                "force. A policy takes effect only after it has been measured as itself.",
                {}, tuple(configured.required_properties), (_MEASURE,),
            )

        document = {"measured_at": state.activated_at,
                    "binding": state.binding, "report": state.report}
        return self.readiness.evaluate_document(
            document, self.report_binding(state.policy_digest),
            state.policy.required_properties)

    def measured_properties(self) -> dict[str, bool]:
        """What this gateway has been SHOWN to do -- from measurements, not from its own say-so.

        This used to read:

            "container_isolation": bool(availability.available),
            "verified_cleanup":    bool(availability.available),

        A container runtime being installed was reported as proof that the runtime isolates and
        that cleanup is verified. Neither follows, and a client asking for `verified_cleanup` was
        checked against that claim and told yes -- a check that could not see its input reporting
        the good answer.

        Now every name here is true only if the conformance suite MEASURED it, on this gateway,
        against this image, recently enough to still describe it. Missing, stale, foreign,
        unmeasured and failed all come out false.
        """
        return dict(self.readiness_now().properties)

    def hello(self) -> dict[str, Any]:
        identity = self.state.identity
        availability = self.worker.can_it_isolate()
        readiness = self.readiness_now()
        # Both have to hold. A runtime that is missing means nothing can run; a gateway that has
        # not been measured means nothing SHOULD run, because it cannot say what it enforces.
        ready = bool(availability.available) and readiness.ready
        if not availability.available:
            reason = availability.reason or "no container runtime is available"
        else:
            reason = readiness.reason
        return {
            "protocol": PROTOCOL_VERSION,
            "gateway": identity.as_dict(),
            "fingerprint": identity.fingerprint,
            "ready": ready,
            "reason": "" if ready else reason,
            "properties": readiness.properties,
            "unproven": list(readiness.unproven),
            "next_steps": list(readiness.next_steps),
            "measured_at": readiness.measured_at,
            # The live state, alongside what was measured and never folded into it. A caller
            # that sees `ready: false` learns from this whether the machine is broken, absent or
            # busy establishing itself -- and an operator's tooling reads it without opening a
            # connection to a worker that may be running foreign code.
            "health": self.health_now().as_dict(),
            "pairing_open": self.state.pairing_active(),
        }

    # ------------------------------------------------------------------ admission

    def authenticate(self, token: str, payload: dict[str, Any], signature: str) -> bytes:
        self.require_private_state()
        secret = self.state.token_secret(token)
        if secret is None:
            raise ProtocolError("this client is not paired with this gateway")
        if not verify_signature(secret, payload, signature):
            raise ProtocolError("the signature does not match the request")
        return secret

    def _what_a_closing_line_will_need(self, record, granted) -> dict:
        """The few values a usage line states that only this process currently knows.

        Written to the ledger at admission so that a gateway which restarts can close the run it
        interrupted with the figures that run was actually admitted under, rather than with
        whatever is configured by the time it is writing.
        """
        from agentnode_sdk.gateway import policy_version as _versions

        operator_digest, operator_version = "", _versions.UNKNOWN
        try:
            operator_digest = self.operator_envelope().digest()
            operator_version = _versions.version_for(self.state.root, operator_digest)
        except Exception:                                     # noqa: BLE001
            pass
        return {
            "cpu": float(granted.limits.cpu),
            "memory_mb": int(granted.limits.memory_mb),
            "wall_clock_s": int(granted.limits.wall_clock_s),
            "allowance_sha256": str(record.admitted_under or ""),
            "operator_policy_sha256": str(operator_digest or ""),
            "operator_policy_version": operator_version,
            "worker_topology": str(record.worker_topology or ""),
        }

    @staticmethod
    def what_to_tell_them_about_an_interruption(reason: str, ever_ran: bool) -> str:
        """The sentence that goes with the reason, saying the same thing the value says.

        The value is what a machine branches on; this is what a person reads. They have to
        agree, and they did not: every interrupted run was told "the gateway restarted", which
        is a specific claim and was wrong for a job cut short by a planned stop -- nothing had
        restarted at that point, and possibly nothing ever would.

        Two things differ between the sentences and both matter to the reader:

          WHAT HAPPENED TO THE GATEWAY   somebody stopped it, or it went without saying so
          WHETHER THEIR JOB EVER RAN     because one of the two costs money and the other does
                                         not, and because only one of them may have done work

        "did not finish" appears in every one of them on purpose: it is the phrase an
        interrupted run is required to carry, and a test reads for it.
        """
        from agentnode_sdk.gateway.protocol import (GATEWAY_CRASHED, GATEWAY_KILLED,
                                                    GATEWAY_STOPPED)

        what = {
            GATEWAY_STOPPED: "this gateway was stopped",
            GATEWAY_CRASHED: "this gateway failed and ended",
            GATEWAY_KILLED: "this gateway was ended from outside",
        }.get(reason, "this gateway went away, and what ended it is not known")
        if ever_ran:
            return (what + " while your job was running, so it did not finish. Whether it got "
                    "anything done before that is not known. It has not been started again -- "
                    "submit it as a new job if you still want it run.")
        return (what + " while your job was still waiting for its turn, so it did not finish "
                "and never started -- nothing was charged for it. It has not been started "
                "again: submit it as a new job if you still want it run.")

    @staticmethod
    def what_became_of_the_sandbox(*, asked_for_a_sandbox: bool, cleanup_verified,
                                   the_worker_answered: bool = True) -> str:
        """Which of the four things a closing line may say about the run's sandbox.

        The one question an interrupted run actually raises is whether something of the
        customer's is still running on somebody else's machine. A line that does not answer it
        leaves them to ask, and there is nobody to ask.

        The mapping, and what each answer is entitled to claim:

          never_created     nothing ever asked the worker for a container for this run, and
                            nothing by its name exists now
          confirmed_gone    one WAS asked for, and the worker confirms nothing by its name is
                            there now
          still_there       the worker says one IS there
          not_established   nobody could ask

        The two tidy answers are kept apart on purpose. Operationally they say the same thing --
        nothing of yours is running -- and they differ in whether anything ever was, which is a
        different fact and not this code's to blur.

        ## What this cannot establish, said plainly

        The note is written on a best effort: a ledger that will not take a write is not a
        reason to refuse a job that already holds a slot. So a run that DID have a container and
        whose note was lost reads as `never_created` rather than `confirmed_gone`. That is the
        weaker of the two claims in the direction that matters least -- both say nothing is left,
        and the worker confirmed that part either way.

        It is keyed on the note and NOT on whether a slot was held, and the difference is not
        academic: `running` goes into the ledger when the slot is taken, before anything is
        asked of a worker. Keyed on that, a run interrupted in between -- interruption point 2
        on the closed alpha, a real case with a real line -- would have been recorded as a
        confirmed cleanup of a container that never existed.
        """
        from agentnode_sdk.gateway.protocol import (SANDBOX_CONFIRMED_GONE,
                                                    SANDBOX_NEVER_CREATED,
                                                    SANDBOX_NOT_ESTABLISHED,
                                                    SANDBOX_STILL_THERE)

        if not the_worker_answered:
            return SANDBOX_NOT_ESTABLISHED
        if cleanup_verified is None:
            return SANDBOX_NOT_ESTABLISHED
        if cleanup_verified is False:
            return SANDBOX_STILL_THERE
        return SANDBOX_CONFIRMED_GONE if asked_for_a_sandbox else SANDBOX_NEVER_CREATED

    def _close_an_interrupted_run(self, record, entry: dict, *, reason: str = "",
                                  owe_it_on_failure: bool = True) -> bool:
        """Write the one signed, chained usage line an interrupted run is owed.

        ## Why this exists

        Before it, a run interrupted by a restart produced NO line at all. Nothing was billed for
        it, and the gateway could still answer what became of it from the ledger -- but the
        signed, chained record, which is the thing a customer would be handed as proof of what
        this service did, did not contain the job. `ALPHA-CAPACITY-QUEUE-0002`, F1: "their waited
        and billed values are absent from the signed chain ... it directly defeats the recording
        criterion."

        The queue makes the case ordinary rather than rare: a job waiting when a restart happens
        is now a normal occurrence.

        ## The times are read, not invented

        `queued_at` is the ledger's `first_seen`; `started_at` is what `note_state('running')`
        wrote, and is ABSENT for a run that never left the queue. The meter derives `seconds` and
        `waited_s` from those, so a job that never started bills zero because there is nothing to
        subtract from -- not because a rule set it to zero afterwards.

        A line written from times this process invented would be a false statement about
        somebody's bill, which is worse than the missing line it replaces.
        """
        from agentnode_sdk.gateway import meter
        from agentnode_sdk.gateway import policy_version as _versions
        from agentnode_sdk.gateway.protocol import outcome_of as _outcome_of

        admitted = dict(entry.get("admitted") or {})
        queued = float(entry.get("first_seen") or 0.0)
        started = float(entry.get("started_at") or 0.0)
        sandbox = self.what_became_of_the_sandbox(
            asked_for_a_sandbox=bool(entry.get("asked_for_a_sandbox")),
            cleanup_verified=getattr(record, "cleanup_verified", None))
        try:
            transport, identity, worker_id = self._who_ran(record.run_id)
            meter.record(
                self.state.root,
                run_id=record.run_id,
                client_id=record.owner_client_id or meter.UNATTRIBUTED,
                account_id=record.owner_account_id or meter.UNATTRIBUTED,
                queued_at=queued or started or record.finished_at,
                started_at=started,
                finished_at=float(record.finished_at or time.time()),
                cpu=float(admitted.get("cpu") or 0.0),
                memory_mb=int(admitted.get("memory_mb") or 0),
                wall_clock_s=int(admitted.get("wall_clock_s") or 0),
                state="interrupted",
                # WHICH INTERRUPTION, as a value a reader can branch on. It used to be the empty
                # string -- the same field every other line fills in, left blank on the one kind
                # of line whose whole subject is that something went wrong. A customer holding
                # it could see that their job did not finish and not why.
                outcome=_outcome_of("interrupted", reason, None),
                termination_reason=str(reason or ""),
                sandbox=sandbox,
                bytes_out=0,
                # UNATTRIBUTED rather than a guess, for anything the ledger did not carry. It is
                # the word this gateway already uses for a policy it cannot name, and a reader
                # can tell it from a real digest.
                worker_topology=str(admitted.get("worker_topology") or meter.UNATTRIBUTED),
                worker_id=worker_id,
                worker_transport=transport,
                worker_identity=identity,
                allowance_sha256=str(admitted.get("allowance_sha256") or meter.UNATTRIBUTED),
                operator_policy_sha256=str(
                    admitted.get("operator_policy_sha256") or meter.UNATTRIBUTED),
                # -1, not 0: the meter refuses a zero here because it cannot be told apart from
                # a field nobody filled in, which is exactly the distinction this line needs.
                operator_policy_version=int(
                    admitted.get("operator_policy_version") or _versions.UNKNOWN),
            )
        except meter.AlreadyRecorded:
            # SOMEBODY ALREADY CLOSED THIS RUN, and that is this path succeeding rather than
            # failing. It is reached when a previous process wrote the line and did not get to
            # move the ledger entry before it went, and it would be reached by a second gateway
            # sharing this directory. Either way the run has its one line, the caller goes on to
            # move the state, and nothing is written twice.
            return True
        except Exception as exc:                              # noqa: BLE001
            # A line that could not be written is not a reason to fail the recovery and leave
            # every other interrupted run unanswered. It is reported the way any other failure
            # to record is.
            self.could_not_record(record, exc)
            # AND IT IS REMEMBERED, so that this gateway tries again while it is still here.
            # Leaving it in the ledger alone means a LATER START could write it, and nothing
            # requires a later start -- which is a possibility rather than a bound.
            if owe_it_on_failure:
                try:
                    self._owe_a_line(record.run_id, record, entry, reason)
                except Exception:                              # noqa: BLE001
                    pass
            # AND THE CALLER IS TOLD, because what it does next decides whether this run ever
            # gets a line at all.
            #
            # It used to return nothing, and the caller moved the ledger entry to `interrupted`
            # either way -- out of the set a later start selects from. So a line that could not
            # be written was not written later; it was never written, and the run was gone from
            # the only place anything would have looked. The run existed, it was accepted, and
            # the signed log did not contain it.
            #
            # Found by driving it rather than by reading it: a check on the ORDER of the two
            # calls passes on this code, because the order was never the problem.
            return False
        return True

    def _restore_interrupted(self) -> None:
        """Runs that were executing when the process died are interrupted, not running.

        They are restored so the client gets an honest answer instead of a status that will
        never change again, and they are emphatically NOT re-executed: the client asked once,
        and the gateway does not get to decide it should happen a second time.
        """
        budget = time.monotonic() + RECOVERY_BUDGET_SECONDS
        for run_id in self.ledger.runs_left_unswept():
            if time.monotonic() >= budget:
                break
            self._ask_again_about(run_id)
        for run_id in self.ledger.unfinished_runs():
            entry = self.ledger.run_entry(run_id) or {}
            # A record rebuilt from the ledger begins where it is. It is CONSTRUCTED there
            # rather than constructed elsewhere and then assigned, so that no state in this
            # module is ever written except through `move_to`, and the test that reads this
            # source can require exactly that rather than name an exception.
            record = RunRecord(
                run_id=run_id,
                job_id=str(entry.get("job_id", "")),
                request_sha256=str(entry.get("request_sha256", "")),
                owner_client_id=str(entry.get("owner_client_id", "")),
                owner_account_id=str(entry.get("owner_account_id", "")),
                state="interrupted",
            )
            # WHICH OF THE TWO THIS WAS, from the last thing the ledger durably saw. `running`
            # is written once, when a slot is held and before a container is asked for; a run
            # still sitting at `accepted` therefore never left the queue.
            #
            # They are told apart because they are not the same event and the customer's next
            # move differs: one had a sandbox that may have done work and left something behind,
            # the other never started and owes nothing. Neither is billed -- `started_at` stays
            # 0.0 on a rebuilt record either way -- but telling somebody their job was running
            # when it was queued is a false statement in the one place they go to find out.
            ever_ran = str(entry.get("state")) == "running"
            record.refusal = self.what_to_tell_them_about_an_interruption(
                self._how_it_was_interrupted(), ever_ran)
            record.finished_at = time.time()
            record.container_name = container_name_for(run_id)
            self.runs[run_id] = record
            # THE SANDBOX FIRST, THEN THE LINE, THEN THE STATE.
            #
            # It used to be line, state, sandbox, and the reason given was that writing the line
            # first risks a SECOND line rather than none if the process dies between the two,
            # "and of the two that is the one a reader can notice". That trade is gone: the
            # meter now refuses a second line for a run it already has, under the lock that
            # serialises appends. Neither order can produce two.
            #
            # So the order is free to serve the line's contents instead, and it does: the line
            # has to say what became of the sandbox, and nobody knows that until the sandbox has
            # been asked about. Written first, the line could only have said "not established"
            # about every run, including the ones this code had just tidied up.
            #
            # What each crash window now costs:
            #
            #   after the sweep, before the line   the run is still selectable, so the next
            #                                      start closes it. The sweep runs again and
            #                                      answers the same way
            #   after the line, before the state   the next start selects it, the meter refuses
            #                                      the second line, and the state moves on
            #
            # SWEPT WHICHEVER IT WAS, and that is deliberate after a first version got it wrong.
            # Skipping the sweep for a run the ledger never saw reach `running` looks tidy: no
            # container existed, so there is nothing to remove. But `running` is written on a
            # best effort -- a ledger that cannot be written is not a reason to refuse a job
            # that already holds a slot -- so a run CAN have a live container and still read as
            # `accepted` here. Not sweeping it leaks that container, with nothing anywhere
            # referring to it. `test_the_sandbox_a_cut_short_run_left_is_removed` says so in
            # exactly those words, and it was right.
            #
            # Asking about a container that never existed costs one question the worker answers
            # with "not there". Losing one that does exist costs a running sandbox nobody knows
            # about. The two are not close.
            if time.monotonic() < budget:
                self._clean_up_what_it_left(record)
            # ONLY IF IT HAS ITS LINE. Moving the entry takes the run out of the set a later
            # start selects from, and doing that for a run whose line could not be written is
            # how a run leaves the signed log for good.
            if self._close_an_interrupted_run(
                    record, entry, reason=self._how_it_was_interrupted()):
                self.ledger.note_state(run_id, "interrupted")

    #: How often a gateway tries again to write a closing line it could not write.
    #:
    #: A bound that does not depend on anybody restarting anything. Leaving the run in the
    #: ledger means a LATER START can write the line -- and nothing requires a later start, so
    #: on its own that is not a bound at all, only a possibility. `INTERRUPTED-AUDIT-RECORD-0001`,
    #: F1: "a failed append leaves no line indefinitely unless a later gateway start happens".
    #:
    #: Short enough that a disk which was briefly full is noticed in seconds; long enough that a
    #: disk which is still full is not hammered.
    OWED_RETRY_SECONDS = 15.0

    def _owe_a_line(self, run_id: str, record, entry: dict, reason: str) -> None:
        """Remember a closing line this gateway could not write, and keep trying while it lives.

        The retry is in this process and not in the next one. A run whose line failed stays in
        the ledger too, so a later start would also find it -- but that is a second chance, not
        a bound, and the criterion asks for a bound.

        The thread exists only while something is owed. A gateway that has never failed to write
        a line does not carry a thread for the possibility.
        """
        with self._owed_lock:
            self._owed[str(run_id)] = (record, dict(entry), str(reason))
            if self._owed_thread is not None and self._owed_thread.is_alive():
                return
            self._owed_thread = threading.Thread(
                target=self._keep_trying_to_pay_what_is_owed,
                name="agentnode-owed-lines", daemon=True)
            self._owed_thread.start()

    def _keep_trying_to_pay_what_is_owed(self) -> None:
        """Until there is nothing owed, or this gateway is closing. Never raises."""
        while not self._closing.is_set():
            if self._closing.wait(self.OWED_RETRY_SECONDS):
                return
            try:
                self.pay_what_is_owed()
            except Exception:                                  # noqa: BLE001
                pass
            with self._owed_lock:
                if not self._owed:
                    return

    def pay_what_is_owed(self) -> list:
        """One attempt at every closing line still owed. Returns the run ids that got one."""
        with self._owed_lock:
            owed = list(self._owed.items())
        written = []
        for run_id, (record, entry, reason) in owed:
            try:
                if not self._close_an_interrupted_run(record, entry, reason=reason,
                                                      owe_it_on_failure=False):
                    continue
                self.ledger.note_state(run_id, "interrupted")
            except Exception:                                  # noqa: BLE001
                continue
            written.append(run_id)
            with self._owed_lock:
                self._owed.pop(run_id, None)
        return written

    def what_is_still_owed(self) -> list:
        with self._owed_lock:
            return sorted(self._owed)

    def close_what_is_still_in_flight(self) -> list:
        """On the way out, close the runs this gateway is still holding. Returns their ids.

        ## Why a stop does not simply leave them

        It could: the next gateway to take this directory over finds them and closes them, which
        is what a killed gateway has to rely on. But "the next start" is not a bound on anything
        -- a directory nobody starts again never gets its lines, and the customer of a job cut
        short by a planned stop would wait on a record that arrives when somebody happens to
        restart a service.

        A stop is the one interruption where the gateway is still there to say what happened. So
        it says it, immediately, and `later` is left to the cases that genuinely have no choice.

        ## The race, and why it is safe

        A run finishing normally at this moment writes its own line from its own thread. Both
        paths reach the same meter, which refuses a second line for a run it already has, under
        the lock that serialises appends. Whichever arrives first writes; the other is told the
        run is already recorded and moves on. There is no window in which both succeed and none
        in which neither does.

        Never raises. A gateway that would not stop because it could not write a line is a
        gateway that has to be killed, which is the worse ending of the two.
        """
        from agentnode_sdk.gateway.protocol import GATEWAY_STOPPED, is_terminal

        closed = []
        for run_id, record in list(self.runs.items()):
            try:
                if is_terminal(getattr(record, "state", "")):
                    continue
                entry = self.ledger.run_entry(run_id) or {}
                if not record.finished_at:
                    record.finished_at = time.time()
                # FROM THE ONE FUNCTION, unconditionally. Keeping an existing value with an
                # `or` would be harmless here -- the function is deterministic, so it produces
                # the same name -- but it is a second spelling of where a container name comes
                # from, and a test reads every assignment to make sure there is only one.
                record.container_name = container_name_for(run_id)
                self._clean_up_what_it_left(record)
                if not self._close_an_interrupted_run(record, entry, reason=GATEWAY_STOPPED):
                    # Left where the next start will find it, for the same reason.
                    continue
                self.ledger.note_state(run_id, "interrupted")
                closed.append(run_id)
            except Exception:                                  # noqa: BLE001
                continue
        return closed

    def _clean_up_what_it_left(self, record: RunRecord) -> None:
        """Ask the worker to remove the sandbox an interrupted run left running.

        Calling the run interrupted answers its client; it does not stop anything. The worker is
        a separate service with its own lifetime, and the gateway's registry of live runs is in
        memory, so a restart leaves a container running with nothing anywhere that refers to it.
        This was measured rather than reasoned about: on the deployed alpha both services were
        restarted while a run was in flight, the run was correctly marked interrupted, and the
        container was still up afterwards with nobody accounting for it.

        The gateway does not touch a runtime to do this -- it asks the worker, by the name it
        chose itself, and the worker addresses nothing outside that run's own prefix.

        Nothing here may stop the gateway starting. A worker that cannot be reached leaves
        `cleanup_verified` at None, which already means "nobody could ask" rather than "nothing
        was left behind"; that difference is the entire reason the third state exists, and a
        restart is exactly when it is true.
        """
        try:
            removed = self.worker.stop(
                record.run_id, record.container_name, RECOVERY_APPEAR_SECONDS
            )
        except Exception:                                            # noqa: BLE001
            return
        if removed:
            record.cleanup_verified = True
            self.ledger.note_cleanup(record.run_id, True)
            return
        # `stop` says False both when there was nothing to remove and when it could not ask, so
        # it is not on its own an answer about what is left. Asking settles which one happened.
        try:
            record.cleanup_verified = self.worker.gone(
                record.container_name, patiently=False
            ).verified
        except Exception:                                            # noqa: BLE001
            record.cleanup_verified = None
        self.ledger.note_cleanup(record.run_id, record.cleanup_verified)

    def _ask_again_about(self, run_id: str) -> None:
        """A sandbox an earlier start could not confirm gone is asked about again.

        The run is already interrupted and already answered; what is unsettled is whether
        anything is still running. Asking costs one question to a worker that is, this time,
        probably up -- and not asking means a gateway that restarted while its worker was down
        leaves a container running for as long as the machine does.
        """
        record = self.runs.get(run_id)
        if record is None:
            entry = self.ledger.run_entry(run_id) or {}
            record = RunRecord(
                run_id=run_id,
                job_id=str(entry.get("job_id", "")),
                request_sha256=str(entry.get("request_sha256", "")),
                owner_client_id=str(entry.get("owner_client_id", "")),
                owner_account_id=str(entry.get("owner_account_id", "")),
                state="interrupted",
            )
            record.refusal = self.what_to_tell_them_about_an_interruption(
                self._how_it_was_interrupted(), True)
            record.finished_at = time.time()
            record.container_name = container_name_for(run_id)
            self.runs[run_id] = record
        self._clean_up_what_it_left(record)

    def require_private_state(self) -> None:
        """Re-check that the gateway's files are still private, on every path that reads them.

        `EM3C-EXTERNAL-0001` found that checking once before binding leaves two windows: between
        the check and the socket, and every moment after. Permissions are not a property of
        startup, so this runs where the tokens are actually read. A stat is cheap; being wrong
        here means somebody else has been able to read every token for as long as it took anyone
        to notice.
        """
        from agentnode_sdk.gateway.statedir import require_private

        require_private(self.state.root)

    def require_client(self, token: str) -> str:
        """The token hash for a paired client, or a refusal. No signature involved.

        Reading a run is not submitting one, so it does not carry a signed payload -- but it must
        still prove which client is asking, because a run's output is the output of somebody's
        code. This is the check that the status endpoint previously did not have at all.
        """
        self.require_private_state()
        client_id = self.state.client_id_for(token)
        if client_id is None or self.state.token_secret(token) is None:
            raise ProtocolError("this client is not paired with this gateway")
        return client_id

    def owned_run(self, run_id: str, token: str) -> "RunRecord | None":
        """This client's run, or None -- and None for somebody else's run too.

        A run belonging to another client is reported exactly like a run that does not exist.
        Distinguishing them would let anyone with a token enumerate which run ids are real, and a
        run id is not a secret -- the ownership check is what protects the output, so it must not
        leak around the edges of its own answer.
        """
        record = self.runs.get(run_id)
        if record is None:
            return None
        client_id = self.state.client_id_for(token)
        if client_id is None or not record.owner_client_id:
            return None
        if not hmac.compare_digest(record.owner_client_id, client_id):
            return None
        return record

    def requested_policy(self, request: JobRequest):
        """The policy the JOB asked for, on its own -- no operator, no client.

        This is the left-hand side of every delta. Composing it the same way the job's scope is
        composed keeps the two shapes comparable.
        """
        from agentnode_sdk.sandbox.contract import Limits, NetworkRules, SandboxPolicy

        if request.network == "none":
            net = NetworkRules(enabled=False, allowed_destinations=frozenset())
        elif request.network == "unrestricted":
            net = NetworkRules(enabled=True, allowed_destinations=None)
        else:
            net = NetworkRules(enabled=True,
                               allowed_destinations=frozenset(request.allowed_domains))
        return SandboxPolicy(
            network=net,
            limits=Limits(wall_clock_s=max(1, int(getattr(request, "wall_clock_s", 60)))),
        )

    def allowance(self):
        """What the operator allows one client. Read on every admission, never held.

        A ceiling lowered while a gateway is running takes effect on the next job. A run already
        in flight is left to finish: stopping one is what the stop is for, and a quota is not a
        cancellation.
        """
        from agentnode_sdk.gateway.allowance import read_allowance

        return read_allowance(self.state.root)

    @property
    def use(self):
        """What each client has used lately, durably."""
        from agentnode_sdk.gateway.allowance import USE_NAME, Use

        held = getattr(self, "_use", None)
        if held is None:
            held = Use(self.state.root / USE_NAME)
            self._use = held
        return held

    @property
    def rate(self):
        """How many requests each device and each account has made lately, durably."""
        from agentnode_sdk.gateway.admission import RATE_NAME, RateLimit

        held = getattr(self, "_rate", None)
        if held is None:
            held = RateLimit(self.state.root / RATE_NAME)
            self._rate = held
        return held

    def standing_of(self, account_id: str, device_id: str = ""):
        """Whether this customer is in good standing, asked once and answered here.

        An unreadable accounts record is NOT good standing. It is reported as "cannot tell",
        which admission treats as a stop -- the same reading as an unreadable kill switch, and
        for the same reason: a gateway that cannot tell whether a customer is suspended is not
        one to keep taking their work.
        """
        from agentnode_sdk.gateway import accounts as _accounts
        from agentnode_sdk.gateway.admission import Standing

        if not account_id:
            return Standing(account_id="", device_id=str(device_id))
        try:
            found = self.state.accounts.get(str(account_id))
        except _accounts.AccountsUnreadable:
            return Standing(account_id=str(account_id), device_id=str(device_id),
                            cannot_tell=True)
        except (_accounts.NoSuchAccount, OSError):
            # A malformed id, or state this gateway will not touch. Neither is an account in
            # good standing, and neither is an account at all.
            return Standing(account_id="", device_id=str(device_id))
        return Standing(account_id=found.account_id, device_id=str(device_id),
                        suspended_because="" if found.active else (
                            found.suspended_because
                            or "the operator suspended this account"))

    def may_this_caller_proceed(self, account_id: str, device_id: str,
                                would_run_work: bool) -> None:
        """The one pre-operation admission decision. Raises `admission.NotAdmitted` to refuse.

        Reached from the dispatcher for EVERY operation. The stop, suspension and the request
        rate are properties of the caller rather than of the job, so asking them per operation is
        what makes them apply to the cheap operations a probe actually uses -- and asking them
        HERE is what keeps there being one implementation of each.
        """
        from agentnode_sdk.gateway import admission
        from agentnode_sdk.gateway.allowance import why_it_is_stopped

        try:
            stopped = why_it_is_stopped(self.state.root) or ""
        except Exception as exc:                              # noqa: BLE001
            stopped = ("this gateway cannot tell whether it has been stopped (%s)"
                       % str(exc)[:120])
        try:
            ceilings = self.allowance()
        except Exception as exc:                              # noqa: BLE001
            # Fail-closed is already right where this is raised. What is added here is a NAME:
            # an exception escaping the dispatcher is a 500, and a 500 reads as "this service is
            # broken" when the truth is "this service is refusing on purpose, tell its operator".
            named = admission.what_it_cannot_read(exc)
            if named is None:
                raise
            raise named from exc
        admission.before_an_operation(
            self.standing_of(account_id, device_id),
            stopped_because=stopped, allowance=ceilings, rate=self.rate,
            would_run_work=bool(would_run_work))

    def within_its_allowance(self, client_id: str, asking_for: int,
                             account_id: str = "") -> str:
        """Raise `OverTheCeiling` if this client may not have another run right now.

        With a `run_id`, the looking and the claiming happen inside ONE transaction: two requests
        arriving together used to read the same remaining allowance and both be admitted, because
        the check and the record were separate steps with the whole of admission in between.

        Returns the digest of the ceilings that were applied, so the run can carry what it was
        admitted UNDER rather than what happens to be configured when it ends.
        """
        from agentnode_sdk.gateway.protocol import is_terminal

        allowed = self.allowance()
        granted = allowed.digest()
        if not client_id:
            # Nothing to count against. Admission refuses an unpaired caller elsewhere; this is
            # not the place that decides that, and counting against "" would pool every client.
            return granted
        self._concurrency(allowed.concurrent_runs, "owner_client_id", client_id, "device")
        self._concurrency(allowed.account_concurrent_runs, "owner_account_id", account_id,
                          "account")
        for whose, key, ceilings in self._window_scopes(allowed, client_id, account_id):
            runs, seconds = self.use.so_far(key)
            self._judge_window(ceilings, runs, seconds, self.use.oldest(key), asking_for,
                               whose=whose)
        return granted

    def _window_scopes(self, allowed, client_id: str, account_id: str):
        """The scopes a run is counted against, each with the ceilings that apply to it.

        Both, always, and the tighter one decides. A per-credential ceiling alone is one a
        customer raises by pairing another device, which is not a ceiling; a per-account ceiling
        alone lets one runaway credential consume the whole customer's allowance before anybody
        notices which one it was.
        """
        from agentnode_sdk.gateway.allowance import Allowance

        out = []
        if client_id and (allowed.runs_per_window or allowed.seconds_per_window):
            out.append(("device", client_id, allowed))
        if account_id and (allowed.account_runs_per_window
                           or allowed.account_seconds_per_window):
            out.append(("account", account_id, Allowance(
                runs_per_window=allowed.account_runs_per_window,
                seconds_per_window=allowed.account_seconds_per_window,
                window_seconds=allowed.window_seconds)))
        return out

    def _concurrency(self, ceiling: int, field_name: str, whose: str, called: str) -> None:
        """How many of this scope's runs are going, and whether another may start."""
        from agentnode_sdk.gateway.protocol import is_terminal

        if not ceiling or not whose:
            return
        with self._lock:
            going = sum(1 for r in self.runs.values()
                        if getattr(r, field_name, "") == whose and not is_terminal(r.state))
        if going >= ceiling:
            raise OverTheCeiling(
                "concurrent_runs" if called == "device" else "account_concurrent_runs",
                "this %s already has %d runs going and may have %d at once. Wait for one to "
                "finish." % (called, going, ceiling))

    def _judge_window(self, allowed, runs: int, seconds: float, oldest: float,
                      asking_for: int, whose: str = "client") -> None:
        """Whether a client may start another run in this window. One definition, two callers.

        The early look in `within_its_allowance` and the authoritative claim in `reserve` have to
        agree about what "over a ceiling" means; two copies of this would be two answers, and the
        more permissive one would be the one that decided.
        """
        lifts = oldest + allowed.window_seconds
        named = "runs_per_window" if whose != "account" else "account_runs_per_window"
        if allowed.runs_per_window and runs >= allowed.runs_per_window:
            raise OverTheCeiling(
                named,
                "this %s has started %d runs and may start %d in this window. The oldest "
                "stops counting in %.0f seconds." % (whose, runs, allowed.runs_per_window,
                                                     max(0.0, lifts - time.time())),
                lifts_at=lifts)
        named = "seconds_per_window" if whose != "account" else "account_seconds_per_window"
        if allowed.seconds_per_window and seconds + asking_for > allowed.seconds_per_window:
            raise OverTheCeiling(
                named,
                "this %s has used %.0f of %d seconds in this window and this job asks "
                "for up to %d more. The oldest stops counting in %.0f seconds."
                % (whose, seconds, allowed.seconds_per_window, asking_for,
                   max(0.0, lifts - time.time())),
                lifts_at=lifts)

    def admit(self, request: JobRequest, artifact: bytes, token: str = "",
              *, client_id: str = "", account_id: str = "") -> tuple:
        """Everything that must hold before a container exists. Raises to refuse.

        Order matters: the cheap structural checks come before anything that costs work, and
        nothing here has a side effect that would survive a refusal.

        `client_id` and `account_id` are WHO this is, established once by the dispatcher. They
        used to be re-derived here from a token, and a browser session has none -- so a run
        started from the console was counted against nobody's concurrency and nobody's window.
        """
        # Before anything else costs anything. `ALPHA-ALLOWANCE`: an operator's stop is the
        # first question asked of every job, and a gateway that cannot tell whether it has been
        # stopped answers it as stopped.
        # THE CHOKEPOINT. `may_this_caller_proceed` asks this too, for every operation, and for
        # a while it was the only thing that asked -- which left `submit` and `admit` as callable
        # paths to execution with no suspension check on them. A caller that did not come through
        # the dispatcher skipped it. `ALPHA-R2-ADMISSION-0010` found it under D1 and D4.
        #
        # Asked here as well, and deliberately not instead: the dispatcher's call refuses the
        # CHEAP operations too, which is what a probe actually uses. This one refuses the
        # expensive one however it is reached. The shared function consumes nothing, so asking
        # twice charges nobody twice -- the rate limit stays at the dispatcher for that reason.
        halted = why_it_is_stopped(self.state.root)
        from agentnode_sdk.gateway import admission as _admission

        _admission.standing_permits_work(
            self.standing_of(account_id, client_id), stopped_because=halted or "")
        if halted:
            raise Stopped(halted)

        check_freshness(request.issued_at)
        # Both stores. The in-memory one has the tighter window; the durable one is what
        # still knows about a captured request after a restart.
        if request.nonce and self.ledger.knows_nonce(request.nonce):
            raise ProtocolError("this request has already been used (replay)")
        self.nonces.check_and_remember(request.nonce)

        # What this client has already used, against what the operator allows it. Before the
        # artefact is digested, because refusing over a ceiling should not cost the work of
        # hashing something that is not going to run.
        # An EARLY LOOK, which claims nothing. It is here so that a client already over a ceiling
        # is refused before this gateway spends the work of hashing an artefact that is not going
        # to run. It is advisory by construction: the authoritative check-and-claim happens at the
        # commit point in `reserve`, because a claim taken here would be kept by a request that
        # one of the checks BELOW went on to refuse -- and a refused request must not consume the
        # allowance it was refused for.
        # How big the job itself is, before anything is done with it. The runtime bounds what
        # a job DOES; it cannot bound what was handed to this gateway, because by then the bytes
        # are already here.
        from agentnode_sdk.gateway import admission as _admission

        try:
            _admission.artifact_within_ceiling(self.allowance(), len(artifact or b""))
        except _admission.NotAdmitted as too_big:
            raise OverTheCeiling("max_artifact_bytes", too_big.because) from too_big

        granted_digest = self.within_its_allowance(
            client_id or (self.state.client_id_for(token) or ""), request.wall_clock_s,
            account_id=account_id or (self.state.account_id_for(token) or ""))

        actual = digest(artifact)
        if actual != request.artifact_sha256:
            raise ProtocolError(
                "the artifact does not match the digest this job was signed for "
                f"(signed {request.artifact_sha256[:16]}…, received {actual[:16]}…)"
            )

        properties = self.measured_properties()
        # `properties` now comes from measurements. A name that was never measured is absent
        # rather than false-but-known, and both are equally "not satisfied" -- unknown does not
        # round up to yes just because a job asked for it.
        missing = [p for p in request.required_properties if not properties.get(p, False)]
        if missing:
            raise ProtocolError(
                "this gateway cannot provide " + ", ".join(sorted(missing))
                + ". The job was not started."
            )

        from agentnode_sdk.gateway.policy_paths import (
            PolicyPathError,
            describe_deltas,
            narrowed_paths,
            policy_shape,
            validate_paths,
            widened_paths,
        )

        try:
            # Both lists are still validated: an unknown path name is refused either way.
            # Only `mandatory` is consulted afterwards -- see the disclosure note in admit().
            mandatory, _optional = validate_paths(request.mandatory, request.optional)
        except PolicyPathError as exc:
            raise ProtocolError(f"this job's requirements cannot be enforced: {exc}") from exc

        granted = self.compose(request, token, client_id=client_id)
        requested_shape = policy_shape(self.requested_policy(request))
        effective_shape = policy_shape(granted)

        # Checked once the EFFECTIVE policy is known -- the operator may have narrowed the
        # destinations, so validating what the job asked for would be checking the wrong set.
        # An allowlist the proxy could not enforce is refused here, before a network, a proxy
        # or a container exists. validate_allowed_domains rejects an IP literal, localhost, a
        # single-label name and an empty set -- each of which would otherwise become either an
        # unenforceable rule or, worse, a quietly open network.
        from agentnode_sdk.sandbox.composition import network_mode

        _mode, _domains = network_mode(granted)
        _net = granted.network
        if _net.enabled and _net.allowed_destinations is not None and not _net.allowed_destinations:
            # "Let me reach a restricted set of hosts" plus "here are none of them" is not a
            # coherent request. Running it with no network would be safe and would also be a
            # different job from the one that was asked for, decided silently. The dangerous
            # reading -- that an empty list means no restriction -- is the reason this is an
            # explicit refusal rather than a quiet substitution either way.
            raise ProtocolError(
                "this job asked for a restricted network but named no host it may reach, so "
                "there is nothing to allow. Name the hosts, or ask for no network at all. "
                "Nothing was started."
            )
        if _mode == "egress":
            from agentnode_sdk.sandbox.egress import validate_allowed_domains

            try:
                validate_allowed_domains(_domains)
            except ValueError as exc:
                raise ProtocolError(
                    "this job asks to reach " + (", ".join(_domains) or "nothing at all") +
                    ", which cannot be enforced as an allowlist: " + str(exc) +
                    ". Nothing was started."
                ) from None


        # A gateway may narrow and never widen. Checked rather than assumed: the fold is supposed
        # to guarantee it, and a check that never fires costs nothing while an unchecked
        # assumption costs everything the one time it is wrong.
        widened = widened_paths(requested_shape, effective_shape)
        if widened:
            raise ProtocolError(
                "this gateway composed a policy WIDER than the job asked for in "
                + ", ".join(widened) + ". Refusing rather than running it."
            )

        narrowed = narrowed_paths(requested_shape, effective_shape)
        broken = [p for p in narrowed if p in mandatory]
        if broken:
            raise ProtocolError(
                "this gateway cannot run the job as required: it must narrow "
                + ", ".join(sorted(broken))
                + ", and the job declared that mandatory. Nothing was started."
            )

        if request.policy_sha256 and request.policy_sha256 != digest(
                canonical_bytes(requested_shape)):
            raise ProtocolError(
                "the policy digest does not match the policy this job describes. "
                "The job was not started."
            )
        # Every narrowing is reported, not only the ones the job thought to list as optional.
        # EM3C-EGRESS-CLASSIFY-0001 found the earlier rule hid real narrowing: a field in
        # neither list was reduced with no delta and no refusal, so a caller holding two
        # unequal policy digests had nothing that said which field moved. `mandatory` still
        # decides what is REFUSED, above; it was never meant to decide what is DISCLOSED.
        return granted, properties, requested_shape, effective_shape, granted_digest, describe_deltas(
            tuple(narrowed), requested_shape, effective_shape)


    def client_policy(self, token: str):
        """What THIS client is allowed, independently of what its job asks for.

        A real layer, not a formality. The earlier version hardcoded an unrestricted USER scope,
        which meant the fold was really operator-over-job with a decorative middle -- a client
        ceiling could not be expressed at all, so "operator > client > job" was three names for
        two levels. EM3C-GATEWAY-0002 was right to call that out.

        The allowance is recorded against the token when the client pairs, so it is bound to an
        authenticated identity rather than to anything the job carries. A client with no recorded
        restriction is unrestricted at this scope, and the operator above it still binds.

        Kept as the TOKEN form for a caller that holds only a token. It resolves who that is and
        asks `policy_of_client`, which is the real one: a browser session holds no token, and a
        lookup that can only be done by token answers "no ceiling of its own" for every person
        using the console.
        """
        return self.policy_of_client(self.state.client_id_for(token) or "")

    def policy_of_client(self, client_id: str):
        """What this DEVICE is allowed, asked by identity rather than by credential."""
        from agentnode_sdk.sandbox.contract import NetworkRules, SandboxPolicy

        allowance = self.state.allowance_of_client(client_id)
        if allowance is None:
            return SandboxPolicy(network=NetworkRules(enabled=True, allowed_destinations=None))
        if not allowance:
            return SandboxPolicy(network=NetworkRules(enabled=False,
                                                      allowed_destinations=frozenset()))
        return SandboxPolicy(network=NetworkRules(enabled=True,
                                                  allowed_destinations=frozenset(allowance)))

    def compose(self, request: JobRequest, token: str = "", *, client_id: str = ""):
        """The fold, server-side. The operator is above the client, and the job is below both.

        WHO the client is comes from `client_id` when a caller knows it -- the dispatcher always
        does -- and is resolved from a token only for a caller that holds nothing else. It used
        to be the token alone, so a browser session, which has no token by design, folded in the
        unrestricted user scope instead of its own.
        """
        from agentnode_sdk.sandbox.contract import (
            Limits,
            NetworkRules,
            SandboxPolicy,
            Scope,
            merge_policies,
        )

        if request.network == "none":
            asked = NetworkRules(enabled=False, allowed_destinations=frozenset())
        elif request.network == "unrestricted":
            asked = NetworkRules(enabled=True, allowed_destinations=None)
        else:
            asked = NetworkRules(enabled=True,
                                 allowed_destinations=frozenset(request.allowed_domains))
        return merge_policies({
            Scope.ORGANISATION: self.operator_policy(),
            Scope.USER: self.policy_of_client(
                client_id or (self.state.client_id_for(token) or "")),
            # The requested wall clock is a REQUEST at the lowest scope, not a setting. Limits
            # narrow by minimum as scopes descend, so an operator's ceiling binds it.
            Scope.PACKAGE: SandboxPolicy(
                network=asked,
                limits=Limits(wall_clock_s=max(1, int(getattr(request, "wall_clock_s", 60)))),
            ),
        })

    # ------------------------------------------------------------------ execution

    def submit(self, request: JobRequest, artifact: bytes, token: str = "",
               *, client_id: str = "", account_id: str = "") -> RunRecord:
        """Admission runs first, always. A re-sent request is a replay and is refused.

        Two earlier versions got this wrong in the same direction, and the second was worse
        because it argued for itself. First, ANY request carrying a known run id returned that
        run's record before the nonce was ever checked. Then a byte-identical repeat was made to
        return the existing run deliberately, reasoning that a client retrying after a dropped
        connection needs it -- which quietly created a signed request that could be captured and
        re-sent forever, while the brief still claimed replays were refused.

        A byte-identical replay is a replayed nonce. It is refused here like any other, and the
        criterion is met rather than argued around.

        A client that loses its connection does not need to re-POST: GET /v1/jobs/<run_id> is
        idempotent, carries no session, and is the reconnection path. Re-sending the submission
        was never the right way to ask whether a job ran.
        """
        # Before anything else about this particular job: may this gateway run ANY job? An
        # unmeasured gateway cannot say what it enforces, so it does not get to run foreign code
        # and find out afterwards. Refused before admission, and long before a container.
        readiness = self.readiness_now()
        if not readiness.ready:
            blocked = RunRecord(run_id=request.run_id, job_id=request.job_id, state="refused")
            blocked.refusal = readiness.reason + (
                (" Next: " + readiness.next_steps[0]) if readiness.next_steps else ""
            )
            blocked.refused_as = "sandbox_unavailable"
            # WHICH kind of unavailable. The name above stays what the contract declares -- it
            # is one HTTP answer and one thing a client does about it -- but one word for every
            # cause is what a client got before, and it could not tell "the worker is gone" from
            # "it is back and being measured" from "it is back and the measurement failed".
            # Those call for three different things from whoever reads them, and waiting helps
            # in only two of them.
            live = self.health_now()
            blocked.refusal_cause = "" if live.may_admit else live.code
            blocked.refusal_remedy = (readiness.next_steps[0] if readiness.next_steps else
                                      "Ask whoever runs this sandbox to measure it again.")
            blocked.finished_at = time.time()
            return blocked

        request_sha = digest(canonical_bytes(request.to_payload()))
        record = RunRecord(run_id=request.run_id, job_id=request.job_id,
                           request_sha256=request_sha,
                           required_properties=tuple(request.required_properties),
                           # WHO owns this run. From the caller's established identity, and
                           # from a token only when that is all a caller has. Deriving it from
                           # the token alone left every console-started run with NO owner -- a
                           # browser session holds a cookie, not a token -- and an ownerless run
                           # was readable by every other customer on this gateway.
                           owner_client_id=client_id or (
                               self.state.client_id_for(token) or ""),
                           owner_account_id=account_id or (
                               self.state.account_id_for(token) or ""))
        try:
            granted, _props, req_shape, eff_shape, granted_digest, deltas = self.admit(
                request, artifact, token, client_id=record.owner_client_id,
                account_id=record.owner_account_id)
            # From here the run carries what it was admitted under. A limit changed while it is
            # going must not rewrite what this run is recorded as having been allowed.
            record.admitted_under = granted_digest
            record.admitted_under_values = dict(self.allowance().as_dict())
            # Where it ran and what that was configured as, taken now rather than at the end:
            # a worker replaced mid-flight must not rewrite what a finished run was measured on.
            record.worker_topology = self.worker.topology
            record.worker_configuration_sha256 = self.worker.configuration_sha256()
            record.backend_version = self.runtime_version()
            # The worker itself, reached the way the run will reach it and checked the way the
            # run will check it, BEFORE anything is claimed. A worker that is down, or one that
            # is not the identity this gateway expects, is a refusal with nothing in the ledger
            # and nothing in the signed log -- not an accepted job that must then be closed as
            # lost. A worker that goes away after this point is `transport_lost`, as before.
            self.worker.confirm_reachable()
        except Exception as exc:                              # noqa: BLE001 - refusal is an answer
            record.move_to("refused")
            record.refusal = str(exc)
            record.refused_as, record.refusal_remedy = name_the_refusal(exc)
            record.finished_at = time.time()
            # Recorded, so a refused job cannot be retried into an acceptance by resending it.
            with self._lock:
                self.runs.setdefault(request.run_id, record)
            # Deliberately NOT written to the ledger. The ledger records what was ACCEPTED and
            # may therefore have run; that is what must survive a restart. Claiming a run id here
            # also made a refusal steal the claim from a concurrent legitimate submission -- in a
            # parallel burst the losers of the nonce race recorded the id before the winner
            # reached its own claim, so all eight were refused and none ran.
            return record
        record.requested_policy = req_shape
        record.effective_policy = eff_shape
        record.request_policy_sha256 = digest(canonical_bytes(req_shape))
        record.effective_policy_sha256 = digest(canonical_bytes(eff_shape))
        record.deltas = deltas
        record.artifact_sha256 = request.artifact_sha256

        # Admitted, so the effective policy is settled -- which is one of the things the challenge
        # is bound to. Issued here, once, and never again for this run: `issue_challenge` writes
        # the binding to the ledger below, after the claim, so what it says is on disk before the
        # container is.
        record.challenge = ch.a_fresh_challenge()

        # Admitted -- so now claim it, atomically and durably, before anything starts. One
        # critical section covers both the look and the write, so two identical requests arriving
        # together cannot both be told they are the first; and the claim is on disk before the
        # container is, so a crash mid-run still leaves the run id spoken for and the job is not
        # quietly executed a second time by a gateway that restarted.
        #
        # It runs AFTER admission, not before. Before, it recorded the very nonce that admission
        # was about to check, so every first submission was refused as a replay of itself.
        if not self.ledger.claim(request.run_id, request.nonce, request_sha,
                                 record.owner_client_id,
                                 owner_account_id=record.owner_account_id,
                                 admitted=self._what_a_closing_line_will_need(record, granted)):
            refused = RunRecord(run_id=request.run_id, job_id=request.job_id,
                                request_sha256=request_sha, state="refused")
            refused.refusal = (
                "this run id has already been submitted; re-sending a signed job is a replay. "
                f"Ask for its status at /v1/jobs/{request.run_id}. Nothing was started."
            )
            refused.refused_as = "malformed"
            refused.refusal_remedy = (
                "Ask for the status of that run id instead of sending it again. A retry needs "
                "a new run id.")
            refused.finished_at = time.time()
            return refused

        # The binding goes down BEFORE the job starts, so that what this gateway wrote about
        # the challenge predates anything the run could produce. It carries the digest; the value
        # is not in it, and `note_challenge` has no way to be handed one.
        #
        # A job that brought its own command is not given a challenge: its standard input is its
        # own, and putting something on it would be altering the job. The binding says that in
        # words rather than leaving a crossing to fail without a reason.
        carried = not bool(request.command)
        if not carried:
            record.challenge = ""
        self.ledger.note_challenge(request.run_id, ch.bind(
            run_id=request.run_id, gateway_id=self.state.identity.gateway_id,
            backend_instance=self.instance,
            effective_policy_sha256=record.effective_policy_sha256,
            value=record.challenge, delivered=carried,
            because="" if carried else ch.BROUGHT_ITS_OWN_COMMAND).as_dict())

        # THE COMMIT POINT. Everything that must be true at the moment this run becomes real
        # happens here, in one critical section, and nothing after it can refuse.
        #
        # Two things were wrong before and are the reason this is one call. The concurrent count
        # was read under the lock and the record inserted under the lock LATER, with the whole of
        # admission in between -- so two requests arriving together both saw a free slot and a
        # ceiling of one admitted two. And the window claim was taken at the top of admission, so
        # a request that a later check refused kept the allowance it had claimed.
        self.reserve(record.owner_client_id, request.run_id, record, request.wall_clock_s,
                     account_id=record.owner_account_id)
        # Kept, not just started. Daemon status is not ownership: it means the interpreter will
        # not wait at exit, which is a different question from whether this service knows what it
        # set going. A review was right that a run thread could outlive the service that created
        # it, so the service holds them and gives them back in close().
        thread = threading.Thread(target=self._run_and_let_go,
                                  args=(request, artifact, granted, record),
                                  daemon=True, name="agentnode-run-%s" % str(request.run_id)[:8])
        with self._running_lock:
            self._running.add(thread)
        thread.start()
        return record

    def _run_and_let_go(self, request: JobRequest, artifact: bytes, granted,
                        record: RunRecord) -> None:
        """What the thread actually targets: carry the run out, then stop being held.

        A wrapper rather than a try/finally inside `_run`, because `_run` IS the run and several
        tests read its source to establish what it does in order -- wrapping the body in a
        `try` would have moved every one of those statements into a different method and left
        those tests reading this frame instead. The ownership belongs to the thread's lifetime,
        not to the work, so it sits where the thread begins and ends.
        """
        try:
            self._run(request, artifact, granted, record)
        finally:
            # THE SLOT GOES BACK HERE, whatever happened -- finished, failed, cancelled, or an
            # exception nobody expected. It belongs in this frame for the same reason the thread
            # bookkeeping does: it is owned by the attempt, not by the work. A slot given back
            # only on the happy path is a machine that runs one fewer job after every failure
            # until it runs none.
            #
            # Safe for a run that never held one: `give_back` for an unknown id does nothing,
            # and it hands any freed slot to whoever is waiting.
            self.slots.give_back(record.run_id)
            # Taken out of the set on the way past, whatever happened, so what the service holds
            # is what is actually running rather than everything it ever started.
            with self._running_lock:
                self._running.discard(threading.current_thread())

    def _wait_for_a_slot(self, record: RunRecord, granted) -> bool:
        """Hold a slot before running, or end the run without ever having started it.

        True means the slot is held and the billed clock has started. False means this run is
        over -- cancelled, suspended, revoked or stopped while it waited -- and it was billed
        nothing, because `started_at` was never set.

        ## What is re-checked here and why it cannot be checked only at admission

        A job may wait. While it waits its account can be suspended, its device withdrawn, or the
        whole gateway told to stop taking work. Admission happened before any of that. So the
        standing is asked again at the moment the slot is granted, which is the last moment
        before foreign code runs and therefore the only one that counts.
        """
        ticket = record.slot_ticket
        if ticket is not None:
            # WHATEVER ALREADY TOOK THIS TICKET OUT KNOWS WHY, and its reason wins.
            #
            # A withdrawal sets `cancel_requested` as well -- every way of ending a run does --
            # so reading that flag first told a customer whose device had been withdrawn that
            # they had cancelled their own job. The ticket carries the specific cause; the flag
            # only says that something ended this. So the flag is consulted ONLY when nothing
            # has dropped the ticket yet, and then it means what it says: the customer asked.
            if not ticket.dropped and record.cancel_requested.is_set():
                self.slots.drop(record.run_id, "cancelled")
            if not self.slots.wait_for_slot(ticket):
                why = getattr(ticket, "dropped", "") or "dropped"
                self._end_without_running(
                    record, granted,
                    "cancelled" if why == "cancelled" else "refused",
                    # EVERY REASON SOMETHING ACTUALLY DROPS A TICKET WITH, and no others.
                    #
                    # A suspension is deliberately absent. It is applied by the operator's CLI,
                    # which is a DIFFERENT PROCESS from the one holding this queue and cannot
                    # reach these tickets at all. It is enforced instead a few lines below, by
                    # asking the account's standing again at the moment the slot is granted --
                    # the last moment before foreign code runs. A wording here for a case
                    # nothing can produce would read like a mechanism that exists.
                    {"cancelled": "cancelled by the client while it was waiting for a slot",
                     "stopped": "this sandbox stopped taking work while this job was waiting",
                     "revoked": "the device that submitted this job was withdrawn while it "
                                "was waiting"}.get(why, why))
                return False
            record.slot_ticket = None

        # THE CUSTOMER'S OWN CANCELLATION IS ANSWERED FIRST, before anything else is asked.
        #
        # This used to be the first thing in the method and moving it cost something: with the
        # standing check ahead of it, a run the customer had cancelled came back `refused` --
        # the gateway telling somebody it would not do a thing they had already called off. It
        # is not a refusal, it is their own decision, and `test_a_run_cancelled_before_it_started`
        # is where that showed.
        #
        # It sits here rather than at the top so that a ticket already dropped for a specific
        # reason -- a withdrawal, a stop -- keeps that reason instead of being reported as a
        # cancellation. Both orderings matter and this is the one that satisfies both.
        if record.cancel_requested.is_set():
            self.slots.give_back(record.run_id)
            self._end_without_running(record, granted, "cancelled",
                                      "cancelled by the client before it started")
            return False

        # STANDING, asked again now rather than trusted from admission -- and asked through the
        # SAME call the dispatcher uses, so there is no second opinion here about what a stop or
        # a suspension means. `may_this_caller_proceed` is the one implementation of the
        # operator's stop, this account's standing and the request rate.
        #
        # The rate ceiling is deliberately not re-applied to a job that is already inside: it
        # bounds how fast work ARRIVES, and a job that has been waiting did not just arrive. So
        # this asks with `would_run_work=True` and treats only a refusal that is about standing
        # as a reason not to run. A job refused here consumed a slot for the length of this check
        # and nothing more, because the billed clock has not started.
        try:
            self.may_this_caller_proceed(record.owner_account_id, record.owner_client_id,
                                         True)
        except Exception as refused:                          # noqa: BLE001
            because = getattr(refused, "because", "") or str(refused)
            what = getattr(refused, "what_to_do", "")
            self.slots.give_back(record.run_id)
            self._end_without_running(
                record, granted, "refused",
                because + ((" " + what) if what else ""))
            return False
        if record.cancel_requested.is_set():
            self.slots.give_back(record.run_id)
            self._end_without_running(record, granted, "cancelled",
                                      "cancelled by the client before it started")
            return False

        # THE BILLED CLOCK STARTS HERE, and nowhere earlier.
        record.started_at = time.time()
        return True

    def _end_without_running(self, record: RunRecord, granted, state: str,
                             why: str) -> None:
        """Finish a run that never ran. Billed nothing, and said so.

        It goes through the same publication as any other ending, so a job that waited and was
        cancelled appears in the signed log like everything else -- with `seconds` at zero and
        the wait recorded beside it. A run that quietly vanished would be the one kind of run
        nobody could check.
        """
        record.finished_at = time.time()
        record.refusal = why
        # WHY IT ENDED, in the field that carries that answer everywhere else.
        #
        # The queue introduced a second way for a run to be cancelled -- taken out before it
        # ever reached the worker -- and this path set the state but not the reason. A run whose
        # state says `cancelled` and whose reason says nothing is the disagreement that
        # `test_the_state_and_the_reason_cannot_disagree` exists to stop, and
        # `test_a_run_cancelled_before_it_started` is where it showed: the reason came back
        # empty on a run everybody agreed was cancelled.
        if state == "cancelled":
            from agentnode_sdk.gateway.protocol import CANCELLED

            record.termination_reason = CANCELLED
        # NOTHING WAS LEFT BEHIND, because nothing was ever created. `container_name` is set in
        # `_run` AFTER the slot is held, so an empty one here is not an assumption -- it is the
        # record saying this job never reached the point of having a sandbox. Guarded on that
        # rather than on the state, so this can never claim cleanup for a run that did create
        # one.
        if not record.container_name:
            record.cleanup_verified = True
        if state == "refused":
            record.refused_as = "over_a_ceiling"
            record.refusal_remedy = "Send it again when this sandbox is taking work."
        try:
            record.move_to(state)
        except Exception:                                     # noqa: BLE001 - already terminal
            return
        # The same publication as any other ending: one line in the signed log, with `seconds`
        # at zero and the wait recorded beside it. A run that vanished silently would be the one
        # kind of run nobody could check afterwards.
        try:
            self.write_down_what_it_used(record, granted, state)
        except Exception as exc:                              # noqa: BLE001
            self.could_not_record(record, exc)

    def _run(self, request: JobRequest, artifact: bytes, granted, record: RunRecord) -> None:
        from agentnode_sdk.sandbox.composition import network_mode

        # WAIT FOR A SLOT, before anything about a container exists and before the billed clock
        # starts. A job that waits here has a record, an owner and a claim; what it does not have
        # is a start time, so nothing about this wait can be charged for.
        if not self._wait_for_a_slot(record, granted):
            return

        mode, domains = network_mode(granted)
        record.container_name = container_name_for(record.run_id)
        record.move_to("running")
        # DURABLY, so that a restart can tell this job from one that only ever waited.
        #
        # The ledger held `accepted` from submission until a terminal state and nothing ever
        # wrote anything between, so after a restart every unfinished run looked identical --
        # and each was told "the gateway restarted while this job was running", including jobs
        # that had never left the queue. That sentence was false for them, and the recovery
        # went on to ask the worker to clean up a sandbox that had never been created.
        #
        # Written BEFORE the container is asked for, never after: a crash between these two
        # lines then leaves a run marked as having run when it may not have, which costs one
        # pointless question to the worker. The other order would leave a run marked as merely
        # waiting while its sandbox was live, and that one loses a container.
        try:
            self.ledger.note_state(record.run_id, "running")
        except Exception:                                     # noqa: BLE001
            # A ledger that cannot be written is not a reason to refuse a job that has already
            # been admitted and holds a slot. The cost is the old behaviour for this one run.
            pass
        payload = base64.b64encode(artifact).decode("ascii")
        # The client's own command if it brought one, otherwise this gateway's bootstrap -- which
        # reads the challenge off the first line of standard input and puts it in its own process
        # environment before running the job. On stdin rather than in an argument because
        # `EM3C-CROSSING-DECISION-0001`, F-A-ARGV-EXPOSURE: a value on the container runtime's
        # command line is one anybody listing processes on this host can read.
        command = list(request.command) or ch.bootstrap(command_was_given=False)
        if record.challenge:
            payload = ch.on_stdin(record.challenge, self.instance, payload)
        job = WorkerJob(
            run_id=record.run_id,
            container_name=record.container_name,
            command=tuple(command),
            artifact=artifact,
            stdin=payload,
            network=mode,
            allowed_domains=tuple(domains or ()),
            limits=WorkerLimits(
                cpu=float(granted.limits.cpu),
                memory_mb=int(granted.limits.memory_mb),
                processes=int(granted.limits.processes),
                wall_clock_s=int(granted.limits.wall_clock_s),
            ),
        )
        left_behind = None
        terminal = "refused"
        # WRITTEN BEFORE THE WORKER IS ASKED, because a closing line has to be able to say
        # whether this run ever had a sandbox at all.
        #
        # `running` does not answer that. It is written when a SLOT is taken, which is before a
        # container is asked for -- so a run interrupted between the two held a slot and never
        # had a container, and a line keyed on `running` would call that a confirmed cleanup of
        # something that never existed. Exercised on the alpha as interruption point 2, which is
        # how the difference came to be noticed.
        #
        # Best effort, like the state note beside it, and for the same reason: a ledger that
        # will not take a write is not a reason to refuse a job that already holds a slot. The
        # cost of losing it is the weaker of the two claims -- `never_created` where
        # `confirmed_gone` was true -- and both say nothing is left.
        try:
            self.ledger.note_a_sandbox_was_asked_for(record.run_id)
        except Exception:                                     # noqa: BLE001
            pass
        try:
            if record.cancel_requested.is_set():
                raise _Cancelled()
            # The composed limit, not the requested one. An earlier version passed
            # request.wall_clock_s straight through, so the fold decided the network and the
            # client decided how long its code could run -- the operator ceiling bound one and
            # not the other, and the policy digest could not catch it because the digested value
            # was not the enforced one. EM3C-GATEWAY-0004 found it.
            outcome = self.worker.run(job)
            left_behind = outcome.egress_gone
            rc, platform = outcome.exit_code, outcome.native_platform
            record.termination_reason = outcome.reason
            record.native_status = outcome.native_status
            record.native_platform = platform
            record.exit_code = rc
            record.stdout = outcome.stdout
            record.stderr = outcome.stderr
            terminal = "cancelled" if record.cancel_requested.is_set() else "finished"
            if terminal == "cancelled":
                # `EM3C-E8-RECORD-0001`: what came back here is whatever the runtime made of a
                # container this gateway had just destroyed -- an ordinary exit, status 137. That
                # is not why the run stopped. It stopped because the client asked for it to, and
                # the record says so whatever the container's death looked like from outside.
                #
                # And nothing that was stopped chose a status: the exit code goes, the runtime's
                # own number is kept beside the reason, and it is kept only if something can say
                # whose number it is. A number nobody can attribute is a number a reader guesses
                # about, so it is not recorded at all.
                from agentnode_sdk.gateway.protocol import CANCELLED as _CANCELLED

                record.termination_reason = _CANCELLED
                whose = platform or outcome.runtime_platform
                record.native_status = rc if whose else None
                record.native_platform = whose if record.native_status is not None else ""
                record.exit_code = None
        except _Cancelled:
            from agentnode_sdk.gateway.protocol import CANCELLED

            terminal = "cancelled"
            record.termination_reason = CANCELLED
            record.refusal = "cancelled by the client before it started"
        except WorkerUnreachable as exc:
            # Not a job that failed. Nobody established whether it ran, and saying it failed
            # would tell a client something nobody knows. `EM3C-EVIDENCE-0002` cost an external
            # run to exactly this distinction.
            #
            # THIS IS THE TRANSPORT, and it is a different boundary from the one the backend
            # reports on. `runtime_lost` is the WORKER asking the container runtime and being
            # told nothing; this is the GATEWAY asking the worker and the connection between
            # them ending. Two links, two ways to lose an answer, and a reader of the record is
            # entitled to know which one went.
            from agentnode_sdk.gateway.protocol import TRANSPORT_LOST

            terminal = "unverified"
            record.termination_reason = TRANSPORT_LOST
            record.refusal = (
                "the sandbox that runs jobs for this gateway could not be reached, so what "
                f"happened to this run is not known: {exc}. It was not established that it ran, "
                "and it was not established that it did not."
            )
        except CouldNotRestrictTheNetwork as exc:
            terminal = "refused"
            record.refusal = (
                "the restricted network this job asked for could not be set up, so it was "
                f"not started: {exc}"
            )
        except JobFailed as exc:
            terminal = "refused"
            left_behind = exc.egress_gone
            record.refusal = f"the run could not be completed: {exc}"
        except Exception as exc:                              # noqa: BLE001
            terminal = "refused"
            record.refusal = f"the run could not be completed: {exc}"
        finally:
            # Asking the worker whether anything is left can itself fail, and it is MOST likely
            # to fail in exactly the case that got us here: a worker lost during the run is a
            # worker that cannot be asked about cleanup either. This used to be an unguarded call
            # in a `finally`, so that exception escaped before the terminal state was published --
            # and the run then had no terminal state at all. A client polling it waits forever on
            # something nobody will ever finish, which is worse than either answer.
            #
            # Not knowing is its own answer: `cleanup_verified` stays None and the run ends
            # `unverified`, which is the word this gateway already uses for "nobody established
            # what happened". It is not turned into a job that failed -- nothing here says
            # anything about the job.
            try:
                record.cleanup_verified = self.worker.gone(record.container_name).verified
            except Exception as exc:                          # noqa: BLE001
                record.cleanup_verified = None
                if terminal not in ("refused", "cancelled"):
                    terminal = "unverified"
                if not record.refusal:
                    record.refusal = (
                        "the run ended, and this gateway could not ask the worker whether "
                        "anything was left behind: " + str(exc)[:200])
            if record.cleanup_verified and left_behind is not None:
                # Cleanup means the whole run, not just the container that carried it. What a run
                # needed BESIDES its container is on the worker's side of the line, so the worker
                # is what says whether it is gone.
                record.cleanup_verified = left_behind
            record.finished_at = time.time()
            # The terminal state is published LAST, and that ordering is the point. An earlier
            # version set it before this block, so a client polling in the window between the two
            # saw state="finished" on a record whose cleanup_verified was still None -- a terminal
            # answer that was not yet true. The real container lane caught it; before that it
            # passed on timing alone, which is the worst way for a race to behave.
            # A job that asked for verified cleanup and got "unknown" did not get what it
            # asked for. Unknown is an honest answer -- there are backends with nothing to ask --
            # but it is not a yes, and rounding it up here would make the requirement decorative
            # while leaving the caller believing it held.
            if (terminal == "finished"
                    and "verified_cleanup" in record.required_properties
                    and record.cleanup_verified is not True):
                terminal = "unverified"
                record.refusal = (
                    "the job ran and finished, but it required verified cleanup and this gateway "
                    "could not confirm the container was removed. Treat the result as unproven: "
                    "what ran is not in question, what was left behind is."
                )
            # The value is dropped here. What survives is the digest, in the binding the
            # ledger holds -- so a challenge is worth nothing once its run has ended, and there is
            # nowhere left to read it from. It was never a credential; this is what makes it also
            # not a leftover.
            record.challenge = ""
            # Counted and written down BEFORE the terminal state is published, for the same
            # reason cleanup is: a reader that sees a terminal state must be seeing a complete
            # record, and a client that saw one and immediately sent another job would otherwise
            # be admitted against a count that had not yet included the run it just finished.
            # The test for one line per run found this the first time it was written.
            try:
                self.write_down_what_it_used(record, granted, terminal)
            except Exception as exc:                          # noqa: BLE001
                # A run that ended and was never recorded is a run that disappeared from the
                # account of what this gateway has done. Leaving the client hanging would be
                # worse, so the run still reaches its terminal state -- but a gateway that cannot
                # write down what it ran must not go on running things, so it stops itself.
                # That is durable (a file), visible (every client is told), and an operator lifts
                # it deliberately once the cause is fixed.
                self.could_not_record(record, exc)
            # A reader that sees a terminal state must be seeing a complete record.
            record.move_to(terminal)
            self.ledger.note_state(record.run_id, terminal)

    #: How long a cancellation waits for the run to actually stop before it answers. The worker
    #: publishes the terminal state LAST, after cleanup -- so waiting for that state is waiting
    #: for the abort to be established and the cleanup to be done, which is the only moment at
    #: which a cancellation is true.
    CANCEL_SETTLE_SECONDS = 45.0

    #: How long removing a container waits for one to appear. A cancel can arrive between the
    #: worker marking a run running and the runtime creating the container; removing nothing in
    #: that instant would leave the payload to run to its wall clock. Named rather than buried,
    #: because a test that has no runtime at all should not wait the length of one.
    CONTAINER_APPEAR_SECONDS = 20.0

    def could_not_record(self, record, exc: Exception) -> None:
        """A run ended and its line was not written. Say so durably, and stop taking work.

        The alternative is a gateway that keeps running jobs it cannot account for, with nothing
        anywhere saying which ones are missing -- and a usage record whose gaps are invisible is
        not a usage record. Stopping is the fail-closed answer and it is reversible:
        `agentnode gateway resume` once whatever prevented the write is fixed.
        """
        from agentnode_sdk.gateway.allowance import stop_everything

        detail = ("run %s ended and this gateway could not write down what it used (%s). It has "
                  "stopped taking work rather than run anything else it cannot account for."
                  % (record.run_id[:12], str(exc)[:160]))
        record.refusal = record.refusal or detail
        sys.stderr.write("\n  " + detail + "\n")
        sys.stderr.flush()
        try:
            stop_everything(self.state.root, detail)
        except Exception:                                     # noqa: BLE001 - never mask the first
            pass

    def write_down_what_it_used(self, record, granted, terminal: str) -> None:
        """One line about one run, for an operator who has to say who used what.

        Everything here is a number or a name this gateway already published. Nothing of the
        job's content and nothing of anybody's credential: see `gateway/meter.py`, where the
        fields are declared and a test walks a real line looking for every secret there is.
        """
        from agentnode_sdk.gateway import meter
        from agentnode_sdk.gateway.protocol import TRANSPORT_LOST as _TRANSPORT_LOST
        from agentnode_sdk.gateway.protocol import outcome_of

        # WHAT IS BILLED, and what is not.
        #
        # `started_at` is zero until a worker slot was actually held, so a job that waited and
        # was then cancelled, suspended or revoked has nothing to subtract from and is billed
        # nothing. That is the whole mechanism: not a rule applied to the number afterwards, but
        # the absence of a number to bill.
        #
        # `queued_at` is when it arrived. The wait is recorded because a customer is entitled to
        # see it, and it is recorded SEPARATELY because it is this gateway's doing and not
        # theirs.
        started = float(record.started_at or 0.0)
        finished = float(record.finished_at or started or time.time())
        billed = max(0.0, finished - started) if started else 0.0
        queued = float(record.queued_at or started or finished)
        # Up to the slot if it ever got one, otherwise up to the end. A job that never started
        # waited until whatever ended it.
        waited = max(0.0, (started or finished) - queued)
        if record.owner_client_id:
            # Every scope that counted it, or an account ceiling would be charged for the run
            # starting and never for it ending.
            # The window quota is charged the BILLED seconds too. A customer whose job sat in
            # this gateway's queue must not have that count against the seconds they are allowed
            # to consume -- that would be charging them twice for our ceiling, once in money and
            # once in quota.
            self.use.finished_every(
                [k for k in (record.owner_client_id, record.owner_account_id) if k],
                record.run_id, billed)
        try:
            # Which policy this run was admitted under, by digest and by ordinal. Read from
            # the record's own effective policy rather than from whatever is configured now: a
            # policy edited while a run was going must not rewrite what that run ran under.
            from agentnode_sdk.gateway import policy_version as _versions

            operator_digest = ""
            operator_version = _versions.UNKNOWN
            try:
                operator_digest = self.operator_envelope().digest()
                operator_version = _versions.version_for(self.state.root, operator_digest)
            except Exception:                                 # noqa: BLE001
                # A policy that cannot be read or ordered leaves the fields EMPTY rather than
                # filled in with a guess. A record binding a version nobody checked looks
                # checked, which is worse than one binding none.
                pass

            transport, identity, worker_id = self._who_ran(record.run_id)
            meter.record(
                self.state.root,
                run_id=record.run_id,
                # A run whose device was withdrawn while it was going has no owner left to
                # name. That is a real state and it is said rather than left blank.
                client_id=record.owner_client_id or meter.UNATTRIBUTED,
                account_id=record.owner_account_id or meter.UNATTRIBUTED,
                worker_id=worker_id,
                worker_transport=transport,
                worker_identity=identity,
                operator_policy_sha256=operator_digest or meter.UNATTRIBUTED,
                operator_policy_version=operator_version,
                started_at=started, finished_at=finished,
                queued_at=queued,
                cpu=float(granted.limits.cpu), memory_mb=int(granted.limits.memory_mb),
                wall_clock_s=int(granted.limits.wall_clock_s),
                # The state it is ENDING in, which the record does not carry yet: publishing
                # it is the last thing that happens, after this.
                state=terminal,
                outcome=outcome_of(terminal, record.termination_reason,
                                   record.exit_code),
                # BESIDE the outcome, not instead of it: five different endings share `failed`,
                # and a reader of one line has to be able to tell which one happened.
                termination_reason=str(record.termination_reason or ""),
                exit_code=record.exit_code,
                # WHAT BECAME OF THE SANDBOX, on the ordinary line too and not only on the one a
                # restart writes. A run that came back with an answer had its container removed
                # by the worker, which proves the container is gone or raises rather than
                # reporting a tidy-up it could not confirm -- so `the worker answered` is the
                # whole condition. A transport that ended before an answer arrived is the case
                # where nobody could establish it, and it says so.
                sandbox=self.what_became_of_the_sandbox(
                    asked_for_a_sandbox=bool(started),
                    cleanup_verified=True,
                    the_worker_answered=(
                        str(record.termination_reason or "") != _TRANSPORT_LOST)),
                bytes_out=len(record.stdout or "") + len(record.stderr or ""),
                worker_topology=self.worker.topology,
                # What it was admitted under, not what is configured now.
                allowance_sha256=record.admitted_under or self.allowance().digest(),
                allowance_admitted_under=(record.admitted_under_values
                                          or self.allowance().as_dict()))
        except meter.AlreadyRecorded:
            # This run is already in the log, so it is accounted for and there is nothing to do.
            # Reached when a stop closed it on the way out and its own thread came back a
            # moment later, which is a race both halves of are correct: whichever reached the
            # meter first wrote the line, and the meter refused the second. Publishing the
            # terminal state still happens after this, so the client is answered either way.
            pass
        except OSError:                                       # pragma: no cover - a full disk
            # A run that happened is not un-happened by a meter that could not be written, and
            # refusing to publish the terminal state over it would lose the run instead.
            pass

    @property
    def slots(self):
        """The machine ceiling and its queue, from whatever the operator currently allows.

        Rebuilt when the ceilings change and reused when they have not: a new object would drop
        every waiting job on the floor, and an operator raising a limit must not be a way to lose
        work that is already queued.
        """
        from agentnode_sdk.gateway.capacity import Slots

        allowed = self.allowance()
        shape = (int(allowed.machine_concurrent_runs or 0), int(allowed.queue_depth or 0))
        with self._slots_lock:
            if self._slots is None or self._slots_for != shape:
                if self._slots is None:
                    self._slots = Slots(ceiling=shape[0], queue_depth=shape[1])
                else:
                    # Changed in place, so tickets already waiting keep waiting on the same
                    # object rather than on one nobody will ever promote from.
                    self._slots.ceiling, self._slots.queue_depth = shape
                self._slots_for = shape
            return self._slots

    def reserve(self, client_id: str, run_id: str, record, asking_for: int,
                account_id: str = "") -> None:
        """Take the slot and the allowance, or raise -- as one indivisible step.

        The count of what a client has going and the insertion of the new run are the same
        decision, so they are made without letting go of the lock in between. Reading the count,
        doing a page of other work, and then inserting is how a ceiling of one admits two.

        Both scopes are claimed in ONE transaction. Claiming the device and then the account as
        two steps means a job counted against the device, refused by the account, and the
        device's allowance spent on a run that never happened -- a quota that charges the
        customer for the gateway's own ordering.
        """
        from agentnode_sdk.gateway.protocol import is_terminal

        allowed = self.allowance()
        if not client_id:
            with self._lock:
                self.runs[run_id] = record
            return

        scopes = self._window_scopes(allowed, client_id, account_id)

        def judging(ceilings, whose):
            def judge(runs: int, seconds: float, oldest: float) -> None:
                self._judge_window(ceilings, runs, seconds, oldest, asking_for, whose=whose)
            return judge

        with self._lock:
            if allowed.concurrent_runs:
                going = sum(1 for r in self.runs.values()
                            if r.owner_client_id == client_id and not is_terminal(r.state))
                if going >= allowed.concurrent_runs:
                    raise OverTheCeiling(
                        "concurrent_runs",
                        "this device already has %d runs going and may have %d at once. Wait "
                        "for one to finish." % (going, allowed.concurrent_runs))
            if allowed.account_concurrent_runs and account_id:
                going = sum(1 for r in self.runs.values()
                            if getattr(r, "owner_account_id", "") == account_id
                            and not is_terminal(r.state))
                if going >= allowed.account_concurrent_runs:
                    raise OverTheCeiling(
                        "account_concurrent_runs",
                        "this account already has %d runs going and may have %d at once. Wait "
                        "for one to finish." % (going, allowed.account_concurrent_runs))
            if scopes:
                # Raises before anything is written if any scope is over a window ceiling.
                self.use.claim_every(
                    [(key, judging(ceilings, whose)) for whose, key, ceilings in scopes],
                    run_id)
            else:
                self.use.note_every([k for k in (client_id, account_id) if k], run_id)
            # Taken in the same breath as it was checked.
            self.runs[run_id] = record

        # THE MACHINE CEILING, after the customer's own and outside the lock above.
        #
        # After, because the customer's ceilings are the customer's own doing and should be the
        # answer they get: telling somebody "this machine is busy" when what is actually true is
        # "you already have two going" sends them to complain to the wrong person.
        #
        # Outside, because this one may put the job in a queue and a queue that is entered while
        # holding the lock every other run needs would stop the machine rather than pace it. The
        # two locks are never held together: `Slots` has its own and takes nothing else.
        #
        # A refusal here happens AFTER the window allowance was claimed above, so it gives it
        # back -- otherwise a job that never ran would still have spent the customer's quota.
        try:
            record.slot_ticket = self.slots.take_or_queue(run_id, account_id or client_id)
        except Exception:
            with self._lock:
                self.runs.pop(run_id, None)
            if (allowed.runs_per_window or allowed.seconds_per_window
                    or allowed.account_runs_per_window
                    or allowed.account_seconds_per_window):
                try:
                    self.use.finished_every(
                        [k for k in (client_id, account_id) if k], run_id, 0.0)
                except Exception:                             # noqa: BLE001
                    pass
            raise

    def stop_what_is_running(self, why: str, settle: float | None = None) -> list:
        """End every run that has not ended, because the operator stopped this gateway.

        The stop used to mean "admit nothing new", and runs already going were left to finish.
        That is the wrong reading of a switch somebody reaches for: the reason to stop a gateway
        at once is usually the code that is running on it right now -- an image being replaced
        under it, a client doing something that must not continue, a host that has to be freed.
        A switch that leaves that running is one an operator cannot rely on.

        Returns what it did to each, so a caller can say so rather than assume. A run that will
        not settle is reported as unsettled rather than counted as stopped; this is a fail-closed
        thing and claiming more than happened would defeat it.
        """
        from agentnode_sdk.gateway.protocol import is_terminal

        # THE QUEUE EMPTIES FIRST, in one pass. A kill switch that stopped the running jobs and
        # left the waiting ones to be promoted into the slots just freed would start new work
        # while shutting down -- the exact opposite of what somebody reaching for it wants.
        # Dropping them all under one lock is what stops a job slipping from waiting to running
        # between two of these decisions.
        self.slots.drop_every(lambda ticket: True, "stopped")

        done = []
        for record in list(self.runs.values()):
            if is_terminal(record.state):
                continue
            # Said before it is stopped, so that whatever publishes the terminal state can see
            # why, and a client is told the gateway stopped rather than that its code was
            # cancelled by somebody unnamed.
            record.halted_by = why
            try:
                _, settled = self.cancel(record.run_id, settle=settle)
            except Exception as exc:                          # noqa: BLE001 - one run, not all
                done.append({"run_id": record.run_id, "stopped": False, "error": str(exc)[:200]})
                continue
            done.append({"run_id": record.run_id, "stopped": bool(settled),
                         "state": record.state})
        return done

    #: How long `close()` waits for run threads before saying which it could not get back.
    CLOSE_SECONDS = 20.0

    def _stop_it_and_confirm(self, run_id: str) -> bool:
        """Stop a run and answer whether the sandbox is CONFIRMED gone. Never guesses.

        This is what the stopping pool calls, and the only thing that decides a cancellation is
        finished. Returning False leaves the run in the pool's journal, so a gateway that dies
        here picks it up again rather than leaving a container with nobody accounting for it.
        Three cases, and the third is the one a restart lands in:

        * still running -- stop it, then confirm;
        * already terminal -- do not stop it again, but still confirm, because reaching a
          terminal state is not the same as the sandbox being gone;
        * not in memory at all -- a restart. The record is rebuilt from the ledger and the
          container is addressed by the name this gateway derives from the run id.
        """
        from agentnode_sdk.gateway.protocol import is_terminal

        run_id = str(run_id)
        record = self.runs.get(run_id)
        if record is not None and not is_terminal(record.state):
            record, _settled = self.cancel(run_id)
        if record is None:
            self._ask_again_about(run_id)
            record = self.runs.get(run_id)
            return bool(record is not None and record.cleanup_verified)
        if record.cleanup_verified:
            return True
        # Terminal, but nobody has confirmed the sandbox is gone. Asking is what makes a terminal
        # state worth anything, so it is asked rather than assumed.
        self._clean_up_what_it_left(record)
        return bool(record.cleanup_verified)

    def close(self) -> list:
        """Release what this service owns, and say what would not let go.

        Explicit, because a finalizer is a safety net. Idempotent: closing twice is what happens
        when a test and a production path both do the right thing, and neither should have to
        know about the other.

        Returns the names of run threads still alive when the wait ran out -- empty when
        everything ended, which is the ordinary case. Also kept on `left_running`, so a caller
        that ignores the return value can still find out.
        """
        # What the pool could not get back is part of what THIS close could not get back.
        # Discarding it meant a cancellation worker could outlive the service while close()
        # reported nothing left running, which is the same mistake in a different place.
        # ONE LAST ATTEMPT AT WHAT IS OWED, before the thread that keeps trying is told to
        # stop. A gateway that is going away is the last one that will hold these in memory.
        self._closing.set()
        # Stopped before anything else is torn down, so a probe cannot open a connection to a
        # worker while the thing that owns the connection is going away. It is idempotent and
        # a no-op when it was never started.
        try:
            self.health.stop()
        except Exception:                                      # noqa: BLE001
            pass
        try:
            self.pay_what_is_owed()
        except Exception:                                      # noqa: BLE001
            pass

        pool = getattr(self, "stopping", None)
        left_stopping = list(pool.close() or ()) if pool is not None else []

        # Then the runs. Bounded, because a job with a long wall clock should not hold a
        # shutdown open for its whole allowance -- what matters is that this waits, reports what
        # it could not get back, and never pretends a thread it abandoned has ended.
        with self._running_lock:
            waiting = list(self._running)
        deadline = time.monotonic() + self.CLOSE_SECONDS
        for thread in waiting:
            thread.join(max(0.0, deadline - time.monotonic()))
        self.left_running = left_stopping + [t.name for t in waiting if t.is_alive()]
        return self.left_running

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
        return False

    def cancel(self, run_id: str, settle: float | None = None):
        """Stop a run and answer once it has stopped. Returns `(record, settled)`.

        `EM3C-E6-RECORD-0001`: this used to remove the container and return immediately, while the
        worker had not yet published the terminal state. The answer said `running` about a run
        whose container had already been destroyed -- true of the record at that instant, and
        false about the world. So it waits, and `settled` says whether the waiting was enough. An
        unsettled cancellation is answered honestly rather than called terminal by default.

        Idempotent from the first line: a run that has already ended is not ended again, nothing
        is looked for, and the answer is the one the previous cancellation gave.
        """
        from agentnode_sdk.gateway.protocol import is_terminal

        record = self.runs.get(run_id)
        if record is None:
            return None, False
        if is_terminal(record.state):
            return record, True
        deadline = time.monotonic() + (self.CANCEL_SETTLE_SECONDS if settle is None else settle)
        record.cancel_requested.set()
        # A JOB THAT IS STILL WAITING HAS NO CONTAINER TO STOP, and asking the worker to stop one
        # that was never created would be asking about nothing. Taken out of the queue instead --
        # which wakes the thread holding it, and that thread ends the run without ever starting
        # it. `drop` answers whether this was the case, so nothing has to be inferred from the
        # absence of a container name.
        self.slots.drop(record.run_id, "cancelled")
        self.worker.stop(record.run_id, record.container_name, self.CONTAINER_APPEAR_SECONDS)
        while time.monotonic() < deadline:
            if is_terminal(record.state):
                return record, True
            time.sleep(0.05)
        return record, is_terminal(record.state)


class _Cancelled(Exception):
    pass


# ---------------------------------------------------------------------------- HTTP

class _Handler(BaseHTTPRequestHandler):
    server_version = "agentnode-gateway/1"
    service: GatewayService = None                            # set by make_server

    def log_message(self, fmt, *args):
        pass

    def _send(self, code: int, body: dict) -> None:
        # Every answer carries the gateway's identity, including refusals. `EM3C-EXTERNAL-0008`
        # found that error bodies were consumed before the client checked who sent them, so a
        # server at a changed address could hand back text that a person would read and act on.
        # An error is a message like any other, and the client cannot check what is not there.
        if isinstance(body, dict) and "gateway" not in body:
            try:
                body = self.service.stamp(dict(body))
            except Exception:                                 # noqa: BLE001
                pass
        data = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):                        # noqa: A003 - stdlib name
        """Say which endpoint was called, and nothing that could be a secret.

        The default handler writes the whole request line. Nothing secret rides in a path today
        -- tokens are headers and codes are bodies -- but "today" is doing a lot of work in that
        sentence, and a log is the one place a leak is permanent and copied elsewhere. Only the
        method and the path with any query string removed are recorded.
        """
        if not getattr(self.server, "agentnode_log", False):
            return
        try:
            line = str(args[0]) if args else ""
            method, _, rest = line.partition(" ")
            path = rest.split(" ", 1)[0].split("?", 1)[0]
            sys.stderr.write(f"gateway {method} {path}\n")
        except Exception:                                     # noqa: BLE001
            pass

    def _token_of(self) -> str:
        """A GET carries its token in a header; the status endpoint is authenticated too."""
        return self.headers.get("X-AgentNode-Token", "") or ""

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            raise ProtocolError("the request body is larger than this gateway accepts")
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")

    def _state_is_private(self) -> bool:
        """Re-checked per request, so the exposure is one request rather than one poll interval.

        The watcher below closes the socket, but polls; `EM3C-EXTERNAL-0004` was right that a poll
        interval is a window. This makes the window a single request, and the token file checks its
        own directory again before it is read, so nothing is disclosed inside that window either.
        """
        from agentnode_sdk.gateway.statedir import inspect as inspect_dir

        verdict = inspect_dir(self.service.state.root)
        if verdict.ok:
            return True
        self._send(503, refusal("this gateway has stopped accepting work: " + verdict.reason))
        return False

    def _the_contract(self, body: bytes = b""):
        """Anything the declared contract covers goes to the dispatcher and nowhere else.

        This handler translates HTTP and makes no decision of its own. The routes are derived
        from the declarations, so there is nowhere for an undeclared one to come from, and the
        dispatcher is the only thing that carries an operation out.
        """
        from agentnode_sdk.access import rest

        if not rest.ours(self.path):
            return None
        status, answer = rest.handle(self.service, self.path, self.command, self.headers, body)
        # Written directly rather than through `_send`. `_send` stamps every answer with this
        # gateway's own identity and protocol version, which is right for its own protocol and
        # wrong here: it overwrote the contract's `protocol` field with the gateway's, so a
        # client asking which version of the CONTRACT it was talking to got the version of
        # something else. The contract declares what its answers contain, and this writes exactly
        # that.
        data = json.dumps(answer, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        return True

    def _hand_over_a_setup_file(self):
        """The one place a device credential is written out, and it goes to a file."""
        from agentnode_sdk.access import enrolment

        form = urllib.parse.parse_qs(
            self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8"))
        asked = {k: (v[0] if v else "") for k, v in form.items()}
        # Collecting a credential changes what can reach this sandbox, so it needs the same
        # confirmation value as anything else that does -- and whether it matches is decided
        # where every other such question is decided.
        who = _dispatch.a_confirmed_session(
            self.service, _rest._cookie(self.headers, _rest.SESSION_COOKIE),
            asked.get("confirm", ""))
        if not who.authenticated:
            return self._send(401, refusal("this is not a session that may collect a setup"))
        try:
            # THIS SESSION'S OWN ACCOUNT, resolved in one step rather than found globally and
            # compared afterwards. `EXISTENCE-ISOLATION-DECISION-0001`: a setup belonging to
            # somebody else must not be FOUND and refused; it must not be found.
            found = self.service.connections.about_for(who.account_id,
                                                       asked.get("challenge", ""))
            # The connection joins the account that set it up. Leaving this out made every AI a
            # person added from their own console a separate customer, with its own ceilings,
            # its own bill and no way for the person to see it in their own device list.
            token = self.service.state.redeem_for_connection(found["label"],
                                                             account_id=who.account_id)
            bound = self.service.connections.spend_the_ticket(
                asked.get("challenge", ""), asked.get("ticket", ""),
                self.service.state.client_id_for(token))
        except enrolment.NoSuchChallenge as exc:
            return self._send(403, refusal(str(exc)))
        name, text = enrolment.setup_file(bound["channel"], self._where_we_are(), token,
                                          bound["label"])
        data = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition", 'attachment; filename="%s"' % name)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)
        return None

    def _where_we_are(self) -> str:
        """The address to put in a setup file: the one this request actually arrived on.

        The attribute is `agentnode_tls`, which is what make_server sets. This read `is_tls`,
        which nothing has ever set, so the getattr default made every TLS gateway hand out an
        http:// URL -- and the gateway refuses plain HTTP, so anybody following the file they
        were just given got a connection refused. The suite did not catch it because its gateway
        runs without TLS, where http:// happens to be right. A deployment with a certificate is
        where it shows, which is where it was found.
        """
        host = self.headers.get("Host") or ("%s:%d" % self.server.server_address[:2])
        return "%s://%s" % ("https" if getattr(self.server, "agentnode_tls", False) else "http",
                            host)

    def _older_door_refuses(self, token: str, refused, run_id: str = "", speaks: int = 0):
        """Refuse an older client in a way it can verify and a person can act on.

        A client from before the consent gate sends no proof that anybody agreed. What it gets
        back is not a silent success, and not a quiet policy of running it anyway: it is a
        refusal that names itself, says what to do, and says which version of this client knows
        how. Failing safely is not a silent break when the answer is this specific.

        Signed with the same envelope as any other answer from this door, because an older
        client checks who it is talking to before it reads anything -- and an unsigned error is
        exactly the thing somebody at a changed address would like to be able to write.
        """
        # A run this caller may not see has always been answered with exactly this and nothing
        # else: no record, no reason beyond the four words. That is deliberate -- telling a
        # stranger that a run exists but is not theirs tells them it exists -- and the shape is
        # pinned by tests that read the answer field by field. Preserved rather than improved.
        if refused.refusal == "no_such_run":
            return self._send(404, self.service.stamp(refusal("no such run")))
        # Shaped the way this door has always shaped a refusal: a record with a state and a
        # reason. Its clients read `answer["state"]`, and handing them a bare error body instead
        # would be a silent break -- they would not crash on a field that had merely changed
        # meaning, they would crash on one that had gone. The structured `refused` name and
        # `what_to_do` come WITH it, so a client that has been migrated gets both.
        # An actual refused record, rendered the way this door has always rendered one.
        # Building it rather than hand-listing its fields is what keeps a replay from disclosing
        # the original run: a fresh record has empty streams and no exit code, so there is
        # nothing of somebody else's in it to leak, and it cannot fall out of step with whatever
        # a record carries next year.
        blocked = RunRecord(run_id=run_id, job_id="", state="refused")
        blocked.refusal = refused.because
        body = blocked.public()
        body.update(refused.as_answer())
        body["error"] = refused.because
        body["refusal"] = refused.because
        body["state"] = "refused"
        if refused.refusal in ("disclosure_required", "upgrade_required"):
            body["needs_client"] = MIGRATED_CLIENT
            body["what_to_do"] = (
                refused.what_to_do
                + " This client is older than the gate: update to agentnode %s or later, which "
                  "calls prepare, shows a person what would happen, and sends back what they "
                  "agreed to." % MIGRATED_CLIENT)
        # 403 for "who are you", 409 for "what you asked for". Both are what this door
        # answered before; `how_it_should_answer` is the contract's mapping and is right for
        # the contract's own addresses, not for one whose callers were written years earlier.
        speaks = speaks or (403 if refused.refusal in ("not_authenticated", "not_permitted")
                            else 409)
        return self._send(speaks, self.service.sign_answer(self.service.stamp(body), token))

    def _the_page(self):
        """The console. Reads one file off disk and writes it back; decides nothing.

        Deliberately not authenticated, and it does not need to be: what it serves is the same
        markup for everybody, carries no credential, and grants nothing. The page then calls the
        contract like any other client, with a token the person supplies -- so an unauthenticated
        page does not become an unauthenticated way in.
        """
        from agentnode_sdk import console

        if not console.ours(self.path):
            return None
        status, content_type, data = console.handle(self.path)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        # A page holding a credential in memory should not be framed by anything, should not be
        # sniffed into another type, and should not leak the address it came from.
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        # No inline exception anywhere. Scripts and styles come from this origin and nowhere
        # else, nothing may be fetched from another host, the page cannot be framed, and a form
        # may only submit back here -- which the setup download needs and nothing else uses.
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'; "
            "object-src 'none'")
        self.end_headers()
        self.wfile.write(data)
        return True

    def do_GET(self):
        if not self._state_is_private():
            return None
        if self._the_contract():
            return None
        if self._the_page():
            return None
        if self.path == "/console/confirm":
            # What a page asks for after a reload. The cookie survived a browser restart; the
            # confirmation value did not, because it lives only in the page's memory -- which is
            # the point of it. A fresh one is issued rather than the old one handed back, since
            # the old one is not kept either.
            cookie = _rest._cookie(self.headers, _rest.SESSION_COOKIE)
            fresh = _dispatch.fresh_confirmation(self.service, cookie)
            if not fresh:
                return self._send(401, refusal("not signed in"))
            who = _dispatch.identify_session(self.service, cookie, fresh, via="browser")
            return self._send(200, {"csrf": fresh, "device": who.client_id,
                                    "device_name": who.device_name})

        if self.path == "/v1/health":
            # Three booleans and, when it is not ready, why. Nothing that identifies a customer,
            # a device, a run or what the operator has configured. A load balancer can poll it
            # and a person can read it, and neither learns anything they could not learn by
            # trying to use the service.
            from agentnode_sdk.gateway import observability

            return self._send(200, observability.health(self.service))
        if self.path == "/v1/hello":
            return self._send(200, _dispatch.before_anyone(
                "hello", {}, service=self.service, via="older_door"))
        if self.path.startswith("/v1/jobs/"):
            # A TRANSLATOR. Who is asking and whether this run is theirs are both established by
            # the dispatcher; this reads an address and renders an envelope.
            #
            # Worth remembering what used to be here: no authentication at all. It looked a run
            # up by id and returned it, and a run id is not a secret while a run's output is
            # somebody's code's output. That check now happens in the one place that makes it.
            token = self._token_of()
            who = _dispatch.identify(self.service, token, via="older_door")
            try:
                answer = _dispatch.rendered_record(
                    self.service, who, self.path.rsplit("/", 1)[-1])
            except _dispatch.Refused as refused:
                return self._older_door_refuses(
                    token, refused, run_id=self.path.rsplit("/", 1)[-1],
                    speaks=404 if refused.refusal == "no_such_run" else 0)
            # What this door SERVED, recorded here rather than inside the render, because this
            # is the call site where the caller genuinely asked for a record. The older cancel
            # renders one too, and recording it there would say a client had called `status`
            # when it had not.
            _dispatch.record_what_was_done(self.service, "status", who)
            return self._send(200, self.service.sign_answer(
                self.service.stamp(answer), token))

        return self._send(404, refusal("no such endpoint"))

    def do_POST(self):
        if not self._state_is_private():
            return None
        from agentnode_sdk.access import rest as _rest

        if _rest.ours(self.path):
            # Read the body as bytes and let the adapter parse it: the contract's own refusal for
            # a malformed body is part of the contract, and this handler must not invent another.
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                return self._send(400, refusal("the request body is larger than this gateway "
                                               "accepts"))
            if self._the_contract(self.rfile.read(length) if length else b""):
                return None

        if self.path == "/console/setup":
            # BEFORE the body is read as JSON, because this one is not JSON. It is a form POST
            # rather than a link: a URL would put the ticket in the address bar, the history and
            # the referrer, and the answer streams back as a download rather than as something a
            # script reads. The credential is created at the moment of collection, so a setup
            # somebody starts and abandons leaves no connection behind.
            return self._hand_over_a_setup_file()

        try:
            body = self._read_json()
        except (ProtocolError, ValueError) as exc:
            return self._send(400, refusal(str(exc)))

        if self.path == "/v1/session":
            # A browser exchanging an invitation. It gets a session, not a token: the credential
            # is created and kept here, and what goes back is an identifier in a cookie the
            # page's own scripts cannot read.
            try:
                answer = _dispatch.before_anyone("open_session", body, service=self.service,
                                                 via="browser")
            except _dispatch.Refused as refused:
                return self._send(403, refusal(refused.because))
            given = answer.pop("session")
            data = json.dumps(answer, sort_keys=True).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            # HttpOnly: no script on the page can read it, so an injected one cannot take it
            # somewhere else. Secure and __Host-: the browser itself refuses to accept this
            # cookie unless it is bound to one origin with no Domain and the whole path, so the
            # binding is the browser's rule rather than a promise made here. SameSite=Strict:
            # another site's requests carry no cookie at all, which is the first of the two
            # locks -- the second is the confirmation value, which lives only in the page.
            self.send_header("Set-Cookie",
                             "%s=%s; Path=/; Max-Age=%d; Secure; HttpOnly; SameSite=Strict"
                             % (_rest.SESSION_COOKIE, given, _sessions.AT_MOST_SECONDS))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return None

        if self.path == "/v1/pair":
            try:
                answer = _dispatch.before_anyone("pair", body, service=self.service,
                                                 via="older_door")
            except _dispatch.Refused as refused:
                # The shape this door has always used for a pairing that did not work. The
                # console tells expired, already-used and mistyped apart from the sentence, so
                # the sentence is passed through rather than generalised away.
                return self._send(403, refusal(refused.because))
            return self._send(200, answer)

        if self.path == "/v1/jobs":
            # A TRANSLATOR. It reads the older shape off the wire, hands the request to the
            # dispatcher, and renders what comes back in the envelope this door has always used.
            # It decides nothing: who is asking, whether they may, what the policy allows, and
            # whether anybody agreed are all established in one place, the same place every
            # other door goes through.
            payload = body.get("payload") or {}
            token = body.get("token", "")
            # The signed request stays a signed request. Verifying it is transport work, but
            # deciding who is asking is not, so the proof goes to the dispatcher rather than
            # being checked here and the answer trusted.
            who = _dispatch.identify(
                self.service, token, proof=(payload, body.get("signature", "")),
                via="older_door")
            # PARSED with the wire format's own reader rather than read field by field.
            # Reading it by hand quietly dropped every check that lives in the parser -- the
            # protocol version, the shape of each field, what a malformed allowlist means -- and
            # a translator that loses checks is not a translator. Parsing is transport work;
            # what the parsed request is ALLOWED to do is still decided in one place.
            try:
                request = JobRequest.from_payload(payload)
            except (ProtocolError, ValueError) as exc:
                return self._older_door_refuses(token, _dispatch.Refused(
                    "malformed", str(exc), "Correct the request and send it again."),
                    run_id=str(payload.get("run_id") or ""), speaks=403)
            asked = {
                "run_id": request.run_id,
                "job_id": request.job_id,
                "artifact": body.get("artifact_b64", "") or "",
                # Carried as CLAIMS for the dispatcher to check, not dropped in favour of what
                # this gateway would have computed. A signature that covers something other than
                # what arrived is a refusal, not a detail to be corrected on the way past.
                "artifact_sha256": request.artifact_sha256,
                "policy_sha256": request.policy_sha256,
                "issued_at": request.issued_at,
                "nonce": request.nonce,
                "command": list(request.command),
                "network": _gc.NETWORK_WORDS.get(request.network, request.network),
                "allowed_domains": list(request.allowed_domains),
                "wall_clock_s": int(request.wall_clock_s),
                "required_properties": list(request.required_properties),
                "mandatory": list(request.mandatory),
                "optional": list(request.optional),
                "accepted_disclosure": str(payload.get("accepted_disclosure") or ""),
            }
            try:
                answer = _dispatch.submitted_record(self.service, who, asked)
            except _dispatch.Refused as refused:
                return self._older_door_refuses(token, refused, run_id=asked["run_id"])
            return self._send(202 if answer.get("state") != "refused" else 409,
                              self.service.sign_answer(self.service.stamp(answer), token))

        if self.path == "/v1/token/rotate":
            # A TRANSLATOR onto `devices.rotate`. Client-initiated on purpose: rotating only
            # from the server side would mean an operator conveying a new secret by hand, which
            # is the moment secrets get pasted into chat windows. The operation is declared
            # `audience=person`, so it reaches the dispatcher and is never offered to a model.
            token = body.get("token", "")
            who = _dispatch.identify(
                self.service, token, proof=(body.get("payload") or {}, body.get("signature", "")),
                via="older_door")
            try:
                answer = _dispatch.dispatch("devices.rotate", {}, who, service=self.service)
            except _dispatch.Refused as refused:
                return self._older_door_refuses(token, refused)
            identity = self.service.state.identity
            return self._send(200, {"token": answer["token"],
                                    "gateway": identity.as_dict(),
                                    "fingerprint": identity.fingerprint})

        if self.path.endswith("/cancel") and self.path.startswith("/v1/jobs/"):
            # A TRANSLATOR, and the one whose ANSWER changed in protocol 2.
            #
            # It used to carry the cancellation out itself and hold the caller while it did,
            # answering 200 for "it stopped" and 202 for "it was asked to and had not stopped
            # yet". It now hands the request to the dispatcher, which comes back at once, and
            # always answers 202.
            #
            # 202 means exactly what it always meant. What has gone is 200, which only a route
            # that waited could ever have said -- so nothing changed meaning quietly: a value
            # stopped being sent, the version says so, and a client that has not been migrated
            # reads "asked, not confirmed stopped", which is true. The waiting moved to the
            # client, where it holds nobody but itself.
            run_id = self.path.split("/")[3]
            token = body.get("token", "")
            who = _dispatch.identify(
                self.service, token, proof=(body.get("payload") or {}, body.get("signature", "")),
                via="older_door")
            try:
                _dispatch.dispatch("cancel", {"run_id": run_id}, who, service=self.service)
                # `asked_for` is what the CALLER asked for. A refusal while rendering the record
                # back is a refused cancel from where this client is standing, and the audit
                # says that rather than inventing a status call it never made.
                answer = _dispatch.rendered_record(self.service, who, run_id,
                                                   asked_for="cancel")
            except _dispatch.Refused as refused:
                return self._older_door_refuses(
                    token, refused, run_id=run_id,
                    speaks=404 if refused.refusal == "no_such_run" else 0)
            return self._send(202, self.service.sign_answer(
                self.service.stamp(answer), token))

        return self._send(404, refusal("no such endpoint"))


class _ServerThatStopsItsWatchers(ThreadingHTTPServer):
    """A server whose background threads end when it does.

    Two threads watch this server for as long as `agentnode_serving` is true: one for the
    operator's stop file and one for the state directory's permissions. Nothing used to clear
    that flag except the privacy watcher deciding to halt, so `shutdown()` stopped serving and
    left both threads alive -- waking every second or two, for the life of the process, reading
    files in a directory that may since have been deleted.

    One server leaking two threads is easy to miss. A test suite that starts dozens of them ends
    up with dozens of timers running underneath everything that comes after, which is how this
    was found: a submission to an unrelated gateway began timing out at thirty seconds, on one
    Python version at a time, about half the time.
    """

    #: How long the watchers are waited for. They wake on a one or two second tick, so this is
    #: a tick or two plus room for a slow filesystem read, not a guess.
    WATCHERS_SECONDS = 8.0

    def shutdown(self) -> None:
        self.agentnode_serving = False
        super().shutdown()
        self.let_the_watchers_go()

    def server_close(self) -> None:
        self.agentnode_serving = False
        super().server_close()
        self.let_the_watchers_go()

    def let_the_watchers_go(self) -> list:
        """Wait for the watcher threads and say which would not end.

        Clearing `agentnode_serving` asks them to stop at their next tick; it does not establish
        that they did. A review was right that starting a thread without keeping it is not
        ownership -- so they are kept, joined here, and whatever is still alive when the wait
        runs out is RETURNED rather than assumed gone.
        """
        watchers = list(getattr(self, "agentnode_watchers", ()))
        deadline = time.monotonic() + self.WATCHERS_SECONDS
        for watcher in watchers:
            watcher.join(max(0.0, deadline - time.monotonic()))
        self.agentnode_left_watching = [w.name for w in watchers if w.is_alive()]
        return self.agentnode_left_watching


def make_server(
    service: GatewayService,
    host: str = "127.0.0.1",
    port: int = 0,
    tls: TlsFiles | None = None,
):
    """A threading HTTP server bound to `host`. Defaults to loopback deliberately.

    Binding to loopback by default means an operator has to make an explicit choice before the
    gateway is reachable from anywhere else -- the safe direction for a default. Making that
    choice is not enough on its own: serving beyond loopback in the clear is refused, because
    the pairing code and the token would be readable by anyone who can reach the machine.
    """
    context = check_bind_address(host, tls)
    # After the transport decision, because that one is about the address the operator typed and
    # should be what they hear about first. Checked here rather than at construction, so that
    # building a service to inspect it is not the same act as exposing one -- this is the moment
    # the tokens become reachable.
    from agentnode_sdk.gateway.statedir import require_private

    require_private(service.state.root)

    def _watch_permissions(target) -> None:
        """Stop serving if the gateway's directory is widened while it is up.

        The check before binding is a point in time, and `EM3C-EXTERNAL-0003` was right that a
        point in time says nothing about the next one. Reading a token already re-checks, so a
        widened directory cannot be USED -- but the socket would stay open, accepting and refusing
        forever without telling anyone why. This closes it instead, which is both the safer state
        and the one an operator will notice.
        """
        from agentnode_sdk.gateway.statedir import inspect as inspect_dir

        # A backstop, not the boundary: every request re-checks, and the token file re-checks
        # its own directory before it is read. This exists so a gateway nobody is using does not
        # sit there exposed until somebody happens to call it.
        while getattr(target, "agentnode_serving", False):
            time.sleep(2.0)
            verdict = inspect_dir(service.state.root)
            if not verdict.ok:
                sys.stderr.write(
                    "\nThe gateway's files stopped being private while it was running:\n  "
                    + verdict.reason + "\n\nIt has stopped. To fix it:\n  "
                    + verdict.remedy + "\n"
                )
                target.agentnode_serving = False
                # On its own thread because shutdown() waits for the serve loop, which is not
                # this one -- but kept, for the same reason as everything else here: something
                # has to be able to say whether it ended.
                closing = threading.Thread(target=target.shutdown, daemon=True,
                                           name="agentnode-shutdown-on-exposure")
                target.agentnode_closing = closing
                closing.start()
                return
    handler = type("_BoundHandler", (_Handler,), {"service": service})
    server = _ServerThatStopsItsWatchers((host, port), handler)
    # Off unless asked for. A gateway that logs every request by default writes a record of who
    # ran what and when, on a machine whose operator never chose to keep one.
    server.agentnode_log = bool(os.environ.get("AGENTNODE_GATEWAY_LOG"))
    if context is not None:
        # The certificate was already loaded by check_bind_address above, before this socket
        # existed, so a certificate that will not load stops the gateway rather than leaving it
        # briefly serving in the clear. Wrapping here only attaches it, before serve_forever.
        server.socket = context.wrap_socket(server.socket, server_side=True)
        server.agentnode_tls = True
    else:
        server.agentnode_tls = False
    server.agentnode_serving = True
    def _watch_the_stop(target) -> None:
        """Act on the operator's stop, which is a FILE and not a call into this process.

        `agentnode gateway stop` runs in a different process from the gateway -- an operator at a
        terminal, or a script -- so it cannot reach the runs held here. The file is the only thing
        both sides share, which makes it the right place for the decision and this the right place
        to act on it.

        Polling rather than watching: a missed notification would be a kill switch that did not
        fire, and there is no filesystem-watch API worth trusting equally on every platform this
        runs on. A second's delay is acceptable for something an operator reaches for; silently
        not firing is not.
        """
        from agentnode_sdk.gateway.allowance import why_it_is_stopped
        from agentnode_sdk.gateway import retention as _retention

        acted_on = ""
        # Retention is swept on the loop that is already running, rather than by a timer somebody
        # has to install: a review was right that an invocable function is not enforcement, and
        # until this existed the periods in retention.json described an intention.
        #
        # WHEN it looks is held in memory, and that is the whole of the care here. The first
        # version asked `sweep_if_due` every second, which reads and parses a file to decide
        # whether an hour has passed -- so this loop went from one stat per tick to several file
        # operations per tick, and the resource tests started catching a descriptor mid-sweep
        # under load. Reading a file every second to learn that an hour has not passed is waste
        # whatever it costs; a monotonic deadline is the same behaviour for none of it.
        look_at_retention = time.monotonic()
        while getattr(target, "agentnode_serving", False):
            time.sleep(1.0)
            if time.monotonic() >= look_at_retention:
                look_at_retention = time.monotonic() + _retention.SWEEP_EVERY_SECONDS
                try:
                    _retention.sweep_if_due(service.state.root)
                except Exception:                             # noqa: BLE001 - never kill the loop
                    # A sweep that cannot run must not stop a gateway serving or acting on the
                    # operator's stop. It is visible: `agentnode gateway watch` reports when the
                    # last sweep was, and "never" is a value it can report.
                    pass
            try:
                halted = why_it_is_stopped(service.state.root)
            except Exception:                                 # noqa: BLE001 - never kill the loop
                continue
            if not halted:
                acted_on = ""                                 # lifted; a later stop acts again
                continue
            if halted == acted_on:
                continue
            acted_on = halted
            stopped = service.stop_what_is_running(halted)
            if stopped:
                unsettled = [r for r in stopped if not r.get("stopped")]
                sys.stderr.write(
                    ("\n  This gateway was stopped, and %d run(s) that were "
                     "going were ended.\n") % len(stopped))
                if unsettled:
                    # Never counted as stopped. An operator reaching for this needs to know
                    # which ones they still have to go and look at.
                    sys.stderr.write(
                        ("  %d did not confirm they had stopped: %s\n")
                        % (len(unsettled),
                           ", ".join(r["run_id"][:12] for r in unsettled)))
                sys.stderr.flush()

    # Held, not just started, so `let_the_watchers_go()` can wait for them and say what it could
    # not get back. Named, because a thread nobody can name is one nobody can report.
    server.agentnode_watchers = [
        threading.Thread(target=_watch_permissions, args=(server,), daemon=True,
                         name="agentnode-watch-permissions"),
        threading.Thread(target=_watch_the_stop, args=(server,), daemon=True,
                         name="agentnode-watch-the-stop"),
    ]
    for watcher in server.agentnode_watchers:
        watcher.start()
    # The health watch starts HERE and not in the service's constructor. A gateway that is
    # serving requests is the one that has to answer for whether its worker is there; a gateway
    # built to be asked a question in a test has no worker that can be lost without it. Skipped
    # for an in-process worker, where the gateway and the thing it would be probing are the same
    # process, and a probe would only establish that this process is running.
    #
    # Reached through `getattr` rather than attribute access because two tests drive this with a
    # stand-in for the service: what they are about is the bind, and they supply no worker and no
    # watch. A stand-in with no worker has no worker that can be lost, which is the same case as
    # an in-process one -- so it is the same answer, not a special one.
    watching = getattr(service, "health", None)
    worker = getattr(service, "worker", None)
    if watching is not None and getattr(worker, "transport", "in-process") != "in-process":
        watching.start()
    return server


def new_run_id() -> str:
    return uuid.uuid4().hex
