"""mtls-revocation-time-r1: the revocation list, the effective time, open connections, recovery.

Decision 5.3, 5.6, 3.4 checks 2 and 6, and stage 5 (b), (c), (d), (e), (f), (g), (h), (i1), (i2),
(s). Real TLS over loopback between certificates from a real issuer, a real signed list, and a
floor the root run (`Issuer.tick`) wrote -- the same builders as the stage 1-4 tests.

**One fatal condition each.** Every test starts from a world where the certificate is valid, the
list is valid and fresh and the floor is valid (`assert_all_else_is_valid`), shows the control
where that matters, and then makes exactly one thing wrong. So a mutation that removes a check
turns the one test about that check red, and no other check can hold it green.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from agentnode_sdk.pki import files as F
from agentnode_sdk.pki import floor as floors
from agentnode_sdk.pki import identity as ids
from agentnode_sdk.pki import revocation as rl
from agentnode_sdk.pki.issuer import COMPROMISE, IssuanceRefused, Issuer, make_request
from agentnode_sdk.worker import WorkerUnreachable
from agentnode_sdk.worker.remote import TlsWorker
from tests.test_mtls_fail_closed import AWorkerThatWaits, presented_to_the_worker, refused_at
from tests.test_mtls_transport import _one_boot  # noqa: F401 - autouse: one boot throughout
from tests.test_mtls_transport import KEY, Door, World

CLIENT = ids.CLIENT_AUTH
HOUR = 3600.0


@pytest.fixture()
def world(tmp_path):
    return World(tmp_path)


def serial_of(folder: Path) -> str:
    from cryptography import x509

    return format(x509.load_pem_x509_certificate((folder / "cert.pem").read_bytes())
                  .serial_number, "x")


def assert_all_else_is_valid(world: World) -> None:
    """The list verifies and is fresh now, and both floors are usable now."""
    from cryptography import x509

    anchor = x509.load_pem_x509_certificate(world.anchor.read_bytes())
    rl.load(world.revocation_list, anchor, time.time())
    for role in floors.ROLES:
        floors.read(floors.path_for(world.floor_dir, role), role)


def a_list(world: World, *, now: float, serials=(), number: int = 900, sign_with=None) -> bytes:
    """A list as root would sign it -- or, with `sign_with`, one signed by another key under
    THIS CA's name, which is what a forged list looks like."""
    ca_key, ca_cert = world._ca()
    return rl.build(sign_with or ca_key, ca_cert, {s: now for s in serials}, number=number,
                    now=now)


def reach(world: World, gateway_dir: Path, address: str, accept=("w1",)):
    """The gateway side, trying to reach a worker door now. None, or the refusal it gave."""
    client = TlsWorker(address, KEY, world.settings(gateway_dir, set(accept)),
                       say=lambda text: None)
    try:
        client.confirm_reachable()
        return None
    except WorkerUnreachable as refused:
        return str(refused)


# ====================================================================== (b) (c) (e) (f) (g) (h)

