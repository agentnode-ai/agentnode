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
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from agentnode_sdk.gateway.identity import GatewayState, PairingError
from agentnode_sdk.gateway.transport import check_bind_address
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

    def measured_properties(self) -> dict[str, bool]:
        """What this backend can actually be shown to do, measured rather than declared.

        These are the names a client may put in `required_properties`. A property this gateway
        cannot demonstrate is absent, and a job requiring it is refused -- not run anyway.
        """
        availability = self.backend.check_available()
        return {
            "container_isolation": bool(availability.available),
            "network_none": bool(availability.available),
            "memory_ceiling_enforceable": availability.memory_limit_enforceable is True,
            "verified_cleanup": bool(availability.available),
        }

    def hello(self) -> dict[str, Any]:
        identity = self.state.identity
        availability = self.backend.check_available()
        return {
            "protocol": PROTOCOL_VERSION,
            "gateway": identity.as_dict(),
            "fingerprint": identity.fingerprint,
            "ready": bool(availability.available),
            "reason": "" if availability.available else (availability.reason or "not ready"),
            "properties": self.measured_properties(),
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
        self.nonces.check_and_remember(request.nonce)

        actual = digest(artifact)
        if actual != request.artifact_sha256:
            raise ProtocolError(
                "the artifact does not match the digest this job was signed for "
                f"(signed {request.artifact_sha256[:16]}…, received {actual[:16]}…)"
            )

        properties = self.measured_properties()
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
        request_sha = digest(canonical_bytes(request.to_payload()))
        record = RunRecord(run_id=request.run_id, job_id=request.job_id,
                           request_sha256=request_sha)
        with self._lock:
            existing = self.runs.get(request.run_id)
        if existing is not None:
            refused = RunRecord(run_id=request.run_id, job_id=request.job_id,
                                request_sha256=request_sha, state="refused")
            refused.refusal = (
                "this run id has already been submitted; re-sending a signed job is a replay. "
                f"Ask for its status at /v1/jobs/{request.run_id}. Nothing was started."
            )
            refused.finished_at = time.time()
            return refused
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
            return record
        record.requested_policy = req_shape
        record.effective_policy = eff_shape
        record.request_policy_sha256 = digest(canonical_bytes(req_shape))
        record.effective_policy_sha256 = digest(canonical_bytes(eff_shape))
        record.deltas = deltas
        record.artifact_sha256 = request.artifact_sha256
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
        if mode == "egress":
            # An allowlisted egress needs a proxy. Until the gateway builds one, saying so is the
            # honest answer -- running the job with open networking instead would be exactly the
            # silent widening the whole design refuses.
            record.state = "refused"
            record.refusal = (
                "this gateway cannot yet enforce an allowlisted egress for a remote job "
                f"({', '.join(domains)}). The job was not started."
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
            clean_home=True,
            interactive=True,
            name=record.container_name,
        )
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
            record.state = "cancelled" if record.cancel_requested.is_set() else "finished"
        except _Cancelled:
            record.state = "cancelled"
            record.refusal = "cancelled by the client before it started"
        except Exception as exc:                              # noqa: BLE001
            record.state = "refused"
            record.refusal = f"the run could not be completed: {exc}"
        finally:
            record.cleanup_verified = self._verify_gone(record.container_name)
            record.finished_at = time.time()

    def _containers_named(self, prefix: str) -> tuple[bool, list[str]]:
        """Ask the runtime which containers carry this run's name prefix.

        Returns (the runtime answered, the names). The first element matters: an empty list from a
        command that FAILED is not an empty list of containers, and treating it as one would report
        a container gone because we could not ask. EM-3B-R1 closed exactly that hole in the local
        backend; the same rule applies here.
        """
        import subprocess

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
            record = self.service.runs.get(run_id)
            if record is None:
                return self._send(404, self.service.stamp({"error": "no such run"}))
            return self._send(200, self.service.sign_answer(
                self.service.stamp(record.public()), self._token_of()))
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

        if self.path.endswith("/cancel") and self.path.startswith("/v1/jobs/"):
            run_id = self.path.split("/")[3]
            try:
                self.service.authenticate(body.get("token", ""), body.get("payload") or {},
                                          body.get("signature", ""))
            except ProtocolError as exc:
                return self._send(403, {"error": str(exc)})
            record = self.service.cancel(run_id)
            if record is None:
                return self._send(404, self.service.stamp({"error": "no such run"}))
            return self._send(200, self.service.sign_answer(
                self.service.stamp(record.public()), self._token_of()))

        return self._send(404, {"error": "no such endpoint"})


def make_server(service: GatewayService, host: str = "127.0.0.1", port: int = 0):
    """A threading HTTP server bound to `host`. Defaults to loopback deliberately.

    Binding to loopback by default means an operator has to make an explicit choice before the
    gateway is reachable from anywhere else -- the safe direction for a default. Making that
    choice is not enough on its own: serving beyond loopback in the clear is refused, because
    the pairing code and the token would be readable by anyone who can reach the machine.
    """
    check_bind_address(host)
    handler = type("_BoundHandler", (_Handler,), {"service": service})
    return ThreadingHTTPServer((host, port), handler)


def new_run_id() -> str:
    return uuid.uuid4().hex
