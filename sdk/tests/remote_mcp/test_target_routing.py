"""Target selection, binding, no fallback, re-checks and exactly-once dispatch, end to end through
the REAL transport, catalog adapter, register and router (target-router T4-T7, T13-T17)."""
from __future__ import annotations

import json
import threading

import pytest

from agentnode_sdk.remote_mcp import targets as T
from agentnode_sdk.remote_mcp.limits import Limits
from agentnode_sdk.remote_mcp.target_router import TARGET_META_KEY

from .catalog_fixtures import call, hash_of, rpc
from .target_stand import CAP_REGEX, REGEX, REGEX_ARGS, TargetStand


@pytest.fixture
def st(tmp_path):
    s = TargetStand(tmp_path)
    s.publish_starters()
    return s


def _call(s, token, tool, args, *, wish=None, meta_route=False):
    if meta_route:
        a = {"tool": tool, "arguments": args}
        if wish is not None:
            a["target"] = wish
        return call(s.app, token, "agentnode_run", a)[1]
    params = {"name": tool, "arguments": args}
    if wish is not None:
        params["_meta"] = {TARGET_META_KEY: wish}
    return rpc(s.app, token, "tools/call", params)[1]


def _sc(resp):
    return resp["result"].get("structuredContent") or {}


def _bodies(s):
    return [json.loads(x)["body"] for x in (s.stand / "catalog-audit.jsonl").read_text().splitlines()]


def _approve_and_run(s, token, tool=REGEX, args=REGEX_ARGS, **kw):
    first = _call(s, token, tool, args, **kw)
    s.approve(hash_of(first), CAP_REGEX if tool == REGEX else _sc(first)["capability_id"])
    return first, _call(s, token, tool, args, **kw)


# ---------------------------------------------------------------- default, wish, account default
def test_managed_default_and_explicit_customer_target_run_the_same_capability(st):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    _, ran = _approve_and_run(st, ta)
    assert _sc(ran)["target"]["target_id"] == "managed-w1" and _sc(ran)["target"]["selection_reason"] == "managed_default"
    assert st.w("managed-w1").starts == 1
    args = dict(REGEX_ARGS, test_string="customer")
    _, ran2 = _approve_and_run(st, ta, args=args, wish="cust-a")
    assert _sc(ran2)["target"]["target_id"] == "cust-a" and _sc(ran2)["target"]["selection_reason"] == "call_wish"
    assert "customer-controlled" in _sc(ran2)["target"]["note"]
    assert st.w("cust-a").starts == 1 and _sc(ran2)["call"]["structuredContent"]["count"] == 0
    st.register.set_account_default(alice, "cust-a", actor="t")      # the account's own default
    args3 = dict(REGEX_ARGS, test_string="default 7")
    _, ran3 = _approve_and_run(st, ta, args=args3)
    assert _sc(ran3)["target"] == dict(_sc(ran3)["target"], target_id="cust-a", selection_reason="account_default")
    args4 = dict(REGEX_ARGS, test_string="override 9")                # per-call override back to managed
    _, ran4 = _approve_and_run(st, ta, args=args4, wish="managed-w1", meta_route=True)
    assert _sc(ran4)["target"]["target_id"] == "managed-w1"


def test_operator_cli_sets_the_assurance_policy(tmp_path, capsys):
    """`catalog_cli set-assurance` is the operator's only way to let a tool run on a customer target."""
    from agentnode_sdk.remote_mcp import catalog_cli
    s = TargetStand(tmp_path)
    s.publish_starters(customer_ok=False)
    alice, ta = s.account("alice")
    s.customer("cust-a", alice)
    refused = _call(s, ta, REGEX, REGEX_ARGS, wish="cust-a")
    assert _sc(refused)["code"] == "target_unsuitable"
    stand = str(s.stand)
    assert catalog_cli.main(["--stand", stand, "set-assurance", "--tool", REGEX,
                             "--classes", T.OPERATOR_MANAGED + "," + T.CUSTOMER_OBSERVED]) == 0
    assert json.loads(capsys.readouterr().out)["published"][REGEX]["accepted_assurance"] == sorted(
        [T.OPERATOR_MANAGED, T.CUSTOMER_OBSERVED])
    _, ran = _approve_and_run(s, ta, wish="cust-a")
    assert _sc(ran)["target"]["target_id"] == "cust-a"
    assert catalog_cli.main(["--stand", stand, "set-assurance", "--tool", REGEX, "--classes", "self_reported"]) == 5
    assert json.loads(capsys.readouterr().out)["code"] == "assurance.invalid"
    assert catalog_cli.main(["--stand", stand, "set-assurance", "--tool", "no_such__tool",
                             "--classes", T.OPERATOR_MANAGED]) == 5
    assert json.loads(capsys.readouterr().out)["code"] == "assurance.unknown_tool"


