"""A key belonging to one gateway and one worker, instead of one key belonging to everybody.

`worker/protocol.py` authenticated every frame with a single file whose bytes were the same on
both sides, covering every job and every customer. Its docstring was honest about the bargain:
*"A shared key on one machine is worth what the machine is worth."* Move the worker to another
machine and the bargain stops holding, because the machine is then one whose whole purpose is
running other people's code.

`remote-worker-r1` R5 asks what a worker is given, judged from what actually crosses rather than
from what the design intends. These tests are about the credential itself: that it belongs to a
pair, that it is chosen by an identity the handshake proved rather than by anything on the wire,
that another pair's key does not work, and that a rotation does not make work in flight
unreadable.

NOT A HOST-ISOLATION TEST. One process, one kernel, loopback. What it establishes is which
credential would be on the other machine, not what having two machines would buy.
"""
from __future__ import annotations

import base64
import os
import sys

import pytest

from agentnode_sdk.worker import SEPARATE_WORKER_HOST, SINGLE_HOST_DEVELOPMENT
from agentnode_sdk.worker import pairkeys as K
from agentnode_sdk.worker import protocol as wire
from agentnode_sdk.worker.remote import TlsWorker
from tests.test_mtls_transport import (_one_boot, a_job, pair,  # noqa: F401  (fixtures)
                                       settle, world)


def a_key(seed: bytes = b"") -> bytes:
    return (seed or base64.urlsafe_b64decode(wire.new_key()))[:48].ljust(48, b"\0")


class TestTheKeyBelongsToAPair:

    def test_a_record_needs_both_names_to_be_found(self):
        """Not a formality: the caller supplies its OWN name from its own certificate and the
        peer's from the certificate the handshake proved, so a record for another pair is not
        reachable -- which is what "reject use by another pair" means when there is no key
        identifier on the wire to reject."""
        ring = K.empty().add(gateway="g1", worker="w1", key=a_key())
        assert ring.for_pair(gateway="g1", worker="w1")
        for wrong in (("g1", "w2"), ("g2", "w1"), ("g2", "w2")):
            with pytest.raises(K.KeyringRefused) as refused:
                ring.for_pair(gateway=wrong[0], worker=wrong[1])
            assert refused.value.cause == K.NO_RECORD

    def test_two_pairs_do_not_share_a_key(self):
        ring = (K.empty().add(gateway="g1", worker="w1", key=a_key())
                         .add(gateway="g1", worker="w2", key=a_key()))
        one = ring.for_pair(gateway="g1", worker="w1")
        two = ring.for_pair(gateway="g1", worker="w2")
        assert one.current != two.current

    def test_an_entry_missing_one_of_the_two_names_is_refused(self, tmp_path):
        """A record naming only one end is a shared key wearing a pair's clothes."""
        at = tmp_path / "keys.json"
        at.write_text('{"format": 1, "pairs": [{"gateway": "g1", "worker": "", '
                      '"current": "%s"}]}' % base64.urlsafe_b64encode(a_key()).decode(),
                      encoding="utf-8")
        with pytest.raises(K.KeyringRefused) as refused:
            K.Keyring.read(at)
        assert refused.value.cause == K.MALFORMED

    def test_the_material_is_never_rendered(self):
        """These objects reach tracebacks, and a traceback is a log."""
        key = a_key()
        one = K.empty().add(gateway="g1", worker="w1", key=key).for_pair(gateway="g1",
                                                                        worker="w1")
        for shown in (repr(one), str(one), "%s" % (one,)):
            assert base64.urlsafe_b64encode(key).decode() not in shown
            assert key.hex() not in shown
            assert "g1" in shown and "w1" in shown

    def test_a_short_key_is_refused(self, tmp_path):
        at = tmp_path / "keys.json"
        at.write_text('{"format": 1, "pairs": [{"gateway": "g1", "worker": "w1", '
                      '"current": "%s"}]}' % base64.urlsafe_b64encode(b"tiny").decode(),
                      encoding="utf-8")
        with pytest.raises(K.KeyringRefused) as refused:
            K.Keyring.read(at)
        assert refused.value.cause == K.TOO_SHORT

    @pytest.mark.skipif(sys.platform == "win32",
                        reason="POSIX file modes; Windows has no group or other bits to set")
    def test_the_file_is_written_narrow(self, tmp_path):
        at = tmp_path / "keys.json"
        K.empty().add(gateway="g1", worker="w1", key=a_key()).write(at)
        assert os.stat(at).st_mode & 0o777 == K.FILE_MODE


