"""EM-3C: the gateway's admission rules, and the vertical flow over a real socket.

Two kinds of test live here and they prove different things.

The **security tests** run everywhere. They use a stand-in backend, because what they are about is
whether a job is refused *before* anything runs — a tampered artifact, a replayed nonce, a policy
digest that does not match what the gateway composed, a required property the gateway cannot show.
None of those should ever reach a container, so the backend they would have reached is irrelevant
to the question.

The **container test** is gated behind `AGENTNODE_SANDBOX_E2E=1` and needs a real runtime. It is
the one that establishes EM-3C actually works: a real gateway process over a real socket, a real
client, and foreign code running in a real container. A stand-in backend cannot establish that, and
this file does not pretend otherwise — the gated test skips loudly rather than passing quietly.
"""
from __future__ import annotations

import base64
import os
import tempfile
import threading
import time

import pytest

from agentnode_sdk.gateway import client as gc
from agentnode_sdk.gateway.identity import (
    GatewayState,
    client_token_secret,
)
from agentnode_sdk.gateway.protocol import (
    JobRequest,
    digest,
    policy_digest,
    sign,
)
from agentnode_sdk.gateway.server import GatewayService, make_server
from agentnode_sdk.sandbox.types import SandboxAvailability


class StandInBackend:
    """Records what it was asked to run. It never runs anything."""

    def __init__(self, available: bool = True, memory_enforceable: bool = True) -> None:
        self.specs: list = []
        self._available = available
        self._memory = memory_enforceable

    def check_available(self):
        return SandboxAvailability(
            available=self._available, backend="docker" if self._available else "none",
            reason="" if self._available else "no runtime", daemon_ok=self._available,
            image_available=self._available, engine_os="linux",
            memory_limit_enforceable=self._memory,
        )

    def run_process(self, spec, input_text=None, timeout=120.0):
        self.specs.append(spec)
        return 0, "RAN", ""


