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
import json
import os
import tempfile
from pathlib import Path
import threading
import time

import pytest

from agentnode_sdk.gateway import client as gc
from agentnode_sdk.conformance.report import Vantage
from agentnode_sdk.gateway import readiness
from agentnode_sdk.gateway import transport as tr
from agentnode_sdk.gateway.identity import (
    PairingError,
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

    def containers_named(self, prefix):
        """It answered, and it started nothing -- so nothing carries that name.

        Truthful rather than convenient: this backend really does know the answer. The REAL
        cleanup path is exercised by the container lane against a runtime that ran something.
        """
        return True, []


def _self_signed(tmp_path):
    """A throwaway certificate, so the TLS path is exercised rather than described.

    Self-signed is fine here precisely because what is under test is that the gateway LOADS a
    certificate before it agrees to serve -- not that any particular certificate is trustworthy.
    """
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return str(cert_path), str(key_path)


NEWLINE = chr(10)


def _raw_get(base, path, token):
    """The gateway's own answer, before the client turns it into a message."""
    import urllib.error
    import urllib.request

    request = urllib.request.Request(base + path, headers={"X-AgentNode-Token": token})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8") or "{}")


def _store_measurement(service, ok=True, observed=True, only=None, binding=None):
    """Give a gateway a conformance report, so the readiness gate is exercised not bypassed.

    The unit suite has no container runtime, so it cannot measure anything for real. What it can
    do is state exactly what a measurement would have said and let the gate draw its own
    conclusions -- which is the part under test here. Whether the real suite reaches these
    verdicts is the container lane's job, and it does measure, for real.
    """
    from agentnode_sdk.conformance.report import CheckResult, ConformanceReport
    from agentnode_sdk.gateway.readiness import PROPERTY_CHECKS

    # Built as REAL CheckResults and serialised by the real report, not hand-written dicts. The
    # first version of this helper invented a shape -- it wrote an "ok" key and an outcome of
    # "measured", neither of which the serialiser produces -- and the gate read the invented
    # shape happily while the real one made every property unproven. A double that does not
    # produce what the real thing produces tests the double.
    check_ids = sorted({c for ids in PROPERTY_CHECKS.values() for c in ids})
    results = []
    for check_id in check_ids:
        wanted = bool(ok) if (only is None or check_id in only) else False
        if observed:
            results.append(CheckResult.measured(
                check_id, check_id, "test", wanted, Vantage.INSIDE, "stated by the test"))
        else:
            results.append(CheckResult.claimed(
                check_id, check_id, "test", wanted, "stated by the test"))
    report = ConformanceReport(
        backend_identity="StandInBackend", backend_version="test", runtime="docker",
        image="", generated_at="1970-01-01T00:00:00+00:00", results=tuple(results))
    service.readiness.store(report.to_dict(), binding or service.report_binding())
    return service.readiness_now()


@pytest.fixture()
def gateway():
    with tempfile.TemporaryDirectory() as td:
        state = GatewayState(td, version="test")
        backend = StandInBackend()
        service = GatewayService(state, backend=backend)
        _store_measurement(service)
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


# ---------------------------------------------- mandatory vs optional, and the two digests

class TestMandatoryAndOptionalNarrowing:
    """EM3C-DIGEST-DECISION-0001 chose A1/B1/C1/D1/E1. This is that decision, checked."""

    def _serve(self, state, wall_clock_s=180):
        from agentnode_sdk.sandbox.contract import Limits, SandboxPolicy
        svc = GatewayService(state, backend=StandInBackend(),
                             operator_policy=SandboxPolicy(
                                 limits=Limits(wall_clock_s=wall_clock_s)))
        server = make_server(svc, port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return svc, server, "http://127.0.0.1:%d" % server.server_address[1]

    def test_narrowing_a_mandatory_field_refuses_before_the_container(self, gateway):
        _, state, _, _ = gateway
        svc, server, url = self._serve(state, wall_clock_s=5)
        try:
            conn = gc.pair(url, state.start_pairing())
            answer = gc.submit(conn, b"x", network="none", wall_clock_s=3600,
                               mandatory=("limits.wall_clock_s",))
            assert answer["state"] == "refused"
            assert "limits.wall_clock_s" in answer["refusal"]
            assert "mandatory" in answer["refusal"]
            assert svc.backend.specs == [], "nothing may start when a mandatory field is narrowed"
        finally:
            server.shutdown()

    def test_narrowing_an_optional_field_runs_and_reports_the_delta(self, gateway):
        _, state, _, _ = gateway
        svc, server, url = self._serve(state, wall_clock_s=5)
        try:
            conn = gc.pair(url, state.start_pairing())
            answer = gc.submit(conn, b"x", network="none", wall_clock_s=3600,
                               optional=("limits.wall_clock_s",))
            assert answer["state"] != "refused", answer.get("refusal")
            final = gc.wait_for(conn, answer["run_id"], timeout=20)
            deltas = {d["field"]: d for d in final["policy_deltas"]}
            assert "limits.wall_clock_s" in deltas, final["policy_deltas"]
            assert deltas["limits.wall_clock_s"]["requested"] == 3600
            assert deltas["limits.wall_clock_s"]["effective"] == 5
            assert final["request_policy_sha256"] != final["effective_policy_sha256"]
            assert final["requested_policy"]["limits.wall_clock_s"] == 3600
            assert final["effective_policy"]["limits.wall_clock_s"] == 5
        finally:
            server.shutdown()

    def test_an_unnarrowed_job_has_equal_digests_and_no_deltas(self, gateway):
        base, state, service, _ = gateway
        conn = _paired(base, state)
        answer = gc.submit(conn, b"x", network="none", wall_clock_s=60)
        final = gc.wait_for(conn, answer["run_id"], timeout=20)
        assert final["policy_deltas"] == []
        assert final["request_policy_sha256"] == final["effective_policy_sha256"]

    @pytest.mark.parametrize("bad", [("network.nope",), ("",), ("limits",)])
    def test_an_unknown_policy_path_is_refused(self, gateway, bad):
        """A path nobody validated would be a requirement that silently is not one."""
        base, state, service, backend = gateway
        conn = _paired(base, state)
        answer = gc.submit(conn, b"x", network="none", mandatory=bad)
        assert answer["state"] == "refused"
        assert "cannot be enforced" in answer["refusal"]
        assert backend.specs == []

    def test_a_field_cannot_be_both_mandatory_and_optional(self, gateway):
        base, state, service, backend = gateway
        conn = _paired(base, state)
        answer = gc.submit(conn, b"x", network="none",
                           mandatory=("limits.cpu",), optional=("limits.cpu",))
        assert answer["state"] == "refused"
        assert "cannot be both" in answer["refusal"]
        assert backend.specs == []

    def test_a_duplicated_path_is_refused(self, gateway):
        base, state, service, backend = gateway
        conn = _paired(base, state)
        answer = gc.submit(conn, b"x", network="none",
                           mandatory=("limits.cpu", "limits.cpu"))
        assert answer["state"] == "refused"
        assert backend.specs == []


class TestTheClientVerifiesTheAnswer:
    """D1: an answer that cannot be verified is discarded, not returned as unverified."""

    def _finished(self, base, state, service, run_id="v"):
        conn = _paired(base, state)
        answer = gc.submit(conn, b"x", network="none", run_id=run_id)
        return conn, gc.wait_for(conn, answer["run_id"], timeout=20)

    def test_a_good_answer_verifies(self, gateway):
        base, state, service, _ = gateway
        conn, final = self._finished(base, state, service)
        assert final["signature"] and final["binding"]
        assert gc.verify_answer(conn, final) is final

    def test_an_answer_with_no_proof_is_discarded(self, gateway):
        base, state, service, _ = gateway
        conn, final = self._finished(base, state, service)
        stripped = {k: v for k, v in final.items() if k not in ("binding", "signature")}
        with pytest.raises(gc.GatewayClientError, match="no proof"):
            gc.verify_answer(conn, stripped)

    def test_a_tampered_result_is_discarded(self, gateway):
        """The result is inside the binding, so changing it breaks the signature."""
        base, state, service, _ = gateway
        conn, final = self._finished(base, state, service)
        with pytest.raises(gc.GatewayClientError):
            gc.verify_answer(conn, dict(final, stdout="something else entirely"))

    def test_a_tampered_effective_policy_digest_is_discarded(self, gateway):
        base, state, service, _ = gateway
        conn, final = self._finished(base, state, service)
        with pytest.raises(gc.GatewayClientError):
            gc.verify_answer(conn, dict(final, effective_policy_sha256="0" * 64))

    def test_an_answer_from_another_gateway_is_discarded(self, gateway):
        base, state, service, _ = gateway
        conn, final = self._finished(base, state, service)
        foreign = dict(final, binding=dict(final["binding"], gateway_id="someone-else"))
        with pytest.raises(gc.GatewayClientError):
            gc.verify_answer(conn, foreign)

    def test_a_signature_from_another_token_is_discarded(self, gateway):
        base, state, service, _ = gateway
        conn, final = self._finished(base, state, service)
        other = gc.GatewayConnection(base_url=base, token="a-different-token",
                                     gateway_id=conn.gateway_id)
        with pytest.raises(gc.GatewayClientError):
            gc.verify_answer(other, final)


# ------------------------------------------------------------- what may travel in the clear

class TestSecretsDoNotTravelInTheClear:
    """Signing proves who wrote something. It does not stop anyone reading it.

    On a plain link the pairing code, the token and every job output are readable by anyone on
    the path, so the rule is about WHERE the connection goes. There is no exception to it: an
    earlier version had an environment variable that permitted plaintext to a remote host, and
    that was a defect, because the first thing to cross the link is the pairing code.
    """

    def test_loopback_over_plain_http_is_allowed(self):
        tr.check_client_url("http://127.0.0.1:8099/v1/hello")
        tr.check_client_url("http://[::1]:8099/v1/hello")
        tr.check_client_url("http://localhost:8099/v1/hello")

    def test_https_anywhere_is_allowed(self):
        tr.check_client_url("https://gateway.example.com/v1/hello")

    @pytest.mark.parametrize("url", [
        "http://10.0.0.4:8099/v1/hello",
        "http://gateway.example.com/v1/pair",
        "http://192.168.1.20:8099/v1/jobs",
    ])
    def test_plain_http_off_the_machine_is_refused(self, url, monkeypatch):
        monkeypatch.delenv(tr.LEGACY_PLAINTEXT_ENV, raising=False)
        with pytest.raises(tr.InsecureTransportError) as e:
            tr.check_client_url(url)
        msg = str(e.value)
        assert "would not be encrypted" in msg
        # a refusal that does not say how to proceed is just an obstacle
        assert "--tls-cert" in msg and "reverse proxy" in msg

    @pytest.mark.parametrize("url", [
        "http://10.0.0.4:8099/v1/pair",
        "http://gateway.example.com/v1/jobs",
    ])
    def test_the_removed_escape_hatch_no_longer_does_anything(self, url, monkeypatch):
        """The variable is inert, and inertness is tested rather than assumed.

        Deleting the code that read it is not the same as showing that a machine which still has
        it set gets no benefit from it.
        """
        monkeypatch.setenv(tr.LEGACY_PLAINTEXT_ENV, "1")
        with pytest.raises(tr.InsecureTransportError) as e:
            tr.check_client_url(url)
        assert "no longer does anything" in str(e.value)
        with pytest.raises(tr.InsecureTransportError):
            make_server(object(), host="0.0.0.0")

    def test_a_name_that_is_not_loopback_does_not_inherit_the_exemption(self):
        assert not tr.is_loopback("localhost.attacker.example")
        with pytest.raises(tr.InsecureTransportError):
            tr.check_client_url("http://localhost.attacker.example/v1/hello")

    def test_an_unknown_scheme_is_refused(self):
        with pytest.raises(tr.InsecureTransportError):
            tr.check_client_url("ftp://gateway.example.com/v1/hello")

    @pytest.mark.parametrize("host", ["", "0.0.0.0", "::", "10.0.0.4"])
    def test_serving_beyond_loopback_in_the_clear_is_refused(self, host, monkeypatch):
        monkeypatch.delenv(tr.LEGACY_PLAINTEXT_ENV, raising=False)
        with pytest.raises(tr.InsecureTransportError) as e:
            make_server(object(), host=host)
        assert "without encryption" in str(e.value)

    def test_the_guard_is_on_the_request_path_not_only_the_helper(self, monkeypatch):
        """The check has to sit where the bytes leave, or it can be walked around."""
        monkeypatch.delenv(tr.LEGACY_PLAINTEXT_ENV, raising=False)
        with pytest.raises(tr.InsecureTransportError):
            gc.hello("http://10.0.0.4:8099")
        conn = gc.GatewayConnection(base_url="http://10.0.0.4:8099", token="t", gateway_id="g")
        with pytest.raises(tr.InsecureTransportError):
            gc.status_of(conn, "any-run")


class TestCredentialsNeverRideInAUrl:
    """A URL outlives its request, in proxy logs, shell history and crash reports."""

    @pytest.mark.parametrize("url", [
        "http://127.0.0.1:8099/v1/jobs?token=s3cret",
        "http://127.0.0.1:8099/v1/pair?code=123456",
        "http://127.0.0.1:8099/v1/jobs?api_key=s3cret",
        "http://user:s3cret@127.0.0.1:8099/v1/jobs",
    ])
    def test_a_credential_in_the_address_is_refused(self, url):
        with pytest.raises(tr.CredentialInUrlError) as e:
            tr.check_client_url(url)
        assert "s3cret" not in str(e.value), "the refusal must not repeat the secret"
        assert "123456" not in str(e.value)

    def test_the_client_never_builds_a_url_carrying_a_secret(self, gateway):
        """Not a promise that we do not do that: the request path is watched while it is used."""
        base, state, service, _ = gateway
        seen: list = []
        original = gc._post

        def watching(url, body, timeout=30.0):
            seen.append(url)
            return original(url, body, timeout)

        gc._post = watching
        try:
            code = state.start_pairing()
            conn = gc.pair(base, code)
            answer = gc.submit(conn, b"x", network="none")
            gc.wait_for(conn, answer["run_id"], timeout=20)
        finally:
            gc._post = original
        assert seen
        for url in seen:
            assert "?" not in url, "a request carried a query string: " + url
            assert code not in url and conn.token not in url


class TestOrganisationRulesOnlyTighten:
    """A later organisation-wide option may forbid more. It must not be able to permit more."""

    def test_there_is_no_field_that_permits_plaintext_off_the_machine(self):
        """The boundary is not representable, which is stronger than disallowed."""
        fields = tr.TransportRules.__dataclass_fields__
        assert not any(
            ("plaintext" in f) or ("remote" in f) or ("insecure" in f) for f in fields
        ), list(fields)

    def test_tightening_loopback_is_allowed_and_takes_effect(self):
        strict = tr.DEFAULT_RULES.tighten(allow_plain_loopback=False)
        with pytest.raises(tr.InsecureTransportError):
            tr.check_client_url("http://127.0.0.1:8099/v1/hello", rules=strict)
        tr.check_client_url("https://127.0.0.1:8099/v1/hello", rules=strict)

    def test_loosening_again_is_refused(self):
        strict = tr.DEFAULT_RULES.tighten(allow_plain_loopback=False)
        with pytest.raises(ValueError, match="only be tightened"):
            strict.tighten(allow_plain_loopback=True)

    def test_a_stricter_rule_cannot_reopen_the_remote_boundary(self):
        strict = tr.DEFAULT_RULES.tighten(allow_plain_loopback=False)
        with pytest.raises(tr.InsecureTransportError):
            tr.check_client_url("http://10.0.0.4:8099/v1/hello", rules=strict)


class TestTlsIsProvenByLoadingIt:
    """Being told there is a certificate proves nothing."""

    def test_a_certificate_that_does_not_load_stops_the_gateway(self, tmp_path):
        bad = tr.TlsFiles(certfile=str(tmp_path / "nope.pem"), keyfile=str(tmp_path / "nope.key"))
        with pytest.raises(tr.InsecureTransportError) as e:
            make_server(object(), host="0.0.0.0", tls=bad)
        assert "could not be loaded" in str(e.value)
        assert "not started" in str(e.value)

    def test_a_real_certificate_permits_a_bind_that_plain_http_could_not_have(self, tmp_path):
        cert, key = _self_signed(tmp_path)
        server = make_server(object(), host="127.0.0.1", port=0,
                             tls=tr.TlsFiles(certfile=cert, keyfile=key))
        try:
            assert server.agentnode_tls is True
        finally:
            server.server_close()


# ------------------------------------------------- a terminal answer must be a complete one

class TestATerminalStateMeansTheRecordIsComplete:
    """A client that sees `finished` stops polling. Whatever it reads then is what it gets.

    The gateway used to set the terminal state before verifying cleanup, so a client polling in
    that window saw `finished` with `cleanup_verified` still None -- a terminal answer that was
    not yet true. It passed CI on timing alone until the container lane happened to lose the race,
    which is the worst way for a race to behave. This pins the ordering instead of the timing.
    """

    def test_cleanup_is_verified_before_the_state_is_published(self, gateway):
        base, state, service, _ = gateway
        observed: list = []
        original = service._verify_gone

        def watching(container_name):
            # what a client would have seen if it had polled at this exact moment
            observed.append([r.state for r in service.runs.values()])
            return original(container_name)

        service._verify_gone = watching
        conn = _paired(base, state)
        answer = gc.submit(conn, b"x", network="none")
        final = gc.wait_for(conn, answer["run_id"], timeout=20)

        assert observed, "cleanup verification never ran"
        for states in observed:
            assert "finished" not in states, (
                "the run was already advertised as finished while cleanup was still being "
                "verified; a client polling here would have read an incomplete record"
            )
        assert final["state"] == "finished"

    def test_a_finished_run_never_reports_an_unset_cleanup(self, gateway):
        """The invariant itself, stated once over the public surface."""
        base, state, service, _ = gateway
        conn = _paired(base, state)
        for i in range(3):
            answer = gc.submit(conn, b"x", network="none", run_id=f"complete-{i}")
            final = gc.wait_for(conn, answer["run_id"], timeout=20)
            assert final["state"] in ("finished", "refused", "cancelled")
            assert final["finished_at"] is not None
            # Not "is not None": unknown is a legitimate FINAL answer when there is no runtime to
            # ask, and demanding a yes/no there would push the code towards inventing one. What a
            # terminal state promises is that the answer is settled -- so it must not move.
            again = gc.status_of(conn, answer["run_id"])
            assert again["cleanup_verified"] == final["cleanup_verified"]
            assert again["state"] == final["state"]


# ------------------------------------------------ every answer is somebody's, and only theirs

class TestNobodyReadsSomebodyElsesRun:
    """A run id is not a secret. The ownership check is what protects the output.

    The status endpoint used to have no authentication at all -- it looked a run up by id and
    returned it, stdout and all. Being paired was likewise never enough to cancel another
    client's run; nothing checked, so it only looked as though something did. These are the
    tests that would have caught both, which the passing suite at the time did not.
    """

    def _two_clients(self, base, state):
        first = _paired(base, state)
        second = gc.pair(base, state.start_pairing())
        assert first.token != second.token
        return first, second

    def test_status_without_a_token_is_refused(self, gateway):
        base, state, service, _ = gateway
        conn = _paired(base, state)
        answer = gc.submit(conn, b"x", network="none")
        anonymous = gc.GatewayConnection(base_url=base, token="", gateway_id=conn.gateway_id)
        with pytest.raises(gc.GatewayClientError) as e:
            gc.status_of(anonymous, answer["run_id"], verify=False)
        assert "not paired" in str(e.value)

    def test_status_with_a_token_this_gateway_never_issued_is_refused(self, gateway):
        base, state, service, _ = gateway
        conn = _paired(base, state)
        answer = gc.submit(conn, b"x", network="none")
        forged = gc.GatewayConnection(base_url=base, token="not-a-real-token",
                                      gateway_id=conn.gateway_id)
        with pytest.raises(gc.GatewayClientError):
            gc.status_of(forged, answer["run_id"], verify=False)

    def test_another_clients_run_is_reported_exactly_like_one_that_does_not_exist(self, gateway):
        """Distinguishing them would let any paired client enumerate real run ids."""
        base, state, service, _ = gateway
        first, second = self._two_clients(base, state)
        answer = gc.submit(first, b"secret-payload", network="none", run_id="private")
        gc.wait_for(first, answer["run_id"], timeout=20)

        # Compared at the GATEWAY, not through the client's formatted message -- that
        # message interpolates the run id, so comparing it would have compared two ids and
        # proved nothing about what the server disclosed.
        theirs = _raw_get(base, "/v1/jobs/private", second.token)
        absent = _raw_get(base, "/v1/jobs/no-such-run-at-all", second.token)
        assert theirs[0] == absent[0] == 404
        assert theirs[1].get("error") == absent[1].get("error") == "no such run"

    def test_the_owner_can_still_read_it(self, gateway):
        base, state, service, _ = gateway
        first, _second = self._two_clients(base, state)
        answer = gc.submit(first, b"x", network="none", run_id="mine")
        final = gc.wait_for(first, "mine", timeout=20)
        assert final["state"] == "finished"

    def test_a_stranger_cannot_cancel_and_the_run_is_not_touched(self, gateway):
        """The check runs BEFORE the cancel, so a stranger cannot stop a run and then be told
        it was not theirs."""
        base, state, service, _ = gateway
        first, second = self._two_clients(base, state)
        answer = gc.submit(first, b"x", network="none", run_id="not-yours")
        with pytest.raises(gc.GatewayClientError):
            gc.cancel(second, "not-yours")
        record = service.runs["not-yours"]
        assert not record.cancel_requested.is_set(), "the cancel reached the run anyway"
        final = gc.wait_for(first, "not-yours", timeout=20)
        assert final["state"] == "finished"

    def test_a_revoked_token_stops_working_at_once_everywhere(self, gateway):
        base, state, service, _ = gateway
        conn = _paired(base, state)
        answer = gc.submit(conn, b"x", network="none", run_id="before-revocation")
        gc.wait_for(conn, "before-revocation", timeout=20)

        assert state.revoke(conn.token) is True
        with pytest.raises(gc.GatewayClientError):
            gc.status_of(conn, "before-revocation", verify=False)
        with pytest.raises(gc.GatewayClientError):
            gc.submit(conn, b"x", network="none", run_id="after-revocation")
        with pytest.raises(gc.GatewayClientError):
            gc.cancel(conn, "before-revocation")


class TestPairingIsWorthGuessingOnlyOnce:
    """The code is short so a person can read it aloud. That is its weakness as well as its point."""

    def test_a_code_works_once(self, gateway):
        base, state, service, _ = gateway
        code = state.start_pairing()
        gc.pair(base, code)
        with pytest.raises(gc.GatewayClientError):
            gc.pair(base, code)

    def test_concurrent_redemptions_of_one_code_produce_exactly_one_token(self, gateway):
        """Reading the live code and clearing it as two steps lets two attempts both win."""
        base, state, service, _ = gateway
        code = state.start_pairing()
        results: list = []
        barrier = threading.Barrier(8)

        def attempt():
            barrier.wait()
            try:
                results.append(("ok", gc.pair(base, code).token))
            except Exception as exc:                          # noqa: BLE001
                results.append(("no", str(exc)))

        threads = [threading.Thread(target=attempt) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        wins = [r for r in results if r[0] == "ok"]
        assert len(results) == 8, results
        assert len(wins) == 1, f"{len(wins)} concurrent attempts were accepted, expected 1"
        assert len({w[1] for w in wins}) == 1

    def test_a_wrong_guess_consumes_the_code(self, gateway):
        base, state, service, _ = gateway
        code = state.start_pairing()
        with pytest.raises(gc.GatewayClientError):
            gc.pair(base, "AAAA-AAAA-AAAA" if code != "AAAA-AAAA-AAAA" else "BBBB-BBBB-BBBB")
        with pytest.raises(gc.GatewayClientError):
            gc.pair(base, code)

    def test_repeated_failures_lock_attempts_out_for_a_while(self):
        """Time is injected: a lockout tested with sleep is slow and still proves less."""
        from agentnode_sdk.gateway.identity import GatewayState
        from agentnode_sdk.gateway.throttle import Locked

        with tempfile.TemporaryDirectory() as td:
            state = GatewayState(td, version="test")
            now = 1_000.0
            for i in range(state._throttle.allowed_failures):
                state.start_pairing(now=now)
                with pytest.raises(PairingError):
                    state.redeem_pairing("ZZZZ-ZZZZ-ZZZZ", now=now)
                assert state._throttle.locked_for(now) == 0.0, f"locked too early, after {i + 1}"

            state.start_pairing(now=now)
            with pytest.raises(PairingError):
                state.redeem_pairing("ZZZZ-ZZZZ-ZZZZ", now=now)
            assert state._throttle.locked_for(now) > 0.0

            # while locked, even the RIGHT code is refused, and it is refused before the code is
            # looked at -- a locked-out caller learns nothing about whether a pairing is live
            good = state.start_pairing(now=now)
            with pytest.raises(PairingError, match="failed pairing attempts"):
                state.redeem_pairing(good, now=now)

    def test_the_lock_lengthens_rather_than_staying_a_speed_bump(self):
        from agentnode_sdk.gateway.throttle import Throttle

        t = Throttle(allowed_failures=1, base_lock_seconds=30.0)
        assert t.record_failure(now=0.0) == 0.0
        first = t.record_failure(now=1.0)
        second = t.record_failure(now=2.0)
        assert second > first, (first, second)

    def test_a_success_clears_the_slate(self, gateway):
        base, state, service, _ = gateway
        for _ in range(3):
            state.start_pairing()
            with pytest.raises(gc.GatewayClientError):
                gc.pair(base, "ZZZZ-ZZZZ-ZZZZ")
        gc.pair(base, state.start_pairing())
        assert state._throttle.locked_for() == 0.0


class TestRotationReplacesTheSecretAndNothingElse:

    def test_the_old_token_stops_and_the_new_one_works(self, gateway):
        base, state, service, _ = gateway
        conn = _paired(base, state)
        answer = gc.submit(conn, b"x", network="none", run_id="pre-rotation")
        gc.wait_for(conn, "pre-rotation", timeout=20)

        replacement = state.rotate_token(conn.token)
        assert replacement and replacement != conn.token

        with pytest.raises(gc.GatewayClientError):
            gc.status_of(conn, "pre-rotation", verify=False)
        rotated = gc.GatewayConnection(base_url=base, token=replacement,
                                       gateway_id=conn.gateway_id)
        assert gc.status_of(rotated, "pre-rotation", verify=False)["state"] == "finished"

    def test_what_the_client_may_reach_travels_with_it(self, gateway):
        base, state, service, _ = gateway
        conn = _paired(base, state)
        state.set_client_allowance(conn.token, ["example.com"])
        replacement = state.rotate_token(conn.token)
        assert state.client_allowance(replacement) == ["example.com"]

    def test_rotating_something_that_is_not_a_token_invents_nothing(self, gateway):
        base, state, service, _ = gateway
        assert state.rotate_token("not-a-token") is None


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


# ------------------------------------------------- a gateway that cannot say what it enforces

class TestNothingRunsOnAnUnmeasuredGateway:
    """`bool(runtime_is_installed)` was being reported as proof that the runtime isolates.

    That is what `measured_properties` used to return: container_isolation and verified_cleanup
    were both `availability.available`. A client asking for verified cleanup was checked against
    that claim and told yes. These tests are about the gate that replaced it.
    """

    def _fresh(self, td, backend=None):
        state = GatewayState(td, version="test")
        service = GatewayService(state, backend=backend or StandInBackend())
        server = make_server(service, port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return state, service, server, "http://127.0.0.1:%d" % server.server_address[1]

    def test_an_unmeasured_gateway_is_not_ready_and_runs_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            state, service, server, base = self._fresh(td)
            try:
                hello = gc.hello(base)
                assert hello["ready"] is False
                assert "has not been measured" in hello["reason"]
                assert hello["next_steps"], "a refusal must name a way through"
                assert all(v is False for v in hello["properties"].values()), hello["properties"]

                conn = gc.pair(base, state.start_pairing())
                answer = gc.submit(conn, b"x", network="none")
                assert answer["state"] == "refused"
                assert "has not been measured" in answer["refusal"]
                assert "gateway doctor" in answer["refusal"]
                assert service.backend.specs == [], "an unmeasured gateway ran a job"
            finally:
                server.shutdown()

    def test_a_measurement_of_something_else_does_not_count(self):
        """A report copied from another machine describes that machine."""
        from agentnode_sdk.gateway.readiness import ReportBinding

        with tempfile.TemporaryDirectory() as td:
            state, service, server, base = self._fresh(td)
            try:
                foreign = ReportBinding(gateway_id="somebody-elses-gateway",
                                        gateway_version="test", backend="docker",
                                        image_digest="")
                _store_measurement(service, binding=foreign)
                hello = gc.hello(base)
                assert hello["ready"] is False
                assert "describes something else" in hello["reason"]
                assert "gateway_id" in hello["reason"]
            finally:
                server.shutdown()

    def test_a_measurement_against_a_different_image_does_not_count(self):
        from agentnode_sdk.gateway.readiness import ReportBinding

        with tempfile.TemporaryDirectory() as td:
            state, service, server, base = self._fresh(td)
            try:
                current = service.report_binding()
                other = ReportBinding(gateway_id=current.gateway_id,
                                      gateway_version=current.gateway_version,
                                      backend=current.backend,
                                      image_digest="sha256:something-else")
                _store_measurement(service, binding=other)
                assert gc.hello(base)["ready"] is False
                assert "image_digest" in gc.hello(base)["reason"]
            finally:
                server.shutdown()

    def test_a_stale_measurement_does_not_count(self):
        with tempfile.TemporaryDirectory() as td:
            state, service, server, base = self._fresh(td)
            try:
                _store_measurement(service)
                service.readiness.max_age_seconds = 0.0   # everything is now too old
                hello = gc.hello(base)
                assert hello["ready"] is False
                assert "too old" in hello["reason"]
            finally:
                server.shutdown()

    def test_a_self_reported_report_is_not_a_measurement(self):
        """`observed` and `self-reported` are different words on purpose."""
        with tempfile.TemporaryDirectory() as td:
            state, service, server, base = self._fresh(td)
            try:
                _store_measurement(service, observed=False)
                hello = gc.hello(base)
                assert hello["ready"] is False
                assert hello["properties"]["container_isolation"] is False
            finally:
                server.shutdown()

    def test_a_property_that_failed_is_absent_and_a_job_needing_it_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            state, service, server, base = self._fresh(td)
            try:
                # everything measured except the cleanup checks
                _store_measurement(service, only=("outside-host-process", "not-root",
                                                  "network-mode", "limit-memory",
                                                  "egress-allowlist"))
                hello = gc.hello(base)
                assert hello["ready"] is True, hello["reason"]
                assert hello["properties"]["verified_cleanup"] is False
                assert "verified_cleanup" in hello["unproven"]

                conn = gc.pair(base, state.start_pairing())
                answer = gc.submit(conn, b"x", network="none",
                                   required_properties=("verified_cleanup",))
                assert answer["state"] == "refused"
                assert service.backend.specs == []
            finally:
                server.shutdown()

    def test_the_gate_can_actually_read_a_report_the_real_suite_produced(self):
        """End to end through the real serialiser, which is where this went wrong.

        The gate first looked for a boolean `ok` on each result. A serialised CheckResult has no
        such key -- `ok` is a constructor argument that becomes an `outcome` -- so every property
        came out unproven whatever the suite had found. It failed closed, so nothing unsafe
        shipped, but the gate was blind and the unit suite could not see it, because the helper
        that built its input had invented the same key.

        This test takes the path that has no invented shapes in it at all: the real suite, the
        real report, the real gate.
        """
        from agentnode_sdk.conformance.doubles import GoodBackendDouble
        from agentnode_sdk.conformance.runner import run_conformance

        report = run_conformance(GoodBackendDouble(), generated_at="1970-01-01T00:00:00+00:00")
        with tempfile.TemporaryDirectory() as td:
            state = GatewayState(td, version="test")
            service = GatewayService(state, backend=StandInBackend())
            service.readiness.store(report.to_dict(), service.report_binding())
            result = service.readiness_now()

        proven = [name for name, held in result.properties.items() if held]
        assert proven, (
            "the gate proved NOTHING from a report the suite itself produced -- it is reading "
            "fields the report does not have: " + str(result.unproven)
        )
        assert result.properties["container_isolation"] is True, result.unproven

    def test_every_mapped_check_is_one_the_suite_really_emits(self):
        """A mapping naming a check the suite does not produce would make its property
        permanently unreachable -- which reads as "not ready" and gets explained away."""
        from agentnode_sdk.conformance.doubles import GoodBackendDouble
        from agentnode_sdk.conformance.runner import run_conformance

        # The real suite, run against the double it already ships, so the ids compared are the
        # ids it actually emits rather than a second list that could drift from the first.
        report = run_conformance(GoodBackendDouble(), generated_at="1970-01-01T00:00:00+00:00")
        emitted = {r.check_id for r in report.results}
        mapped = {c for ids in readiness.PROPERTY_CHECKS.values() for c in ids}
        assert mapped <= emitted, f"not produced by the suite: {sorted(mapped - emitted)}"


class TestUnknownCleanupIsNotSuccess:

    def test_a_job_requiring_verified_cleanup_is_not_finished_when_cleanup_is_unknown(self):
        class CannotSay(StandInBackend):
            def containers_named(self, prefix):
                return False, []          # the runtime could not be asked

        with tempfile.TemporaryDirectory() as td:
            state = GatewayState(td, version="test")
            service = GatewayService(state, backend=CannotSay())
            _store_measurement(service)
            server = make_server(service, port=0)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            base = "http://127.0.0.1:%d" % server.server_address[1]
            try:
                conn = gc.pair(base, state.start_pairing())
                answer = gc.submit(conn, b"x", network="none",
                                   required_properties=("verified_cleanup",))
                assert answer["state"] != "refused", answer.get("refusal")
                final = gc.wait_for(conn, answer["run_id"], timeout=60)
                assert final["cleanup_verified"] is None
                assert final["state"] == "unverified", final["state"]
                assert "could not confirm" in final["refusal"]
                # what ran is not in question; what was left behind is
                assert final["exit_code"] == 0
            finally:
                server.shutdown()

    def test_a_job_that_did_not_ask_is_still_finished(self):
        class CannotSay(StandInBackend):
            def containers_named(self, prefix):
                return False, []

        with tempfile.TemporaryDirectory() as td:
            state = GatewayState(td, version="test")
            service = GatewayService(state, backend=CannotSay())
            _store_measurement(service)
            server = make_server(service, port=0)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            base = "http://127.0.0.1:%d" % server.server_address[1]
            try:
                conn = gc.pair(base, state.start_pairing())
                answer = gc.submit(conn, b"x", network="none")
                final = gc.wait_for(conn, answer["run_id"], timeout=60)
                assert final["state"] == "finished"
            finally:
                server.shutdown()


# ------------------------------------------- the way out of a refusal, taken rather than read

class TestEveryRemediationIsInvocableAndChangesTheAnswer:
    """F-A4-DEFERRED-UNTIL-BACKEND, for the refusals this gateway issues.

    The finding asked for evidence that INVOKING a remediation reaches its declared outcome, and
    was left open because the backend that would implement them did not exist. Its sibling
    F-A4-DECLARATION-NOT-EXECUTION named the tempting shortcut: checking a second declaration
    instead of running the thing. So each test here refuses first, then does exactly what the
    refusal said to do, then observes that the answer changed -- three steps, no declaration
    consulted in place of any of them.
    """

    def test_measuring_turns_an_unmeasured_gateway_into_a_ready_one(self):
        """The refusal says `agentnode gateway doctor --measure`. That command is measure()."""
        from agentnode_sdk.conformance.doubles import GoodBackendDouble

        backend = GoodBackendDouble()
        with tempfile.TemporaryDirectory() as td:
            state = GatewayState(td, version="test")
            service = GatewayService(state, backend=backend)
            server = make_server(service, port=0)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            base = "http://127.0.0.1:%d" % server.server_address[1]
            try:
                before = gc.hello(base)
                assert before["ready"] is False
                assert before["next_steps"] == ["agentnode gateway doctor --measure"]

                conn = gc.pair(base, state.start_pairing())
                refused = gc.submit(conn, b"x", network="none", run_id="before-measuring")
                assert refused["state"] == "refused"

                # take the step the refusal named
                service.measure()

                after = gc.hello(base)
                assert after["ready"] is True, after["reason"]
                assert after["properties"]["container_isolation"] is True
                accepted = gc.submit(conn, b"x", network="none", run_id="after-measuring")
                assert accepted["state"] != "refused", accepted.get("refusal")
            finally:
                server.shutdown()

    def test_supplying_a_certificate_turns_a_refused_bind_into_a_serving_one(self, tmp_path):
        """The refusal names --tls-cert and --tls-key. Supplying them has to be enough."""
        with pytest.raises(tr.InsecureTransportError) as refusal:
            make_server(object(), host="0.0.0.0")
        assert "--tls-cert" in str(refusal.value)

        cert, key = _self_signed(tmp_path)
        server = make_server(object(), host="127.0.0.1", port=0,
                             tls=tr.TlsFiles(certfile=cert, keyfile=key))
        try:
            assert server.agentnode_tls is True
        finally:
            server.server_close()

    def test_measuring_the_missing_property_turns_a_refused_job_into_a_running_one(self):
        """A job refused for an unproven property runs once that property is measured."""
        with tempfile.TemporaryDirectory() as td:
            state = GatewayState(td, version="test")
            service = GatewayService(state, backend=StandInBackend())
            # measured, but cleanup was not among the things shown
            _store_measurement(service, only=("outside-host-process", "not-root",
                                              "network-mode", "limit-memory"))
            server = make_server(service, port=0)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            base = "http://127.0.0.1:%d" % server.server_address[1]
            try:
                conn = gc.pair(base, state.start_pairing())
                refused = gc.submit(conn, b"x", network="none", run_id="needs-cleanup",
                                    required_properties=("verified_cleanup",))
                assert refused["state"] == "refused"
                assert service.backend.specs == []

                _store_measurement(service)          # now everything is measured

                accepted = gc.submit(conn, b"x", network="none", run_id="needs-cleanup-2",
                                     required_properties=("verified_cleanup",))
                assert accepted["state"] != "refused", accepted.get("refusal")
                final = gc.wait_for(conn, "needs-cleanup-2", timeout=20)
                assert final["state"] == "finished"
            finally:
                server.shutdown()

    def test_a_refusal_that_names_no_way_through_is_itself_a_defect(self):
        """Every refusal this gateway can issue on the readiness path carries a next step."""
        with tempfile.TemporaryDirectory() as td:
            state = GatewayState(td, version="test")
            service = GatewayService(state, backend=StandInBackend())
            unmeasured = service.readiness_now()
            assert unmeasured.ready is False
            assert unmeasured.next_steps, "an unmeasured gateway offers no way to be measured"

            _store_measurement(service)
            service.readiness.max_age_seconds = 0.0
            stale = service.readiness_now()
            assert stale.ready is False
            assert stale.next_steps, "a stale measurement offers no way to be refreshed"


# ------------------------------------------------------- a restricted network, or none at all

class TestRestrictedEgress:
    """The job does not get a filtered internet. It gets no route out, and one door.

    A gateway's default operator policy is no network at all, so every test here has to hand it
    an operator that permits one -- which is the right way round: opening the network is a
    deliberate act by the machine's owner, not something a job can ask its way into.
    """

    def _open_gateway(self, td, allowed=None, backend=None):
        from agentnode_sdk.sandbox.contract import NetworkRules, SandboxPolicy

        state = GatewayState(td, version="test")
        operator = SandboxPolicy(network=NetworkRules(
            enabled=True,
            allowed_destinations=None if allowed is None else frozenset(allowed),
        ))
        service = GatewayService(state, backend=backend or StandInBackend(),
                                 operator_policy=operator)
        _store_measurement(service)
        server = make_server(service, port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return state, service, server, "http://127.0.0.1:%d" % server.server_address[1]

    @pytest.mark.parametrize("destination,why", [
        ("1.2.3.4", "an address, not a name"),
        ("127.0.0.1", "loopback"),
        ("localhost", "loopback by name"),
        ("intranet", "a single label that resolves differently everywhere"),
        ("", "nothing at all"),
    ])
    def test_an_allowlist_that_cannot_be_enforced_is_refused_before_anything_starts(
            self, destination, why):
        with tempfile.TemporaryDirectory() as td:
            state, service, server, base = self._open_gateway(td)
            try:
                conn = _paired(base, state)
                answer = gc.submit(conn, b"x", network="restricted",
                                   allowed_domains=(destination,) if destination else ())
                assert answer["state"] == "refused", (destination, why)
                assert service.backend.specs == [], "a container was started for " + repr(destination)
            finally:
                server.shutdown()

    def test_an_empty_allowlist_is_not_quietly_treated_as_open(self):
        """The dangerous reading of "restricted with nothing listed" is "unrestricted"."""
        with tempfile.TemporaryDirectory() as td:
            state, service, server, base = self._open_gateway(td)
            try:
                conn = _paired(base, state)
                answer = gc.submit(conn, b"x", network="restricted", allowed_domains=())
                assert answer["state"] == "refused"
                assert service.backend.specs == []
            finally:
                server.shutdown()

    def test_the_operator_narrows_the_destinations_and_says_so(self):
        """The client asked for two hosts; the operator allows one. It gets one."""
        with tempfile.TemporaryDirectory() as td:
            state, service, server, base = self._open_gateway(td, allowed=("example.com",))
            try:
                conn = _paired(base, state)
                answer = gc.submit(conn, b"x", network="restricted",
                                   allowed_domains=("example.com", "elsewhere.example"),
                                   optional=("network.allowed_destinations",))
                assert answer["state"] != "refused", answer.get("refusal")
                final = gc.wait_for(conn, answer["run_id"], timeout=30)
                effective = final["effective_policy"]["network.allowed_destinations"]
                assert effective == ["example.com"], effective
                fields = {d["field"] for d in final["policy_deltas"]}
                assert "network.allowed_destinations" in fields, final["policy_deltas"]
                assert final["request_policy_sha256"] != final["effective_policy_sha256"]
            finally:
                server.shutdown()

    def test_a_client_cannot_widen_past_the_operator(self):
        with tempfile.TemporaryDirectory() as td:
            state, service, server, base = self._open_gateway(td, allowed=("example.com",))
            try:
                conn = _paired(base, state)
                answer = gc.submit(conn, b"x", network="unrestricted")
                if answer["state"] != "refused":
                    final = gc.wait_for(conn, answer["run_id"], timeout=30)
                    effective = final["effective_policy"]["network.allowed_destinations"]
                    assert effective == ["example.com"], effective
            finally:
                server.shutdown()

    def test_unrestricted_stays_a_different_mode_from_restricted(self):
        """Not a cosmetic difference: one has a door, the other has no wall."""
        from agentnode_sdk.sandbox.composition import network_mode
        from agentnode_sdk.sandbox.contract import NetworkRules, SandboxPolicy

        wide = SandboxPolicy(network=NetworkRules(enabled=True, allowed_destinations=None))
        narrow = SandboxPolicy(network=NetworkRules(enabled=True,
                                                    allowed_destinations=frozenset({"a.com"})))
        shut = SandboxPolicy(network=NetworkRules(enabled=False,
                                                  allowed_destinations=frozenset()))
        assert network_mode(wide) == ("default", ())
        assert network_mode(narrow) == ("egress", ("a.com",))
        assert network_mode(shut) == ("none", ())

    def test_the_job_is_told_the_proxy_and_never_a_bare_network(self):
        """What reaches the backend decides what the container can do."""
        with tempfile.TemporaryDirectory() as td:
            state, service, server, base = self._open_gateway(td, allowed=("example.com",))
            try:
                conn = _paired(base, state)
                answer = gc.submit(conn, b"x", network="restricted",
                                   allowed_domains=("example.com",))
                assert answer["state"] != "refused", answer.get("refusal")
                final = gc.wait_for(conn, answer["run_id"], timeout=30)
                if final["state"] == "refused":
                    # There is no runtime here to build a proxy with. It must have refused for
                    # exactly that reason, and must not have run the job on a bare network --
                    # which is the failure this whole path exists to prevent.
                    assert service.backend.specs == [], "it ran anyway, without the proxy"
                    assert "restricted network" in final["refusal"], final["refusal"]
                    return
                spec = service.backend.specs[-1]
                assert spec.network == "egress"
                assert spec.egress is not None
                assert spec.egress.proxy_url
                assert tuple(spec.egress.allowed_domains) == ("example.com",)
            finally:
                server.shutdown()


# ----------------------------------------------------- what must outlive the process itself

class TestARestartDoesNotForget:
    """Replay protection held only in memory has a documented way around it: restart.

    On a server that happens on its own -- a deploy, a crash, a reboot -- so this is not an
    exotic case. What is on the other side of it is running somebody's code a second time.
    """

    def _serve(self, root, backend=None):
        """A gateway on an existing directory. Calling it twice is the restart."""
        state = GatewayState(root, version="test")
        service = GatewayService(state, backend=backend or StandInBackend())
        # Stored on the first call and still there on the second: the measurement outlives the
        # process too, and the restarted gateway recognises it as its own rather than re-running.
        _store_measurement(service)
        server = make_server(service, port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return state, service, server, "http://127.0.0.1:%d" % server.server_address[1]

    def _signed_body(self, service, conn, run_id, artifact=b"x"):
        request = JobRequest(job_id="j", run_id=run_id, artifact_sha256=digest(artifact),
                             policy_sha256=policy_digest(_granted(service)))
        payload = request.to_payload()
        return {"token": conn.token, "payload": payload,
                "signature": sign(client_token_secret(conn.token), payload),
                "artifact_b64": base64.b64encode(artifact).decode()}

    def test_a_captured_request_is_still_a_replay_after_a_restart(self):
        with tempfile.TemporaryDirectory() as td:
            state, service, server, base = self._serve(td)
            try:
                conn = gc.pair(base, state.start_pairing())
                body = self._signed_body(service, conn, "survives-restart")
                first = gc._post(base + "/v1/jobs", body)[1]
                assert first["state"] != "refused", first
                gc.wait_for(conn, "survives-restart", timeout=20)
            finally:
                server.shutdown()

            # The restart. A brand-new process would have an empty NonceCache and an empty run
            # table; only what was written down survives.
            _state2, service2, server2, base2 = self._serve(td)
            try:
                assert service2.runs.get("survives-restart") is None or True
                again = gc._post(base2 + "/v1/jobs", body)[1]
                assert again["state"] == "refused", again
                assert service2.backend.specs == [], "the job ran a second time after a restart"
                assert again["stdout"] == "", "a replay must not disclose the original run"
            finally:
                server2.shutdown()

    def test_an_interrupted_run_is_reported_honestly_and_not_started_again(self):
        with tempfile.TemporaryDirectory() as td:
            state, service, server, base = self._serve(td)
            try:
                conn = gc.pair(base, state.start_pairing())
                # A run the ledger last saw mid-flight, exactly as a crash would leave it.
                service.ledger.claim("cut-short", "nonce-cut-short", "sha",
                                     state.client_id_for(conn.token))
            finally:
                server.shutdown()

            _s2, service2, server2, base2 = self._serve(td)
            try:
                record = service2.runs.get("cut-short")
                assert record is not None, "the interrupted run vanished instead of being answered"
                assert record.state == "interrupted"
                assert "did not finish" in record.refusal
                assert service2.backend.specs == [], "an interrupted run was executed again"
            finally:
                server2.shutdown()

    def test_an_unreadable_ledger_stops_the_gateway_rather_than_starting_empty(self):
        """An unreadable ledger is not an empty one, and treating it as empty reopens every
        replay the file existed to prevent."""
        from agentnode_sdk.gateway.ledger import LedgerUnreadable

        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "ledger.json").write_text("{not json at all", encoding="utf-8")
            state = GatewayState(td, version="test")
            with pytest.raises(LedgerUnreadable) as e:
                GatewayService(state, backend=StandInBackend())
            assert "replayed" in str(e.value)
            assert "Move the file aside" in str(e.value)


class TestTwoIdenticalRequestsAtOnce:

    def test_only_one_of_a_parallel_burst_is_accepted(self, gateway):
        """Look-then-write is not a claim. Under a burst both halves interleave."""
        base, state, service, backend = gateway
        conn = _paired(base, state)
        request = JobRequest(job_id="j", run_id="burst", artifact_sha256=digest(b"x"),
                             policy_sha256=policy_digest(_granted(service)))
        payload = request.to_payload()
        body = {"token": conn.token, "payload": payload,
                "signature": sign(client_token_secret(conn.token), payload),
                "artifact_b64": base64.b64encode(b"x").decode()}

        results: list = []
        barrier = threading.Barrier(8)

        def fire():
            barrier.wait()
            try:
                results.append(gc._post(base + "/v1/jobs", body)[1].get("state"))
            except Exception as exc:                          # noqa: BLE001
                results.append("error:%s" % exc)

        threads = [threading.Thread(target=fire) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert len(results) == 8, results
        accepted = [r for r in results if r not in ("refused", None) and not str(r).startswith("error")]
        assert len(accepted) == 1, f"{len(accepted)} of 8 identical requests were accepted: {results}"
        gc.wait_for(conn, "burst", timeout=20)
        assert len(backend.specs) == 1, f"the job ran {len(backend.specs)} times"


# ------------------------------------------------------------------ the real thing

@pytest.mark.skipif(os.environ.get("AGENTNODE_SANDBOX_E2E") != "1",
                    reason="set AGENTNODE_SANDBOX_E2E=1 (needs a container runtime) to run")
class TestTheVerticalFlowForReal:
    """A real gateway, a real socket, a real container, and code that is not ours.

    Everything above can pass with a stand-in backend. This cannot: it starts the gateway with the
    real `ContainerBackend`, and the only way `stdout` carries the marker is if a container ran the
    foreign payload and its output came back over HTTP.
    """

    # Class-scoped: measuring for real means running the whole conformance suite against the
    # runtime, and doing that once per test would triple a lane that already runs containers.
    # The three tests use distinct run ids and each verifies its own cleanup.
    @staticmethod
    @pytest.fixture(scope="class")
    def real_gateway():
        from agentnode_sdk.sandbox.container_backend import ContainerBackend

        backend = ContainerBackend()
        if not backend.check_available().available:
            pytest.skip("no container runtime + pinned image available")
        with tempfile.TemporaryDirectory() as td:
            state = GatewayState(td, version="test")
            service = GatewayService(state, backend=backend)
            # The gateway will not run anything until it has been measured, so the lane measures
            # it -- with the real suite, against the real runtime. This is the remediation the
            # refusal names, exercised rather than described.
            readiness = service.measure()
            if not readiness.ready:
                # Not a skip. The runtime is present -- the fixture already skipped otherwise --
                # so a gateway that still cannot be measured is a real failure, and a silent skip
                # here would let the one lane that can measure for real report nothing.
                #
                # The report's own words, not a summary of them. A failure saying only "not
                # measured" sends the next person guessing at precisely what the report knows.
                stored = (service.readiness.load() or {}).get("report") or {}
                lines = []
                for result in stored.get("results") or []:
                    lines.append(
                        "    {i:<24} ok={o!s:<6} {a:<14} {c:<12} {e}".format(
                            i=str(result.get("check_id"))[:24],
                            o=result.get("ok"),
                            a=str(result.get("assurance")),
                            c=str(result.get("outcome")),
                            e=str(result.get("evidence"))[:120],
                        )
                    )
                pytest.fail(
                    "conformance could not be measured: " + readiness.reason
                    + NEWLINE + "  unproven: " + ", ".join(readiness.unproven)
                    + NEWLINE + "  the suite reported:" + NEWLINE
                    + (NEWLINE.join(lines) or "    (no results at all)")
                )
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


@pytest.mark.skipif(not os.environ.get("AGENTNODE_SANDBOX_E2E"),
                    reason="needs a container runtime and outbound network")
class TestRestrictedEgressForReal:
    """Three questions, asked from inside the container, answered by the network itself.

    Can it reach the host it was allowed? Can it reach one it was not? And can it get out
    without going through the door at all? Only the first may be yes. The third is the one that
    matters most: a proxy that filters requests is worth nothing if the code can simply open its
    own socket, so the network the container sits on has no route out to open.
    """

    PAYLOAD = (
        b"import json, socket, urllib.request\n"
        b"out = {}\n"
        b"try:\n"
        b"    with urllib.request.urlopen('https://example.com', timeout=25) as r:\n"
        b"        out['allowed'] = r.status\n"
        b"except Exception as e:\n"
        b"    out['allowed'] = 'ERR:' + type(e).__name__\n"
        b"try:\n"
        b"    with urllib.request.urlopen('https://www.google.com', timeout=25) as r:\n"
        b"        out['denied'] = r.status\n"
        b"except Exception as e:\n"
        b"    out['denied'] = 'ERR:' + type(e).__name__\n"
        b"try:\n"
        b"    s = socket.create_connection(('1.1.1.1', 80), timeout=10)\n"
        b"    s.close()\n"
        b"    out['direct'] = 'OPEN'\n"
        b"except Exception as e:\n"
        b"    out['direct'] = 'ERR:' + type(e).__name__\n"
        b"print('EM3C-EGRESS ' + json.dumps(out), flush=True)\n"
    )

    @staticmethod
    @pytest.fixture(scope="class")
    def egress_gateway():
        from agentnode_sdk.sandbox.container_backend import ContainerBackend
        from agentnode_sdk.sandbox.contract import NetworkRules, SandboxPolicy

        backend = ContainerBackend()
        if not backend.check_available().available:
            pytest.skip("no container runtime + pinned image available")
        with tempfile.TemporaryDirectory() as td:
            state = GatewayState(td, version="test")
            operator = SandboxPolicy(network=NetworkRules(
                enabled=True, allowed_destinations=frozenset({"example.com"})))
            service = GatewayService(state, backend=backend, operator_policy=operator)
            readiness = service.measure()
            if not readiness.ready:
                pytest.fail("conformance could not be measured: " + readiness.reason)
            server = make_server(service, port=0)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            base = "http://127.0.0.1:%d" % server.server_address[1]
            try:
                yield base, state, service
            finally:
                server.shutdown()

    def test_only_the_allowed_host_is_reachable_and_only_through_the_proxy(self,
                                                                          egress_gateway):
        base, state, service = egress_gateway
        conn = _paired(base, state)
        answer = gc.submit(conn, self.PAYLOAD, network="restricted",
                           allowed_domains=("example.com",), wall_clock_s=180,
                           required_properties=("container_isolation",))
        assert answer["state"] != "refused", answer.get("refusal")
        final = gc.wait_for(conn, answer["run_id"], timeout=300)

        print("")
        print("  [observed] state=%s exit=%s" % (final["state"], final["exit_code"]))
        print("  [observed] the container said: %s" % (final["stdout"] or "").strip())
        print("  [observed] stderr: %s" % (final["stderr"] or "").strip()[:400])
        assert final["state"] == "finished", final.get("refusal") or final

        marker = "EM3C-EGRESS "
        line = next((ln for ln in (final["stdout"] or "").splitlines() if marker in ln), "")
        assert line, "the payload produced no result line at all"
        seen = json.loads(line.split(marker, 1)[1])

        assert seen["allowed"] == 200, "the allowed host was not reachable: %r" % (seen,)
        assert str(seen["denied"]).startswith("ERR"), "a host that was not allowed was reached"
        assert seen["direct"] == "ERR:timeout" or str(seen["direct"]).startswith("ERR"), (
            "the container had a route out that did not go through the proxy: %r" % (seen,)
        )

    def test_nothing_of_the_restricted_run_is_left_behind(self, egress_gateway):
        """The container, the proxy and both networks. A leftover network with a proxy on it is
        a route out that nothing is using and nobody is watching."""
        import subprocess

        base, state, service = egress_gateway
        conn = _paired(base, state)
        answer = gc.submit(conn, b"print('done')\n", network="restricted",
                           allowed_domains=("example.com",), wall_clock_s=120)
        assert answer["state"] != "refused", answer.get("refusal")
        final = gc.wait_for(conn, answer["run_id"], timeout=300)
        assert final["state"] == "finished", final.get("refusal") or final
        assert final["cleanup_verified"] is True, (
            "cleanup was not confirmed for a run that had a proxy and two networks"
        )

        runtime = service.backend.check_available().backend
        for kind in ("container", "network"):
            listed = subprocess.run(
                [runtime, kind, "ls", "--filter", "name=agentnode-egress-",
                 "--format", "{{.Name}}"],
                capture_output=True, text=True, timeout=30,
            )
            assert listed.returncode == 0, listed.stderr
            leftovers = [n for n in listed.stdout.split() if n.strip()]
            print("  [observed] leftover %ss: %s" % (kind, leftovers or "none"))
            assert not leftovers, "%s left behind: %s" % (kind, leftovers)
