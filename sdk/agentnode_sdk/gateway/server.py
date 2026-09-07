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
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from agentnode_sdk.gateway.identity import GatewayState, PairingError
from agentnode_sdk.gateway.ledger import Ledger
from agentnode_sdk.gateway.readiness import (
    ReadinessGate,
    ReportBinding,
    describe_missing,
)
from agentnode_sdk.gateway.transport import TlsFiles, check_bind_address
from agentnode_sdk.gateway.protocol import (
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
    stdout: str = ""
    stderr: str = ""
    refusal: str = ""
    container_name: str = ""
    cleanup_verified: bool | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    cancel_requested: threading.Event = field(default_factory=threading.Event)

    def public(self) -> dict[str, Any]:
        """What a client may see. No secrets, and no fields that only mean something inside."""
        return {
            "run_id": self.run_id,
            "job_id": self.job_id,
            "state": self.state,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "refusal": self.refusal,
            "artifact_sha256": self.artifact_sha256,
            "cleanup_verified": self.cleanup_verified,
            # Invariant 5: an optional narrowing may run, but the answer has to SAY what changed.
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
        id, artifact digest, both policy digests, and the result itself. An answer lifted out of
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
        )
        return {**body, "binding": binding, "signature": sign(secret, binding)}

    def stamp(self, body: dict[str, Any]) -> dict[str, Any]:
        """Bind an answer to the gateway that produced it.

        T-C only means something if a client can tell WHICH build answered. An earlier version
        stamped only /v1/hello and /v1/pair while the protocol claimed every response carried it,
        so a job result could not be tied to the gateway that produced it -- the review was right
        that the claim was broader than the code. Every answer carries it now.
        """
        identity = self.state.identity
        return {**body, "gateway": identity.as_dict(), "fingerprint": identity.fingerprint,
                "protocol": PROTOCOL_VERSION}

    def __init__(self, state: GatewayState, backend=None, operator_policy=None) -> None:
        self.state = state
        self._backend = backend
        self._operator_policy = operator_policy
        self.nonces = NonceCache()
        self.runs: dict[str, RunRecord] = {}
        # What must survive this process. In-memory replay protection has a documented way
        # around it: restart the gateway, which on a server happens on its own.
        self.ledger = Ledger(self.state.root / "ledger.json")
        self.readiness = ReadinessGate(self.state.root)
        self._restore_interrupted()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ backend

    @property
    def backend(self):
        if self._backend is None:
            from agentnode_sdk.sandbox.container_backend import ContainerBackend

            self._backend = ContainerBackend()
        return self._backend

    def operator_policy(self):
        """What this machine's owner allows. The highest scope in the fold."""
        if self._operator_policy is not None:
            return self._operator_policy
        from agentnode_sdk.sandbox.contract import NetworkRules, SandboxPolicy

        # A gateway defaults to no network for foreign code. The operator opens it deliberately.
        return SandboxPolicy(network=NetworkRules(enabled=False,
                                                  allowed_destinations=frozenset()))

    # ------------------------------------------------------------------ capabilities

    def report_binding(self) -> ReportBinding:
        """What a conformance report about this gateway would have to be about."""
        identity = self.state.identity
        availability = self.backend.check_available()
        return ReportBinding(
            gateway_id=identity.gateway_id,
            gateway_version=identity.version,
            backend=str(availability.backend or ""),
            image_digest=str(availability.image_digest or ""),
        )

    def measure(self, options=None, now: float | None = None):
        """Run the conformance suite against this backend and keep the result.

        This is what closes the loop. A gate that can refuse but offers no way through is not a
        gate, it is a wall -- and the remediation the refusal names has to be a command that
        really runs and really changes the answer, not a label. `agentnode gateway doctor
        --measure` is this method.

        The report is stored with what it is about, so it cannot later be read as evidence for a
        different gateway, a different image, or a version that has since been upgraded.
        """
        from datetime import datetime, timezone

        from agentnode_sdk.conformance.runner import run_conformance

        stamp = datetime.fromtimestamp(now or time.time(), tz=timezone.utc).isoformat()
        report = run_conformance(self.backend, generated_at=stamp, options=options)
        self.readiness.store(report.to_dict(), self.report_binding(), now)
        return self.readiness_now()

    def readiness_now(self):
        """The current answer to whether this gateway may take work, with its reason."""
        return self.readiness.evaluate(self.report_binding())

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
        availability = self.backend.check_available()
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
            record = RunRecord(
                run_id=run_id,
                job_id=str(entry.get("job_id", "")),
                request_sha256=str(entry.get("request_sha256", "")),
                owner_client_id=str(entry.get("owner_client_id", "")),
            )
            record.state = "interrupted"
            record.refusal = (
                "the gateway restarted while this job was running, so it did not finish. It has "
                "not been started again -- submit it as a new job if you still want it run."
            )
            record.finished_at = time.time()
            self.runs[run_id] = record
            self.ledger.note_state(run_id, "interrupted")

    def require_client(self, token: str) -> str:
        """The token hash for a paired client, or a refusal. No signature involved.

        Reading a run is not submitting one, so it does not carry a signed payload -- but it must
        still prove which client is asking, because a run's output is the output of somebody's
        code. This is the check that the status endpoint previously did not have at all.
        """
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
            mandatory, optional = validate_paths(request.mandatory, request.optional)
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
        return granted, properties, requested_shape, effective_shape, describe_deltas(
            tuple(p for p in narrowed if p in optional), requested_shape, effective_shape)


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
            record.state = "refused"
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

        with self._lock:
            self.runs[request.run_id] = record
        thread = threading.Thread(target=self._run, args=(request, artifact, granted, record),
                                  daemon=True)
        thread.start()
        return record

    def _run(self, request: JobRequest, artifact: bytes, granted, record: RunRecord) -> None:
        from agentnode_sdk.sandbox.composition import network_mode
        from agentnode_sdk.sandbox.types import ProcessSpec

        mode, domains = network_mode(granted)
        egress = None
        if mode == "egress":
            # The same mechanism the local runners already use: an --internal network with no
            # route out, plus a dual-homed CONNECT proxy that is the only way through. The job
            # does not get a filtered internet -- it gets no route at all, and one door.
            #
            # Built here rather than declared: an earlier version refused this case outright and
            # said so honestly, which was right at the time. Running it with open networking
            # instead would have been the silent widening this whole design exists to refuse.
            from agentnode_sdk.sandbox.egress import start_egress_proxy

            try:
                egress = start_egress_proxy(domains)
            except Exception as exc:                          # noqa: BLE001
                record.state = "refused"
                record.refusal = (
                    "the restricted network this job asked for could not be set up, so it was "
                    f"not started: {exc}"
                )
                record.finished_at = time.time()
                return

        record.container_name = f"agentnode-em3c-{record.run_id[:16]}"
        record.state = "running"
        payload = base64.b64encode(artifact).decode("ascii")
        command = list(request.command) or [
            "python", "-c",
            "import base64,sys;exec(base64.b64decode(sys.stdin.read()).decode())",
        ]
        spec = ProcessSpec(
            command=command,
            network=mode,
            egress=egress.spec if egress is not None else None,
            clean_home=True,
            interactive=True,
            name=record.container_name,
        )
        terminal = "refused"
        try:
            if record.cancel_requested.is_set():
                raise _Cancelled()
            # The composed limit, not the requested one. An earlier version passed
            # request.wall_clock_s straight through, so the fold decided the network and the
            # client decided how long its code could run -- the operator ceiling bound one and
            # not the other, and the policy digest could not catch it because the digested value
            # was not the enforced one. EM3C-GATEWAY-0004 found it.
            rc, out, err = self.backend.run_process(
                spec, input_text=payload, timeout=float(granted.limits.wall_clock_s)
            )
            record.exit_code = rc
            record.stdout = out or ""
            record.stderr = err or ""
            terminal = "cancelled" if record.cancel_requested.is_set() else "finished"
        except _Cancelled:
            terminal = "cancelled"
            record.refusal = "cancelled by the client before it started"
        except Exception as exc:                              # noqa: BLE001
            terminal = "refused"
            record.refusal = f"the run could not be completed: {exc}"
        finally:
            if egress is not None:
                # The proxy and its two networks are part of this run. Leaving them behind would
                # leave a route out that nothing is using and nobody is watching.
                from agentnode_sdk.sandbox.egress import stop_egress_proxy

                try:
                    stop_egress_proxy(egress)
                except Exception:                             # noqa: BLE001
                    pass
            record.cleanup_verified = self._verify_gone(record.container_name)
            if record.cleanup_verified and egress is not None:
                # Cleanup means the whole run, not just the container that carried it.
                record.cleanup_verified = self._egress_gone(egress)
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
            # A reader that sees a terminal state must be seeing a complete record.
            record.state = terminal
            self.ledger.note_state(record.run_id, terminal)

    def _containers_named(self, prefix: str) -> tuple[bool, list[str]]:
        """Ask the runtime which containers carry this run's name prefix.

        Returns (the runtime answered, the names). The first element matters: an empty list from a
        command that FAILED is not an empty list of containers, and treating it as one would report
        a container gone because we could not ask. EM-3B-R1 closed exactly that hole in the local
        backend; the same rule applies here.
        """
        import subprocess

        # A backend that ran the container is best placed to say whether it is gone. Where one can
        # answer, ask it; the shell-out below is the fallback for backends that cannot. This also
        # keeps the question honest under test: a stand-in that never started a container was
        # previously interrogated by asking the REAL runtime about a name it had never created, so
        # the unit suite was quietly driving docker and waiting on it.
        asker = getattr(self.backend, "containers_named", None)
        if asker is not None:
            return asker(prefix)

        availability = self.backend.check_available()
        runtime = availability.backend
        if not runtime or runtime == "none" or not prefix:
            return False, []
        try:
            listed = subprocess.run(
                [runtime, "ps", "-a", "--filter", f"name={prefix}", "--format", "{{.Names}}"],
                capture_output=True, text=True, timeout=30,
            )
        except Exception:                                     # noqa: BLE001
            return False, []
        if listed.returncode != 0:
            return False, []
        return True, [n for n in listed.stdout.split() if n.startswith(prefix)]

    def _egress_gone(self, handle) -> bool | None:
        """Whether this run's proxy and its two networks are gone. None when unaskable.

        Separate from the container check because they are separate objects: a container can be
        removed while the network it sat on stays, and a leftover network with a proxy on it is a
        route out that nothing is using and nobody is watching.
        """
        import subprocess

        availability = self.backend.check_available()
        runtime = availability.backend
        if not runtime or runtime == "none":
            return None
        # A container is listed with .Names and a network with .Name. Asking for the wrong one
        # makes the runtime fail the template rather than answer, which came back as "could not
        # ask" -- unknown rather than a false yes, but still blind.
        for kind, field, name in (("container", "{{.Names}}", handle.proxy_name),
                                  ("network", "{{.Name}}", handle.int_net),
                                  ("network", "{{.Name}}", handle.ext_net)):
            try:
                listed = subprocess.run(
                    [runtime, kind, "ls", "--filter", f"name={name}", "--format", field],
                    capture_output=True, text=True, timeout=30,
                )
            except Exception:                                 # noqa: BLE001
                return None
            if listed.returncode != 0:
                return None                                   # could not ask is not "gone"
            if any(line.strip() == name for line in listed.stdout.splitlines()):
                return False
        return True

    def _verify_gone(self, container_name: str) -> bool | None:
        """Absence has to be stated by the runtime, not inferred from a command that failed.

        The prefix, not the exact name: the backend gives every run its own generated identity
        (`<name>-<suffix>`), so the name this service chose is a PREFIX of the container that
        actually ran. An earlier version of this method asked about the bare name, which matched
        nothing -- so a cancellation removed nothing and a cleanup check reported success about a
        container that was still running. The real container lane caught it.
        """
        # Removal is not instantaneous, and sampling once can catch a container mid-teardown --
        # which would report "not gone" about something that is going. Ask repeatedly until the
        # runtime says it is absent, or until the deadline; a listing that never succeeds stays
        # unknown rather than becoming a "yes".
        # Retrying is only worth anything against a runtime that ANSWERS. If there is no runtime
        # to ask, thirty seconds of asking again produces the same "unknown" it produced at once,
        # and every run pays for it -- which is what happened when the terminal state began waiting
        # on this method. Distinguish the two cases before looping: unknowable now is unknowable
        # later, while "still present" is exactly the thing that changes with time.
        availability = self.backend.check_available()
        if not availability.backend or availability.backend == "none":
            return None

        deadline = time.monotonic() + 30.0
        answered = False
        names: list[str] = ["pending"]
        while time.monotonic() < deadline:
            answered, names = self._containers_named(container_name)
            if answered and not names:
                return True
            time.sleep(0.25)
        if not answered:
            return None
        return not names
    def cancel(self, run_id: str) -> RunRecord | None:
        record = self.runs.get(run_id)
        if record is None:
            return None
        record.cancel_requested.set()
        if record.state in ("finished", "refused", "cancelled"):
            return record
        self._end_container(record)
        return record

    def _end_container(self, record: RunRecord) -> None:
        """Remove this run's container by the identity the backend actually gave it.

        Nothing outside this run's own prefix is ever addressed, and a listing that failed stops
        the removal rather than making it guess at a name.
        """
        import subprocess

        availability = self.backend.check_available()
        runtime = availability.backend
        if not runtime or runtime == "none" or not record.container_name:
            return
        # A cancel can arrive before the container exists: the worker marks the run "running"
        # and then the runtime takes a moment to create it. Removing nothing at that instant and
        # returning would leave the payload to run to its wall clock, which is not what the client
        # asked for -- so wait briefly for it to appear. Bounded, because a container that never
        # appears is a run that never started, and the record already says so.
        deadline = time.monotonic() + 20.0
        names: list[str] = []
        while time.monotonic() < deadline:
            answered, names = self._containers_named(record.container_name)
            if answered and names:
                break
            if record.state in ("finished", "refused", "cancelled") and not names:
                return
            time.sleep(0.25)
        for name in names:
            try:
                subprocess.run([runtime, "rm", "-f", name], capture_output=True, timeout=60)
            except Exception:                                 # noqa: BLE001
                pass


class _Cancelled(Exception):
    pass


# ---------------------------------------------------------------------------- HTTP

class _Handler(BaseHTTPRequestHandler):
    server_version = "agentnode-gateway/1"
    service: GatewayService = None                            # set by make_server

    def log_message(self, fmt, *args):
        pass

    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _token_of(self) -> str:
        """A GET carries its token in a header; the status endpoint is authenticated too."""
        return self.headers.get("X-AgentNode-Token", "") or ""

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            raise ProtocolError("the request body is larger than this gateway accepts")
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")

    def do_GET(self):
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
                return self._send(403, {"error": str(exc)})
            record = self.service.owned_run(run_id, token)
            if record is None:
                return self._send(404, self.service.stamp({"error": "no such run"}))
            return self._send(200, self.service.sign_answer(
                self.service.stamp(record.public()), token))
        return self._send(404, {"error": "no such endpoint"})

    def do_POST(self):
        try:
            body = self._read_json()
        except (ProtocolError, ValueError) as exc:
            return self._send(400, {"error": str(exc)})

        if self.path == "/v1/pair":
            try:
                token = self.service.state.redeem_pairing(
                    body.get("code", ""), client_name=body.get("client_name", "")
                )
            except PairingError as exc:
                return self._send(403, {"error": str(exc)})
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
                return self._send(403, {"error": str(exc)})
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
                return self._send(403, {"error": str(exc)})
            replacement = self.service.state.rotate_token(token)
            if replacement is None:
                return self._send(403, {"error": "this client is not paired with this gateway"})
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
                return self._send(403, {"error": str(exc)})
            # Being paired was never enough to cancel somebody else's run; it only looked like it
            # was, because nothing checked. Ownership is checked BEFORE the cancel, so a stranger
            # cannot stop a run and then be told it was not theirs.
            if self.service.owned_run(run_id, token) is None:
                return self._send(404, self.service.stamp({"error": "no such run"}))
            record = self.service.cancel(run_id)
            if record is None:
                return self._send(404, self.service.stamp({"error": "no such run"}))
            # Signed with the token that authenticated, not with whatever a header claimed. The
            # two were different variables, and only one of them had been checked.
            return self._send(200, self.service.sign_answer(
                self.service.stamp(record.public()), token))

        return self._send(404, {"error": "no such endpoint"})


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
    handler = type("_BoundHandler", (_Handler,), {"service": service})
    server = ThreadingHTTPServer((host, port), handler)
    if context is not None:
        # The certificate was already loaded by check_bind_address above, before this socket
        # existed, so a certificate that will not load stops the gateway rather than leaving it
        # briefly serving in the clear. Wrapping here only attaches it, before serve_forever.
        server.socket = context.wrap_socket(server.socket, server_side=True)
        server.agentnode_tls = True
    else:
        server.agentnode_tls = False
    return server


def new_run_id() -> str:
    return uuid.uuid4().hex
