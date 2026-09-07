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
from agentnode_sdk.gateway.protocol import (
    PROTOCOL_VERSION,
    JobRequest,
    NonceCache,
    ProtocolError,
    check_freshness,
    digest,
    policy_digest,
    verify_signature,
)

MAX_BODY_BYTES = 32 * 1024 * 1024


@dataclass
class RunRecord:
    """One run, and enough about it to answer the same question twice with the same answer."""

    run_id: str
    job_id: str
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
            "cleanup_verified": self.cleanup_verified,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class GatewayService:
    """The decisions. Kept apart from HTTP so they can be tested without a socket."""

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

    def admit(self, request: JobRequest, artifact: bytes) -> tuple[Any, dict[str, bool]]:
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

        granted = self.compose(request)
        if policy_digest(granted) != request.policy_sha256:
            raise ProtocolError(
                "the policy this gateway composed is not the one the job was signed for. "
                "The job was not started."
            )
        return granted, properties

    def compose(self, request: JobRequest):
        """The fold, server-side. The operator is above the client, and the job is below both."""
        from agentnode_sdk.sandbox.contract import (
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
            Scope.USER: SandboxPolicy(network=NetworkRules(enabled=True,
                                                           allowed_destinations=None)),
            Scope.PACKAGE: SandboxPolicy(network=asked),
        })

    # ------------------------------------------------------------------ execution

    def submit(self, request: JobRequest, artifact: bytes) -> RunRecord:
        record = RunRecord(run_id=request.run_id, job_id=request.job_id)
        with self._lock:
            existing = self.runs.get(request.run_id)
            if existing is not None:
                # Idempotent: the same run id is the same run, not a second one.
                return existing
            self.runs[request.run_id] = record
        try:
            granted, _ = self.admit(request, artifact)
        except (ProtocolError, Exception) as exc:            # noqa: BLE001 - refusal is an answer
            record.state = "refused"
            record.refusal = str(exc)
            record.finished_at = time.time()
            return record

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
            rc, out, err = self.backend.run_process(
                spec, input_text=payload, timeout=float(request.wall_clock_s)
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

    def _verify_gone(self, container_name: str) -> bool | None:
        """Absence has to be stated by the runtime, not inferred from a command that failed."""
        import re
        import subprocess

        availability = self.backend.check_available()
        runtime = availability.backend
        if not runtime or runtime == "none" or not container_name:
            return None
        try:
            probe = subprocess.run(
                [runtime, "inspect", "--format", "{{.Id}}", container_name],
                capture_output=True, text=True, timeout=30,
            )
        except Exception:                                     # noqa: BLE001
            return None
        if probe.returncode == 0:
            return False
        blob = (probe.stderr or "") + (probe.stdout or "")
        return bool(re.search(r"no such (object|container)", blob, re.IGNORECASE))

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
        import subprocess

        availability = self.backend.check_available()
        runtime = availability.backend
        if not runtime or runtime == "none" or not record.container_name:
            return
        try:
            subprocess.run([runtime, "rm", "-f", record.container_name],
                           capture_output=True, timeout=60)
        except Exception:                                     # noqa: BLE001
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
                return self._send(404, {"error": "no such run"})
            return self._send(200, record.public())
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
            record = self.service.submit(request, artifact)
            return self._send(202 if record.state != "refused" else 409, record.public())

        if self.path.endswith("/cancel") and self.path.startswith("/v1/jobs/"):
            run_id = self.path.split("/")[3]
            try:
                self.service.authenticate(body.get("token", ""), body.get("payload") or {},
                                          body.get("signature", ""))
            except ProtocolError as exc:
                return self._send(403, {"error": str(exc)})
            record = self.service.cancel(run_id)
            if record is None:
                return self._send(404, {"error": "no such run"})
            return self._send(200, record.public())

        return self._send(404, {"error": "no such endpoint"})


def make_server(service: GatewayService, host: str = "127.0.0.1", port: int = 0):
    """A threading HTTP server bound to `host`. Defaults to loopback deliberately.

    Binding to loopback by default means an operator has to make an explicit choice before the
    gateway is reachable from anywhere else -- the safe direction for a default.
    """
    handler = type("_BoundHandler", (_Handler,), {"service": service})
    return ThreadingHTTPServer((host, port), handler)


def new_run_id() -> str:
    return uuid.uuid4().hex
