"""mtls-loopback-identity-r1, decision stage 3: every named failure is closed, and each for its
own reason.

Two kinds of test here.

**One fixture per check.** Each certificate below is wrong in exactly ONE way and right in every
other, so that exactly one check can reject it. That is what lets a counter-check remove that
check and see this test -- and only this test -- turn red. A fixture wrong in two ways would stay
refused with either check gone, and prove nothing about either.

    check 1  chain       a foreign CA, with a perfect name and usage
    check 2  validity    expired, otherwise perfect
    check 3  usage       no extended-key-usage extension at all (OpenSSL lets that through)
    check 4  name        outside the grammar / another deployment / the wrong role
    check 5  instance    regularly issued, for an instance this side does not accept

**The named cases the approval lists**: no client certificate, a foreign CA, an expired
certificate, the wrong role, swapped certificates, a worker endpoint that changed, a certificate
changed without the binding, and the transport lost in the middle of a running job. For each:
refused, the refusal names the check, and nothing ran.

Validity is judged by the system clock in this arc. The decision's rollback-resistant floor is
stage 5 and is not here.
"""
from __future__ import annotations

import socket
import threading
import time

import pytest

from agentnode_sdk.pki import identity as ids
from agentnode_sdk.worker import WorkerUnreachable
from agentnode_sdk.worker.remote import TlsWorker
from tests.test_mtls_transport import (DEPLOYMENT, KEY, Door, World, a_job, a_raw_tls_client,
                                       send_a_sealed_run, settle)

SERVER = ids.SERVER_AUTH
CLIENT = ids.CLIENT_AUTH


@pytest.fixture()
def world(tmp_path):
    return World(tmp_path)


def presented_to_the_worker(world: World, gateway_dir, *, accept=("g1",), anchor=None):
    """A worker w1 accepting `accept`; a caller presents the certificate in `gateway_dir`,
    holding the MAC key. Returns the door after the attempt."""
    existing = world.root / "worker-w1"
    worker = existing if existing.exists() else world.service("worker", "w1")
    door = Door(world, worker, set(accept), label="w1", anchor=anchor)
    try:
        try:
            connection = a_raw_tls_client(world, door.port, folder=gateway_dir)
            send_a_sealed_run(connection)
            connection.close()
        except OSError:
            pass
        settle()
    finally:
        door.close()
    return door


def refused_at(door, check: str) -> bool:
    return any(("at the %s check" % check) in s for s in door.said)


# ====================================================================== one fixture per check

