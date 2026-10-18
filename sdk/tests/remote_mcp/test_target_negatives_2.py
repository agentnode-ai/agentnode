"""The mandatory negatives a strict per-case check found only partly covered (target-router T19).

Each test names the profile's mandatory negative it closes. Where a case is about the network or a
certificate, it runs over REAL loopback sockets and REAL mutual TLS (the mTLS arcs' World/Door), through
the router's own preflight -- not over a synthetic exception."""
from __future__ import annotations

import json
import socket
import threading
import time
from types import SimpleNamespace

import pytest

from agentnode_sdk.remote_mcp import targets as T
from agentnode_sdk.remote_mcp.server import build_catalog_adapter
from agentnode_sdk.remote_mcp.target_router import TARGET_META_KEY, TargetRefusal, TargetRouter
from agentnode_sdk.worker import WorkerUnreachable
from agentnode_sdk.worker.remote import TlsWorker
from tests.test_mtls_transport import _one_boot  # noqa: F401 - autouse: one boot throughout
from tests.test_mtls_transport import KEY, Door, World

from .catalog_fixtures import call, hash_of, rpc
from .target_fixtures import attestation, customer_spec, make_register
from .target_stand import CAP_REGEX, REGEX, REGEX_ARGS, TargetStand
from .test_target_routing import _bodies, _call, _sc

ALICE = "acct-00000000000000a1"
BOB = "acct-00000000000000b2"


@pytest.fixture
def st(tmp_path):
    s = TargetStand(tmp_path)
    s.publish_starters()
    return s


def _forge_signed(reg, mutate):
    """The OPERATOR's key signs a next generation that `mutate` made -- history and head written to
    match. Only the register's own validation can refuse it on load."""
    gen = json.loads(reg.head_path.read_text())["generation"]
    good = json.loads((reg.history / f"{gen}.json").read_text())
    doc = {k: v for k, v in good.items() if k != "signature"}
    doc["generation"], doc["prev_digest"] = gen + 1, T.digest(good)
    mutate(doc)
    doc["signature"] = {"key_id": good["signature"]["key_id"],
                        "signature": reg._private.sign(T._signed_bytes(doc)).hex()}
    raw = json.dumps(doc)
    reg.path.write_text(raw)
    (reg.history / f"{gen + 1}.json").write_text(raw)
    reg.head_path.write_bytes(T.canonical({"generation": gen + 1, "digest": T.digest(doc)}))
    reg._verified = None


# 3 / 4 / 8 / 10 / 11 -- operator-SIGNED registers that are wrong in one way: refused on load
@pytest.mark.parametrize("case", ["managed_with_owner", "customer_without_owner", "expiry_before_creation",
                                  "default_to_absent_target", "default_to_foreign_target",
                                  "two_managed_defaults", "non_string_default", "non_string_used_id"])
def test_a_signed_but_invalid_register_is_refused_on_load(tmp_path, case):
    reg, _ = make_register(tmp_path / "stand")
    reg.add_customer(customer_spec("cust-aaa", ALICE, "c1"), actor="t")

    def mutate(doc):
        m, c = doc["targets"]["managed-w1"], doc["targets"]["cust-aaa"]
        if case == "managed_with_owner":
            m["owner_account"] = ALICE
        elif case == "customer_without_owner":
            c["owner_account"] = None
        elif case == "expiry_before_creation":
            c["expires_at"] = c["created_at"] - 1
        elif case == "default_to_absent_target":
            doc["account_defaults"] = {ALICE: "cust-gone"}
        elif case == "default_to_foreign_target":
            doc["account_defaults"] = {BOB: "cust-aaa"}
        elif case == "two_managed_defaults":
            doc["default_target"] = ["managed-w1", "managed-w1"]   # one field: "two defaults" cannot be said
        elif case == "non_string_default":
            doc["account_defaults"] = {ALICE: ["cust-aaa"]}
        else:
            doc["used_target_ids"] = doc["used_target_ids"] + [["x"]]
    _forge_signed(reg, mutate)
    with pytest.raises(T.TargetsUnavailable) as refused:
        reg.load()
    assert "invalid" in str(refused.value)


# 5 -- a live duplicate target id
def test_a_live_target_id_cannot_be_added_twice(tmp_path):
    reg, _ = make_register(tmp_path / "stand")
    reg.add_customer(customer_spec("cust-aaa", ALICE, "c1"), actor="t")
    with pytest.raises(T.TargetsRefused) as refused:
        reg.add_customer(customer_spec("cust-aaa", ALICE, "c2"), actor="t")
    assert refused.value.code == "target.id_used"


