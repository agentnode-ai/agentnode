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
from agentnode_sdk.gateway.transport import check_client_url

from agentnode_sdk.gateway.protocol import (
    JobRequest,
    new_nonce,
    TERMINAL_STATES,
    canonical_bytes,
    digest,
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


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Follow nothing. A redirect is a second destination the guard never saw.

    EM3C-GATEWAY-0007 found this: `check_client_url` validated the address it was given, and then
    urllib's default opener followed 30x responses on its own. An https or loopback URL could
    redirect to plaintext on another host, and because a redirected request keeps the headers that
    were set on it, the access token went along. The boundary held for exactly one hop.

    The fix is not to re-check each hop. This API has no legitimate redirect -- every endpoint
    answers directly -- so following one is never something a caller asked for, and refusing
    outright leaves no ordering subtlety to get wrong later. It also removes the more interesting
    version of the attack, where a redirect stays on loopback and simply moves the token to a
    different process listening there.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise GatewayClientError(
            "the gateway answered with a redirect to " + str(newurl).split("?", 1)[0] +
            ", and AgentNode does not follow redirects. Your access token is not sent anywhere "
            "except the address you connected to. If the gateway has genuinely moved, connect to "
            "its new address directly."
        )


#: One opener for every request this module makes, so no call site can opt out of the rules by
#: reaching for urlopen. Built once: it holds no per-request state.
_OPENER = urllib.request.build_opener(_RefuseRedirects)


def _post(url: str, body: dict, timeout: float = 30.0) -> tuple[int, dict]:
    check_client_url(url)
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
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


def _get(url: str, timeout: float = 30.0, token: str = "") -> tuple[int, dict]:
    check_client_url(url)
    req = urllib.request.Request(url, method="GET")
    if token:
        req.add_header("X-AgentNode-Token", token)
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
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


def submit(connection: GatewayConnection, artifact: bytes, *, granted=None,
           command: tuple[str, ...] = (), network: str = "none",
           allowed_domains: tuple[str, ...] = (), wall_clock_s: int = 60,
           required_properties: tuple[str, ...] = (), mandatory: tuple[str, ...] = (),
           optional: tuple[str, ...] = (), job_id: str = "",
           run_id: str = "") -> dict[str, Any]:
    """Send a job. What is signed is what the gateway will check it against."""
    import uuid

    from agentnode_sdk.gateway.policy_paths import policy_shape
    from agentnode_sdk.sandbox.contract import Limits, NetworkRules, SandboxPolicy

    # Invariant 1: the client signs the REQUESTED policy -- what this job asks for, on its own.
    # An earlier version digested a composed policy the caller supplied, which meant the client
    # and the gateway were digesting two different things and every submission depended on the
    # caller having composed it identically. What a job asks for is knowable from the job.
    if network == "none":
        _net = NetworkRules(enabled=False, allowed_destinations=frozenset())
    elif network == "unrestricted":
        _net = NetworkRules(enabled=True, allowed_destinations=None)
    else:
        _net = NetworkRules(enabled=True, allowed_destinations=frozenset(allowed_domains))
    _requested = SandboxPolicy(network=_net,
                               limits=Limits(wall_clock_s=max(1, int(wall_clock_s))))
    _request_policy_sha = digest(canonical_bytes(policy_shape(_requested)))

    request = JobRequest(
        job_id=job_id or uuid.uuid4().hex,
        run_id=run_id or uuid.uuid4().hex,
        artifact_sha256=digest(artifact),
        policy_sha256=_request_policy_sha,
        required_properties=tuple(required_properties),
        mandatory=tuple(mandatory),
        optional=tuple(optional),
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
    assert_same_gateway(connection, answer)
    if status not in (200, 202, 409):
        raise GatewayClientError(answer.get("error", f"the gateway answered {status}"))
    return answer


def verify_answer(connection: GatewayConnection, answer: dict[str, Any]) -> dict[str, Any]:
    """Check that this answer belongs to this gateway, job, artifact and policy -- or discard it.

    EM3C-DIGEST-DECISION-0001 chose D1: a result that cannot be verified is not returned as an
    unverified result, it is refused. A job may have run and the caller still gets nothing usable,
    which is the cost; the alternative is an unverified answer being used as though it were
    verified, which is the failure this exists to prevent.
    """
    from agentnode_sdk.gateway.identity import client_token_secret
    from agentnode_sdk.gateway.protocol import response_binding, verify_signature

    binding = answer.get("binding")
    signature = answer.get("signature")
    if not binding or not signature:
        raise GatewayClientError(
            "this answer carries no proof that it came from the gateway you paired with. "
            "It was discarded."
        )
    expected = response_binding(
        gateway_id=binding.get("gateway_id", ""), version=binding.get("version", ""),
        job_id=answer.get("job_id", ""), run_id=answer.get("run_id", ""),
        artifact_sha256=answer.get("artifact_sha256", ""),
        request_policy_sha256=answer.get("request_policy_sha256", ""),
        effective_policy_sha256=answer.get("effective_policy_sha256", ""),
        result=answer.get("stdout", ""),
    )
    if expected != binding:
        raise GatewayClientError(
            "this answer does not describe the job it claims to. It was discarded."
        )
    if connection.gateway_id and binding.get("gateway_id") != connection.gateway_id:
        raise GatewayClientError(
            "this answer came from a different gateway than the one you paired with. "
            "It was discarded."
        )
    if not verify_signature(client_token_secret(connection.token), binding, signature):
        raise GatewayClientError(
            "this answer could not be verified as coming from your gateway. It was discarded."
        )
    return answer


def assert_same_gateway(connection: GatewayConnection, body: dict) -> None:
    """Refuse an answer from a gateway that is not the one this connection was paired with.

    `EM3C-REMOTE-ACCESS-0001` asked for this as defence in depth, independent of which secure
    transport is in front. The transport authenticates the channel; this authenticates the peer at
    the other end of it, using what was learned when the two were introduced. If the address is
    ever pointed somewhere else -- a changed tunnel route, a proxy reconfigured, a name that now
    resolves elsewhere -- the answer stops being accepted rather than being quietly used.

    A connection that pinned nothing cannot check anything, and refusing those would break every
    pairing saved before fingerprints were recorded. But once a connection HAS pinned a value, an
    answer that simply omits the field is refused rather than skipped -- `EM3C-EXTERNAL-0001` found
    that the earlier "compare only when both are present" rule handed an attacker the bypass, since
    the field is theirs to leave out.
    """
    said = body.get("gateway") or {}
    seen_id = str(said.get("gateway_id", "") or body.get("gateway_id", "") or "")
    seen_print = str(body.get("fingerprint", "") or "")

    if connection.gateway_id and not seen_id:
        raise GatewayClientError(
            "the answer from " + connection.base_url + " does not say which gateway it came from, "
            "and this connection is paired with a particular one. Nothing was accepted from it."
        )
    if connection.gateway_id and seen_id and seen_id != connection.gateway_id:
        raise GatewayClientError(
            "the machine answering at " + connection.base_url + " is not the sandbox you paired "
            "with. Nothing was sent to it. If the gateway genuinely moved, connect to it again; "
            "if it did not, something else is answering on that address."
        )
    if connection.fingerprint and not seen_print:
        raise GatewayClientError(
            "the answer from " + connection.base_url + " carries no gateway fingerprint, and this "
            "connection recorded one when it paired. Nothing was accepted from it."
        )
    if connection.fingerprint and seen_print and seen_print != connection.fingerprint:
        raise GatewayClientError(
            "the sandbox at " + connection.base_url + " no longer identifies itself the way it "
            "did when you paired with it. Nothing was sent to it."
        )


def rotate(connection: GatewayConnection) -> GatewayConnection:
    """Trade the current token for a fresh one. Returns the updated connection.

    The old token stops working the moment this returns, so the caller must store the new one
    before doing anything else with it.
    """
    from agentnode_sdk.gateway.identity import client_token_secret

    payload = {"purpose": "rotate", "nonce": new_nonce(), "issued_at": time.time()}
    status, body = _post(f"{connection.base_url}/v1/token/rotate", {
        "token": connection.token,
        "payload": payload,
        "signature": sign(client_token_secret(connection.token), payload),
    })
    # Checked before the body is read at all: taking a credential -- or an error message -- from
    # a machine that is not the one you paired with is how you end up holding somebody else's key
    # and calling it yours.
    assert_same_gateway(connection, body)
    if status != 200 or not body.get("token"):
        raise GatewayClientError(str(body.get("error") or "the gateway would not rotate the token"))
    return GatewayConnection(
        base_url=connection.base_url,
        token=str(body["token"]),
        gateway_id=str((body.get("gateway") or {}).get("gateway_id", connection.gateway_id)),
        version=str((body.get("gateway") or {}).get("version", connection.version)),
        fingerprint=str(body.get("fingerprint", connection.fingerprint)),
    )


def status_of(connection: GatewayConnection, run_id: str, verify: bool = True) -> dict[str, Any]:
    """Idempotent: asking twice gives the same answer, and asking is free."""
    status, body = _get(f"{connection.base_url}/v1/jobs/{run_id}", token=connection.token)
    # BEFORE the status is interpreted. An earlier version checked afterwards, reasoning that a
    # refusal should read as a refusal rather than as a complaint about identity -- but that let a
    # server at a changed address supply the error text a person then read and acted on. Every
    # answer is stamped now, including refusals, so there is nothing to trade off.
    assert_same_gateway(connection, body)
    if status == 404:
        raise GatewayClientError(f"the gateway does not know a run {run_id}")
    if status != 200:
        raise GatewayClientError(body.get("error", f"the gateway answered {status}"))
    return verify_answer(connection, body) if verify else body


def cancel(connection: GatewayConnection, run_id: str) -> dict[str, Any]:
    from agentnode_sdk.gateway.identity import client_token_secret

    payload = {"run_id": run_id, "issued_at": time.time()}
    body = {
        "token": connection.token,
        "payload": payload,
        "signature": sign(client_token_secret(connection.token), payload),
    }
    status, answer = _post(f"{connection.base_url}/v1/jobs/{run_id}/cancel", body)
    assert_same_gateway(connection, answer)
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
        if last.get("state") in TERMINAL_STATES:
            return last
        time.sleep(poll)
    raise GatewayClientError(
        f"the run {run_id} was still {last.get('state', 'unknown')} after {timeout:.0f}s"
    )