class TestEachCheckRefusesOnItsOwn:

    def test_check_1_a_foreign_ca_with_a_perfect_name_is_refused(self, world):
        key, cert, _ = world.foreign_ca()
        forged = world.forge("foreign", uri="agentnode://alpha/gateway/g1", usages=[CLIENT],
                             ca=(key, cert))
        door = presented_to_the_worker(world, forged)
        assert door.bytes_in == 0 and door.stub.ran == [], "it got through to the worker"
        assert any("handshake" in s for s in door.said), door.said

    def test_check_2_an_expired_certificate_is_refused(self, world):
        forged = world.forge("expired", uri="agentnode://alpha/gateway/g1", usages=[CLIENT],
                             not_before_days=-30, not_after_days=-1)
        door = presented_to_the_worker(world, forged)
        assert door.bytes_in == 0 and door.stub.ran == [], "it got through to the worker"
        assert any("handshake" in s for s in door.said), door.said

    def test_check_3_a_certificate_with_no_usage_is_refused(self, world):
        forged = world.forge("no-usage", uri="agentnode://alpha/gateway/g1", usages=None)
        door = presented_to_the_worker(world, forged)
        assert door.bytes_in == 0 and door.stub.ran == [], "it got through to the worker"
        assert refused_at(door, ids.CHECK_USAGE), door.said

    def test_check_4_a_name_outside_the_grammar_is_refused(self, world):
        forged = world.forge("bad-name", uri="agentnode://alpha/gateway/G1", usages=[CLIENT])
        door = presented_to_the_worker(world, forged, accept=("g1", "G1"))
        assert door.bytes_in == 0 and door.stub.ran == [], "it got through to the worker"
        assert refused_at(door, ids.CHECK_SAN), door.said

    def test_check_4_another_deployment_is_refused(self, world):
        forged = world.forge("elsewhere", uri="agentnode://beta/gateway/g1", usages=[CLIENT])
        door = presented_to_the_worker(world, forged)
        assert door.bytes_in == 0 and door.stub.ran == [], "it got through to the worker"
        assert refused_at(door, ids.CHECK_DEPLOYMENT), door.said

    def test_check_4_the_right_usage_with_the_wrong_role_is_refused(self, world):
        """clientAuth -- right for a gateway -- on a name that says `worker`. Only the role check
        can see it: OpenSSL is satisfied by the usage."""
        forged = world.forge("wrong-role", uri="agentnode://alpha/worker/g1", usages=[CLIENT])
        door = presented_to_the_worker(world, forged)
        assert door.bytes_in == 0 and door.stub.ran == [], "it got through to the worker"
        assert refused_at(door, ids.CHECK_ROLE), door.said

    def test_check_5_an_instance_this_side_does_not_accept_is_refused(self, world):
        other = world.service("gateway", "g9")
        door = presented_to_the_worker(world, other, accept=("g1",))
        assert door.bytes_in == 0 and door.stub.ran == [], "it got through to the worker"
        assert refused_at(door, ids.CHECK_INSTANCE), door.said

    def test_the_control_a_correct_gateway_gets_through(self, world):
        """Without this, every refusal above could be a door that refuses everybody."""
        good = world.service("gateway", "g1")
        door = presented_to_the_worker(world, good)
        assert door.bytes_in > 0
        assert [j.run_id for j in door.stub.ran] == ["sneaked"]


# ====================================================================== the named cases

class TestTheNamedCases:

    def test_no_client_certificate(self, world):
        door = presented_to_the_worker(world, None)
        assert door.bytes_in == 0 and door.stub.ran == [], "it got through to the worker"

    def test_swapped_certificates_are_refused_in_both_directions(self, world):
        gateway = world.service("gateway", "g1")
        worker = world.service("worker", "w1")
        # The gateway's end presents the WORKER's certificate.
        door = Door(world, worker, {"g1"}, label="w1")
        try:
            connection = a_raw_tls_client(world, door.port, folder=worker)
            send_a_sealed_run(connection)
            connection.close()
            settle()
            assert door.bytes_in == 0 and door.stub.ran == [], "it got through to the worker"
        finally:
            door.close()
        # And a "worker" presenting the GATEWAY's certificate is not believed by the gateway.
        wrong_door = Door(world, gateway, {"g1"}, label="w1")
        try:
            client = TlsWorker(wrong_door.address, KEY, world.settings(gateway, {"w1"}))
            with pytest.raises(WorkerUnreachable):
                client.run(a_job("swapped"))
            settle()
            assert wrong_door.bytes_in == 0 and wrong_door.stub.ran == [], "it got through"
        finally:
            wrong_door.close()

    def test_a_worker_endpoint_that_changed_is_refused(self, world):
        """The gateway is configured for w1. The address now reaches a different worker -- a
        real one, regularly issued, w2. It is refused at the instance check, nothing is sent."""
        gateway = world.service("gateway", "g1")
        moved = world.service("worker", "w2")
        door = Door(world, moved, {"g1"}, label="w2")
        try:
            client = TlsWorker(door.address, KEY, world.settings(gateway, {"w1"}))
            with pytest.raises(WorkerUnreachable) as refused:
                client.run(a_job("moved"))
            settle()
            assert "instance" in str(refused.value)
            assert door.bytes_in == 0 and door.stub.ran == [], "it got through to the worker"
        finally:
            door.close()

    def test_a_certificate_changed_without_the_binding_is_refused(self, world):
        """The worker's certificate is replaced by a valid one naming a DIFFERENT instance, and
        the gateway's configuration was not updated. Refused -- the binding is to the name."""
        gateway = world.service("gateway", "g1")
        replaced = world.service("worker", "w1b")
        door = Door(world, replaced, {"g1"}, label="w1")
        try:
            client = TlsWorker(door.address, KEY, world.settings(gateway, {"w1"}))
            with pytest.raises(WorkerUnreachable):
                client.run(a_job("changed"))
            settle()
            assert door.bytes_in == 0 and door.stub.ran == [], "it got through to the worker"
        finally:
            door.close()

    def test_and_a_renewed_certificate_with_the_same_name_is_accepted(self, world):
        """The other half of binding to the name rather than to a fingerprint: a new key for the
        same identity is the same worker."""
        import json

        from agentnode_sdk.pki.issuer import make_request

        gateway = world.service("gateway", "g1")
        worker = world.service("worker", "w1")
        renewal = json.loads(make_request(worker, renew=True).read_text())
        world.issuer.renew(renewal["csr"].encode(), renewal["current"].encode(),
                           bytes.fromhex(renewal["signature"]))
        (worker / "cert.pem").write_bytes((worker / "cert.pem.next").read_bytes())
        (worker / "key.pem").write_bytes((worker / "key.next.pem").read_bytes())
        door = Door(world, worker, {"g1"}, label="w1")
        try:
            client = TlsWorker(door.address, KEY, world.settings(gateway, {"w1"}))
            assert client.run(a_job("renewed")).stdout == "RAN"
        finally:
            door.close()


