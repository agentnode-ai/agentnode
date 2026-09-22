"""mtls-revocation-time-r1: renewal over a bounded overlap (decision 3.3, 5.4, 5.5; stage 5 (a)).

What is shown here:

* a certificate renewed while a job runs on a connection made with the previous one is accepted,
  and the job finishes -- on both sides, gateway and worker;
* each service takes its renewed certificate up on its next connection, without a restart, and a
  pair found half-moved never makes it present a certificate with a key that is not its own;
* the overlap is BOUNDED, and the bound is enforced through the revocation list: a certificate
  that has left the overlap -- because a second renewal superseded it, or because its overlap ran
  out -- is refused by the peer;
* only the current key renews, and a revoked or locked entry does not renew at all;
* the operator's view shows the remaining validity and the date renewal falls due.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

from agentnode_sdk.pki import identity as ids
from agentnode_sdk.pki import issuer as issuer_module
from agentnode_sdk.pki.issuer import (OVERLAPPING, SUPERSEDED, IssuanceRefused, install_renewal,
                                      make_request)
from agentnode_sdk.worker.remote import TlsWorker
from tests.test_mtls_fail_closed import AWorkerThatWaits, presented_to_the_worker, refused_at
from tests.test_mtls_transport import _one_boot  # noqa: F401 - autouse: one boot throughout
from tests.test_mtls_transport import KEY, Door, World, a_raw_tls_client


@pytest.fixture()
def world(tmp_path):
    return World(tmp_path)


def serial_in(pem_or_der: bytes) -> str:
    from cryptography import x509

    if pem_or_der.startswith(b"-----"):
        certificate = x509.load_pem_x509_certificate(pem_or_der)
    else:
        certificate = x509.load_der_x509_certificate(pem_or_der)
    return format(certificate.serial_number, "x")


def renew(world: World, folder: Path, *, install: bool = True) -> str:
    """The whole operation: the service asks with its current key, root renews, the service puts
    the renewed pair in place. Returns the new serial."""
    body = json.loads(make_request(folder, renew=True).read_text())
    pem = world.issuer.renew(body["csr"].encode(), body["current"].encode(),
                             bytes.fromhex(body["signature"]))
    if install:
        install_renewal(folder, world.anchor)
    return serial_in(pem)


def keep_a_copy(folder: Path, where: Path) -> Path:
    where.mkdir()
    for name in ("cert.pem", "key.pem"):
        shutil.copy2(folder / name, where / name)
    return where


def the_worker_presents(world: World, door: Door, gateway_dir: Path) -> str:
    connection = a_raw_tls_client(world, door.port, folder=gateway_dir)
    try:
        return serial_in(connection.getpeercert(binary_form=True))
    finally:
        connection.close()


# ====================================================================== (a) nothing fails

class TestARenewalDuringTheOverlapIsAccepted:

    def _running(self, tmp_path, seen_gateway_serials):
        from tests.test_mtls_parity import a_gateway_over

        world = World(tmp_path / "pki")
        gateway_dir = world.service("gateway", "g1")
        worker_dir = world.service("worker", "w1")
        stub = AWorkerThatWaits()
        door = Door(world, worker_dir, {"g1"}, stub=stub, label="w1")
        inner = door.bench.converse

        def noting(connection, noted=None):
            der = getattr(connection, "gateway_der", None)
            if der:
                seen_gateway_serials.append(serial_in(der))
            return inner(connection, noted=noted)

        door.bench.converse = noting
        client = TlsWorker(door.address, KEY, world.settings(gateway_dir, {"w1"}),
                           say=lambda text: None)
        gw = a_gateway_over(tmp_path / "gw", client)
        return world, gateway_dir, worker_dir, stub, door, gw

    def _run(self, gw, run_id):
        from tests.test_mtls_parity import submit_and_wait

        return submit_and_wait(gw, run_id)

    def test_a_the_worker_renews_while_a_job_runs_and_nothing_fails(self, tmp_path):
        import threading

        seen: list = []
        world, gateway_dir, worker_dir, stub, door, gw = self._running(tmp_path, seen)
        try:
            old = the_worker_presents(world, door, gateway_dir)
            box: dict = {}
            first = threading.Thread(target=lambda: box.update(self._run(gw, "a-worker-1")),
                                     daemon=True)
            first.start()
            assert stub.started.wait(timeout=20), "the job never reached the worker"
            new = renew(world, worker_dir)                     # on the worker, mid-job
            assert new != old
            stub.let_go.set()
            first.join(timeout=60)
            assert box.get("state") == "finished" and box.get("exit_code") == 0, box
            # The same running worker, not restarted, now presents the renewed certificate...
            assert the_worker_presents(world, door, gateway_dir) == new
            # ...and the gateway accepts it: a second job goes through on it.
            second = self._run(gw, "a-worker-2")
            assert second.get("state") == "finished" and second.get("exit_code") == 0, second
        finally:
            stub.let_go.set()
            gw.close()
            door.close()

    def test_a_the_gateway_renews_while_a_job_runs_and_nothing_fails(self, tmp_path):
        import threading

        seen: list = []
        world, gateway_dir, worker_dir, stub, door, gw = self._running(tmp_path, seen)
        try:
            box: dict = {}
            first = threading.Thread(target=lambda: box.update(self._run(gw, "a-gw-1")),
                                     daemon=True)
            first.start()
            assert stub.started.wait(timeout=20), "the job never reached the worker"
            old = seen[-1]
            new = renew(world, gateway_dir)                    # on the gateway, mid-job
            stub.let_go.set()
            first.join(timeout=60)
            assert box.get("state") == "finished" and box.get("exit_code") == 0, box
            second = self._run(gw, "a-gw-2")
            assert second.get("state") == "finished" and second.get("exit_code") == 0, second
            assert old != new and seen[-1] == new, (
                "the running gateway did not take up its renewed certificate: %r" % seen)
        finally:
            stub.let_go.set()
            gw.close()
            door.close()

    def test_a_pair_found_half_moved_keeps_the_previous_certificate(self, world):
        gateway_dir = world.service("gateway", "g1")
        worker_dir = world.service("worker", "w1")
        door = Door(world, worker_dir, {"g1"}, label="w1")
        try:
            old = the_worker_presents(world, door, gateway_dir)
            new = renew(world, worker_dir, install=False)
            (worker_dir / "key.next.pem").replace(worker_dir / "key.pem")   # key moved, cert not
            assert the_worker_presents(world, door, gateway_dir) == old
            assert any("kept the previous certificate" in s for s in door.said), door.said
            (worker_dir / "cert.pem.next").replace(worker_dir / "cert.pem")
            assert the_worker_presents(world, door, gateway_dir) == new
        finally:
            door.close()


# ====================================================================== the bound

class TestTheOverlapIsBounded:

    def test_a_second_renewal_takes_the_overlapping_certificate_out_of_use(self, world,
                                                                           tmp_path):
        g1 = world.service("gateway", "g1")
        first = keep_a_copy(g1, tmp_path / "first")
        renew(world, g1)
        second = keep_a_copy(g1, tmp_path / "second")
        assert presented_to_the_worker(world, first).stub.ran, "inside the overlap it must pass"
        renew(world, g1)
        statuses = [c["status"] for c in world.issuer.inventory()["entries"]["gateway/g1"]
                    ["certificates"]]
        assert statuses == [SUPERSEDED, OVERLAPPING, "current"]
        door = presented_to_the_worker(world, first)
        assert door.stub.ran == [] and refused_at(door, ids.CHECK_REVOKED), (
            "a superseded certificate was still accepted: %r" % door.said)
        assert presented_to_the_worker(world, second).stub.ran, "the overlapping one still passes"

    def test_when_the_overlap_runs_out_the_previous_certificate_is_refused(
            self, world, tmp_path, monkeypatch):
        g1 = world.service("gateway", "g1")
        previous = keep_a_copy(g1, tmp_path / "previous")
        monkeypatch.setattr(issuer_module, "OVERLAP_SECONDS", 1)
        renew(world, g1)
        assert presented_to_the_worker(world, previous).stub.ran, "inside the overlap it passes"
        time.sleep(1.2)
        report = world.tick()
        door = presented_to_the_worker(world, previous)
        assert door.stub.ran == [] and refused_at(door, ids.CHECK_REVOKED), (
            "a certificate outside its overlap was still accepted: %r" % door.said)
        assert report["overlaps_ended"], report
        assert presented_to_the_worker(world, g1).stub.ran, "the renewed one must still pass"


# ====================================================================== who may renew

class TestWhoMayRenew:

    def test_only_the_current_key_renews(self, world, tmp_path):
        g1 = world.service("gateway", "g1")
        previous = keep_a_copy(g1, tmp_path / "previous")
        renew(world, g1)
        with pytest.raises(IssuanceRefused, match="not the current one"):
            renew(world, previous, install=False)

    def test_a_revoked_certificate_does_not_renew(self, world):
        g1 = world.service("gateway", "g1")
        world.issuer.revoke(serial_in((g1 / "cert.pem").read_bytes()))
        with pytest.raises(IssuanceRefused, match="revoked"):
            renew(world, g1, install=False)

    def test_the_view_shows_what_is_left_and_when_renewal_falls_due(self, world):
        world.service("gateway", "g1")
        record = world.issuer.inventory()["entries"]["gateway/g1"]["certificates"][0]
        assert 89 < record["remaining_days"] <= 90
        assert record["renewal_due_from"] == pytest.approx(record["not_after"] - 30 * 86400,
                                                           abs=1)
