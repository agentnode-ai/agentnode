"""The signed target register (target-router profile T2, T3, T4; Codex D1)."""
from __future__ import annotations

import json
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agentnode_sdk.remote_mcp import targets as T

from .target_fixtures import Clock, activate, customer_spec, make_register, raw_pub

ALICE = "acct-00000000000000a1"
BOB = "acct-00000000000000b2"


@pytest.fixture
def reg(tmp_path):
    return make_register(tmp_path / "stand")[0]


def test_init_has_exactly_one_active_managed_default(reg):
    snap = reg.load()
    assert snap.default_target == "managed-w1" and snap.target("managed-w1").status == T.ACTIVE
    assert [t.target_id for t in snap.visible_targets(ALICE)] == ["managed-w1"]


def test_customer_targets_are_visible_only_to_their_owner(reg):
    reg.add_customer(customer_spec("cust-aaa", ALICE, "c1"), actor="t")
    snap = reg.load()
    assert snap.visible(ALICE, "cust-aaa") is not None
    assert snap.visible(BOB, "cust-aaa") is None and snap.visible(BOB, "nope") is None
    assert [t.target_id for t in snap.visible_targets(BOB)] == ["managed-w1"]


def test_terminal_target_is_immutable(reg):
    reg.add_customer(customer_spec("cust-aaa", ALICE, "c1"), actor="t")
    reg.set_status("cust-aaa", T.REVOKED, actor="t")
    for status in (T.ACTIVE, T.INVITED, T.SUSPENDED):
        with pytest.raises(T.TargetsRefused):
            reg.set_status("cust-aaa", status, actor="t")
    with pytest.raises(T.TargetsRefused):
        reg.update_worker("cust-aaa", {"key_slot": "k2"}, actor="t")
    # and a hand-signed successor that changes it is refused at load
    snap = reg.load()
    doc = json.loads(json.dumps({k: v for k, v in snap.document.items() if k != "signature"}))
    doc["targets"]["cust-aaa"]["status"] = T.ACTIVE
    assert any("terminal" in e for e in T.check_successor(snap.document, dict(doc, generation=snap.generation + 1,
                                                                                prev_digest=T.digest(snap.document))))


def test_target_id_never_reused(reg):
    reg.add_customer(customer_spec("cust-aaa", ALICE, "c1"), actor="t")
    reg.set_status("cust-aaa", T.REVOKED, actor="t")
    with pytest.raises(T.TargetsRefused) as refused:
        reg.add_customer(customer_spec("cust-aaa", ALICE, "c9"), actor="t")
    assert refused.value.code == "target.id_used"


def test_worker_uri_never_reused(reg):
    reg.add_customer(customer_spec("cust-aaa", ALICE, "c1"), actor="t")
    reg.set_status("cust-aaa", T.REVOKED, actor="t")
    with pytest.raises(T.TargetsRefused) as refused:
        reg.add_customer(customer_spec("cust-bbb", BOB, "c1"), actor="t")
    assert refused.value.code == "target.uri_used"
    with pytest.raises(T.TargetsRefused):                     # nor a second live target on one URI
        reg.add_customer(customer_spec("cust-ccc", BOB, "w1"), actor="t")


def test_expiry_is_monotonic(tmp_path):
    clock = Clock(time.time())
    reg, _ = make_register(tmp_path / "stand", clock=clock)
    reg.add_customer(customer_spec("cust-aaa", ALICE, "c1", expires_at=clock.t + 600), actor="t")
    assert reg.load().target("cust-aaa").effective_status(reg.load().now) == T.INVITED
    clock.t += 3600                                          # past expiry: expired, whatever is stored
    snap = reg.load()
    assert snap.target("cust-aaa").effective_status(snap.now) == T.EXPIRED
    clock.t -= 7200                                          # the clock set back: still expired
    snap = reg.load()
    assert snap.now >= clock.t + 7200 - 1
    assert snap.target("cust-aaa").effective_status(snap.now) == T.EXPIRED
    with pytest.raises(T.TargetsRefused):
        reg.set_status("cust-aaa", T.ENROLLED, actor="t")


def test_missing_target_history_fails_closed(reg):
    reg.add_customer(customer_spec("cust-aaa", ALICE, "c1"), actor="t")
    reg.set_account_default(ALICE, "cust-aaa", actor="t")
    (reg.history / "2.json").unlink()
    reg._verified = None
    with pytest.raises(T.TargetsUnavailable):
        reg.load()


def test_mismatched_history_fails_closed(reg):
    reg.add_customer(customer_spec("cust-aaa", ALICE, "c1"), actor="t")
    reg._verified = None
    (reg.history / "1.json").write_text("{}")
    with pytest.raises(T.TargetsUnavailable):
        reg.load()


def test_target_register_rollback_refused(reg):
    gen1 = reg.path.read_bytes()
    reg.add_customer(customer_spec("cust-aaa", ALICE, "c1"), actor="t")
    reg.path.write_bytes(gen1)                               # an older, validly signed generation
    with pytest.raises(T.TargetsUnavailable) as refused:
        reg.load()
    assert "older than the pinned head" in str(refused.value)


