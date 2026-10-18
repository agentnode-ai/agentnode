"""A worker takes fresher CA-signed trust lists from its control plane, and nothing worse
(target-router profile T21, resolution R3).
"""
from __future__ import annotations

import dataclasses
import shutil
import time

import pytest

from agentnode_sdk.pki import revocation as rl
from agentnode_sdk.pki import tombstones as tomb
from agentnode_sdk.worker.trust_install import TrustRefused, install
from tests.test_mtls_transport import _one_boot  # noqa: F401 - autouse: one boot throughout
from tests.test_mtls_transport import World


@pytest.fixture()
def world(tmp_path):
    return World(tmp_path)


@pytest.fixture()
def worker(world):
    """A worker's settings with its OWN copies of the two lists, as on a separate host."""
    folder = world.service("worker", "w1")
    shutil.copy(world.revocation_list, folder / "revoked.crl")
    shutil.copy(world.root / "trust" / tomb.LIST_NAME, folder / tomb.LIST_NAME)
    settings = dataclasses.replace(world.settings(folder, {"g1"}, role="worker"),
                                   revocation_list=str(folder / "revoked.crl"),
                                   identity_tombstones=str(folder / tomb.LIST_NAME),
                                   tombstones_required=True)
    return settings


def _issued(world):
    return world.revocation_list.read_bytes(), (world.root / "trust" / tomb.LIST_NAME).read_bytes()


def _sign_tombstones(world, uris, *, now):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec

    key, _ = world._ca()
    return tomb.publish(world.deployment, uris, now=now, signer=lambda b: key.sign(b, ec.ECDSA(hashes.SHA256())))


def test_a_newer_list_from_the_issuer_is_installed_and_acknowledged(world, worker):
    before = rl.number_of(open(worker.revocation_list, "rb").read())
    world.issuer.publish()
    crl, tombs = _issued(world)
    done = install(worker, crl, tombs)
    assert done["installed"] is True and done["revocation_list_number"] > before
    assert open(worker.revocation_list, "rb").read() == crl
    assert open(worker.identity_tombstones, "rb").read() == tombs
    again = install(worker, crl, tombs)                      # the same documents: nothing to do
    assert again["installed"] is False


def test_an_older_list_is_refused(world, worker):
    old_crl, old_tombs = _issued(world)
    world.issuer.publish()
    install(worker, *_issued(world))
    with pytest.raises(TrustRefused) as refused:
        install(worker, old_crl, old_tombs)
    assert refused.value.reason == "revocation_list_older"


def test_two_lists_with_one_number_are_equivocation(world, worker):
    key, cert = world._ca()
    installed = rl.number_of(open(worker.revocation_list, "rb").read())
    twin = rl.build(key, cert, {"abc123": time.time()}, number=installed, now=time.time())
    with pytest.raises(TrustRefused) as refused:
        install(worker, twin, open(worker.identity_tombstones, "rb").read())
    assert refused.value.reason == "revocation_list_equivocation"


def test_a_list_from_another_ca_is_refused(world, worker):
    key, cert, _ = world.foreign_ca()
    forged = rl.build(key, cert, {}, number=99, now=time.time())
    with pytest.raises(TrustRefused) as refused:
        install(worker, forged, open(worker.identity_tombstones, "rb").read())
    assert refused.value.reason == "revocation_list_" + rl.SIGNATURE
    assert open(worker.revocation_list, "rb").read() != forged


def test_a_shrinking_tombstone_list_is_refused(world, worker):
    withdrawn = "agentnode://%s/worker/w9" % world.deployment
    now = time.time()
    install(worker, world.revocation_list.read_bytes(), _sign_tombstones(world, [withdrawn], now=now))
    with pytest.raises(TrustRefused) as refused:
        install(worker, world.revocation_list.read_bytes(), _sign_tombstones(world, [], now=now + 5))
    assert refused.value.reason == "tombstones_shrink"
    assert withdrawn in tomb.read(open(worker.identity_tombstones, "rb").read(),
                                  deployment=world.deployment,
                                  verifier=worker.trust("worker")._verify_with_anchor).uris


def test_an_older_tombstone_list_is_refused(world, worker):
    now = time.time()
    install(worker, world.revocation_list.read_bytes(), _sign_tombstones(world, [], now=now))
    with pytest.raises(TrustRefused) as refused:
        install(worker, world.revocation_list.read_bytes(), _sign_tombstones(world, [], now=now - 60))
    assert refused.value.reason == "tombstones_older"