class TestTheGatewayRefusesBeforeClaimingAnything:
    """The gateway-side cases, through a real gateway. It was measured against the real w1; then
    the endpoint underneath it changes. The submission is refused -- and because the worker is
    reached and checked BEFORE the run id is claimed, there is no ledger entry and no signed
    line for it, and the other end received nothing. (A worker lost AFTER the claim is the
    job-in-flight case below, which ends with exactly one line, as it must.)"""

    def _swapped(self, tmp_path, to_instance: str):
        from tests.test_mtls_parity import a_gateway_over

        world = World(tmp_path / "pki")
        gateway_dir = world.service("gateway", "g1")
        real = Door(world, world.service("worker", "w1"), {"g1"}, label="w1")
        other = Door(world, world.service("worker", to_instance), {"g1"}, label="w1")
        client = TlsWorker(real.address, KEY, world.settings(gateway_dir, {"w1"}))
        gw = a_gateway_over(tmp_path / "gw", client)
        client.address = other.address
        return gw, real, other

    def _nothing_claimed(self, gw, run_id: str) -> None:
        import json
        from pathlib import Path

        from agentnode_sdk.gateway import meter

        ledger = (Path(gw.state.root) / "ledger.json")
        on_disk = ledger.read_text(encoding="utf-8") if ledger.exists() else ""
        assert run_id not in on_disk, "the refused run was claimed in the ledger"
        assert [l for l in meter.read(gw.state.root) if l.get("run_id") == run_id] == [],             "the refused run has a signed line"

    @pytest.mark.parametrize("to_instance", ["w2", "w1b"])
    def test_a_changed_endpoint_or_certificate_is_refused_before_anything_is_claimed(
            self, tmp_path, to_instance):
        from tests.test_mtls_parity import submit_and_wait

        gw, real, other = self._swapped(tmp_path, to_instance)
        run_id = "refused-before-claim-" + to_instance
        try:
            record = submit_and_wait(gw, run_id)
            assert record.get("state") == "refused", (
                "the job was accepted before the worker was reached: %r" % record)
            assert other.bytes_in == 0 and other.stub.ran == [], "it got through to the worker"
            assert real.stub.ran == []
            self._nothing_claimed(gw, run_id)
        finally:
            gw.close()
            real.close()
            other.close()