class TestEachFailureOnItsOwn:

    def test_b_a_valid_unrevoked_certificate_of_another_instance_is_refused(self, world):
        g2 = world.service("gateway", "g2")
        assert_all_else_is_valid(world)
        control = presented_to_the_worker(world, g2, accept=("g2",))
        assert control.stub.ran and control.said == [], "the control did not get through"
        door = presented_to_the_worker(world, g2, accept=("g1",))
        assert door.bytes_in == 0 and door.stub.ran == [], "it got through to the worker"
        assert refused_at(door, ids.CHECK_INSTANCE), door.said

    def test_c_a_revoked_certificate_is_refused_while_the_list_is_fresh(self, world):
        g1 = world.service("gateway", "g1")
        control = presented_to_the_worker(world, g1)
        assert control.stub.ran, "the control did not get through"
        done = world.issuer.revoke(serial_of(g1))
        assert done["effective"] is True
        assert_all_else_is_valid(world)
        door = presented_to_the_worker(world, g1)
        assert door.bytes_in == 0 and door.stub.ran == [], "a revoked gateway got through"
        assert refused_at(door, ids.CHECK_REVOKED), door.said

    def test_c_and_the_gateway_refuses_a_revoked_worker(self, world):
        gateway = world.service("gateway", "g1")
        worker = world.service("worker", "w1")
        door = Door(world, worker, {"g1"}, label="w1")
        try:
            assert reach(world, gateway, door.address) is None               # the control
            assert world.issuer.revoke(serial_of(worker))["effective"] is True
            assert_all_else_is_valid(world)
            said = reach(world, gateway, door.address)
            assert said and "at the revoked check" in said, said
            assert door.bytes_in == 0
        finally:
            door.close()

    def test_e_an_expired_list_is_refused_while_the_certificate_is_valid(self, world):
        g1 = world.service("gateway", "g1")
        assert presented_to_the_worker(world, g1).stub.ran, "the control did not get through"
        world.revocation_list.write_bytes(
            a_list(world, now=time.time() - rl.VALID_SECONDS - HOUR))
        door = presented_to_the_worker(world, g1)
        assert door.bytes_in == 0 and door.stub.ran == [], "an expired list let it through"
        assert refused_at(door, rl.EXPIRED), door.said

    @pytest.mark.parametrize("how", ["missing", "garbage"])
    def test_f_no_readable_list_is_refused_never_taken_as_nothing_revoked(self, world, how):
        g1 = world.service("gateway", "g1")
        assert presented_to_the_worker(world, g1).stub.ran, "the control did not get through"
        if how == "missing":
            world.revocation_list.unlink()
        else:
            world.revocation_list.write_bytes(b"-----BEGIN X509 CRL-----\nnot a list\n")
        door = presented_to_the_worker(world, g1)
        assert door.bytes_in == 0 and door.stub.ran == [], "no list was taken as an empty one"
        assert refused_at(door, rl.UNREADABLE), door.said

    def test_g_a_list_with_the_wrong_signature_is_refused(self, world):
        from cryptography.hazmat.primitives.asymmetric import ec

        g1 = world.service("gateway", "g1")
        assert presented_to_the_worker(world, g1).stub.ran, "the control did not get through"
        forged = a_list(world, now=time.time(), sign_with=ec.generate_private_key(ec.SECP256R1()))
        world.revocation_list.write_bytes(forged)
        door = presented_to_the_worker(world, g1)
        assert door.bytes_in == 0 and door.stub.ran == [], "a forged list was believed"
        assert refused_at(door, rl.SIGNATURE), door.said

    def test_h_an_expired_certificate_is_refused_with_a_fresh_list_and_a_right_clock(self, world):
        assert_all_else_is_valid(world)
        late = world.forge("late", uri="agentnode://alpha/gateway/g1", usages=[CLIENT],
                           not_before_days=-30, not_after_days=-1 / 24)
        door = presented_to_the_worker(world, late)
        assert door.bytes_in == 0 and door.stub.ran == [], "an expired certificate got through"
        assert refused_at(door, ids.CHECK_VALIDITY), door.said


# ====================================================================== (i1) (i2)

class TestAClockSetBackCannotUndoTime:
    """The system clock set back two hours; the floor the root run wrote stands at now.

    The issuer here was set up a DAY ago, and its first list with it: otherwise the CA itself --
    created seconds before the test -- would not yet be valid two hours back, and a test that let
    a mutation to the effective time fail on the CA's window would not be about the effective
    time. Root then published a list now and wrote the floors now, as it would.
    """

    def _world(self, tmp_path, monkeypatch) -> World:
        from agentnode_sdk.pki import issuer as issuer_module

        real = time.time()
        honest = issuer_module._now
        monkeypatch.setattr(issuer_module, "_now", lambda: real - 24 * HOUR)
        world = World(tmp_path)
        monkeypatch.setattr(issuer_module, "_now", honest)
        world.issuer.publish()
        world.tick()
        assert_all_else_is_valid(world)
        return world

    def _set_back(self, monkeypatch, seconds: float):
        real = time.time()
        monkeypatch.setattr(floors, "_system_now", lambda: real - seconds)

    def _valid_g1(self, world: World, name: str = "fine"):
        """A certificate of g1 from this CA, valid from a month ago to tomorrow."""
        return world.forge(name, uri="agentnode://alpha/gateway/g1", usages=[CLIENT],
                           not_before_days=-30, not_after_days=1)

    def test_i1_an_expired_certificate_stays_refused_because_the_floor_stays_ahead(
            self, tmp_path, monkeypatch):
        world = self._world(tmp_path, monkeypatch)
        self._set_back(monkeypatch, 2 * HOUR)
        fine = self._valid_g1(world)
        control = presented_to_the_worker(world, fine)
        assert control.stub.ran, "with the clock set back, even a valid certificate failed"
        # Expired an hour ago: by the set-back clock it would still have an hour to go.
        late = world.forge("late", uri="agentnode://alpha/gateway/g1", usages=[CLIENT],
                           not_before_days=-30, not_after_days=-1 / 24)
        door = presented_to_the_worker(world, late)
        assert door.bytes_in == 0 and door.stub.ran == [], (
            "with the clock set back, an expired certificate was taken as valid again")
        assert refused_at(door, ids.CHECK_VALIDITY), door.said

    def test_i2_an_expired_list_stays_refused_because_the_floor_stays_ahead(
            self, tmp_path, monkeypatch):
        world = self._world(tmp_path, monkeypatch)
        g1 = self._valid_g1(world)
        self._set_back(monkeypatch, 2 * HOUR)
        assert presented_to_the_worker(world, g1).stub.ran, "the control did not get through"
        # Expired an hour ago: by the set-back clock it would still be current.
        world.revocation_list.write_bytes(
            a_list(world, now=time.time() - rl.VALID_SECONDS - HOUR))
        door = presented_to_the_worker(world, g1)
        assert door.bytes_in == 0 and door.stub.ran == [], (
            "with the clock set back, an expired list was taken as current again")
        assert refused_at(door, rl.EXPIRED), door.said


