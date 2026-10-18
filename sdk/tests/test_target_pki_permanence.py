"""A withdrawn identity stays withdrawn: no later add, enrol, renew or recovery brings it back
(target-router profile T11, decision Q-TGT-0001 D5 / R6).

Before this, `recover_entry` handed out a fresh secret for the SAME name and `enroll` cleared the
lock, so the next publication dropped the identity from the tombstones. The inventory now keeps
every identity it ever withdrew, and the tombstones always carry that whole set.
"""
from __future__ import annotations

import json
import time

import pytest

from agentnode_sdk.pki import tombstones as tomb
from agentnode_sdk.pki.issuer import IssuanceRefused, make_request
from tests.test_mtls_transport import _one_boot  # noqa: F401 - autouse: one boot throughout
from tests.test_mtls_transport import World


@pytest.fixture()
def world(tmp_path):
    return World(tmp_path)


def _published_uris(world) -> set:
    raw = (world.root / "trust" / tomb.LIST_NAME).read_bytes()
    body = json.loads(raw)["body"]
    body = json.loads(body) if isinstance(body, str) else body
    return set(body["uris"])


def _uri(world, instance: str) -> str:
    return "agentnode://%s/worker/%s" % (world.deployment, instance)


def test_withdrawn_uri_cannot_reenroll(world):
    folder = world.service("worker", "w5")
    serials = [c["serial"] for c in world.issuer.inventory()["entries"]["worker/w5"]["certificates"]]
    for serial in serials:
        world.issuer.revoke(serial)
    inventory = world.issuer.inventory()
    assert _uri(world, "w5") in inventory["ever_withdrawn"]
    # the name cannot be added again, so no secret for it can ever exist
    with pytest.raises(IssuanceRefused):
        world.issuer.add("worker", "w5", secret_at=folder / "secret2", deliver_to=folder / "cert.pem")
    # even an entry hand-edited back to an unclaimed secret is refused at enrolment
    inv = json.loads((world.root / "ca" / "inventory.json").read_text())
    from agentnode_sdk.pki import enrolment
    secret = enrolment.mint_a_secret()
    import hashlib
    inv["entries"]["worker/w5"]["secret_sha256"] = hashlib.sha256(secret.encode()).hexdigest()
    inv["entries"]["worker/w5"]["secret_expires"] = time.time() + 3600
    inv["entries"]["worker/w5"]["renewal_locked"] = False
    (world.root / "ca" / "inventory.json").write_text(json.dumps(inv))
    (folder / "key.pem").unlink()
    body = json.loads(make_request(folder, secret).read_text())
    with pytest.raises(IssuanceRefused):
        world.issuer.enroll(body["csr"].encode("ascii"), body["secret"])
    world.issuer.publish()
    assert _uri(world, "w5") in _published_uris(world)


def test_recovery_cannot_reactivate_uri(world):
    folder = world.service("worker", "w6")
    done = world.issuer.recover_entry("worker", "w6", secret_at=folder / "secret.fresh")
    assert done["withdrawn"] == _uri(world, "w6")
    assert not (folder / "secret.fresh").exists()                 # no secret for the same name
    entry = world.issuer.inventory()["entries"]["worker/w6"]
    assert entry["claimed"] is True and entry["renewal_locked"] is True   # no live secret
    with pytest.raises(IssuanceRefused):
        world.issuer.add("worker", "w6", secret_at=folder / "s", deliver_to=folder / "cert.pem")
    # a later, unrelated commit (another enrolment) and a fresh publication keep it withdrawn
    world.service("worker", "w7")
    world.issuer.publish()
    world.tick()
    assert _uri(world, "w6") in _published_uris(world)
    assert _uri(world, "w6") in world.issuer.inventory()["ever_withdrawn"]
    assert _uri(world, "w7") not in _published_uris(world)


def test_a_withdrawal_survives_the_entry_being_edited_back(world):
    world.service("worker", "w8")
    world.issuer.recover_entry("worker", "w8")
    inv = json.loads((world.root / "ca" / "inventory.json").read_text())
    entry = inv["entries"]["worker/w8"]
    entry["renewal_locked"] = False                                # undo every per-entry trace
    for c in entry["certificates"]:
        c["status"] = "current"
        c.pop("revoked_at", None)
    (world.root / "ca" / "inventory.json").write_text(json.dumps(inv))
    world.issuer.publish()
    assert _uri(world, "w8") in _published_uris(world)