def test_target_binding_nonce_mutation(st):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    h_managed = hash_of(_call(st, ta, REGEX, REGEX_ARGS))
    h_customer = hash_of(_call(st, ta, REGEX, REGEX_ARGS, wish="cust-a"))
    assert h_managed != h_customer                                    # the target is in the hash
    entry = st.catalog.load().published[REGEX]
    snap = st.register.load()
    rd = T.digest({"x": 1})
    base = st.adapter.nonce_for(alice, entry, REGEX_ARGS, snap.target("managed-w1").binding(rd))
    for field in ("target_id", "worker_identity_uri", "endpoint_digest", "requirements_digest",
                  "properties_digest", "attestation_assurance", "target_generation"):
        b = dict(snap.target("managed-w1").binding(rd))
        b[field] = (b[field] + 1) if isinstance(b[field], int) else (str(b[field]) + "x")
        assert st.adapter.nonce_for(alice, entry, REGEX_ARGS, b) != base, field


def test_direct_meta_same_target_hash(st):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    for wish in (None, "cust-a"):
        d = hash_of(_call(st, ta, REGEX, REGEX_ARGS, wish=wish))
        m = hash_of(_call(st, ta, REGEX, REGEX_ARGS, wish=wish, meta_route=True))
        assert d == m


def test_client_target_cannot_widen_visibility(st):
    alice, ta = st.account("alice")
    bob, tb = st.account("bob")
    st.customer("cust-a", alice)
    out = _call(st, tb, REGEX, REGEX_ARGS, wish="cust-a")
    assert _sc(out) == {"status": "refused", "code": "target_unknown"}
    with pytest.raises(T.TargetsRefused):
        st.register.set_account_default(bob, "cust-a", actor="t")
    assert st.w("cust-a").starts == 0


def test_foreign_target_indistinguishable(st):
    alice, _ = st.account("alice")
    bob, tb = st.account("bob")
    st.customer("cust-a", alice)
    foreign = _call(st, tb, REGEX, REGEX_ARGS, wish="cust-a")["result"]
    unknown = _call(st, tb, REGEX, REGEX_ARGS, wish="cust-zzz")["result"]
    assert foreign == unknown
    describe = call(st.app, tb, "agentnode_describe", {"tool": REGEX})[1]
    assert [t["target_id"] for t in _sc(describe)["targets"]] == ["managed-w1"]
    refused = [b for b in _bodies(st) if b.get("reason") == "target_unknown"]
    assert all("target_id" not in b for b in refused)                 # not even the audit names it


@pytest.mark.parametrize("wish", [5, "", "x" * 65, "Bad Id!", ["cust-a"]])
def test_malformed_wishes_are_refused(st, wish):
    _, ta = st.account("alice")
    out = _call(st, ta, REGEX, REGEX_ARGS, wish=wish)
    assert _sc(out)["code"] == "target_bad_wish"


def test_conflicting_meta_and_argument_wishes_are_refused(st):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    out = rpc(st.app, ta, "tools/call", {"name": "agentnode_run",
                                         "arguments": {"tool": REGEX, "arguments": REGEX_ARGS, "target": "cust-a"},
                                         "_meta": {TARGET_META_KEY: "managed-w1"}})[1]
    assert out["result"]["isError"] and "target_bad_wish" in out["result"]["content"][0]["text"]


# ---------------------------------------------------------------- no fallback
def test_offline_never_falls_back(st):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    first = _call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")
    st.approve(hash_of(first), CAP_REGEX)
    st.w("cust-a").offline = True
    out = _call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")
    assert _sc(out)["code"] == "target_offline"
    assert st.w("managed-w1").starts == 0 and st.w("cust-a").starts == 0
    assert not any((st.stand / "consumed").iterdir())                 # the approval survives