# ====================================================================== publication and effect

class TestARevocationIsEffectiveOnlyOncePublished:

    def test_a_revocation_whose_list_cannot_be_made_durable_is_not_reported_effective(
            self, world):
        g1 = world.service("gateway", "g1")
        recording = F.RecordingFiles()
        # The list reaches its stage name, and making THAT durable fails -- right after the
        # rename, which is exactly where an effect must not yet be claimed.
        recording.fail_after("list:stage-renamed", "fsync_dir", OSError(5, "EIO, on purpose"))
        failing = Issuer(world.root / "ca", world.root / "trust", files=recording)
        done = failing.revoke(serial_of(g1))
        assert done["effective"] is False, "a revocation was called effective before it was"
        inventory = json.loads((world.root / "ca" / "inventory.json").read_text())
        record = inventory["entries"]["gateway/g1"]["certificates"][0]
        assert record["revoked_at"] is not None, "the revocation was not even recorded"
        # Honest about what that means: the list the services read does not carry it yet.
        published = rl.load(world.revocation_list, world._ca()[1], time.time())
        assert serial_of(g1) not in published.serials
        # Once the disk behaves, the root run publishes it and it takes effect.
        report = world.tick()
        assert report["pending_revocation"] is False, report
        door = presented_to_the_worker(world, g1)
        assert refused_at(door, ids.CHECK_REVOKED), door.said

    def test_the_root_run_reconciles_a_list_that_lost_a_serial(self, world):
        g1 = world.service("gateway", "g1")
        before = world.revocation_list.read_bytes()
        assert world.issuer.revoke(serial_of(g1))["effective"]
        world.revocation_list.write_bytes(before)             # the published list lost it
        report = world.tick()
        assert "published" in report["list"], report
        published = rl.load(world.revocation_list, world._ca()[1], time.time())
        assert serial_of(g1) in published.serials


# ====================================================================== (d) open connections

def _submit_in_background(gw, run_id):
    from tests.test_mtls_parity import submit_and_wait

    box: dict = {}
    thread = threading.Thread(target=lambda: box.update(submit_and_wait(gw, run_id)),
                              daemon=True)
    thread.start()
    return thread, box


def _lines(gw, run_id):
    from agentnode_sdk.gateway import meter

    return [line for line in meter.read(gw.state.root) if line.get("run_id") == run_id]


