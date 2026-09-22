"""mtls-revocation-time-r1: two-stage publication of the list and the floor (decision 5.0; stage 5
(r), (r')), under the forced two-outcome fault model.

The model is the stage-1 one (`pki/files.py`, `RecordingFiles`): every file operation goes
through one seam, and a simulated crash ends in a FORCED outcome -- LOST (every directory
operation since that directory's last fsync undone, unsynced contents torn) or SURVIVED
(everything kept). Each crash case runs in both. The implementation must hold in both; each
counter-check must go red in LOST.

(r) is observed in the restored namespace BEFORE the root run is allowed to do anything -- so that
neither regenerating the list from the inventory nor the floor's boot rule can repair what the
crash left. What must be there, under the name the services read or under the stage name, is
durable content carrying every serial a service could have seen before the crash, and a floor
that is not behind the one a service could have seen.
"""
from __future__ import annotations

import time

import pytest

from agentnode_sdk.pki import files as F
from agentnode_sdk.pki import floor as floors
from agentnode_sdk.pki import revocation as rl
from agentnode_sdk.pki.issuer import Issuer
from tests.test_mtls_transport import _one_boot  # noqa: F401 - autouse: one boot throughout
from tests.test_mtls_transport import World


def serial_of(folder) -> str:
    from cryptography import x509

    return format(x509.load_pem_x509_certificate((folder / "cert.pem").read_bytes())
                  .serial_number, "x")


def _list_serials(world, path) -> set:
    try:
        return set(rl.read(path.read_bytes(), world._ca()[1], time.time()).serials)
    except (OSError, rl.ListUnusable):
        return set()


def _floor_value(path):
    try:
        return floors.parse(path.read_bytes()).floor
    except (OSError, floors.FloorUnusable):
        return None


class _Seeing(F.RecordingFiles):
    """Records what a SERVICE could read under the published name at a given point."""

    def __init__(self, watch_point, look):
        super().__init__()
        self.watch_point = watch_point
        self.look = look
        self.seen = None

    def point(self, name):
        # The FIRST time only: that is the moment the question is about.
        if name == self.watch_point and self.seen is None:
            self.seen = self.look()
        super().point(name)


# ====================================================================== (r) the list

class TestTheListUnderTheFaultModel:

    @pytest.mark.parametrize("outcome", F.OUTCOMES)
    def test_r_a_crash_between_promotion_and_its_fsync_loses_no_seen_serial(self, tmp_path,
                                                                           outcome):
        world = World(tmp_path)
        g1 = world.service("gateway", "g1")
        stage, _tmp = F.stage_names(world.revocation_list)
        recording = _Seeing("list:promoted",
                            lambda: _list_serials(world, world.revocation_list))
        recording.crash_at("list:promoted")
        with pytest.raises(F.SimulatedCrash):
            Issuer(world.root / "ca", world.root / "trust", files=recording).revoke(
                serial_of(g1))
        assert serial_of(g1) in recording.seen, "the model did not reach the promotion"
        recording.settle(outcome)
        # The machine is back. The root run is held: nothing has touched the namespace since.
        durable = [_list_serials(world, p) for p in (world.revocation_list, stage)]
        assert any(recording.seen <= found for found in durable), (
            "after a crash in %s, no durable list carries every serial a service saw: saw %r, "
            "found %r" % (outcome, sorted(recording.seen), [sorted(d) for d in durable]))

    @pytest.mark.parametrize("outcome", F.OUTCOMES)
    def test_a_crash_before_the_stage_was_durable_changed_nothing_a_service_reads(
            self, tmp_path, outcome):
        world = World(tmp_path)
        g1 = world.service("gateway", "g1")
        before = world.revocation_list.read_bytes()
        recording = F.RecordingFiles()
        recording.crash_at("list:stage-renamed")
        with pytest.raises(F.SimulatedCrash):
            Issuer(world.root / "ca", world.root / "trust", files=recording).revoke(
                serial_of(g1))
        recording.settle(outcome)
        assert world.revocation_list.read_bytes() == before
        report = world.tick()                                 # and the root run carries it on
        assert serial_of(g1) in _list_serials(world, world.revocation_list), report