class TestRotationKeepsWorkInFlightReadable:

    def test_the_previous_key_is_accepted_during_the_overlap(self):
        was = a_key()
        ring = K.empty().add(gateway="g1", worker="w1", key=was)
        rotated = ring.rotate(gateway="g1", worker="w1", key=a_key())
        now = rotated.for_pair(gateway="g1", worker="w1")
        assert now.generation == 2
        assert now.current != was
        assert was in now.accepted(), (
            "a request already sealed with the old key must still verify, or a rotation makes "
            "work in flight unknowable")

    def test_and_stops_being_accepted_once_the_overlap_is_ended(self):
        was = a_key()
        ring = (K.empty().add(gateway="g1", worker="w1", key=was)
                         .rotate(gateway="g1", worker="w1", key=a_key())
                         .retire_overlap(gateway="g1", worker="w1"))
        assert was not in ring.for_pair(gateway="g1", worker="w1").accepted()

    def test_ending_the_overlap_is_a_separate_step(self):
        """So it happens when somebody decides it does, not when they were not looking."""
        assert hasattr(K.Keyring, "retire_overlap")
        ring = K.empty().add(gateway="g1", worker="w1", key=a_key())
        after = ring.rotate(gateway="g1", worker="w1", key=a_key())
        assert len(after.for_pair(gateway="g1", worker="w1").accepted()) == 2

    def test_a_frame_verifies_under_either_key_of_the_overlap(self):
        old, new = a_key(), a_key()
        body = wire.request("describe", {}, deadline=9e9)
        sealed_old = wire.seal(body, old)
        payload = sealed_old[36:]
        mac = sealed_old[4:36]
        assert wire.unseal(payload, mac, (new, old)) == body
        with pytest.raises(wire.ProtocolError):
            wire.unseal(payload, mac, (new,))