class TestOpenConnectionsAreReEvaluated:
    """A job is running over mutual TLS when one side's certificate is revoked. Only the OTHER
    side checks that certificate, so only its re-evaluation can cut the connection -- which is
    what lets each direction's re-evaluation be shown on its own."""

    def _running(self, tmp_path):
        from tests.test_mtls_parity import a_gateway_over

        world = World(tmp_path / "pki")
        gateway_dir = world.service("gateway", "g1")
        worker_dir = world.service("worker", "w1")
        stub = AWorkerThatWaits()
        door = Door(world, worker_dir, {"g1"}, stub=stub, label="w1")
        client = TlsWorker(door.address, KEY, world.settings(gateway_dir, {"w1"}),
                           say=lambda text: None)
        gw = a_gateway_over(tmp_path / "gw", client)
        return world, gateway_dir, worker_dir, stub, door, client, gw

    def _cut_within(self, gw, run_id, promised: float) -> tuple[list, float]:
        started = time.monotonic()
        deadline = started + promised + 10.0
        while time.monotonic() < deadline:
            lines = _lines(gw, run_id)
            if lines:
                return lines, time.monotonic() - started
            time.sleep(0.02)
        return [], time.monotonic() - started

    def test_d_the_worker_cuts_a_gateway_revoked_under_a_running_job(self, tmp_path):
        world, gateway_dir, _w, stub, door, client, gw = self._running(tmp_path)
        try:
            thread, _ = _submit_in_background(gw, "d-worker")
            assert stub.started.wait(timeout=20), "the job never reached the worker"
            assert world.issuer.revoke(serial_of(gateway_dir))["effective"] is True
            lines, took = self._cut_within(gw, "d-worker", client.tls.promised_seconds())
            assert lines, ("the worker did not cut the connection of a revoked gateway: the job "
                           "was still running %.1f s after the revocation took effect" % took)
            assert len(lines) == 1, lines
            assert lines[0]["termination_reason"] == "transport_lost"
            assert lines[0]["worker_identity"] == "agentnode://alpha/worker/w1"
            assert any("cut an open connection" in s and "revoked" in s for s in door.said)
            # And the worker stopped the run it was carrying for the revoked caller.
            assert [r for r, _c, _a in stub.inner.stopped] == ["d-worker"], stub.inner.stopped
        finally:
            stub.let_go.set()
            gw.close()
            door.close()

    def test_d_the_gateway_cuts_a_worker_revoked_under_a_running_job(self, tmp_path):
        world, _g, worker_dir, stub, door, client, gw = self._running(tmp_path)
        cuts: list = []
        client.watch.say = cuts.append
        try:
            thread, _ = _submit_in_background(gw, "d-gateway")
            assert stub.started.wait(timeout=20), "the job never reached the worker"
            assert world.issuer.revoke(serial_of(worker_dir))["effective"] is True
            lines, took = self._cut_within(gw, "d-gateway", client.tls.promised_seconds())
            assert lines, ("the gateway did not cut the connection to a revoked worker: the job "
                           "was still running %.1f s after the revocation took effect" % took)
            assert len(lines) == 1, lines
            assert lines[0]["termination_reason"] == "transport_lost"
            assert any("revoked" in s for s in cuts), cuts
        finally:
            stub.let_go.set()
            gw.close()
            door.close()

    def test_a_floor_that_ages_out_under_an_open_connection_cuts_it(self, tmp_path, monkeypatch):
        world, _g, _w, stub, door, client, gw = self._running(tmp_path)
        try:
            thread, _ = _submit_in_background(gw, "d-aged")
            assert stub.started.wait(timeout=20), "the job never reached the worker"
            now = floors._monotonic()
            monkeypatch.setattr(floors, "_monotonic", lambda: now + 10_000.0)
            lines, took = self._cut_within(gw, "d-aged", client.tls.promised_seconds())
            assert lines, "a connection stayed open on a floor nobody keeps up"
            assert lines[0]["termination_reason"] == "transport_lost"
        finally:
            stub.let_go.set()
            gw.close()
            door.close()


# ====================================================================== 5.6 recovery

def _renewal(folder: Path) -> tuple:
    body = json.loads(make_request(folder, renew=True).read_text())
    return body["csr"].encode(), body["current"].encode(), bytes.fromhex(body["signature"])