@pytest.fixture()
def gateway():
    with tempfile.TemporaryDirectory() as td:
        state = GatewayState(td, version="test")
        backend = StandInBackend()
        service = GatewayService(state, backend=backend)
        server = make_server(service, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            yield base, state, service, backend
        finally:
            server.shutdown()


def _paired(base, state):
    return gc.pair(base, state.start_pairing(), client_name="test")


def _granted(service, network="none", domains=(), wall_clock_s=60, token=""):
    """Compose the way the gateway will, INCLUDING the wall clock it will fold in.

    Leaving the wall clock out here signs the job for a policy the gateway would never compose,
    and every submission is then refused for a digest mismatch the caller created. The container
    lane found exactly that once the wall clock started coming from the fold.
    """
    return service.compose(
        type("R", (), {"network": network, "allowed_domains": tuple(domains),
                       "wall_clock_s": wall_clock_s})(),
        token,
    )


# ------------------------------------------------------------------ pairing

class TestPairing:
    def test_a_person_can_mistype_the_code_and_still_pair(self, gateway):
        base, state, _, _ = gateway
        code = state.start_pairing()
        conn = gc.pair(base, "  " + code.lower().replace("-", "") + " ")
        assert conn.token and conn.gateway_id

    def test_a_code_works_once(self, gateway):
        base, state, _, _ = gateway
        code = state.start_pairing()
        gc.pair(base, code)
        with pytest.raises(gc.GatewayClientError):
            gc.pair(base, code)

    def test_a_wrong_code_spends_the_pairing(self, gateway):
        """One guess per code the operator issues, in person, at the machine."""
        base, state, _, _ = gateway
        code = state.start_pairing()
        with pytest.raises(gc.GatewayClientError):
            gc.pair(base, "AAAA-BBBB-CCCC")
        with pytest.raises(gc.GatewayClientError):
            gc.pair(base, code)

    def test_an_expired_code_is_refused(self, gateway):
        base, state, _, _ = gateway
        state.start_pairing(now=time.time() - 3600)
        with pytest.raises(gc.GatewayClientError, match="expired"):
            gc.pair(base, "AAAA-BBBB-CCCC")

    def test_hello_works_before_pairing(self, gateway):
        base, _, _, _ = gateway
        assert gc.hello(base)["protocol"] == "em3c/1"

    def test_the_token_file_never_holds_a_usable_token(self, gateway):
        base, state, _, _ = gateway
        conn = _paired(base, state)
        stored = (state.root / "tokens.json").read_text(encoding="utf-8")
        assert conn.token not in stored


# ------------------------------------------------------------------ refusal before execution

class TestNothingRunsUntilItIsAdmitted:
    def test_an_unpaired_client_is_refused(self, gateway):
        base, state, service, backend = gateway
        conn = gc.GatewayConnection(base_url=base, token="not-a-token")
        with pytest.raises(gc.GatewayClientError, match="not paired"):
            gc.submit(conn, b"print(1)", granted=_granted(service))
        assert backend.specs == []

    def test_a_tampered_artifact_is_refused(self, gateway):
        """The digest is inside the signed envelope; swapping the payload breaks it."""
        base, state, service, backend = gateway
        conn = _paired(base, state)
        request = JobRequest(job_id="j", run_id="r", artifact_sha256=digest(b"honest"),
                             policy_sha256=policy_digest(_granted(service)))
        payload = request.to_payload()
        body = {"token": conn.token, "payload": payload,
                "signature": sign(client_token_secret(conn.token), payload),
                "artifact_b64": base64.b64encode(b"MALICIOUS").decode()}
        status, answer = gc._post(base + "/v1/jobs", body)
        assert answer["state"] == "refused"
        assert "does not match the digest" in answer["refusal"]
        assert backend.specs == []

    def test_a_tampered_signature_is_refused(self, gateway):
        base, state, service, backend = gateway
        conn = _paired(base, state)
        request = JobRequest(job_id="j", run_id="r", artifact_sha256=digest(b"x"),
                             policy_sha256=policy_digest(_granted(service)))
        payload = request.to_payload()
        payload["wall_clock_s"] = 9999          # changed after signing
        body = {"token": conn.token, "payload": payload,
                "signature": sign(client_token_secret(conn.token), request.to_payload()),
                "artifact_b64": base64.b64encode(b"x").decode()}
        status, answer = gc._post(base + "/v1/jobs", body)
        assert status == 403 and "signature" in answer["error"]
        assert backend.specs == []

    def test_a_replayed_request_is_refused(self, gateway):
        base, state, service, backend = gateway
        conn = _paired(base, state)
        request = JobRequest(job_id="j", run_id="r1", artifact_sha256=digest(b"x"),
                             policy_sha256=policy_digest(_granted(service)))
        payload = request.to_payload()
        body = {"token": conn.token, "payload": payload,
                "signature": sign(client_token_secret(conn.token), payload),
                "artifact_b64": base64.b64encode(b"x").decode()}
        first = gc._post(base + "/v1/jobs", body)[1]
        assert first["state"] != "refused"
        # same nonce, different run id: the nonce cache is what has to catch this
        payload2 = dict(payload, run_id="r2")
        body2 = {"token": conn.token, "payload": payload2,
                 "signature": sign(client_token_secret(conn.token), payload2),
                 "artifact_b64": base64.b64encode(b"x").decode()}
        gc.wait_for(conn, "r1", timeout=20)
        calls_after_first = len(backend.specs)
        second = gc._post(base + "/v1/jobs", body2)[1]
        assert second["state"] == "refused" and "replay" in second["refusal"]
        assert len(backend.specs) == calls_after_first, (
            "the replayed request must not have reached a container"
        )

    def test_a_stale_request_is_refused(self, gateway):
        base, state, service, backend = gateway
        conn = _paired(base, state)
        request = JobRequest(job_id="j", run_id="r", artifact_sha256=digest(b"x"),
                             policy_sha256=policy_digest(_granted(service)),
                             issued_at=time.time() - 3600)
        payload = request.to_payload()
        body = {"token": conn.token, "payload": payload,
                "signature": sign(client_token_secret(conn.token), payload),
                "artifact_b64": base64.b64encode(b"x").decode()}
        answer = gc._post(base + "/v1/jobs", body)[1]
        assert answer["state"] == "refused" and "old" in answer["refusal"]
        assert backend.specs == []

    def test_a_future_dated_request_is_refused(self, gateway):
        """Without this, a captured request given a far-future timestamp stays valid forever."""
        base, state, service, backend = gateway
        conn = _paired(base, state)
        request = JobRequest(job_id="j", run_id="r", artifact_sha256=digest(b"x"),
                             policy_sha256=policy_digest(_granted(service)),
                             issued_at=time.time() + 3600)
        payload = request.to_payload()
        body = {"token": conn.token, "payload": payload,
                "signature": sign(client_token_secret(conn.token), payload),
                "artifact_b64": base64.b64encode(b"x").decode()}
        answer = gc._post(base + "/v1/jobs", body)[1]
        assert answer["state"] == "refused"
        assert "future" in answer["refusal"]
        assert backend.specs == [], "a future-dated request must not reach a container"

    def test_a_policy_digest_that_does_not_match_is_refused(self, gateway):
        """A job cannot be replayed against a laxer policy than the one it was signed for."""
        base, state, service, backend = gateway
        conn = _paired(base, state)
        request = JobRequest(job_id="j", run_id="r", artifact_sha256=digest(b"x"),
                             policy_sha256="0" * 64)
        payload = request.to_payload()
        body = {"token": conn.token, "payload": payload,
                "signature": sign(client_token_secret(conn.token), payload),
                "artifact_b64": base64.b64encode(b"x").decode()}
        answer = gc._post(base + "/v1/jobs", body)[1]
        assert answer["state"] == "refused" and "policy" in answer["refusal"]
        assert backend.specs == []

    def test_a_required_property_the_gateway_cannot_show_is_refused(self, gateway):
        base, state, service, backend = gateway
        conn = _paired(base, state)
        answer = gc.submit(conn, b"x", granted=_granted(service),
                           required_properties=("microvm_isolation",))
        assert answer["state"] == "refused"
        assert "cannot provide microvm_isolation" in answer["refusal"]
        assert backend.specs == []

    def test_an_unknown_protocol_version_is_refused(self, gateway):
        base, state, service, backend = gateway
        conn = _paired(base, state)
        request = JobRequest(job_id="j", run_id="r", artifact_sha256=digest(b"x"),
                             policy_sha256=policy_digest(_granted(service)))
        payload = dict(request.to_payload(), protocol="em3c/999")
        body = {"token": conn.token, "payload": payload,
                "signature": sign(client_token_secret(conn.token), payload),
                "artifact_b64": base64.b64encode(b"x").decode()}
        status, answer = gc._post(base + "/v1/jobs", body)
        assert status == 403 and "em3c/1" in answer["error"]
        assert backend.specs == [], "an unknown protocol version must not reach a container"


# ------------------------------------------------------------------ the operator is above the client

class TestTheOperatorPolicyWins:
    def test_a_client_cannot_ask_past_the_operator(self, gateway):
        """The gateway's operator sits at ORGANISATION; a job asking for more is narrowed."""
        base, state, service, _ = gateway
        from agentnode_sdk.sandbox.composition import network_mode

        granted = service.compose(
            type("R", (), {"network": "unrestricted", "allowed_domains": ()})()
        )
        # the default operator policy is network-off, so the fold produces no network at all
        assert network_mode(granted) == ("none", ())

    def test_the_client_scope_bounds_the_job_below_it(self, gateway):
        """operator > client > job, with three distinct levels rather than two and a label."""
        base, state, service, _ = gateway
        from agentnode_sdk.sandbox.composition import network_mode
        from agentnode_sdk.sandbox.contract import NetworkRules, SandboxPolicy

        conn = _paired(base, state)
        operator = SandboxPolicy(network=NetworkRules(
            enabled=True, allowed_destinations=frozenset({"a.example", "b.example",
                                                          "c.example"})))
        svc = GatewayService(state, backend=StandInBackend(), operator_policy=operator)
        # this CLIENT may reach two of the operator's three hosts
        assert state.set_client_allowance(conn.token, ["a.example", "b.example"])
        request = type("R", (), {"network": "restricted",
                                 "allowed_domains": ("a.example", "c.example")})()
        # the job asks for one the client may have and one only the operator allows
        granted = svc.compose(request, conn.token)
        assert network_mode(granted) == ("egress", ("a.example",))

    def test_a_client_with_no_network_allowance_gets_none(self, gateway):
        base, state, _, _ = gateway
        from agentnode_sdk.sandbox.composition import network_mode
        from agentnode_sdk.sandbox.contract import NetworkRules, SandboxPolicy

        conn = _paired(base, state)
        operator = SandboxPolicy(network=NetworkRules(
            enabled=True, allowed_destinations=frozenset({"a.example"})))
        svc = GatewayService(state, backend=StandInBackend(), operator_policy=operator)
        state.set_client_allowance(conn.token, [])
        granted = svc.compose(type("R", (), {"network": "restricted",
                                             "allowed_domains": ("a.example",)})(), conn.token)
        assert network_mode(granted) == ("none", ())

    def test_the_operator_ceiling_binds_the_wall_clock_too(self, gateway):
        """Not only the network. An operator limit must bind every limit, or it binds none.

        EM3C-GATEWAY-0004: the composed policy decided the network while the client's requested
        wall_clock_s went straight to the backend, so a signed request could buy itself a longer
        run than the operator allowed -- and the policy digest could not catch it, because the
        digested value was not the enforced one.
        """
        base, state, service, _ = gateway
        from agentnode_sdk.sandbox.contract import Limits, SandboxPolicy

        conn = _paired(base, state)
        operator = SandboxPolicy(limits=Limits(wall_clock_s=5))
        svc = GatewayService(state, backend=StandInBackend(), operator_policy=operator)
        request = type("R", (), {"network": "none", "allowed_domains": (),
                                 "wall_clock_s": 3600})()
        granted = svc.compose(request, conn.token)
        assert granted.limits.wall_clock_s == 5, (
            "a client asking for an hour under a five-second operator ceiling must get five"
        )

    def test_a_job_cannot_ask_for_more_runtime_than_it_is_given(self, gateway):
        """The enforced timeout is the composed one, observed at the backend call."""
        base, state, service, _ = gateway
        from agentnode_sdk.sandbox.contract import Limits, SandboxPolicy

        conn = _paired(base, state)
        recorded: dict = {}

        class RecordingBackend(StandInBackend):
            def run_process(self, spec, input_text=None, timeout=120.0):
                recorded["timeout"] = timeout
                return super().run_process(spec, input_text=input_text, timeout=timeout)

        svc = GatewayService(state, backend=RecordingBackend(),
                             operator_policy=SandboxPolicy(limits=Limits(wall_clock_s=7)))
        server = make_server(svc, port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            conn2 = gc.pair(url, state.start_pairing())
            granted = svc.compose(type("R", (), {
                "network": "none", "allowed_domains": (), "wall_clock_s": 3600})(), conn2.token)
            answer = gc.submit(conn2, b"x", granted=granted, network="none", wall_clock_s=3600)
            assert answer["state"] != "refused", answer.get("refusal")
            gc.wait_for(conn2, answer["run_id"], timeout=30)
            assert recorded["timeout"] == 7.0, (
                f"the backend was given {recorded['timeout']}s; the operator ceiling is 7"
            )
        finally:
            server.shutdown()

    def test_an_operator_who_allows_a_host_still_bounds_the_job(self, gateway):
        base, state, _, _ = gateway
        from agentnode_sdk.sandbox.composition import network_mode
        from agentnode_sdk.sandbox.contract import NetworkRules, SandboxPolicy

        operator = SandboxPolicy(network=NetworkRules(
            enabled=True, allowed_destinations=frozenset({"api.allowed.example"})))
        service = GatewayService(GatewayState(state.root, "test"), backend=StandInBackend(),
                                 operator_policy=operator)
        granted = service.compose(type("R", (), {
            "network": "restricted",
            "allowed_domains": ("api.allowed.example", "evil.example"),
        })())
        assert network_mode(granted) == ("egress", ("api.allowed.example",))


class TestEveryAnswerNamesTheGatewayThatGaveIt:
    """T-C: an answer a client cannot tie to a build is not a measurement of that build."""

    def test_hello_pair_submit_status_and_cancel_all_carry_the_identity(self, gateway):
        base, state, service, _ = gateway
        conn = _paired(base, state)
        expected = state.identity

        answers = {"hello": gc.hello(base)}
        answers["submit"] = gc.submit(conn, b"x", granted=_granted(service), run_id="stamped")
        gc.wait_for(conn, "stamped", timeout=20)
        answers["status"] = gc.status_of(conn, "stamped")
        answers["cancel"] = gc.cancel(conn, "stamped")

        for name, answer in answers.items():
            assert answer.get("protocol") == "em3c/1", name
            assert answer.get("gateway", {}).get("gateway_id") == expected.gateway_id, name
            assert answer.get("gateway", {}).get("version") == expected.version, name
            assert answer.get("fingerprint") == expected.fingerprint, name

    def test_the_fingerprint_changes_when_the_version_does(self, gateway):
        base, state, _, _ = gateway
        before = state.identity.fingerprint
        state.version = "a-different-build"
        assert state.identity.fingerprint != before
        assert state.identity.gateway_id == GatewayState(state.root, "x").identity.gateway_id


# ------------------------------------------------------------------ idempotence

class TestAskingTwiceGivesTheSameAnswer:
    def test_an_exact_repeat_is_a_replay_and_is_refused(self, gateway):
        """A byte-identical re-POST carries a used nonce. It is a replay like any other."""
        base, state, service, backend = gateway
        conn = _paired(base, state)
        request = JobRequest(job_id="j", run_id="exact", artifact_sha256=digest(b"x"),
                             policy_sha256=policy_digest(_granted(service)))
        payload = request.to_payload()
        body = {"token": conn.token, "payload": payload,
                "signature": sign(client_token_secret(conn.token), payload),
                "artifact_b64": base64.b64encode(b"x").decode()}
        first = gc._post(base + "/v1/jobs", body)[1]
        assert first["state"] != "refused", first
        gc.wait_for(conn, "exact", timeout=20)
        calls = len(backend.specs)
        again = gc._post(base + "/v1/jobs", body)[1]
        assert again["state"] == "refused"
        assert again["stdout"] == "", "a replay must not disclose the original run"
        assert len(backend.specs) == calls

    def test_the_reconnection_path_is_the_status_endpoint(self, gateway):
        """What a client with a dropped connection does, and it needs no re-POST."""
        base, state, service, backend = gateway
        conn = _paired(base, state)
        answer = gc.submit(conn, b"x", granted=_granted(service), run_id="dropped")
        assert answer["state"] != "refused"
        gc.wait_for(conn, "dropped", timeout=20)
        calls = len(backend.specs)
        for _ in range(3):
            again = gc.status_of(conn, "dropped")
            assert again["run_id"] == "dropped" and again["state"] == "finished"
        assert len(backend.specs) == calls

    def test_a_different_request_reusing_a_run_id_is_refused(self, gateway):
        base, state, service, backend = gateway
        conn = _paired(base, state)
        first = JobRequest(job_id="j", run_id="shared", artifact_sha256=digest(b"honest"),
                           policy_sha256=policy_digest(_granted(service)))
        p1 = first.to_payload()
        gc._post(base + "/v1/jobs", {
            "token": conn.token, "payload": p1,
            "signature": sign(client_token_secret(conn.token), p1),
            "artifact_b64": base64.b64encode(b"honest").decode()})
        gc.wait_for(conn, "shared", timeout=20)
        calls = len(backend.specs)
        second = JobRequest(job_id="j", run_id="shared", artifact_sha256=digest(b"other"),
                            policy_sha256=policy_digest(_granted(service)))
        p2 = second.to_payload()
        answer = gc._post(base + "/v1/jobs", {
            "token": conn.token, "payload": p2,
            "signature": sign(client_token_secret(conn.token), p2),
            "artifact_b64": base64.b64encode(b"other").decode()})[1]
        assert answer["state"] == "refused"
        assert answer["stdout"] == "", "the original run must not be disclosed"
        assert len(backend.specs) == calls

    def test_a_refusal_cannot_be_retried_into_an_acceptance(self, gateway):
        base, state, service, backend = gateway
        conn = _paired(base, state)
        for _ in range(2):
            answer = gc.submit(conn, b"x", granted=_granted(service), run_id="refused-once",
                               required_properties=("microvm_isolation",))
            assert answer["state"] == "refused"
        assert backend.specs == []

    def test_status_of_an_unknown_run_says_so(self, gateway):
        base, state, _, _ = gateway
        conn = _paired(base, state)
        with pytest.raises(gc.GatewayClientError, match="does not know"):
            gc.status_of(conn, "never-existed")


# ------------------------------------------------------------------ the real thing

@pytest.mark.skipif(os.environ.get("AGENTNODE_SANDBOX_E2E") != "1",
                    reason="set AGENTNODE_SANDBOX_E2E=1 (needs a container runtime) to run")
class TestTheVerticalFlowForReal:
    """A real gateway, a real socket, a real container, and code that is not ours.

    Everything above can pass with a stand-in backend. This cannot: it starts the gateway with the
    real `ContainerBackend`, and the only way `stdout` carries the marker is if a container ran the
    foreign payload and its output came back over HTTP.
    """

    @pytest.fixture()
    def real_gateway(self):
        from agentnode_sdk.sandbox.container_backend import ContainerBackend

        backend = ContainerBackend()
        if not backend.check_available().available:
            pytest.skip("no container runtime + pinned image available")
        with tempfile.TemporaryDirectory() as td:
            state = GatewayState(td, version="test")
            service = GatewayService(state, backend=backend)
            server = make_server(service, port=0)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                yield base, state, service
            finally:
                server.shutdown()

    def test_foreign_code_runs_in_a_container_and_the_result_comes_back(self, real_gateway):
        """The runtime is asked what it created, so the observation names a real container.

        EM3C-GATEWAY-0002 was right that the earlier version could have come from any backend:
        every assertion was about the gateway's own answer. A watcher now reads the container out
        of the runtime while the job runs, and the test requires the runtime to have named one.
        """
        import subprocess

        base, state, service = real_gateway
        assert gc.hello(base)["ready"] is True
        conn = _paired(base, state)
        runtime = service.backend.check_available().backend
        seen: dict = {}

        def watch():
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline and not seen:
                listed = subprocess.run(
                    [runtime, "ps", "--filter", "name=agentnode-em3c-",
                     "--format", "{{.Names}}|{{.ID}}|{{.Image}}"],
                    capture_output=True, text=True)
                if listed.returncode == 0 and listed.stdout.strip():
                    name, cid, image = listed.stdout.strip().splitlines()[0].split("|")
                    seen.update(name=name, id=cid, image=image)
                    return
                time.sleep(0.05)

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        # The payload stays alive for a moment on purpose. Its work is instant, and a
        # container that exists for under one poll interval is one the watcher can miss --
        # which it did, reporting name=None while the job had plainly run. A few seconds makes
        # the observation reliable without changing what is being observed.
        artifact = (b"import os, time\n"
                    b"print('EM3C-RAN-AS', os.getuid(), flush=True)\n"
                    b"time.sleep(3)\n")
        answer = gc.submit(conn, artifact,
                           granted=_granted(service, wall_clock_s=120, token=conn.token),
                           network="none",
                           required_properties=("container_isolation", "verified_cleanup"),
                           wall_clock_s=120)
        assert answer["state"] != "refused", answer.get("refusal")
        final = gc.wait_for(conn, answer["run_id"], timeout=180)
        # Printed so the LANE OUTPUT carries the observation, not only the fact that an assertion
        # passed. A reviewer reading the log should be able to see the container's own words.
        watcher.join(timeout=5)
        print("")
        print(f"  [observed runtime] the runtime reported a container while the job ran: "
              f"name={seen.get('name')} id={seen.get('id')} image={seen.get('image')}", flush=True)
        print(f"  [observed] state={final['state']} exit={final['exit_code']} "
              f"stdout={final['stdout']!r} cleanup_verified={final['cleanup_verified']} "
              f"gateway={final.get('gateway')} fingerprint={str(final.get('fingerprint'))[:16]}…",
              flush=True)
        assert final["state"] == "finished", final
        assert final["exit_code"] == 0, final
        assert "EM3C-RAN-AS" in final["stdout"], final
        # uid 1000, not root: the container hardening applies to remote jobs too
        assert "EM3C-RAN-AS 1000" in final["stdout"], final
        assert final["cleanup_verified"] is True, "the container must be gone, and shown to be"
        # the runtime itself named a container for this run -- not our own record of one
        assert seen.get("name", "").startswith("agentnode-em3c-"), seen
        assert seen.get("id"), "the runtime reported no container id for this run"

    def test_a_cancelled_run_ends_its_container_and_says_so(self, real_gateway):
        base, state, service = real_gateway
        conn = _paired(base, state)
        artifact = (b"import signal,sys,time\n"
                    b"for n in ('SIGTERM','SIGINT','SIGHUP'):\n"
                    b"    s=getattr(signal,n,None)\n"
                    b"    if s: signal.signal(s, signal.SIG_IGN)\n"
                    b"print('EM3C-IGNORING', flush=True)\n"
                    b"time.sleep(600)\n")
        answer = gc.submit(conn, artifact,
                           granted=_granted(service, wall_clock_s=300, token=conn.token),
                           network="none", wall_clock_s=300)
        assert answer["state"] != "refused", answer.get("refusal")
        run_id = answer["run_id"]
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if gc.status_of(conn, run_id)["state"] == "running":
                break
            time.sleep(0.25)
        gc.cancel(conn, run_id)
        final = gc.wait_for(conn, run_id, timeout=180)
        print("")
        print(f"  [observed] cancelled run: state={final['state']} "
              f"cleanup_verified={final['cleanup_verified']}", flush=True)
        assert final["state"] in ("cancelled", "finished"), final
        assert final["cleanup_verified"] is True, "a cancelled run must leave nothing behind"

    def test_a_run_that_exceeds_its_wall_clock_is_ended(self, real_gateway):
        base, state, service = real_gateway
        conn = _paired(base, state)
        artifact = b"import time\nprint('EM3C-SLEEPING', flush=True)\ntime.sleep(600)\n"
        answer = gc.submit(conn, artifact,
                           granted=_granted(service, wall_clock_s=8, token=conn.token),
                           network="none", wall_clock_s=8)
        assert answer["state"] != "refused", answer.get("refusal")
        final = gc.wait_for(conn, answer["run_id"], timeout=180)
        print("")
        print(f"  [observed] timed-out run: state={final['state']} exit={final['exit_code']} "
              f"cleanup_verified={final['cleanup_verified']}", flush=True)
        assert final["state"] in ("finished", "cancelled"), final
        assert final["exit_code"] != 0, "a timed-out run must not report success"
        assert final["cleanup_verified"] is True
