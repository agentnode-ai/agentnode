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
import json
import os
import secrets
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from agentnode_sdk.gateway.identity import GatewayState, PairingError
from agentnode_sdk.gateway import challenge as ch
from agentnode_sdk.gateway.ledger import Ledger
from agentnode_sdk.gateway.readiness import (
    Readiness,
    ReadinessGate,
    ReportBinding,
    describe_missing,
)
from agentnode_sdk.gateway.transport import TlsFiles, check_bind_address
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
    native_status: int | None = None
    native_platform: str = ""
    stdout: str = ""
    stderr: str = ""
    refusal: str = ""
    container_name: str = ""
    cleanup_verified: bool | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    cancel_requested: threading.Event = field(default_factory=threading.Event)
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

        return outcome_of(self.state, self.termination_reason)

    def public(self) -> dict[str, Any]:
        """What a client may see. No secrets, and no fields that only mean something inside."""
        return {
            "run_id": self.run_id,
            "job_id": self.job_id,
            "state": self.state,
            "exit_code": self.exit_code,
            "termination_reason": self.termination_reason,
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
        }


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
                 worker=None) -> None:
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
        self.runs: dict[str, RunRecord] = {}
        # What must survive this process. In-memory replay protection has a documented way
        # around it: restart the gateway, which on a server happens on its own.
        self.ledger = Ledger(self.state.root / "ledger.json")
        #: Which gateway process, and which sandbox behind it. A restart is a different instance,
        #: and a challenge says which one issued it.
        self.instance = "%s:%s" % (self.worker.instance_label(), secrets.token_hex(8))
        self.readiness = ReadinessGate(self.state.root)
        self._restore_interrupted()
        self._lock = threading.Lock()

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
        """Whatever runs foreign code. Here for now; elsewhere later, without this class caring."""
        if self._worker is None:
            from agentnode_sdk.worker.local import LocalWorker

            self._worker = LocalWorker(self.backend)
        return self._worker

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

        identity = self.state.identity
        isolation = self.worker.can_it_isolate()
        boot_value, _method = boot_identity()
        return ReportBinding(
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
        )

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
                            "binding": binding.as_dict(), "report": report.to_dict()}

                # Kept as a diagnostic copy. Readiness does not read it -- it reads the snapshot
                # -- so a stale file here can never make a gateway look ready.
                self.readiness.store(report.to_dict(), binding, now)

                verdict = self.readiness.evaluate_document(document, binding,
                                                           envelope.required_properties)
                if not verdict.ready:
                    self._restore_config(previous)
                    store.clear_pending()
                    return verdict
                # Past this call the change is committed: `activate` treats its own rename as
                # the commit point and cannot raise after it. So anything that reaches the
                # handler below happened BEFORE the commit, and restoring the intent is right.
                store.activate(envelope, report.to_dict(), binding.as_dict(), now)
            except BaseException:
                self._restore_config(previous)
                store.clear_pending()
                raise
        return self.readiness_now()

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

        Every part of this is recomputed: the policy is re-read and re-digested, and the report
        is judged against the properties THAT policy requires. Nothing here is remembered from
        when the process started.
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

    def _restore_interrupted(self) -> None:
        """Runs that were executing when the process died are interrupted, not running.

        They are restored so the client gets an honest answer instead of a status that will
        never change again, and they are emphatically NOT re-executed: the client asked once,
        and the gateway does not get to decide it should happen a second time.
        """
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
                state="interrupted",
            )
            record.refusal = (
                "the gateway restarted while this job was running, so it did not finish. It has "
                "not been started again -- submit it as a new job if you still want it run."
            )
            record.finished_at = time.time()
            self.runs[run_id] = record
            self.ledger.note_state(run_id, "interrupted")

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

    def admit(self, request: JobRequest, artifact: bytes,
              token: str = "") -> tuple:
        """Everything that must hold before a container exists. Raises to refuse.

        Order matters: the cheap structural checks come before anything that costs work, and
        nothing here has a side effect that would survive a refusal.
        """
        check_freshness(request.issued_at)
        # Both stores. The in-memory one has the tighter window; the durable one is what
        # still knows about a captured request after a restart.
        if request.nonce and self.ledger.knows_nonce(request.nonce):
            raise ProtocolError("this request has already been used (replay)")
        self.nonces.check_and_remember(request.nonce)

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

        granted = self.compose(request, token)
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
        return granted, properties, requested_shape, effective_shape, describe_deltas(
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
        """
        from agentnode_sdk.sandbox.contract import NetworkRules, SandboxPolicy

        allowance = self.state.client_allowance(token)
        if allowance is None:
            return SandboxPolicy(network=NetworkRules(enabled=True, allowed_destinations=None))
        if not allowance:
            return SandboxPolicy(network=NetworkRules(enabled=False,
                                                      allowed_destinations=frozenset()))
        return SandboxPolicy(network=NetworkRules(enabled=True,
                                                  allowed_destinations=frozenset(allowance)))

    def compose(self, request: JobRequest, token: str = ""):
        """The fold, server-side. The operator is above the client, and the job is below both."""
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
            Scope.USER: self.client_policy(token),
            # The requested wall clock is a REQUEST at the lowest scope, not a setting. Limits
            # narrow by minimum as scopes descend, so an operator's ceiling binds it.
            Scope.PACKAGE: SandboxPolicy(
                network=asked,
                limits=Limits(wall_clock_s=max(1, int(getattr(request, "wall_clock_s", 60)))),
            ),
        })

    # ------------------------------------------------------------------ execution

    def submit(self, request: JobRequest, artifact: bytes, token: str = "") -> RunRecord:
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
            blocked.finished_at = time.time()
            return blocked

        request_sha = digest(canonical_bytes(request.to_payload()))
        record = RunRecord(run_id=request.run_id, job_id=request.job_id,
                           request_sha256=request_sha,
                           required_properties=tuple(request.required_properties),
                           owner_client_id=self.state.client_id_for(token) or "")
        try:
            granted, _props, req_shape, eff_shape, deltas = self.admit(
                request, artifact, token)
        except Exception as exc:                              # noqa: BLE001 - refusal is an answer
            record.move_to("refused")
            record.refusal = str(exc)
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
                                 record.owner_client_id):
            refused = RunRecord(run_id=request.run_id, job_id=request.job_id,
                                request_sha256=request_sha, state="refused")
            refused.refusal = (
                "this run id has already been submitted; re-sending a signed job is a replay. "
                f"Ask for its status at /v1/jobs/{request.run_id}. Nothing was started."
            )
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

        with self._lock:
            self.runs[request.run_id] = record
        thread = threading.Thread(target=self._run, args=(request, artifact, granted, record),
                                  daemon=True)
        thread.start()
        return record

    def _run(self, request: JobRequest, artifact: bytes, granted, record: RunRecord) -> None:
        from agentnode_sdk.sandbox.composition import network_mode
        mode, domains = network_mode(granted)
        record.container_name = f"agentnode-em3c-{record.run_id[:16]}"
        record.move_to("running")
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
            terminal = "unverified"
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
            record.cleanup_verified = self.worker.gone(record.container_name).verified
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

    def do_GET(self):
        if not self._state_is_private():
            return None
        if self.path == "/v1/hello":
            return self._send(200, self.service.hello())
        if self.path.startswith("/v1/jobs/"):
            run_id = self.path.rsplit("/", 1)[-1]
            token = self._token_of()
            # This endpoint previously had NO authentication at all: it looked the run up by id
            # and returned it. A run id is not a secret, and a run's output is the output of
            # somebody's code, so that handed every job's stdout to anyone who could reach the
            # port. Both checks now happen before the record is touched.
            try:
                self.service.require_client(token)
            except ProtocolError as exc:
                return self._send(403, refusal(str(exc)))
            record = self.service.owned_run(run_id, token)
            if record is None:
                return self._send(404, self.service.stamp(refusal("no such run")))
            return self._send(200, self.service.sign_answer(
                self.service.stamp(record.public()), token))
        return self._send(404, refusal("no such endpoint"))

    def do_POST(self):
        if not self._state_is_private():
            return None
        try:
            body = self._read_json()
        except (ProtocolError, ValueError) as exc:
            return self._send(400, refusal(str(exc)))

        if self.path == "/v1/pair":
            try:
                self.service.require_private_state()
                # No source is passed, and that is the decision rather than an omission: the
                # limits it feeds are address-free, because behind a reverse proxy every client
                # shares the peer address and a forwarding header is set by whoever can set one.
                token = self.service.state.redeem_pairing(
                    body.get("code", ""), client_name=body.get("client_name", ""),
                )
            except PairingError as exc:
                return self._send(403, refusal(str(exc)))
            identity = self.service.state.identity
            return self._send(200, {"token": token, "gateway": identity.as_dict(),
                                    "fingerprint": identity.fingerprint})

        if self.path == "/v1/jobs":
            payload = body.get("payload") or {}
            try:
                self.service.authenticate(body.get("token", ""), payload,
                                          body.get("signature", ""))
                request = JobRequest.from_payload(payload)
                artifact = base64.b64decode(body.get("artifact_b64", "") or "")
            except (ProtocolError, ValueError) as exc:
                return self._send(403, refusal(str(exc)))
            record = self.service.submit(request, artifact, body.get("token", ""))
            return self._send(202 if record.state != "refused" else 409,
                              self.service.sign_answer(
                                  self.service.stamp(record.public()),
                                  body.get("token", "")))

        if self.path == "/v1/token/rotate":
            # Rotation is client-initiated on purpose. Doing it only from the server side would
            # mean the operator has to convey a new secret by hand, which is the moment tokens
            # get pasted into chat windows. The client proves it holds the current token, and
            # gets its replacement over the same connection it was already trusted on.
            token = body.get("token", "")
            try:
                self.service.authenticate(token, body.get("payload") or {},
                                          body.get("signature", ""))
            except ProtocolError as exc:
                return self._send(403, refusal(str(exc)))
            replacement = self.service.state.rotate_token(token)
            if replacement is None:
                return self._send(403, refusal("this client is not paired with this gateway"))
            identity = self.service.state.identity
            return self._send(200, {"token": replacement, "gateway": identity.as_dict(),
                                    "fingerprint": identity.fingerprint})

        if self.path.endswith("/cancel") and self.path.startswith("/v1/jobs/"):
            run_id = self.path.split("/")[3]
            token = body.get("token", "")
            try:
                self.service.authenticate(token, body.get("payload") or {},
                                          body.get("signature", ""))
            except ProtocolError as exc:
                return self._send(403, refusal(str(exc)))
            # Being paired was never enough to cancel somebody else's run; it only looked like it
            # was, because nothing checked. Ownership is checked BEFORE the cancel, so a stranger
            # cannot stop a run and then be told it was not theirs.
            if self.service.owned_run(run_id, token) is None:
                return self._send(404, self.service.stamp(refusal("no such run")))
            record, settled = self.service.cancel(run_id)
            if record is None:
                return self._send(404, self.service.stamp(refusal("no such run")))
            # Signed with the token that authenticated, not with whatever a header claimed. The
            # two were different variables, and only one of them had been checked.
            #
            # 200 means it stopped; 202 means it was asked to and had not stopped by the time this
            # gateway would wait no longer. The record is signed either way and says which state
            # it is really in -- the status is what keeps an unsettled cancellation from reading
            # like a finished one. No field of the answer changed, so this protocol version still
            # says everything a client of it needs.
            return self._send(200 if settled else 202, self.service.sign_answer(
                self.service.stamp(record.public()), token))

        return self._send(404, refusal("no such endpoint"))


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
                threading.Thread(target=target.shutdown, daemon=True).start()
                return
    handler = type("_BoundHandler", (_Handler,), {"service": service})
    server = ThreadingHTTPServer((host, port), handler)
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
    threading.Thread(target=_watch_permissions, args=(server,), daemon=True).start()
    return server


def new_run_id() -> str:
    return uuid.uuid4().hex
