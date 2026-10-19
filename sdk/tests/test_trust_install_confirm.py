"""The worker confirms what it INSTALLED, refuses everything worse, and survives an interrupted
install (trust-refresh C8, C9, C10).

Real CA, real lists, real files. The acknowledgement is computed from the files read back after the
write; a refusal before the write leaves both files byte-identical; a failure between the tombstone
and list writes is stricter, never acknowledged, and repaired by the next install."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import shutil
import time
from pathlib import Path

import pytest

from agentnode_sdk.pki import files as F
from agentnode_sdk.pki import revocation as rl
from agentnode_sdk.pki import tombstones as tomb
from agentnode_sdk.worker import trust_install as TI
from agentnode_sdk.worker.trust_install import FACT_FIELDS, TrustRefused, install
from tests.test_mtls_transport import _one_boot  # noqa: F401 - autouse: one boot throughout
from tests.test_mtls_transport import World


@pytest.fixture()
def world(tmp_path):
    return World(tmp_path)


@pytest.fixture()
def worker(world):
    folder = world.service("worker", "w1")
    shutil.copy(world.revocation_list, folder / "revoked.crl")
    shutil.copy(world.root / "trust" / tomb.LIST_NAME, folder / tomb.LIST_NAME)
    return dataclasses.replace(world.settings(folder, {"g1"}, role="worker"),
                               revocation_list=str(folder / "revoked.crl"),
                               identity_tombstones=str(folder / tomb.LIST_NAME), tombstones_required=True)


def _issued(world):
    return world.revocation_list.read_bytes(), (world.root / "trust" / tomb.LIST_NAME).read_bytes()


def _held(worker):
    return Path(worker.revocation_list).read_bytes(), Path(worker.identity_tombstones).read_bytes()


def _sign_tombstones(world, uris, *, now, deployment=None):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec

    key, _ = world._ca()
    return tomb.publish(deployment or world.deployment, uris, now=now,
                        signer=lambda b: key.sign(b, ec.ECDSA(hashes.SHA256())))


def test_the_answer_names_every_fact_of_the_installed_pair(world, worker):
    world.issuer.publish()
    crl, tombs = _issued(world)
    done = install(worker, crl, tombs)
    assert set(done) == set(FACT_FIELDS) | {"installed"} and done["installed"] is True
    assert done["revocation_list_sha256"] == hashlib.sha256(crl).hexdigest()
    assert done["tombstones_sha256"] == hashlib.sha256(tombs).hexdigest()
    assert done["revocation_list_number"] == rl.number_of(crl)
    assert done == dict(TI.facts_of(crl, tombs), installed=True)
    assert TI.differences(done, TI.facts_of(crl, tombs)) == []


def test_the_digests_are_of_what_was_installed_not_of_what_was_offered(world, worker, monkeypatch):
    """A write that silently did not happen is acknowledged as the OLD pair, so the control plane sees
    the mismatch (C10)."""
    old = _held(worker)
    world.issuer.publish()
    crl, tombs = _issued(world)
    monkeypatch.setattr(F, "durable_replace", lambda *a, **k: None)
    done = install(worker, crl, tombs)
    assert _held(worker) == old
    assert done["revocation_list_sha256"] == hashlib.sha256(old[0]).hexdigest() != hashlib.sha256(crl).hexdigest()
    assert "revocation_list_sha256" in TI.differences(done, TI.facts_of(crl, tombs))


def test_an_unreadable_read_back_is_a_refusal(world, worker, monkeypatch):
    world.issuer.publish()
    crl, tombs = _issued(world)
    real = TI._current
    calls = {"n": 0}

    def flaky(path):
        calls["n"] += 1
        return None if calls["n"] > 2 else real(path)           # the two pre-write reads succeed
    monkeypatch.setattr(TI, "_current", flaky)
    with pytest.raises(TrustRefused) as refused:
        install(worker, crl, tombs)
    assert refused.value.reason == "installed_unreadable"


def _refusal_cases(world, worker):
    key, cert = world._ca()
    crl, tombs = _issued(world)
    number = rl.number_of(_held(worker)[0])
    fkey, fcert, _ = world.foreign_ca()
    tampered = bytearray(crl)
    tampered[len(tampered) // 2] ^= 1
    now = time.time()
    return {
        "empty": (b"", tombs),
        "malformed": (b"-----BEGIN X509 CRL-----\nnot base64\n-----END X509 CRL-----\n", tombs),
        "tampered": (bytes(tampered), tombs),
        "wrong_signer": (rl.build(fkey, fcert, {}, number=number + 5, now=now), tombs),
        "expired": (rl.build(key, cert, {}, number=number + 5, now=now - 8 * 86400), tombs),
        "not_yet_valid": (rl.build(key, cert, {}, number=number + 5, now=now + 3600), tombs),
        "older": (rl.build(key, cert, {}, number=max(number - 1, 0), now=now), tombs) if number > 0 else None,
        "equivocal": (rl.build(key, cert, {"abc": now}, number=number, now=now), tombs),
        "tombstones_empty": (crl, b""),
        "tombstones_malformed": (crl, b'{"body": "x", "signature": "y"}'),
        "tombstones_wrong_deployment": (crl, _sign_tombstones(world, [], now=now, deployment="elsewhere")),
        "tombstones_expired": (crl, _sign_tombstones(world, [], now=now - 8 * 86400)),
        "tombstones_not_yet_valid": (crl, _sign_tombstones(world, [], now=now + 3600)),
    }


@pytest.mark.parametrize("case", ["empty", "malformed", "tampered", "wrong_signer", "expired", "not_yet_valid",
                                  "older", "equivocal", "tombstones_empty", "tombstones_malformed",
                                  "tombstones_wrong_deployment", "tombstones_expired", "tombstones_not_yet_valid"])
def test_a_refusal_before_the_write_leaves_both_files_byte_identical(world, worker, case):
    world.issuer.publish()
    install(worker, *_issued(world))                       # number >= 1 installed, so "older" exists
    offered = _refusal_cases(world, worker)[case]
    before = _held(worker)
    with pytest.raises(TrustRefused):
        install(worker, *offered)
    assert _held(worker) == before


class _FailBetween(F.Files):
    """Real files; an I/O failure right after the list's stage was written -- the tombstones are
    already replaced, the list is not."""

    def point(self, name):
        if name == "trust-list:written":
            raise OSError("disk went away")


def test_a_failure_between_the_two_writes_is_stricter_unacknowledged_and_repaired(world, worker):
    old_crl, old_tombs = _held(worker)
    withdrawn = "agentnode://%s/worker/w9" % world.deployment
    world.issuer.publish()
    crl = world.revocation_list.read_bytes()
    tombs = _sign_tombstones(world, [withdrawn], now=time.time())
    with pytest.raises(OSError):
        install(worker, crl, tombs, files=_FailBetween())     # no answer = no acknowledgement
    held_crl, held_tombs = _held(worker)
    assert held_crl == old_crl and held_tombs == tombs        # stricter: the withdrawal is in force
    assert withdrawn in tomb.read(held_tombs, deployment=world.deployment,
                                  verifier=worker.trust("worker")._verify_with_anchor).uris
    rl.read(held_crl, world._ca()[1], time.time())            # the old list is still a valid list
    done = install(worker, crl, tombs)                        # the retry completes it, despite the stale stage
    assert done["installed"] is True and _held(worker) == (crl, tombs)
    assert not list(Path(worker.revocation_list).parent.glob(".*.tmp"))
    assert install(worker, crl, tombs)["installed"] is False  # and is idempotent from then on


def test_differences_names_each_field(world, worker):
    facts = TI.facts_of(*_issued(world))
    assert TI.differences(facts, facts) == []
    assert TI.differences(None, facts) == list(FACT_FIELDS)
    assert TI.differences({k: v for k, v in facts.items() if k != "tombstones_sha256"}, facts) == ["tombstones_sha256"]
    for field in FACT_FIELDS:
        bad = dict(facts, **{field: ("0" * 64 if field.endswith("sha256") else 10 ** 6)})
        assert TI.differences(bad, facts) == [field]
    assert TI.differences(dict(facts, revocation_list_number=True), facts) == ["revocation_list_number"]


def test_facts_of_refuses_what_does_not_parse(world):
    crl, tombs = _issued(world)
    for bad in ((b"", tombs), (crl, b"{}"), (crl, b"not json")):
        with pytest.raises(ValueError):
            TI.facts_of(*bad)
    assert json.loads(tombs)["body"]["published_at"] == TI.facts_of(crl, tombs)["tombstones_published_at"]
