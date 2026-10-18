"""The remaining mandatory negatives of the frozen target profile, one by one (target-router T19)."""
from __future__ import annotations

import json
import socket
import ssl
import time

import pytest

from agentnode_sdk.remote_mcp import target_probe as P
from agentnode_sdk.remote_mcp import targets as T
from agentnode_sdk.remote_mcp.catalog_audit import AuditUnavailable
from agentnode_sdk.remote_mcp.target_router import classify
from agentnode_sdk.worker import WorkerUnreachable, pairkeys

from .catalog_fixtures import call, hash_of, rpc
from .target_fixtures import attestation, customer_spec, make_register
from .target_stand import CAP_REGEX, REGEX, REGEX_ARGS, TargetStand
from .test_target_routing import _call, _sc

ALICE = "acct-00000000000000a1"


@pytest.fixture
def st(tmp_path):
    s = TargetStand(tmp_path)
    s.publish_starters()
    return s


# 7 -- a pinned head that disagrees with the live register
def test_mismatched_history_head_fails_closed(tmp_path):
    reg, _ = make_register(tmp_path / "stand")
    head = json.loads(reg.head_path.read_text())
    head["digest"] = "0" * 64
    reg.head_path.write_text(json.dumps(head))
    with pytest.raises(T.TargetsUnavailable):
        reg.load()


# 8 -- a target created in the future
def test_future_dated_creation_fails_closed(tmp_path):
    from .target_fixtures import Clock
    clock = Clock(time.time())
    reg, _ = make_register(tmp_path / "stand", clock=clock)
    clock.t += 7200
    reg.add_customer(customer_spec("cust-f", ALICE, "c1"), actor="t")       # stamped two hours ahead
    clock.t -= 7200
    reg.clock_path.unlink()                                                # no pinned high-water either
    reg._clock = time.time
    with pytest.raises(T.TargetsUnavailable) as refused:
        reg.load()
    assert "future" in str(refused.value)


# 9 / 10 -- invited, probing, and an account default pointing at an inactive target
@pytest.mark.parametrize("status", [T.INVITED, T.PROBING])
def test_not_yet_active_targets_are_never_selected(st, status):
    alice, ta = st.account("alice")
    st.register.add_customer(customer_spec("cust-n", alice, "c2", port=8445), actor="t")
    if status == T.PROBING:
        st.register.set_status("cust-n", T.ENROLLED, actor="t")
        st.register.set_status("cust-n", T.PROBING, actor="t")
    assert _sc(_call(st, ta, REGEX, REGEX_ARGS, wish="cust-n"))["code"] == "target_inactive"


def test_account_default_on_an_inactive_target_never_falls_back(st):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    st.register.set_account_default(alice, "cust-a", actor="t")
    st.register.set_status("cust-a", T.SUSPENDED, actor="t")
    out = _call(st, ta, REGEX, REGEX_ARGS)
    assert _sc(out)["code"] == "target_inactive"              # NOT run on the managed default
    assert st.w("managed-w1").starts == 0


# 11 -- the register needs exactly one managed default
def test_missing_managed_default_is_invalid():
    c = T.Register._new_entry(customer_spec("cust-x", ALICE, "c1"), generation=1)
    doc = {"schema": T.SCHEMA, "generation": 1, "prev_digest": T.GENESIS, "targets": {"cust-x": c},
           "account_defaults": {}, "default_target": "cust-x", "used_target_ids": ["cust-x"],
           "used_identity_uris": [c["worker"]["identity_uri"]], "signature": {"key_id": "k", "signature": "s"}}
    errs = T.validate_document(doc)
    assert any("default_target" in e for e in errs) and any("needs a managed target" in e for e in errs)


# 13 -- an unknown key in the reserved namespace is a routing claim
def test_unknown_reserved_meta_key_is_refused(st):
    _, ta = st.account("alice")
    out = rpc(st.app, ta, "tools/call", {"name": REGEX, "arguments": REGEX_ARGS,
                                         "_meta": {"agentnode/target": "managed-w1"}})[1]
    assert out["error"]["code"] == -32602 and "routing claims" in out["error"]["message"]