def test_a_fork_with_the_pinned_generation_is_refused(tmp_path):
    reg, op = make_register(tmp_path / "stand")
    snap = reg.load()
    fork = {k: v for k, v in snap.document.items() if k != "signature"}
    fork["targets"]["managed-w1"]["limits"]["queue"] = 99
    fork["signature"] = {"key_id": reg.trusted_key_id, "signature": op.sign(T._signed_bytes(fork)).hex()}
    reg.path.write_text(json.dumps(fork))
    with pytest.raises(T.TargetsUnavailable) as refused:
        reg.load()
    assert "equivocation" in str(refused.value)


def test_unsigned_target_register_refused(reg):
    doc = json.loads(reg.path.read_text())
    doc["signature"]["signature"] = Ed25519PrivateKey.generate().sign(T._signed_bytes(doc)).hex()
    reg.path.write_text(json.dumps(doc))
    with pytest.raises(T.TargetsUnavailable):
        reg.load()
    other = Ed25519PrivateKey.generate()
    foreign = T.Register(reg.stand, raw_pub(other))
    with pytest.raises(T.TargetsUnavailable):
        foreign.load()


@pytest.mark.parametrize("damage", ["corrupt", "truncated", "missing"])
def test_damaged_register_fails_closed(reg, damage):
    if damage == "corrupt":
        reg.path.write_text("{not json")
    elif damage == "truncated":
        reg.path.write_bytes(reg.path.read_bytes()[:40])
    else:
        reg.path.unlink()
    with pytest.raises(T.TargetsUnavailable):
        reg.load()


def test_document_shape_negatives():
    base = make_register.__globals__  # noqa: F841  (keep the import used)
    from .target_fixtures import managed_spec
    m = T.Register._new_entry(managed_spec(), generation=1)
    good = {"schema": T.SCHEMA, "generation": 1, "prev_digest": T.GENESIS, "targets": {m["target_id"]: m},
            "account_defaults": {}, "default_target": m["target_id"], "used_target_ids": [m["target_id"]],
            "used_identity_uris": [m["worker"]["identity_uri"]], "signature": {"key_id": "x", "signature": "y"}}
    assert T.validate_document(good) == []
    owned = json.loads(json.dumps(good))
    owned["targets"][m["target_id"]]["owner_account"] = ALICE      # managed target falsely owned
    assert any("carries an owner" in e for e in T.validate_document(owned))
    c = T.Register._new_entry(customer_spec("cust-x", ALICE, "c1"), generation=1)
    c["owner_account"] = None                                       # customer target without owner
    no_owner = json.loads(json.dumps(good))
    no_owner["targets"]["cust-x"] = c
    no_owner["used_target_ids"] = sorted(no_owner["used_target_ids"] + ["cust-x"])
    no_owner["used_identity_uris"] = sorted(no_owner["used_identity_uris"] + [c["worker"]["identity_uri"]])
    assert any("no well-formed owner" in e for e in T.validate_document(no_owner))
    nodefault = json.loads(json.dumps(good))
    nodefault["default_target"] = "nope"
    assert any("default_target" in e for e in T.validate_document(nodefault))
    future = json.loads(json.dumps(good))
    future["targets"][m["target_id"]]["expires_at"] = future["targets"][m["target_id"]]["created_at"] - 5
    assert any("invalid expiry" in e for e in T.validate_document(future))


def test_account_default_must_be_visible(reg):
    reg.add_customer(customer_spec("cust-aaa", ALICE, "c1"), actor="t")
    with pytest.raises(T.TargetsRefused):
        reg.set_account_default(BOB, "cust-aaa", actor="t")
    reg.set_account_default(ALICE, "cust-aaa", actor="t")
    assert reg.load().default_for(ALICE) == ("cust-aaa", "account_default")
    assert reg.load().default_for(BOB) == ("managed-w1", "managed_default")


def test_lifecycle_activation_needs_an_attestation_and_moves_facts_generation(reg):
    reg.add_customer(customer_spec("cust-aaa", ALICE, "c1"), actor="t")
    reg.set_status("cust-aaa", T.ENROLLED, actor="t")
    reg.set_status("cust-aaa", T.PROBING, actor="t")
    with pytest.raises(T.TargetsRefused):
        reg.set_status("cust-aaa", T.ACTIVE, actor="t")
    before = reg.load().target("cust-aaa").facts_generation
    activate_rest = reg.load().target("cust-aaa")
    from .target_fixtures import attestation
    reg.set_attestation("cust-aaa", attestation(T.CUSTOMER, activate_rest.worker), actor="t")
    reg.set_status("cust-aaa", T.ACTIVE, actor="t")
    t = reg.load().target("cust-aaa")
    assert t.status == T.ACTIVE and t.facts_generation > before
    g = t.facts_generation
    reg.set_account_default(ALICE, "cust-aaa", actor="t")       # not a binding fact
    assert reg.load().target("cust-aaa").facts_generation == g


def test_customer_cannot_carry_operator_assurance(reg):
    reg.add_customer(customer_spec("cust-aaa", ALICE, "c1"), actor="t")
    t = reg.load().target("cust-aaa")
    from .target_fixtures import attestation
    forged = attestation(T.MANAGED, t.worker)                   # operator class on a customer host
    with pytest.raises(T.TargetsRefused):
        reg.set_attestation("cust-aaa", forged, actor="t")


def test_activate_helper(reg):
    reg.add_customer(customer_spec("cust-aaa", ALICE, "c1"), actor="t")
    activate(reg, "cust-aaa")
    assert reg.load().target("cust-aaa").status == T.ACTIVE
