"""The client half: pair with a gateway, send it work, watch it, stop it.

Everything a job needs in order to be refused for the right reason is computed here and signed
before it leaves: the artifact's digest, the digest of the policy the client believes it is running
under, the properties it requires, a nonce and a timestamp.

The gateway recomputes all of it. That is the point of sending it rather than trusting it: a
mismatch between what the client signed and what the gateway derives is a refusal, not a
negotiation.

Standard-library HTTP on purpose -- see `server.py` for why the transport is deliberately left
undecided at this stage.
"""
from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from agentnode_sdk.gateway.protocol import (
    JobRequest,
    digest,
    policy_digest,
    sign,
)


class GatewayClientError(Exception):
    """The gateway refused, or could not be reached. The message is meant to be read by a person."""


@dataclass
class GatewayConnection:
    """A paired gateway: where it is, the token for it, and who it said it was."""

    base_url: str
    token: str
    gateway_id: str = ""
    version: str = ""
    fingerprint: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "base_url": self.base_url,
            "token": self.token,
            "gateway_id": self.gateway_id,
            "version": self.version,
            "fingerprint": self.fingerprint,
        }


def _post(url: str, body: dict, timeout: float = 30.0) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")
        except ValueError:
            return exc.code, {"error": exc.reason}
    except urllib.error.URLError as exc:
        raise GatewayClientError(
            f"could not reach the gateway at {url}: {exc.reason}. Check the address, and that the "
            "gateway is running on that machine."
        ) from exc


def _get(url: str, timeout: float = 30.0) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")
        except ValueError:
            return exc.code, {"error": exc.reason}
    except urllib.error.URLError as exc:
        raise GatewayClientError(
            f"could not reach the gateway at {url}: {exc.reason}."
        ) from exc


def hello(base_url: str) -> dict[str, Any]:
    """What the gateway says it is and what it can do. Unauthenticated on purpose.

    A person needs to be able to ask "is this thing alive and ready" before they have paired with
    it, or the first failure has nothing to say.
    """
    status, body = _get(base_url.rstrip("/") + "/v1/hello")
    if status != 200:
        raise GatewayClientError(body.get("error", f"the gateway answered {status}"))
    return body


def pair(base_url: str, code: str, client_name: str = "") -> GatewayConnection:
    """Exchange a pairing code for a token. The code is spent either way."""
    status, body = _post(base_url.rstrip("/") + "/v1/pair",
                         {"code": code, "client_name": client_name})
    if status != 200:
        raise GatewayClientError(body.get("error", f"pairing failed ({status})"))
    gateway = body.get("gateway") or {}
    return GatewayConnection(
        base_url=base_url.rstrip("/"),
        token=body["token"],
        gateway_id=gateway.get("gateway_id", ""),
        version=gateway.get("version", ""),
        fingerprint=body.get("fingerprint", ""),
    )


def submit(connection: GatewayConnection, artifact: bytes, *, granted,
           command: tuple[str, ...] = (), network: str = "none",
           allowed_domains: tuple[str, ...] = (), wall_clock_s: int = 60,
           required_properties: tuple[str, ...] = (), job_id: str = "",
           run_id: str = "") -> dict[str, Any]:
    """Send a job. What is signed is what the gateway will check it against."""
    import uuid

    from dataclasses import replace as _replace

    # The digest has to cover what will actually be enforced. The gateway folds the requested
    # wall clock in at the lowest scope, so the client narrows its own view the same way before
    # signing -- otherwise a job would be signed for a policy nobody ever runs, and every
    # submission would be refused for a mismatch the client itself created.
    try:
        limits = _replace(granted.limits,
                          wall_clock_s=min(granted.limits.wall_clock_s, int(wall_clock_s)))
        granted = _replace(granted, limits=limits)
    except (AttributeError, TypeError):
        pass

    request = JobRequest(
        job_id=job_id or uuid.uuid4().hex,
        run_id=run_id or uuid.uuid4().hex,
        artifact_sha256=digest(artifact),
        policy_sha256=policy_digest(granted),
        required_properties=tuple(required_properties),
        command=tuple(command),
        network=network,
        allowed_domains=tuple(allowed_domains),
        wall_clock_s=int(wall_clock_s),
    )
    payload = request.to_payload()
    from agentnode_sdk.gateway.identity import client_token_secret

    body = {
        "token": connection.token,
        "payload": payload,
        "signature": sign(client_token_secret(connection.token), payload),
        "artifact_b64": base64.b64encode(artifact).decode("ascii"),
    }
    status, answer = _post(connection.base_url + "/v1/jobs", body)
    if status not in (200, 202, 409):
        raise GatewayClientError(answer.get("error", f"the gateway answered {status}"))
    return answer


def status_of(connection: GatewayConnection, run_id: str) -> dict[str, Any]:
    """Idempotent: asking twice gives the same answer, and asking is free."""
    status, body = _get(f"{connection.base_url}/v1/jobs/{run_id}")
    if status == 404:
        raise GatewayClientError(f"the gateway does not know a run {run_id}")
    if status != 200:
        raise GatewayClientError(body.get("error", f"the gateway answered {status}"))
    return body


def cancel(connection: GatewayConnection, run_id: str) -> dict[str, Any]:
    from agentnode_sdk.gateway.identity import client_token_secret

    payload = {"run_id": run_id, "issued_at": time.time()}
    body = {
        "token": connection.token,
        "payload": payload,
        "signature": sign(client_token_secret(connection.token), payload),
    }
    status, answer = _post(f"{connection.base_url}/v1/jobs/{run_id}/cancel", body)
    if status != 200:
        raise GatewayClientError(answer.get("error", f"the gateway answered {status}"))
    return answer


def wait_for(connection: GatewayConnection, run_id: str, timeout: float = 120.0,
             poll: float = 0.25) -> dict[str, Any]:
    """Poll until the run reaches a terminal state, or give up and say so.

    Polling is honest about what it is: reconnecting mid-run is exactly the same call, because the
    status endpoint carries no session.
    """
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = status_of(connection, run_id)
        if last.get("state") in ("finished", "refused", "cancelled"):
            return last
        time.sleep(poll)
    raise GatewayClientError(
        f"the run {run_id} was still {last.get('state', 'unknown')} after {timeout:.0f}s"
    )