# 14 / 16 -- requirements (assurance policy) changed between prepare and submit
def test_assurance_policy_change_between_prepare_and_submit_is_refused(st):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    st.approve(hash_of(_call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")), CAP_REGEX)

    def at(name):
        if name == "before_admission":
            st.catalog.set_assurance(REGEX, [T.OPERATOR_MANAGED], actor="t")
    st.adapter._barrier = at
    out = _call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")
    assert out["result"]["content"][0]["text"] in ("refused: catalog_changed", "refused: target_unsuitable")
    assert st.w("cust-a").starts == 0 and not any((st.stand / "consumed").iterdir())


# 18 / 19 / 20 / 21 -- how a failure to reach a target is named
@pytest.mark.parametrize("exc,code", [
    (ConnectionRefusedError("refused"), "target_offline"),
    (socket.timeout("handshake timed out"), "target_offline"),
    (ConnectionResetError("mid-handshake"), "target_offline"),
    (ssl.SSLError("bad record"), "target_offline"),
])
def test_transport_failures_are_offline(exc, code):
    try:
        raise WorkerUnreachable("unreachable") from exc
    except WorkerUnreachable as wrapped:
        assert classify(wrapped) == code


@pytest.mark.parametrize("check,code", [
    ("instance", "target_identity_mismatch"), ("deployment", "target_identity_mismatch"),
    ("role", "target_identity_mismatch"), ("subject-alternative-name", "target_identity_mismatch"),
    ("extended-key-usage", "target_identity_mismatch"), ("no-certificate", "target_identity_mismatch"),
    ("revoked", "target_trust_unavailable"), ("validity", "target_trust_unavailable"),
    ("revocation-list-expired", "target_trust_unavailable"), ("time-floor-stale", "target_trust_unavailable"),
    ("identity-tombstones-signature", "target_trust_unavailable"),
])
def test_peer_refusals_are_classified(check, code):
    from agentnode_sdk.pki.identity import PeerRefused
    try:
        raise WorkerUnreachable("refused") from PeerRefused(check, "agentnode://d/worker/x", "detail")
    except WorkerUnreachable as wrapped:
        assert classify(wrapped) == code


def test_a_missing_pair_key_is_trust_unavailable(tmp_path):
    ring = pairkeys.empty().add(gateway="g1", worker="c1", key=b"k" * 32)
    ring = ring.remove(gateway="g1", worker="c1")             # both keys gone (revocation)
    with pytest.raises(pairkeys.KeyringRefused) as refused:
        ring.for_pair(gateway="g1", worker="c1")
    assert classify(refused.value) == "target_trust_unavailable"


# 23 / 24 / 25 -- probe nonces, a failing canary, a job reporting it got out
class _ReportingWorker:
    def __init__(self, uri, egress):
        self.uri, self.egress, self.ran = uri, egress, []

    def run(self, job):
        from agentnode_sdk.worker import Outcome
        self.ran.append(job.run_id)
        prog = job.artifact.decode()
        if "create_connection" in prog:
            return Outcome(exit_code=0, stdout=P.SENTINEL + json.dumps(self.egress), stderr="")
        if "bytearray" in prog:
            mb = int(prog.split("range(")[1].split(")")[0])
            return Outcome(exit_code=137 if mb > job.limits.memory_mb else 0,
                           stdout="" if mb > job.limits.memory_mb else P.SENTINEL + "{}", stderr="")
        s = int(prog.split("_t.sleep(")[1].split(")")[0])
        return Outcome(exit_code=137 if s > job.limits.wall_clock_s else 0,
                       stdout="" if s > job.limits.wall_clock_s else P.SENTINEL + "{}", stderr="")

    def who_ran(self, run_id):
        return "mtls", self.uri, ""


def _target(reg_dir):
    reg, _ = make_register(reg_dir)
    reg.add_customer(customer_spec("cust-p", ALICE, "c1"), actor="t")
    return reg.load().target("cust-p")


@pytest.mark.parametrize("egress", [{"ipv4": "OSError", "ipv6": "connected", "dns": "gaierror"},
                                    {"ipv4": "OSError", "ipv6": "OSError", "dns": "resolved"},
                                    {"ipv4": "connected", "ipv6": "OSError", "dns": "gaierror"}])
def test_a_job_reporting_egress_is_never_network_none(tmp_path, egress):
    t = _target(tmp_path / "stand")
    canary = P.Canary("127.0.0.1")
    try:
        rec = P.probe(t, _ReportingWorker(t.identity_uri, egress), canary)
    finally:
        canary.close()
    assert rec["properties"]["network_none_observed"] is False and "network" in P.passed(rec)


def test_probe_nonces_are_fresh_and_a_dead_canary_refuses(tmp_path):
    t = _target(tmp_path / "stand")
    clean = {"ipv4": "OSError", "ipv6": "OSError", "dns": "gaierror"}
    canary = P.Canary("127.0.0.1")
    try:
        a = P.probe(t, _ReportingWorker(t.identity_uri, clean), canary)
        b = P.probe(t, _ReportingWorker(t.identity_uri, clean), canary)
    finally:
        canary.close()
    assert a["challenge_nonces"] != b["challenge_nonces"] and a["record_digest"] != b["record_digest"]
    dead = P.Canary("127.0.0.1")
    dead.positive_control = lambda: False
    try:
        with pytest.raises(RuntimeError):
            P.probe(t, _ReportingWorker(t.identity_uri, clean), dead)
    finally:
        dead.close()


# 27 -- a capability needing more than was measured
def test_more_memory_than_measured_is_unsuitable(st):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    t = st.register.load().target("cust-a")
    small = attestation(T.CUSTOMER, t.worker, properties={"network_none_observed": True, "memory_ceiling_mb": 512,
                                                          "wall_clock_enforced": True, "storage_mb": 512})
    st.register.set_attestation("cust-a", small, actor="t")
    out = _call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")
    assert _sc(out) == {"status": "refused", "code": "target_unsuitable"}


# 32 / 33 -- RUN removed, target expired, unreadable kill switch / authority while queued
@pytest.mark.parametrize("change,code", [("run_withdrawn", "authority_changed"),
                                         ("authority_unverifiable", "authority_changed"),
                                         ("kill_switch_unreadable", "kill_switch"),
                                         ("target_expired", "target_expired_while_queued")])
def test_more_changes_while_queued(st, change, code):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    st.approve(hash_of(_call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")), CAP_REGEX)

    def at(name):
        if name != "before_admission":
            return
        if change in ("run_withdrawn", "authority_unverifiable"):
            st.authority_says[alice] = change
        elif change == "kill_switch_unreadable":
            def unreadable():
                raise OSError("permission denied")
            st.adapter.kill_switch = unreadable
        else:
            st.register.change(lambda doc, now: (doc["targets"]["cust-a"].update(expires_at=now), "expire")[1],
                               actor="t")
    st.adapter._barrier = at
    out = _call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")
    assert out["result"]["content"][0]["text"] == "refused: " + code
    assert st.w("cust-a").starts == 0 and not any((st.stand / "consumed").iterdir())


# 34 -- a dispatch intent that cannot be recorded never reaches the worker
def test_unrecordable_dispatch_intent_never_dispatches(st, monkeypatch):
    alice, ta = st.account("alice")
    st.customer("cust-a", alice)
    st.approve(hash_of(_call(st, ta, REGEX, REGEX_ARGS, wish="cust-a")), CAP_REGEX)
    real = st.adapter.ledger.append

    def failing(kind, **fields):
        if fields.get("decision") == "admitted":
            raise AuditUnavailable("disk full")
        return real(kind, **fields)
    monkeypatch.setattr(st.adapter.ledger, "append", failing)
    status, body = rpc(st.app, ta, "tools/call", {"name": REGEX, "arguments": REGEX_ARGS,
                                                  "_meta": {"net.agentnode/target": "cust-a"}})
    assert body["error"]["code"] == -32603 and st.w("cust-a").starts == 0
    assert not any((st.stand / "consumed").iterdir())


# 44 -- a public account can neither select nor default to a private customer target
def test_public_account_cannot_reach_the_private_customer_target(st):
    alice, _ = st.account("alice")
    bob, tb = st.account("bob")
    st.customer("cust-a", alice)
    assert _sc(_call(st, tb, REGEX, REGEX_ARGS, wish="cust-a"))["code"] == "target_unknown"
    with pytest.raises(T.TargetsRefused):
        st.register.set_account_default(bob, "cust-a", actor="t")
    d = _sc(call(st.app, tb, "agentnode_describe", {"tool": REGEX})[1])
    assert [t["target_id"] for t in d["targets"]] == ["managed-w1"]