class TestRecoveryFromACompromisedKey:

    def test_recovery_locks_revokes_every_serial_publishes_and_hands_out_a_fresh_secret(
            self, world):
        worker = world.service("worker", "w1")
        world.issuer.renew(*_renewal(worker))                 # now: one overlapping, one current
        serials = [c["serial"] for c in world.issuer.inventory()["entries"]["worker/w1"]
                   ["certificates"]]
        assert len(serials) == 2
        done = world.issuer.recover_entry("worker", "w1", secret_at=worker / "secret.fresh")
        assert done["effective"] is True
        assert sorted(done["revoked"]) == sorted(serials)
        view = world.issuer.inventory()["entries"]["worker/w1"]
        assert view["renewal_locked"] is True
        assert all(c["revocation_reason"] == COMPROMISE for c in view["certificates"])
        published = rl.load(world.revocation_list, world._ca()[1], time.time())
        assert set(serials) <= published.serials
        assert (worker / "secret.fresh").exists()
        # The stolen key cannot renew its way back in.
        with pytest.raises(IssuanceRefused):
            world.issuer.renew(*_renewal(worker))
        # A fresh key, with the fresh secret, is enrolled -- and that lifts the lock.
        (worker / "key.pem").unlink()
        (worker / "key.next.pem").unlink()
        body = json.loads(make_request(worker, (worker / "secret.fresh").read_text()).read_text())
        world.issuer.enroll(body["csr"].encode(), body["secret"])
        assert world.issuer.inventory()["entries"]["worker/w1"]["renewal_locked"] is False

    def test_a_renewal_that_lands_before_the_recovery_is_revoked_by_it(self, world):
        worker = world.service("worker", "w1")
        world.issuer.renew(*_renewal(worker))
        renewed = [c["serial"] for c in world.issuer.inventory()["entries"]["worker/w1"]
                   ["certificates"] if c["status"] == "current"][0]
        world.issuer.recover_entry("worker", "w1", secret_at=worker / "secret.fresh")
        assert renewed in rl.load(world.revocation_list, world._ca()[1], time.time()).serials

    def test_a_renewal_that_comes_after_the_recovery_is_refused_whatever_key(self, world):
        worker = world.service("worker", "w1")
        request = _renewal(worker)
        world.issuer.recover_entry("worker", "w1", secret_at=worker / "secret.fresh")
        with pytest.raises(IssuanceRefused):
            world.issuer.renew(*request)

    def test_racing_them_gives_one_of_those_two_every_time(self, tmp_path):
        for attempt in range(6):
            place = World(tmp_path / ("race-%d" % attempt))
            worker = place.service("worker", "w1")
            request = _renewal(worker)
            barrier = threading.Barrier(2)
            outcome: dict = {}

            def renew():
                barrier.wait()
                try:
                    Issuer(place.root / "ca", place.root / "trust").renew(*request)
                    outcome["renew"] = "granted"
                except IssuanceRefused:
                    outcome["renew"] = "refused"

            def recover():
                barrier.wait()
                Issuer(place.root / "ca", place.root / "trust").recover_entry(
                    "worker", "w1", secret_at=worker / "secret.fresh")

            threads = [threading.Thread(target=renew), threading.Thread(target=recover)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=60)
            inventory = json.loads((place.root / "ca" / "inventory.json").read_text())
            certs = inventory["entries"]["worker/w1"]["certificates"]
            unrevoked = [c["serial"] for c in certs if c.get("revoked_at") is None]
            assert unrevoked == [], ("a renewal survived a recovery unrevoked (%s): %r"
                                     % (outcome.get("renew"), unrevoked))


# ====================================================================== (s) the window is bounded

class TestTheWindowIsBounded:

    def test_s_a_list_that_cannot_be_published_holds_back_the_floor_and_the_services_stop(
            self, world, monkeypatch):
        gateway = world.service("gateway", "g1")
        worker = world.service("worker", "w1")
        door = Door(world, worker, {"g1"}, label="w1")
        try:
            recording = F.RecordingFiles()
            recording.fail_in(world.root / "trust", "fsync_dir", OSError(5, "EIO, on purpose"))
            failing = Issuer(world.root / "ca", world.root / "trust", files=recording)
            assert failing.revoke(serial_of(gateway))["effective"] is False
            generations = {role: floors.parse(floors.path_for(world.floor_dir, role)
                                              .read_bytes()).generation for role in floors.ROLES}
            report = failing.tick(world.floor_dir)
            assert report["pending_revocation"] is True, report
            after = {role: floors.parse(floors.path_for(world.floor_dir, role)
                                        .read_bytes()).generation for role in floors.ROLES}
            assert after == generations, "a floor was promoted while a revocation was pending"
            # The revocation is not in effect (it was never published), and the services still
            # serve -- until the floor's maximum age. Then nobody is served.
            assert reach(world, gateway, door.address) is None
            now = floors._monotonic()
            monkeypatch.setattr(floors, "_monotonic", lambda: now + 901.0)
            said = reach(world, gateway, door.address)
            assert said and floors.TOO_OLD in said, said
        finally:
            door.close()