class TestTheRefusalSaysWhichCheckAndNothingSecret:

    def test_the_gateway_is_told_the_check_and_the_name_and_no_more(self, world):
        gateway = world.service("gateway", "g1")
        other = world.service("worker", "w2")
        door = Door(world, other, {"g1"}, label="w2")
        try:
            client = TlsWorker(door.address, KEY, world.settings(gateway, {"w1"}))
            with pytest.raises(WorkerUnreachable) as refused:
                client.run(a_job("told"))
        finally:
            door.close()
        said = str(refused.value)
        assert "instance" in said and "agentnode://alpha/worker/w2" in said
        for secret_shape in ("PRIVATE KEY", "BEGIN CERTIFICATE", KEY.decode(), KEY.hex()):
            assert secret_shape not in said

    def test_the_worker_logs_the_check_and_the_name_and_no_more(self, world):
        other = world.service("gateway", "g9")
        door = presented_to_the_worker(world, other, accept=("g1",))
        text = "\n".join(door.said)
        assert "instance" in text and "agentnode://alpha/gateway/g9" in text
        for secret_shape in ("PRIVATE KEY", "BEGIN CERTIFICATE", KEY.decode(), KEY.hex()):
            assert secret_shape not in text

    def test_a_failure_says_no_more_than_a_success(self, world):
        """The worker's log for a good connection and a refused one, side by side: the refused one
        adds the check and the presented name, and nothing that looks like key material."""
        good = world.service("gateway", "g1")
        ok_door = presented_to_the_worker(world, good)
        assert ok_door.said == []
        bad = world.forge("no-usage-2", uri="agentnode://alpha/gateway/g1", usages=None)
        bad_door = presented_to_the_worker(world, bad)
        assert len(bad_door.said) == 1 and len(bad_door.said[0]) < 300


# ====================================================================== the job in flight

class AWorkerThatWaits:
    """A stand-in whose run blocks until it is let go, so a connection can be cut mid-job."""

    def __init__(self):
        from tests.test_socket_worker import AWorkerThatAnswers

        self.inner = AWorkerThatAnswers()
        self.started = threading.Event()
        self.let_go = threading.Event()

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def run(self, job):
        self.started.set()
        self.let_go.wait(timeout=30)
        return self.inner.run(job)

    @property
    def ran(self):
        return self.inner.ran


class TestTheTransportLostInTheMiddleOfAJob:

    def test_it_ends_with_exactly_one_line_that_says_so(self, tmp_path):
        """A real gateway, a real TLS worker, a job running -- and the worker end cuts the
        connection. The run ends as `transport_lost`, with one signed line, against the identity
        its connection proved, and the sandbox named as not established."""
        from agentnode_sdk.gateway import meter
        from tests.test_mtls_parity import a_gateway_over, submit_and_wait

        world = World(tmp_path / "pki")
        gateway_dir = world.service("gateway", "g1")
        worker_dir = world.service("worker", "w1")
        stub = AWorkerThatWaits()
        door = Door(world, worker_dir, {"g1"}, stub=stub, label="w1")
        live = []
        counted = door.bench.converse

        def keeping(connection):
            live.append(connection)
            return counted(connection)

        door.bench.converse = keeping
        client = TlsWorker(door.address, KEY, world.settings(gateway_dir, {"w1"}))
        gw = a_gateway_over(tmp_path / "gw", client)
        try:
            done = threading.Thread(target=submit_and_wait, args=(gw, "cut-me"), daemon=True)
            done.start()
            assert stub.started.wait(timeout=20), "the job never reached the worker"
            carrying = [c for c in live if c.fileno() != -1][-1]
            carrying.shutdown(socket.SHUT_RDWR)
            carrying.close()
            done.join(timeout=60)
            stub.let_go.set()
            lines = [l for l in meter.read(gw.state.root) if l.get("run_id") == "cut-me"]
            assert len(lines) == 1, lines
            line = lines[0]
            assert line["termination_reason"] == "transport_lost"
            assert line["worker_transport"] == "mtls"
            assert line["worker_identity"] == "agentnode://alpha/worker/w1"
            assert line["worker_id"] == "w1"
            assert line["sandbox"] == "not_established"
            assert meter.verify(gw.state.root)["ok"]
        finally:
            stub.let_go.set()
            gw.close()
            door.close()