# ====================================================================== (r) the floor

class TestTheFloorUnderTheFaultModel:

    @pytest.mark.parametrize("outcome", F.OUTCOMES)
    def test_r_a_crash_between_promotion_and_its_fsync_leaves_no_floor_behind_the_seen(
            self, tmp_path, outcome, monkeypatch):
        world = World(tmp_path)
        path = floors.path_for(world.floor_dir, "worker")
        stage, _tmp = F.stage_names(path)
        before = _floor_value(path)
        later = time.time() + 120.0
        monkeypatch.setattr("agentnode_sdk.pki.issuer._now", lambda: later)
        now = floors._monotonic()
        monkeypatch.setattr(floors, "_monotonic", lambda: now + 120.0)
        recording = _Seeing("floor-worker:promoted", lambda: _floor_value(path))
        recording.crash_at("floor-worker:promoted")
        with pytest.raises(F.SimulatedCrash):
            Issuer(world.root / "ca", world.root / "trust", files=recording).tick(
                world.floor_dir)
        assert recording.seen is not None and recording.seen > before, (
            "the model did not reach a floor that moved")
        recording.settle(outcome)
        durable = [v for v in (_floor_value(path), _floor_value(stage)) if v is not None]
        assert durable and max(durable) >= recording.seen, (
            "after a crash in %s, the durable floor is behind the one a service saw: saw %.3f, "
            "found %r" % (outcome, recording.seen, durable))


# ====================================================================== (r') the order after a restart

class TestAfterARestart:

    def test_r_prime_nothing_is_served_before_the_stage_is_promoted_and_the_floor_written(
            self, tmp_path, monkeypatch):
        from agentnode_sdk.pki.trust import TrustView
        from agentnode_sdk.pki import identity as ids

        world = World(tmp_path)
        g1 = world.service("gateway", "g1")
        recording = F.RecordingFiles()
        recording.crash_at("list:promoted")
        with pytest.raises(F.SimulatedCrash):
            Issuer(world.root / "ca", world.root / "trust", files=recording).revoke(
                serial_of(g1))
        recording.settle(F.LOST)                               # the list is back in its stage
        monkeypatch.setattr(floors, "_boot", lambda: "after-the-crash")

        def view():
            return TrustView.read(anchor=world.anchor, revocation_list=world.revocation_list,
                                  floor=floors.path_for(world.floor_dir, "worker"),
                                  role="worker")

        with pytest.raises(ids.PeerRefused) as caught:        # before the root run: no service
            view().effective_time()
        assert caught.value.check == floors.OTHER_BOOT

        order = _Seeing("floor-worker:stage-written",
                        lambda: _list_serials(world, world.revocation_list))
        Issuer(world.root / "ca", world.root / "trust", files=order).tick(world.floor_dir)
        assert order.seen is not None, "the root run did not write the floor"
        assert serial_of(g1) in order.seen, (
            "the floor was written while the list the services read still lacked a revoked "
            "serial: the stage was not promoted first")
        assert view().effective_time() > 0                    # and now it serves

    def test_an_older_stage_is_dropped_and_a_newer_one_promoted(self, tmp_path):
        world = World(tmp_path)
        stage, _ = F.stage_names(world.revocation_list)
        current = world.revocation_list.read_bytes()
        number = rl.number_of(current)
        ca_key, ca_cert = world._ca()
        stage.write_bytes(rl.build(ca_key, ca_cert, {}, number=number - 1, now=time.time()))
        assert F.settle_stage(F.Files(), world.revocation_list,
                              lambda s, c: (rl.number_of(s) or 0) > (rl.number_of(c) or 0)) \
            == "removed"
        assert world.revocation_list.read_bytes() == current and not stage.exists()
        stage.write_bytes(rl.build(ca_key, ca_cert, {}, number=number + 5, now=time.time()))
        report = world.tick()
        assert "promoted" in report["list"], report
        assert rl.number_of(world.revocation_list.read_bytes()) >= number + 5