# 12 -- a per-call wish overriding the account's OWN default with a target it cannot see
def test_a_wish_cannot_override_a_default_with_an_invisible_target(st):
    alice, ta = st.account("alice")
    bob, _ = st.account("bob")
    st.customer("cust-a", alice)
    st.customer("cust-b", bob, "c2", port=8446)
    st.register.set_account_default(alice, "cust-a", actor="t")
    out = _call(st, ta, REGEX, REGEX_ARGS, wish="cust-b")
    assert _sc(out) == {"status": "refused", "code": "target_unknown"}
    assert st.w("cust-a").starts == st.w("cust-b").starts == st.w("managed-w1").starts == 0


# 13 -- manipulated _meta values: never a target, never ignored
@pytest.mark.parametrize("value", [{"target_id": "cust-a"}, True, 1.5, ["cust-a"], "cust-a\n", "CUST-A"])
def test_a_manipulated_meta_target_is_refused(st, value):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    out = rpc(st.app, ta, "tools/call", {"name": REGEX, "arguments": REGEX_ARGS, "_meta": {TARGET_META_KEY: value}})[1]
    assert _sc(out)["code"] in ("target_bad_wish", "target_unknown")
    assert st.w("cust-a").starts == 0 and st.w("managed-w1").starts == 0


# 14 -- identity and assurance record changed between prepare and submit
def test_an_identity_change_is_never_a_change_of_the_same_target(st):
    alice, _ = st.account("alice")
    st.customer("cust-a", alice)

    def move(doc, now):
        w = doc["targets"]["cust-a"]["worker"]
        w["instance"], w["identity_uri"] = "c9", "agentnode://%s/worker/c9" % w["deployment"]
        w["pair"]["worker"] = "c9"
        return "identity swap"
    with pytest.raises((T.TargetsRefused, T.TargetsUnavailable)):
        st.register.change(move, actor="t")
    assert st.register.load().target("cust-a").worker["instance"] == "c1"