def test_full_never_retargets(tmp_path):
    s = TargetStand(tmp_path, limits=Limits(max_concurrent_calls=4, max_queued_calls=8,
                                            max_concurrent_calls_per_principal=5))
    s.publish_starters()
    alice, ta = s.account("alice")
    bob, tb = s.account("bob")
    s.customer("cust-a", alice)
    s.register.change(lambda doc, now: (doc["targets"]["cust-a"]["limits"].update(concurrency=1, queue=1), "cap")[1],
                      actor="t")
    s.ack("cust-a")
    s.approve(hash_of(_call(s, ta, REGEX, REGEX_ARGS, wish="cust-a")), CAP_REGEX)
    args2 = dict(REGEX_ARGS, test_string="second")
    s.approve(hash_of(_call(s, ta, REGEX, args2, wish="cust-a")), CAP_REGEX)
    args3 = dict(REGEX_ARGS, test_string="third")
    worker = s.w("cust-a")
    worker.gate = threading.Event()
    import asyncio
    import httpx

    from .catalog_fixtures import HOST, META
    out = {}

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=s.app), base_url="http://%s" % HOST) as c:
            def req(args):
                return c.post("/mcp", headers={"content-type": "application/json",
                                               "accept": "application/json, text/event-stream",
                                               "mcp-protocol-version": "2026-07-28", "mcp-method": "tools/call",
                                               "mcp-name": REGEX, "authorization": "Bearer %s" % ta},
                              content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                                  "name": REGEX, "arguments": args, "_meta": dict(META, **{TARGET_META_KEY: "cust-a"})}}))
            first = asyncio.ensure_future(req(REGEX_ARGS))
            while not worker.started.is_set():
                await asyncio.sleep(0.02)
            second = asyncio.ensure_future(req(args2))           # queued: the one queue place
            await asyncio.sleep(0.2)
            third = await req(args3)                             # the target's queue is full
            out["third"] = (third.status_code, third.json())
            worker.gate.set()
            out["first"] = (await first).json()
            out["second"] = (await second).json()
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(go())
    finally:
        loop.close()
    assert out["third"][0] == 429 and out["third"][1]["error"] == "target_full"
    assert s.w("managed-w1").starts == 0                         # never retargeted
    assert worker.starts == 2