class TestOverTheRealTransport:

    def _ring(self, worker_name="w1"):
        return K.empty().add(gateway="g1", worker=worker_name, key=a_key())

    def test_a_job_crosses_authenticated_by_the_pair_key(self, pair):  # noqa: F811  (a pytest fixture, imported)
        """The whole path: TLS proves who, the pair key authenticates the frames, the job runs.
        Neither side holds a key shared with anything else."""
        world, gateway, _worker, door = pair  # noqa: F811  (a pytest fixture, imported)
        ring = self._ring()
        door.listener.use_keyring(ring, "w1")

        client = TlsWorker(door.address, b"", world.settings(gateway, {"w1"}))
        client.use_keyring(ring, "g1")
        outcome = client.run(a_job("paired"))

        assert outcome.stdout == "RAN"
        assert [j.run_id for j in door.stub.ran] == ["paired"]

    def test_a_gateway_the_worker_holds_no_key_for_is_not_served(self, pair):  # noqa: F811  (a pytest fixture, imported)
        """The certificate is accepted -- it is a real gateway of this deployment -- and the
        worker still refuses, because it has no key for THIS pair and will not fall back to
        one it has for another."""
        world, gateway, _worker, door = pair  # noqa: F811  (a pytest fixture, imported)
        door.listener.use_keyring(self._ring(worker_name="w9"), "w1")

        client = TlsWorker(door.address, b"", world.settings(gateway, {"w1"}))
        client.use_keyring(self._ring(worker_name="w9"), "g1")
        with pytest.raises(Exception):
            client.run(a_job("unpaired"))
        settle()
        assert door.stub.ran == [], "nothing ran for a pair the worker holds no key for"

    def test_and_the_refusal_does_not_hand_back_the_keyrings_index(self, pair):  # noqa: F811  (a pytest fixture, imported)
        """Found by the suite's own warning rather than by a test: the refusal escaped into the
        connection thread, where Python printed a traceback listing every pair this worker DOES
        hold ("This side holds keys for: g1<->w9"). A caller the worker has no key for should
        learn nothing at all, and the log should not be handed the index either."""
        world, gateway, _worker, door = pair  # noqa: F811  (a pytest fixture, imported)
        door.listener.use_keyring(self._ring(worker_name="w9"), "w1")
        said = []
        door.listener.say = said.append

        client = TlsWorker(door.address, b"", world.settings(gateway, {"w1"}))
        client.use_keyring(self._ring(worker_name="w9"), "g1")
        with pytest.raises(Exception):
            client.run(a_job("unpaired"))
        settle()

        whole = " ".join(said)
        # The leak first, deliberately. With both orders the test goes red when the exception's
        # own message comes back -- but only this order goes red ON THE LEAK, and a counter-check
        # that fails on the line above it has shown nothing about the property it names.
        assert "w9" not in whole, "and nothing else this worker holds is"
        assert "no_key_for_this_pair" in whole, "the cause is named, once"

    def test_another_pairs_key_does_not_authenticate(self, pair):  # noqa: F811  (a pytest fixture, imported)
        """Two valid keyrings, for two different pairs. The frames do not verify."""
        world, gateway, _worker, door = pair  # noqa: F811  (a pytest fixture, imported)
        door.listener.use_keyring(self._ring(), "w1")

        client = TlsWorker(door.address, b"", world.settings(gateway, {"w1"}))
        client.use_keyring(self._ring(), "g1")          # a DIFFERENT random key for the same pair
        with pytest.raises(Exception):
            client.run(a_job("mismatched"))
        settle()
        assert door.stub.ran == [], "a different key for the same names is still a different key"


class TestTheGlobalKeyDoesNotCrossTheBoundary:

    def test_a_remote_gateway_without_a_keyring_is_refused(self, tmp_path):
        """R5: the single `worker_key` is one secret covering every job and every customer. It
        is not discouraged across the boundary -- it is refused."""
        from types import SimpleNamespace

        from agentnode_sdk.gateway.server import GatewayService

        # `config` is a property on the real class, so the method is called against a stand-in
        # that has the one attribute it reads. What is being tested is the rule, not the class.
        service = SimpleNamespace(config={"worker_keyring": ""})
        with pytest.raises(K.KeyringRefused) as refused:
            GatewayService._pair_keys(service, SEPARATE_WORKER_HOST, None)
        assert refused.value.cause == K.NO_FILE
        assert "worker_keyring" in refused.value.what_to_do

    def test_and_locally_it_is_still_allowed(self):
        from types import SimpleNamespace

        from agentnode_sdk.gateway.server import GatewayService

        service = SimpleNamespace(config={})
        assert GatewayService._pair_keys(service, SINGLE_HOST_DEVELOPMENT, None) == (None, "")

    def test_a_remote_worker_without_a_keyring_refuses_to_serve(self, tmp_path):
        from agentnode_sdk.worker.service import serve

        with pytest.raises(K.KeyringRefused) as refused:
            # A journal IS given, so the only thing missing is the keyring and the refusal
            # under test is the one this test is about.
            serve("", str(tmp_path / "nokey"), None, worker=object(),
                  tls_address="tcps://10.0.0.9:8443", tls=object(),
                  topology=SEPARATE_WORKER_HOST, journal_at=str(tmp_path / "journal"))
        assert refused.value.cause == K.NO_FILE
        assert "--keyring" in refused.value.what_to_do