def test_a_new_observation_record_between_prepare_and_submit_is_refused(st):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    st.approve(hash_of(_call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")), CAP_REGEX)

    def at(name):
        if name == "before_admission":
            t = st.register.load().target("cust-a")
            st.register.set_attestation("cust-a", attestation(T.CUSTOMER, t.worker, properties=dict(
                t.properties, reported_platform={"runtime_version": "another"})), actor="t")
    st.adapter._barrier = at
    out = _call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")
    assert _sc(out) == {"status": "refused", "code": "target_changed"}
    assert st.w("cust-a").starts == 0 and not any((st.stand / "consumed").iterdir())


# 15 -- the catalog changes between prepare and submit, on a customer target
def test_a_catalog_revocation_at_admission_refuses_on_a_customer_target(st):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    st.approve(hash_of(_call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")), CAP_REGEX)

    def at(name):
        if name == "before_admission":
            st.catalog.unpublish(REGEX, actor="t")
    st.adapter._barrier = at
    out = _call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")
    assert out["result"]["isError"] is True and _sc(out)["status"] == "refused"
    assert st.w("cust-a").starts == 0 and not any((st.stand / "consumed").iterdir())


# 16 -- an approval made for the managed class does not run on the customer class
def test_an_approval_for_another_assurance_class_does_not_run(st):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    h_managed = hash_of(_call(st, ta, REGEX, REGEX_ARGS))
    st.approve(h_managed, CAP_REGEX)                               # operator_managed_measured binding
    out = _call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")          # customer_controlled_observed
    assert _sc(out)["status"] == "approval_required" and hash_of(out) != h_managed
    assert st.w("cust-a").starts == 0


# 17 -- replay after a reconciliation, and after a control-plane restart
def test_a_replay_after_reconciliation_and_after_a_restart_never_runs_again(st):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    st.approve(hash_of(_call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")), CAP_REGEX)
    w = st.w("cust-a")
    w.lose_after_start = True
    assert _sc(_call(st, ta, REGEX, REGEX_ARGS, wish="cust-a"))["reconciled"] is True
    w.lose_after_start = False
    assert _sc(_call(st, ta, REGEX, REGEX_ARGS, wish="cust-a"))["status"] == "approval_required"
    st.app.adapter = st.adapter = _restarted(st)                   # a new process over the same stand
    again = _call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")
    assert _sc(again)["status"] == "approval_required" and w.starts == 1
    terms = [b for b in _bodies(st) if b.get("kind") == "catalog_terminal"]
    assert len(terms) == 1                                          # still exactly one terminal


def _restarted(st):
    return build_catalog_adapter(str(st.stand), st.worker, router=st.router,
                                 authority=lambda p: st.authority_says.get(p.principal_id),
                                 kill_switch=lambda: st.stopped)


# 38 -- a control-plane restart at every dispatch boundary
class _Crash(BaseException):
    """A process that dies: not an exception any handler of the product catches."""


def _crash_run(st, where):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    first = _call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")
    st.approve(hash_of(first), CAP_REGEX)
    w = st.w("cust-a")
    if where == "after_intent":                                    # admitted, approval not yet spent
        def at(name):
            if name == "after_admission":
                raise _Crash(name)
        st.adapter._barrier = at
    else:
        real = w.run

        def run(job):
            if where == "after_spend":                             # approval spent, nothing started
                raise _Crash("before start")
            out = real(job)                                        # "after_start": ran and journalled
            raise _Crash("before terminal")
        w.run = run
    # straight into the adapter: a dying process takes its transport with it, so nothing answers
    msg = {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
           "params": {"name": REGEX, "arguments": REGEX_ARGS, "_meta": {TARGET_META_KEY: "cust-a"}}}
    with pytest.raises(_Crash):
        st.adapter.handle_for(SimpleNamespace(principal_id=alice, token_id="tok-crash"), msg)
    w.__dict__.pop("run", None)                                    # the worker as it was
    return ta, hash_of(first)


@pytest.mark.parametrize("where,decision", [("after_intent", "not_started"), ("after_spend", "not_started"),
                                            ("after_start", "ran")])
def test_a_restart_at_every_dispatch_boundary_ends_each_dispatch_exactly_once(st, where, decision):
    ta, h = _crash_run(st, where)
    assert not [b for b in _bodies(st) if b.get("kind") == "catalog_terminal"]   # the dead process ended nothing
    time.sleep(0.01)
    restarted = _restarted(st)
    settled = restarted.settle_earlier_dispatches()
    assert [s["decision"] for s in settled] == [decision]
    assert restarted.settle_earlier_dispatches() == []                         # once per process
    terms = [b for b in _bodies(st) if b.get("kind") == "catalog_terminal"]
    assert [(t["decision"], t["reason"]) for t in terms] == [(decision, "control_restart")]
    st.app.adapter = st.adapter = restarted
    replay = _call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")
    assert _sc(replay)["status"] == "approval_required"                         # never admitted twice
    assert len([b for b in _bodies(st) if b.get("kind") == "catalog_terminal"]) == 1
    assert st.w("cust-a").starts == (1 if where == "after_start" else 0)


def test_a_restart_while_the_outcome_is_unknown_blocks_the_invocation(st):
    ta, h = _crash_run(st, "after_start")
    w = st.w("cust-a")
    w.result = lambda run_id, wait=10.0: (_ for _ in ()).throw(TimeoutError("no answer"))
    time.sleep(0.01)
    restarted = _restarted(st)
    assert [s["decision"] for s in restarted.settle_earlier_dispatches()] == ["indeterminate"]
    st.app.adapter = st.adapter = restarted
    st.approve(h, CAP_REGEX)                                       # even a fresh approval
    out = _call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")
    assert _sc(out) == {"status": "refused", "code": "dispatch_indeterminate"}


def test_a_dispatch_of_the_running_process_is_not_settled(st):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    st.approve(hash_of(_call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")), CAP_REGEX)
    seen = {}

    def at(name):
        if name == "after_admission":                              # this process's own run is in flight
            seen["settled"] = st.adapter.recover_after_restart()
    st.adapter._barrier = at
    out = _call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")
    assert seen["settled"] == [] and out["result"]["isError"] is False


# 20 -- this side's trust material for a target is unreadable: trust unavailable, not offline
def test_an_unbuildable_target_connection_is_trust_unavailable(st):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    st.approve(hash_of(_call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")), CAP_REGEX)

    def unreadable(target):
        raise PermissionError("pair-keys.json: permission denied")
    st.router._worker_for = unreadable
    st.router._workers.clear()
    out = _call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")
    assert _sc(out) == {"status": "refused", "code": "target_trust_unavailable"}
    assert not any((st.stand / "consumed").iterdir()) and st.w("managed-w1").starts == 0


def test_unreadable_trust_acknowledgements_refuse_every_run(st):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    st.acks.path.write_text("{not json")
    status, _ = rpc(st.app, ta, "tools/call", {"name": REGEX, "arguments": REGEX_ARGS,
                                               "_meta": {TARGET_META_KEY: "cust-a"}})
    assert status == 503 and st.w("cust-a").starts == 0 and st.w("managed-w1").starts == 0


# 21 -- after a pair-key rotation the router never uses a connection built on the old slot
def test_a_rotated_pair_key_slot_builds_a_new_connection(st):
    alice, _ = st.account("alice")
    st.customer("cust-a", alice)
    built = []
    st.router._worker_for = lambda target: built.append(target.worker["key_slot"]) or object()
    st.router._workers.clear()
    st.router.worker(st.register.load().target("cust-a"))
    st.router.worker(st.register.load().target("cust-a"))           # same slot: the same connection
    st.register.update_worker("cust-a", {"key_slot": "k2"}, actor="t")
    st.router.worker(st.register.load().target("cust-a"))
    assert built == ["k1", "k2"]


# 27 -- more wall clock or storage than was measured
@pytest.mark.parametrize("props", [{"wall_clock_enforced": False}, {"storage_mb": 256}])
def test_more_time_or_storage_than_measured_is_unsuitable(st, props):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    t = st.register.load().target("cust-a")
    st.register.set_attestation("cust-a", attestation(T.CUSTOMER, t.worker, properties=dict(
        {"network_none_observed": True, "memory_ceiling_mb": 1024, "wall_clock_enforced": True,
         "storage_mb": 512}, **props)), actor="t")
    assert _sc(_call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")) == {"status": "refused", "code": "target_unsuitable"}


# 35 / 37 -- the connection lost before the start, and a status that is "still running"
def test_lost_before_the_start_is_not_started_and_running_is_indeterminate(st):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    w = st.w("cust-a")
    st.approve(hash_of(_call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")), CAP_REGEX)
    w.run = lambda job: (_ for _ in ()).throw(WorkerUnreachable("lost before the worker accepted it"))
    out = _call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")
    assert _sc(out) == {"status": "refused", "code": "not_started"}
    args = dict(REGEX_ARGS, test_string="running")
    st.approve(hash_of(_call(st, ta, REGEX, args, wish="cust-a")), CAP_REGEX)
    from .target_stand import Recovered
    w.result = lambda run_id, wait=10.0: Recovered(known=True, state="running")
    out = _call(st, ta, REGEX, args, wish="cust-a")
    assert _sc(out) == {"status": "refused", "code": "dispatch_indeterminate"}
    terms = [(b["decision"], b.get("reconciliation")) for b in _bodies(st) if b.get("kind") == "catalog_terminal"]
    assert terms == [("not_started", "never_ran"), ("indeterminate", "unknown")]


# 36 -- the same dispatch id for DIFFERENT work: the real journal refuses rather than choosing
def test_a_conflicting_payload_under_a_dispatch_id_is_refused_by_the_journal(tmp_path):
    from agentnode_sdk.capability import tree_execution as TE
    from agentnode_sdk.worker import Limits as WL
    from agentnode_sdk.worker.journal import Journal, JournalRefused, digest_of_job

    s = TargetStand(tmp_path)
    s.publish_starters()
    man = s.adapter.client.index.resolve(CAP_REGEX, "1.0.0").manifest
    pkg = man["source"]["package"]
    tar = s.adapter.client.index.get_blob(pkg["sha256"])
    lim = WL(memory_mb=1024, wall_clock_s=120, storage_mb=512)
    jobs = []
    for text in ("one", "two"):
        inv = s.adapter.client.prepare(CAP_REGEX, "data_cleaning", dict(REGEX_ARGS, test_string=text),
                                       version="1.0.0", nonce="n" * 64)
        jobs.append(TE._build_job(inv, tar, interpreter=pkg["interpreter"], entry=pkg["entry"],
                                  expected_tree_digest=pkg["tree_digest"], limits=lim, run_id="cap-" + "e" * 32))
    j = Journal(str(tmp_path / "journal"))
    assert digest_of_job(jobs[0]) != digest_of_job(jobs[1])
    j.claim(jobs[0].run_id, digest_of_job(jobs[0]), str(jobs[0].container_name))
    with pytest.raises(JournalRefused) as refused:
        j.claim(jobs[1].run_id, digest_of_job(jobs[1]), str(jobs[1].container_name))
    assert "different work" in str(refused.value)


# 41 -- a retry after a transport loss asking for another target
def test_a_retry_after_a_loss_cannot_move_to_another_target(st):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    h = hash_of(_call(st, ta, REGEX, REGEX_ARGS, wish="cust-a"))
    st.approve(h, CAP_REGEX)
    w = st.w("cust-a")
    w.run = lambda job: (_ for _ in ()).throw(WorkerUnreachable("lost"))
    w.result = lambda run_id, wait=10.0: (_ for _ in ()).throw(TimeoutError("no answer"))
    assert _sc(_call(st, ta, REGEX, REGEX_ARGS, wish="cust-a"))["code"] == "dispatch_indeterminate"
    moved = _call(st, ta, REGEX, REGEX_ARGS, wish="managed-w1")
    assert _sc(moved)["status"] == "approval_required" and hash_of(moved) != h
    assert st.w("managed-w1").starts == 0


# 42 -- the target revoked while its run is on the connection: that run ends, nothing new starts
def test_a_target_revoked_during_a_running_connection(st):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    st.approve(hash_of(_call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")), CAP_REGEX)

    def at(name):
        if name == "after_admission":
            st.register.set_status("cust-a", T.REVOKED, actor="t")
    st.adapter._barrier = at
    out = _call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")
    assert out["result"]["isError"] is False                       # admitted before: it finishes
    st.adapter._barrier = lambda name: None
    args = dict(REGEX_ARGS, test_string="after")
    assert _sc(_call(st, ta, REGEX, args, wish="cust-a"))["code"] in ("target_revoked", "target_unknown")
    assert st.w("cust-a").starts == 1


# 43 -- tools/list and agentnode_search never carry a target fact
def test_list_and_search_carry_no_target_facts(st):
    alice, _ = st.account("alice")
    _, tb = st.account("bob")
    st.customer("cust-a", alice)
    t = st.register.load().target("cust-a")
    for resp in (rpc(st.app, tb, "tools/list")[1], call(st.app, tb, "agentnode_search", {"query": "regex"})[1]):
        blob = json.dumps(resp)
        for secret in ("cust-a", t.identity_uri, t.worker["address"], t.worker["instance"], "concurrency",
                       "customer_controlled_observed"):
            assert secret not in blob, secret


# 18 / 19 -- REAL sockets and REAL mutual TLS through the router's preflight
@pytest.fixture
def tls(tmp_path):
    world = World(tmp_path / "pki")
    gateway = world.service("gateway", "g1")
    reg, _ = make_register(tmp_path / "stand")
    acks = T.TrustAcks(reg.stand, reg._public.public_bytes_raw(), operator_private=reg._private)
    where: dict[str, str] = {}
    router = TargetRouter(register=reg, acks=acks, deployment_list_number=lambda: 1,
                          worker_for=lambda t: TlsWorker(where[t.target_id], KEY,
                                                         world.settings(gateway, {t.worker["instance"]})))
    target = reg.load().target("managed-w1")                      # expects agentnode://alpha?/worker/w1
    return SimpleNamespace(world=world, router=router, where=where, target=target)


def _refusal(tls, address):
    tls.where[tls.target.target_id] = address
    tls.router._workers.clear()
    with pytest.raises(TargetRefusal) as refused:
        tls.router.preflight(tls.target, budget=2.0)
    return refused.value.code


def _listener(behaviour):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)

    def serve():
        try:
            conn, _ = srv.accept()
        except OSError:
            return
        if behaviour == "close":
            conn.close()
        else:
            time.sleep(5)                                          # accepts, never speaks
            conn.close()
    threading.Thread(target=serve, daemon=True).start()
    return srv


def test_real_socket_failures_are_offline(tls):
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    port = closed.getsockname()[1]
    closed.close()
    assert _refusal(tls, "tcps://127.0.0.1:%d" % port) == "target_offline"           # refused connection
    for behaviour in ("silent", "close"):                                              # handshake timeout / cut
        srv = _listener(behaviour)
        try:
            assert _refusal(tls, "tcps://127.0.0.1:%d" % srv.getsockname()[1]) == "target_offline", behaviour
        finally:
            srv.close()


def test_a_real_peer_with_the_wrong_identity_or_trust_is_never_offline(tls):
    world = tls.world
    expected = tls.target.worker["instance"]
    impostor = Door(world, world.service("worker", "w2" if expected != "w2" else "w3"), {"g1"})
    try:
        assert _refusal(tls, impostor.address) == "target_identity_mismatch"            # another URI/instance
    finally:
        impostor.close()
    _key, _cert, foreign_anchor = world.foreign_ca()
    ca = (_key, _cert)
    forged = world.forge("w-foreign", uri="agentnode://%s/worker/%s" % (world.deployment, expected),
                         usages=["1.3.6.1.5.5.7.3.1", "1.3.6.1.5.5.7.3.2"], ca=ca)
    foreign = Door(world, forged, {"g1"}, settings=world.settings(forged, {"g1"}, anchor=foreign_anchor,
                                                                  role="worker"))
    try:
        assert _refusal(tls, foreign.address) == "target_identity_mismatch"            # another CA's certificate
    finally:
        foreign.close()