def test_no_local_execution_fallback(st):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    st.approve(hash_of(_call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")), CAP_REGEX)
    st.w("cust-a").offline = True
    _call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")
    assert st.worker.dispatched == 0                             # the stand's own worker is never used
    assert st.w("managed-w1").starts == 0


@pytest.mark.parametrize("status,code", [(T.SUSPENDED, "target_inactive"), (T.REVOKED, "target_revoked")])
def test_inactive_and_revoked_targets_refuse(st, status, code):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    st.register.set_status("cust-a", status, actor="t")
    out = _call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")
    assert _sc(out)["code"] == (code if status != T.REVOKED else "target_unknown") or _sc(out)["code"] == code
    assert st.w("cust-a").starts == 0 and st.w("managed-w1").starts == 0


def test_expired_target_refuses(tmp_path):
    from .target_fixtures import Clock
    s = TargetStand(tmp_path)
    s.publish_starters()
    alice, ta = s.account("alice")
    s.customer("cust-a", alice)
    s.register.change(lambda doc, now: (doc["targets"]["cust-a"].update(expires_at=now + 0.5), "expiry")[1], actor="t")
    s.ack("cust-a")
    import time
    time.sleep(0.7)
    out = _call(s, ta, REGEX, REGEX_ARGS, wish="cust-a")
    assert _sc(out)["code"] == "target_expired"
    del Clock


# ---------------------------------------------------------------- properties, assurance, trust
def test_self_report_never_authorizes(st):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    t = st.register.load().target("cust-a")
    from .target_fixtures import attestation
    bad = attestation(T.CUSTOMER, t.worker, properties={"network_none_observed": False, "memory_ceiling_mb": 1024,
                                                        "wall_clock_enforced": True, "storage_mb": 512})
    st.register.set_attestation("cust-a", bad, actor="t")
    st.ack("cust-a")
    # the worker's own describe still says it can isolate -- and that decides nothing
    assert st.w("cust-a").can_it_isolate().available is True
    out = _call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")
    assert _sc(out) == {"status": "refused", "code": "target_unsuitable"}


def test_stale_properties_refused(st):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    t = st.register.load().target("cust-a")
    from .target_fixtures import attestation
    import time
    old = attestation(T.CUSTOMER, t.worker, now=time.time() - 25 * 3600, seconds=24 * 3600 - 60)
    st.register.set_attestation("cust-a", old, actor="t")
    out = _call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")
    assert _sc(out)["code"] == "target_properties_stale"


def test_manifest_assurance_policy_enforced(tmp_path):
    s = TargetStand(tmp_path)
    s.publish_starters(customer_ok=False)               # operator-managed only
    alice, ta = s.account("alice")
    s.customer("cust-a", alice)
    out = _call(s, ta, REGEX, REGEX_ARGS, wish="cust-a")
    assert _sc(out) == {"status": "refused", "code": "target_unsuitable"}
    assert _sc(_call(s, ta, REGEX, REGEX_ARGS))["status"] == "approval_required"   # managed still fine


def test_expired_trust_refuses_all_dispatch(st):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    st.list_number = 2                                   # the control published a newer list...
    for wish in (None, "cust-a"):                        # ...no worker acknowledged it yet
        assert _sc(_call(st, ta, REGEX, REGEX_ARGS, wish=wish))["code"] == "target_trust_unavailable"
    st.ack("managed-w1")
    assert _sc(_call(st, ta, REGEX, REGEX_ARGS))["status"] == "approval_required"
    st.ack("cust-a", next_update=1.0)                    # acknowledged, but expired
    assert _sc(_call(st, ta, REGEX, REGEX_ARGS, wish="cust-a"))["code"] == "target_trust_unavailable"


# ---------------------------------------------------------------- binding changes need a new approval
def test_endpoint_change_requires_approval(st):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    target = st.register.load().target("cust-a")
    # the endpoint itself is bound -- not only through the facts generation that a move also bumps
    assert target.binding("r")["endpoint_digest"] == target.endpoint_digest
    h1 = hash_of(_call(st, ta, REGEX, REGEX_ARGS, wish="cust-a"))
    st.approve(h1, CAP_REGEX)
    from .target_fixtures import attestation

    def move(doc, now):
        e = doc["targets"]["cust-a"]
        e["worker"]["address"] = "tcps://10.0.0.9:8444"
        # a new endpoint voids the old observation (the register refuses it); the move carries a new one
        e["attestation"] = attestation(T.CUSTOMER, e["worker"])
        return "move"
    st.register.change(move, actor="t")
    out = _call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")
    assert _sc(out)["status"] == "approval_required" and hash_of(out) != h1
    assert st.w("cust-a").starts == 0


def test_target_change_between_prepare_and_submit_is_refused(st):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    st.approve(hash_of(_call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")), CAP_REGEX)
    t = st.register.load().target("cust-a")
    from .target_fixtures import attestation

    def at(name):
        if name == "before_admission":
            st.register.set_attestation("cust-a", attestation(T.CUSTOMER, t.worker), actor="t")
    st.adapter._barrier = at
    out = _call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")
    assert out["result"]["content"][0]["text"] == "refused: target_changed"
    assert st.w("cust-a").starts == 0 and not any((st.stand / "consumed").iterdir())


def test_retry_cannot_change_target(st):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    h_managed = hash_of(_call(st, ta, REGEX, REGEX_ARGS))
    st.approve(h_managed, CAP_REGEX)                     # approved for MANAGED
    out = _call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")
    assert _sc(out)["status"] == "approval_required" and hash_of(out) != h_managed
    assert st.w("cust-a").starts == 0 and st.w("managed-w1").starts == 0


def test_expected_uri_required(st):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    st.approve(hash_of(_call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")), CAP_REGEX)
    st.w("cust-a").wrong_identity = True
    out = _call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")
    assert _sc(out)["code"] == "target_identity_mismatch"
    assert st.w("cust-a").starts == 0


def test_audit_uses_handshake_identity(st):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    _approve_and_run(st, ta, wish="cust-a")
    entries = [json.loads(x)["body"] for x in (st.stand / "audit.jsonl").read_text().splitlines()]
    assert entries[-1]["worker_identity"] == "mtls:" + st.register.load().target("cust-a").identity_uri
    # a run carried by a connection that proved ANOTHER identity is not audited as this target's
    st.w("cust-a").wrong_identity = True
    st.w("cust-a").confirm_reachable = lambda budget=None: None      # passes the preflight...
    args = dict(REGEX_ARGS, test_string="x9")
    st.approve(hash_of(_call(st, ta, REGEX, args, wish="cust-a")), CAP_REGEX)
    out = _call(st, ta, REGEX, args, wish="cust-a")["result"]       # ...but not the proof per run
    assert out["isError"] and "no proved worker identity" in out["content"][0]["text"]
    assert len((st.stand / "audit.jsonl").read_text().splitlines()) == len(entries)


# ---------------------------------------------------------------- re-checks while queued
@pytest.mark.parametrize("change,code", [
    ("account_suspended", "authority_changed"),
    ("device_revoked", "authority_changed"),
    ("target_revoked", "target_revoked_while_queued"),
    ("target_suspended", "target_suspended_while_queued"),
    ("kill_switch", "kill_switch"),
])
def test_queued_changes_refuse_before_dispatch(st, change, code):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    st.approve(hash_of(_call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")), CAP_REGEX)

    def at(name):
        if name != "before_admission":
            return
        if change in ("account_suspended", "device_revoked"):
            st.authority_says[alice] = change
        elif change == "target_revoked":
            st.register.set_status("cust-a", T.REVOKED, actor="t")
        elif change == "target_suspended":
            st.register.set_status("cust-a", T.SUSPENDED, actor="t")
        else:
            st.stopped = "operator stop"
    st.adapter._barrier = at
    out = _call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")
    assert out["result"]["content"][0]["text"] == "refused: " + code
    assert st.w("cust-a").starts == 0 and st.w("managed-w1").starts == 0
    assert not any((st.stand / "consumed").iterdir())          # the approval survives a refusal


def test_queued_account_suspension_refuses(st):
    test_queued_changes_refuse_before_dispatch(st, "account_suspended", "authority_changed")


def test_queued_device_withdrawal_refuses(tmp_path):
    s = TargetStand(tmp_path)
    s.publish_starters()
    test_queued_changes_refuse_before_dispatch(s, "device_revoked", "authority_changed")


def test_queued_target_revocation_refuses(tmp_path):
    s = TargetStand(tmp_path)
    s.publish_starters()
    test_queued_changes_refuse_before_dispatch(s, "target_revoked", "target_revoked_while_queued")


def test_queued_kill_switch_refuses(tmp_path):
    s = TargetStand(tmp_path)
    s.publish_starters()
    test_queued_changes_refuse_before_dispatch(s, "kill_switch", "kill_switch")


def test_refusal_before_dispatch_preserves_approval(st):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    first = _call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")
    st.approve(hash_of(first), CAP_REGEX)
    st.stopped = "operator stop"
    _call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")
    st.stopped = ""
    ran = _call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")      # the SAME approval still runs it
    assert ran["result"]["isError"] is False and st.w("cust-a").starts == 1


# ---------------------------------------------------------------- exactly once
def test_ambiguous_dispatch_requires_reconciliation(st):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    st.approve(hash_of(_call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")), CAP_REGEX)
    w = st.w("cust-a")
    w.lose_after_start = True
    out = _call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")["result"]
    # the job DID run; the lost connection is reconciled from the worker's journal, not called not-started
    assert out["isError"] is False and _sc({"result": out})["reconciled"] is True and w.starts == 1
    terms = [b for b in _bodies(st) if b.get("kind") == "catalog_terminal"]
    assert [t["decision"] for t in terms] == ["ran"] and terms[0]["reconciliation"] == "outcome_recovered"
    # and a dispatch whose outcome cannot be established blocks the same invocation
    args = dict(REGEX_ARGS, test_string="lost")
    h = hash_of(_call(st, ta, REGEX, args, wish="cust-a"))
    st.approve(h, CAP_REGEX)
    w.result = lambda run_id, wait=10.0: (_ for _ in ()).throw(TimeoutError("no answer"))
    out2 = _call(st, ta, REGEX, args, wish="cust-a")["result"]
    assert "dispatch_indeterminate" in out2["content"][0]["text"]
    st.approve(h, CAP_REGEX)                                   # even a fresh approval does not resend
    out3 = _call(st, ta, REGEX, args, wish="cust-a")["result"]
    assert out3["content"][0]["text"] == "refused: dispatch_indeterminate" and w.starts == 2


def test_catalog_terminal_exactly_once(st):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    _approve_and_run(st, ta, wish="cust-a")
    _approve_and_run(st, ta, args=dict(REGEX_ARGS, test_string="m"))
    admitted = [b for b in _bodies(st) if b.get("decision") == "admitted"]
    terms = [b for b in _bodies(st) if b.get("kind") == "catalog_terminal"]
    assert len(admitted) == 2 and len(terms) == 2
    assert sorted(b["dispatch_id"] for b in admitted) == sorted(b["dispatch_id"] for b in terms)


def test_worker_deduplicates_dispatch_id(tmp_path):
    """The REAL worker journal: the catalog's stable dispatch id builds the same job twice, so a
    repeated start is recognised as THIS run and never started again."""
    from agentnode_sdk.capability import tree_execution as TE
    from agentnode_sdk.worker import Limits as WL
    from agentnode_sdk.worker.journal import DONE, Journal, digest_of_job

    s = TargetStand(tmp_path)
    s.publish_starters()
    inv = s.adapter.client.prepare(CAP_REGEX, "data_cleaning", REGEX_ARGS, version="1.0.0", nonce="n" * 64)
    man = s.adapter.client.index.resolve(CAP_REGEX, "1.0.0").manifest
    pkg = man["source"]["package"]
    tar = s.adapter.client.index.get_blob(pkg["sha256"])
    lim = WL(memory_mb=1024, wall_clock_s=120, storage_mb=512)
    jobs = [TE._build_job(inv, tar, interpreter=pkg["interpreter"], entry=pkg["entry"],
                          expected_tree_digest=pkg["tree_digest"], limits=lim, run_id="cap-" + "d" * 32)
            for _ in range(2)]
    assert jobs[0].run_id == jobs[1].run_id and jobs[0].container_name == jobs[1].container_name
    assert digest_of_job(jobs[0]) == digest_of_job(jobs[1])
    j = Journal(str(tmp_path / "journal"))
    first = j.claim(jobs[0].run_id, digest_of_job(jobs[0]), str(jobs[0].container_name))
    j.note_started(jobs[0].run_id)
    j.note_finished(jobs[0].run_id, {"exit_code": 0, "stdout": "{}", "stderr": ""})
    again = j.claim(jobs[1].run_id, digest_of_job(jobs[1]), str(jobs[1].container_name))
    assert first.verdict != DONE and again.verdict == DONE
    random_runs = [TE._build_job(inv, tar, interpreter=pkg["interpreter"], entry=pkg["entry"],
                                 expected_tree_digest=pkg["tree_digest"], limits=lim) for _ in range(2)]
    assert random_runs[0].run_id != random_runs[1].run_id          # every other caller unchanged


# ---------------------------------------------------------------- audit and discovery
def test_audit_contains_complete_target_binding(st):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    _approve_and_run(st, ta, wish="cust-a")
    admitted = [b for b in _bodies(st) if b.get("decision") == "admitted"][-1]
    for k in ("target_id", "target_type", "selection_reason", "target_generation", "endpoint_digest",
              "properties_digest", "requirements_digest", "assurance", "attestation_expires_at",
              "worker_identity_uri", "dispatch_id", "intent_at", "queued_at", "granted_at"):
        assert admitted.get(k) not in (None, ""), k
    term = [b for b in _bodies(st) if b.get("kind") == "catalog_terminal"][-1]
    assert term["target_id"] == "cust-a" and term["dispatch_id"] == admitted["dispatch_id"] and term["finished_at"]


def test_target_audit_privacy_projection(st):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    secret_arg = "very-secret-argument-value"
    _approve_and_run(st, ta, args=dict(REGEX_ARGS, test_string=secret_arg), wish="cust-a")
    raw = (st.stand / "catalog-audit.jsonl").read_text()
    assert secret_arg not in raw
    for forbidden in ("tcps://", "key_slot", "pair", "BEGIN", "secret"):
        assert forbidden not in raw.replace("very-secret", ""), forbidden


def test_describe_hides_target_transport_data(st):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    d = _sc(call(st.app, ta, "agentnode_describe", {"tool": REGEX})[1])
    assert {t["target_id"] for t in d["targets"]} == {"managed-w1", "cust-a"}
    for t in d["targets"]:
        assert set(t) == {"target_id", "type", "status", "eligible", "reasons", "default", "assurance",
                          "attestation_expires_at"}
    text = json.dumps(d)
    for leak in ("tcps://", "agentnode://", "10.0.0.2", "key_slot", "concurrency"):
        assert leak not in text, leak


def test_public_default_is_managed_only(st):
    alice, _ = st.account("alice")
    bob, tb = st.account("bob")
    st.customer("cust-a", alice)
    st.register.set_account_default(alice, "cust-a", actor="t")
    snap = st.register.load()
    assert snap.default_target == "managed-w1" and snap.target("managed-w1").type == T.MANAGED
    assert snap.default_for(bob) == ("managed-w1", "managed_default")
    _approve_and_run(st, tb)
    assert st.w("cust-a").starts == 0


def test_register_unavailable_is_503_never_a_fallback(st):
    _, ta = st.account("alice")
    (st.stand / "targets" / "targets.json").write_text("{}")
    status, body = rpc(st.app, ta, "tools/call", {"name": REGEX, "arguments": REGEX_ARGS})
    assert status == 503 and body == {"error": "catalog_unavailable"}
    assert all(w.starts == 0 for w in st.workers.values())
