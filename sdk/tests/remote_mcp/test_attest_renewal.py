"""Automated, uninterrupted renewal of execution-target attestations (attest-renewal profile, Codex
Q-ATT-0001: C2-C10, C13, C15-C17, C19).

Real register, issuer, keyring, canary listener and probe suite; workers are test doubles that answer
the probe jobs like an enforcing sandbox (or, for the negatives, like a host that does not). The
traffic tests go through the REAL transport, catalog adapter and router of the target stand."""
from __future__ import annotations

import copy
import json
import os
import ssl
import threading
import time
from pathlib import Path

import pytest

from agentnode_sdk.pki.identity import PeerRefused
from agentnode_sdk.remote_mcp import target_probe as P
from agentnode_sdk.remote_mcp import targets as T
from agentnode_sdk.remote_mcp import targets_cli
from agentnode_sdk.remote_mcp.target_ops import (MAX_PROBES_PER_RUN, RENEW_POLICY, OperationFailed, Operations,
                                                 due_at, is_due, load_renew_state)
from agentnode_sdk.remote_mcp.target_router import TargetRefusal
from agentnode_sdk.worker import WorkerUnreachable, pairkeys
from tests.test_mtls_transport import _one_boot  # noqa: F401 - autouse: one boot throughout

from .catalog_fixtures import hash_of, rpc
from .target_fixtures import attestation, customer_spec
from .target_stand import CAP_REGEX, REGEX, REGEX_ARGS, TargetStand
from .test_target_lifecycle import SandboxLikeWorker, env  # noqa: F401 - the lifecycle stand

H = 3600


# ---------------------------------------------------------------------- helpers
class ProbeWorker(SandboxLikeWorker):
    """SandboxLikeWorker with knobs for the renewal's outcome classes."""

    def __init__(self, uri, world=None, folder=None, **kw):
        self.controls_fail = kw.pop("controls_fail", False)
        self.raise_exc = kw.pop("raise_exc", None)
        self.wrong_uri = kw.pop("wrong_uri", "")
        self.delay = kw.pop("delay", 0.0)
        self.platform = kw.pop("platform", "sha256:" + "0" * 64)
        self.on_job = kw.pop("on_job", None)
        super().__init__(uri, world, folder, **kw)
        self.active = 0
        self.max_active = 0
        self.jobs = 0

    def run(self, job):
        self.jobs += 1
        if self.on_job is not None:
            self.on_job(self.jobs, job)
        if self.raise_exc is not None:
            raise self.raise_exc
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                time.sleep(self.delay)
            prog = job.artifact.decode("utf-8")
            if self.controls_fail and "bytearray" in prog and "range(64)" in prog:
                self.ran.append(job.run_id)
                from agentnode_sdk.worker import Outcome
                return Outcome(exit_code=1, stdout="", stderr="podman: cannot start")
            return super().run(job)
        finally:
            self.active -= 1

    def who_ran(self, run_id):
        kind, uri, inst = super().who_ran(run_id)
        return kind, (self.wrong_uri or uri) if uri else uri, inst

    def image_digest(self):
        return self.platform


def _pair(env, instance="w1"):
    path = env["keyring"]
    ring = pairkeys.Keyring.read(path) if Path(path).exists() else pairkeys.empty()
    if ("g1", instance) not in ring.pairs():
        ring.add(gateway="g1", worker=instance, key=os.urandom(32)).write(path)


def _managed(env, **kw) -> ProbeWorker:
    """managed-w1 answered by a ProbeWorker; its pair key present (as on the live control)."""
    _pair(env)
    reg = env["reg"]
    base = env.setdefault("_mw", env["workers"]["managed-w1"])      # its folder and floor: install_trust works
    w = ProbeWorker(reg.load().target("managed-w1").identity_uri, env["world"], base.folder, **kw)
    w.floor = base.floor
    env["workers"]["managed-w1"] = w
    return w


def _left(env, tid, hours):
    """Make `tid`'s CURRENT record expire `hours` from now -- an equivalent rewrite (binding view kept)."""
    reg = env["reg"] if isinstance(env, dict) else env

    def m(doc, now):
        a = copy.deepcopy(doc["targets"][tid]["attestation"])
        a["expires_at"] = now + hours * H
        a["measured_at"] = a["expires_at"] - T.ATTESTATION_MAX_SECONDS[a["assurance"]] + 60
        a["record_digest"] = T.digest({k: a[k] for k in sorted(a) if k != "record_digest"})
        doc["targets"][tid]["attestation"] = a
        return "test: %s has %s h left" % (tid, hours)
    reg.change(m, actor="t")


def _record(env, tid):
    return env["reg"].load().target(tid).attestation


def _establish(env, tid="managed-w1"):
    """One real renewal first, so the record carries what the probe measures (platform, bounds) and the
    next renewal is EQUIVALENT -- as on the live stand, whose record came from a probe."""
    op = env["ops"].renew(tid, force=True)
    assert op["params"]["result"] in ("renewed", "renewed_changed"), op
    return _record(env, tid)


# ---------------------------------------------------------------------- C3/C4: the binding view
BASE = attestation(T.MANAGED, {"identity_uri": "agentnode://d/worker/w", "address": "tcps://10.0.0.2:8443",
                               "deployment": "d", "instance": "w", "topology": "separate-worker-host",
                               "protocols": ["agentnode-worker/1"]})


@pytest.mark.parametrize("key", ["measured_at", "expires_at", "results", "challenge_nonces", "record_digest"])
def test_what_an_equivalent_renewal_may_change(key):
    other = dict(BASE, **{key: "changed"})
    assert T.attestation_binding_view(other) == T.attestation_binding_view(BASE)


@pytest.mark.parametrize("key,value", [("suite", "agentnode-target-probe/2"), ("assurance", T.CUSTOMER_OBSERVED),
                                       ("identity_uri", "agentnode://d/worker/x"), ("endpoint_digest", "0" * 64),
                                       ("properties", dict(BASE["properties"], storage_mb=1024))])
def test_what_makes_a_renewal_not_equivalent(key, value):
    assert T.attestation_binding_view(dict(BASE, **{key: value})) != T.attestation_binding_view(BASE)


def test_the_binding_does_not_contain_the_record_digest():
    t1 = T.Target.from_doc(dict(_entry(), attestation=BASE))
    again = dict(BASE, measured_at=BASE["measured_at"] + 5, challenge_nonces=["other"], record_digest="f" * 64)
    t2 = T.Target.from_doc(dict(_entry(), attestation=again))
    assert t1.properties_digest == t2.properties_digest and t1.binding("r") == t2.binding("r")


def _entry():
    w = {"address": "tcps://10.0.0.2:8443", "deployment": "d", "instance": "w",
         "identity_uri": "agentnode://d/worker/w", "topology": "separate-worker-host",
         "protocols": ["agentnode-worker/1"], "pair": {"gateway": "g1", "worker": "w"}, "key_slot": "k1",
         "config_digest": ""}
    return {"target_id": "managed-w", "type": T.MANAGED, "owner_account": None, "created_at": 1.0,
            "status": T.ACTIVE, "expires_at": None, "worker": w, "endpoint_digest": T.endpoint_digest(w),
            "limits": {"concurrency": 1, "queue": 1}, "attestation": BASE, "facts_generation": 1}


@pytest.mark.parametrize("what", ["suite", "reported_platform", "bounds", "property"])
def test_a_non_equivalent_record_bumps_the_facts_generation(env, what):
    """C4 / CC1 / CC3: suite, platform, bounds or any property changed -> new facts generation."""
    _managed(env)
    rec = _establish(env)
    fg = env["reg"].load().target("managed-w1").facts_generation
    new = copy.deepcopy(rec)
    if what == "suite":
        new["suite"] = "agentnode-target-probe/2"
    elif what == "reported_platform":
        new["properties"]["reported_platform"]["image_digest"] = "sha256:" + "1" * 64
    elif what == "bounds":
        new["properties"]["bounds"] = "other words"
    else:
        new["properties"]["storage_mb"] = 1024
    new["record_digest"] = T.digest({k: new[k] for k in sorted(new) if k != "record_digest"})
    env["reg"].set_attestation("managed-w1", new, actor="t")
    assert env["reg"].load().target("managed-w1").facts_generation > fg
    # and an equivalent one does not
    fg2 = env["reg"].load().target("managed-w1").facts_generation
    _left(env, "managed-w1", 70)
    assert env["reg"].load().target("managed-w1").facts_generation == fg2


# ---------------------------------------------------------------------- the renewal itself
def test_renewal_of_the_managed_default_publishes_atomically_without_probing(env):
    """C5/C6 (operation level): status stays active throughout, one new signed generation, fresh
    nonces, later expiry, same identity and properties, facts generation unchanged."""
    w = _managed(env)
    first = _establish(env)
    _left(env, "managed-w1", 70)
    before = env["reg"].load()
    seen = []
    w.on_job = lambda n, job: seen.append(env["reg"].load().target("managed-w1").status)
    statuses = []
    real = env["reg"].set_status
    env["reg"].set_status = lambda *a, **k: statuses.append(a) or real(*a, **k)
    report = env["ops"].attest_renew()
    after = env["reg"].load()
    t0, t1 = before.target("managed-w1"), after.target("managed-w1")
    assert report["results"] == {"managed-w1": "renewed"} and report["status"] == "ok"
    assert set(seen) == {T.ACTIVE} and len(seen) == 5 and statuses == []          # never probing
    assert after.generation == before.generation + 1
    assert t1.attestation["expires_at"] > t0.attestation["expires_at"]
    assert t1.attestation["challenge_nonces"] != t0.attestation["challenge_nonces"] == first["challenge_nonces"]
    assert T.attestation_binding_view(t1.attestation) == T.attestation_binding_view(t0.attestation)
    assert t1.facts_generation == t0.facts_generation and t1.binding("r") == t0.binding("r")
    hist = json.loads((Path(env["reg"].stand) / "targets" / "history" / ("%d.json" % after.generation)).read_text())
    assert hist["signature"]["key_id"] == env["reg"].trusted_key_id                 # signed with the generation
    state = load_renew_state(env["ops"].state_path)
    assert state["targets"]["managed-w1"]["published_record_digest"] == t1.attestation["record_digest"]


def test_operator_probe_on_an_active_target_is_a_renewal(env):
    """D3: `probe` on an active target (the managed default included) never sets it probing."""
    _managed(env)
    op = env["ops"].probe("managed-w1")
    assert op["kind"] == "renew" and env["reg"].load().target("managed-w1").status == T.ACTIVE


def test_a_parent_probe_left_open_for_the_default_is_closed_as_superseded(env):
    """F3: a36ddcdd left an open `probe` operation when it could not set the default probing."""
    _managed(env)
    op = env["ops"]._begin("probe", "managed-w1", {})
    assert env["ops"].resume() == [op["op_id"]]
    done = json.loads((env["ops"].ops_dir / (op["op_id"] + ".json")).read_text())
    assert done["state"] == "superseded" and env["reg"].load().target("managed-w1").status == T.ACTIVE
    assert env["ops"].open_operations() == []


@pytest.mark.parametrize("race", ["record", "endpoint", "status", "revoked"])
def test_a_change_during_the_probe_supersedes_the_renewal(env, race):
    """C7 / CC5 / CC6: whatever changed first wins; the renewal writes nothing measured."""
    w = _managed(env)
    _establish(env)
    reg = env["reg"]
    start = w.jobs

    def change(n, job):
        if n != start + 3:                                    # in the middle of THIS probe
            return
        if race == "record":
            _left(env, "managed-w1", 100)
        elif race == "endpoint":
            def m(doc, now):
                e = doc["targets"]["managed-w1"]
                e["worker"]["address"] = "tcps://10.0.0.9:8443"
                a = e["attestation"]                          # the register keeps record and endpoint consistent
                a["endpoint_digest"] = T.endpoint_digest(e["worker"])
                a["record_digest"] = T.digest({k: a[k] for k in sorted(a) if k != "record_digest"})
                return "test: endpoint moved"
            reg.change(m, actor="t")
        elif race == "status":
            reg.block("managed-w1", actor="t", why="operator")
        else:
            def m(doc, now):
                doc["targets"]["managed-w1"]["status"] = T.REVOKED
                return "test: revoked"
            reg.change(m, actor="t")
    w.on_job = change
    op = env["ops"].renew("managed-w1", force=True)
    assert op["state"] == "superseded"
    measured = op["params"]["record"]["record_digest"]
    assert reg.load().target("managed-w1").attestation["record_digest"] != measured


@pytest.mark.parametrize("what", ["start_identity", "start_endpoint", "record_identity", "record_endpoint"])
def test_publication_compares_identity_and_endpoint(env, what):
    """C7 / CC6 at the register itself: a start tuple or a record that names another identity or endpoint
    than the target holds NOW is never published -- even when the record digests agree."""
    reg = env["reg"]
    t = reg.load().target("managed-w1")
    start = {"status": t.status, "identity_uri": t.identity_uri, "endpoint_digest": t.endpoint_digest,
             "record_digest": t.attestation["record_digest"]}
    record = copy.deepcopy(t.attestation)
    record["measured_at"] += 1
    record["expires_at"] += 1
    if what == "start_identity":
        start["identity_uri"] = "agentnode://rmcp-test/worker/other"
    elif what == "start_endpoint":
        start["endpoint_digest"] = "0" * 64
    elif what == "record_identity":
        record["identity_uri"] = "agentnode://rmcp-test/worker/other"
    else:
        record["endpoint_digest"] = "0" * 64
    record["record_digest"] = T.digest({k: record[k] for k in sorted(record) if k != "record_digest"})
    before = reg.load()
    with pytest.raises(T.TargetsRefused) as refused:
        reg.renew_attestation("managed-w1", record, start, actor="t")
    assert refused.value.code == "renew.superseded"
    assert reg.load().digest == before.digest


def test_a_resumed_renewal_of_a_target_that_changed_probes_nothing(env):
    """A renewal journaled, then the target revoked before its probe: closed superseded, no job runs."""
    w = _managed(env)

    def crash(name):
        if name == "evidence":
            raise Crash(name)
    env["ops"]._crash_after = crash
    with pytest.raises(Crash):
        env["ops"].renew("managed-w1", force=True)
    env["ops"]._crash_after = lambda name: None
    _left(env, "managed-w1", 50)                               # another change won meanwhile
    jobs = w.jobs
    (op,) = env["ops"].open_operations()
    env["ops"].resume()
    done = json.loads((env["ops"].ops_dir / (op["op_id"] + ".json")).read_text())
    assert done["state"] == "superseded" and w.jobs == jobs


def test_positive_evidence_against_a_target_revoked_meanwhile_closes_superseded(env):
    """The probe finds an escape, but the target was revoked during it: nothing to block, nothing left open."""
    w = _managed(env, leaky=True)

    def revoke(n, job):
        if n == 3:
            env["reg"].change(lambda d, now: (d["targets"]["managed-w1"].update(status=T.REVOKED), "test")[1], actor="t")
    w.on_job = revoke
    op = env["ops"].renew("managed-w1", force=True)
    assert op["state"] == "superseded" and env["ops"].open_operations() == []
    assert env["reg"].load().target("managed-w1").status == T.REVOKED


def test_concurrent_state_writers_lose_nothing(env):
    """The timer's runner and an operator's probe of ANOTHER target record at the same time."""
    ops = env["ops"]
    errors = []

    def writer(tid):
        try:
            for i in range(25):
                ops._state_note(tid, result="renewed", reason=str(i), op_id="op-%s-%d" % (tid, i))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    ths = [threading.Thread(target=writer, args=("t-%d" % k,)) for k in range(4)]
    for th in ths:
        th.start()
    for th in ths:
        th.join(60)
    doc = load_renew_state(ops.state_path)
    assert errors == [] and sorted(doc["targets"]) == ["t-0", "t-1", "t-2", "t-3"]
    assert all(doc["targets"][t]["reason"] == "24" for t in doc["targets"])


def test_a_slot_is_held_only_while_the_connection_is_open_and_silent():
    import socket as _s
    from agentnode_sdk.remote_mcp import probe_slot as PS
    a, b = _s.socketpair()
    slot = PS.ProbeSlot(a, "t", 0.0, 1)
    assert slot.still_held() is True
    b.sendall(b'{"refused": "protocol"}\n')                    # anything after the grant: gone
    time.sleep(0.1)
    assert slot.still_held() is False
    c, d = _s.socketpair()
    slot2 = PS.ProbeSlot(c, "t", 0.0, 1)
    d.close()                                                  # the server went away
    time.sleep(0.1)
    assert slot2.still_held() is False
    slot.release(), slot2.release(), b.close()


class Crash(BaseException):
    pass


@pytest.mark.parametrize("step", ["evidence", "observe", "judge"])
def test_a_crash_after_any_step_publishes_nothing_until_resumed(env, step):
    """C17 / CC7: the record reaches the register only in the last step; resume completes it."""
    _managed(env)
    before = _record(env, "managed-w1")

    def crash(name):
        if name == step:
            raise Crash(name)
    env["ops"]._crash_after = crash
    with pytest.raises(Crash):
        env["ops"].renew("managed-w1", force=True)
    assert _record(env, "managed-w1") == before
    assert env["reg"].load().target("managed-w1").status == T.ACTIVE
    env["ops"]._crash_after = lambda name: None
    report = env["ops"].attest_renew()
    assert report["results"]["managed-w1"].startswith("renewed")
    assert _record(env, "managed-w1")["record_digest"] != before["record_digest"]
    assert env["ops"].open_operations() == []


def test_a_crash_inside_the_observation_restarts_it_with_fresh_nonces(env):
    """C17 / CC8: an observation that was not journaled complete is never used; a new one is made."""
    _managed(env)
    _establish(env)
    nonces, calls = [], {"n": 0}

    def probe(t, worker, canary, now=None):
        rec = P.probe(t, worker, canary, now=now)
        nonces.append(rec["challenge_nonces"])
        calls["n"] += 1
        if calls["n"] == 1:
            raise Crash("power cut before the journal write")
        return rec
    env["ops"]._probe_fn = probe
    with pytest.raises(Crash):
        env["ops"].renew("managed-w1", force=True)
    (op,) = env["ops"].open_operations()
    assert "record" not in op["params"]
    env["ops"].resume()
    assert len(nonces) == 2 and nonces[0] != nonces[1]
    assert _record(env, "managed-w1")["challenge_nonces"] == nonces[1]


def test_a_journaled_observation_resumed_too_late_is_not_published(env, monkeypatch):
    _managed(env)
    _establish(env)
    before = _record(env, "managed-w1")

    def crash(name):
        if name == "judge":
            raise Crash(name)
    env["ops"]._crash_after = crash
    with pytest.raises(Crash):
        env["ops"].renew("managed-w1", force=True)
    env["ops"]._crash_after = lambda name: None
    from agentnode_sdk.remote_mcp import target_ops as O
    monkeypatch.setattr(O, "STALE_OBSERVATION_SECONDS", -1.0)
    env["ops"].resume()
    assert _record(env, "managed-w1") == before
    assert load_renew_state(env["ops"].state_path)["targets"]["managed-w1"]["reason"] == "stale_observation"


# ---------------------------------------------------------------------- C8: what blocks, what degrades
def _withdraw(env):
    env["world"].issuer.recover_entry("worker", "w1")


def _unpair(env):
    ring = pairkeys.Keyring.read(env["keyring"])
    ring.remove(gateway="g1", worker="w1").write(env["keyring"])


INCONCLUSIVE = {
    "transport": dict(raise_exc=WorkerUnreachable("could not be reached: connection refused")),
    "control_side_list": dict(raise_exc=WorkerUnreachable("tls")),       # cause set in the test
    "untrusted_chain": dict(raise_exc=WorkerUnreachable("tls")),
    "memory_control_failed": dict(controls_fail=True),
}


@pytest.mark.parametrize("case", sorted(INCONCLUSIVE))
def test_inconclusive_keeps_the_old_record_and_never_suspends(env, case):
    """C8/C9 / CC9 / CC10: nothing here is evidence against the target -- degraded, alerted, old record."""
    kw = dict(INCONCLUSIVE[case])
    if case == "control_side_list":
        exc = WorkerUnreachable("tls")
        exc.__cause__ = PeerRefused("revocation-list-expired", "", "this side's list expired")
        kw["raise_exc"] = exc
    if case == "untrusted_chain":
        exc = WorkerUnreachable("tls")
        exc.__cause__ = ssl.SSLCertVerificationError("unable to get local issuer certificate")
        kw["raise_exc"] = exc
    _managed(env)
    _establish(env)
    before = _record(env, "managed-w1")
    _managed(env, **kw)
    report = env["ops"].attest_renew(target_id="managed-w1", force=True)
    assert report["status"] == "degraded" and report["results"]["managed-w1"] == "degraded"
    t = env["reg"].load().target("managed-w1")
    assert t.status == T.ACTIVE and t.attestation == before and t.attestation_valid(env["reg"].now())
    st = load_renew_state(env["ops"].state_path)["targets"]["managed-w1"]
    assert st["result"] == "degraded" and st["consecutive_failures"] == 1


def test_a_failed_canary_positive_control_is_inconclusive(env):
    _managed(env)
    _establish(env)

    def no_canary(*a, **k):
        raise RuntimeError("the canary did not see its own positive control; no egress observation is possible")
    env["ops"]._probe_fn = no_canary
    report = env["ops"].attest_renew(target_id="managed-w1", force=True)
    assert report["results"]["managed-w1"] == "degraded"
    assert env["reg"].load().target("managed-w1").status == T.ACTIVE


LOST = {
    "wrong_peer": None, "wrong_run_identity": dict(wrong_uri="agentnode://rmcp-test/worker/other"),
    "withdrawn": None, "pair_key_gone": None, "egress": dict(leaky=True), "limits": dict(no_limits=True),
}


@pytest.mark.parametrize("case", sorted(LOST))
def test_positive_evidence_suspends_at_once(env, case):
    """C8 / CC11: a proved wrong identity, an authenticated withdrawal, a missing pair key, an escape or
    a limit not enforced (controls completed) -- suspended, the managed default included."""
    _managed(env)
    _establish(env)
    kw = dict(LOST[case] or {})
    if case == "wrong_peer":
        exc = WorkerUnreachable("identity refused")
        exc.__cause__ = PeerRefused("instance", "agentnode://rmcp-test/worker/intruder", "another instance")
        kw["raise_exc"] = exc
    _managed(env, **kw)
    if case == "withdrawn":
        _withdraw(env)
    if case == "pair_key_gone":
        _unpair(env)
    report = env["ops"].attest_renew(target_id="managed-w1", force=True)
    assert report["status"] == "blocked" and report["results"]["managed-w1"] == "blocked"
    assert env["reg"].load().target("managed-w1").status == T.SUSPENDED
    assert load_renew_state(env["ops"].state_path)["targets"]["managed-w1"]["result"] == "blocked"


def test_a_kill_switch_runs_no_probe(env):
    """CC26: the gateway stopped -> no probe job at all; inconclusive."""
    w = _managed(env)
    _establish(env)
    jobs = w.jobs
    env["ops"]._kill_switch = lambda: "stopped by the operator"
    report = env["ops"].attest_renew(target_id="managed-w1", force=True)
    assert report["results"]["managed-w1"] == "degraded" and w.jobs == jobs
    assert "kill_switch" in load_renew_state(env["ops"].state_path)["targets"]["managed-w1"]["reason"]


def test_a_blocked_target_is_never_unblocked_by_the_timer(env):
    """CC12 / C15: only the operator's full probe brings a suspended target back."""
    _managed(env)
    _establish(env)
    _managed(env, leaky=True)
    env["ops"].attest_renew(target_id="managed-w1", force=True)
    assert env["reg"].load().target("managed-w1").status == T.SUSPENDED
    w = _managed(env)                                          # the host is fixed
    _left(env, "managed-w1", 1)                                # and the record is due
    report = env["ops"].attest_renew()
    assert w.jobs == 0 and "managed-w1" not in report["results"]
    assert env["reg"].load().target("managed-w1").status == T.SUSPENDED
    env["ops"].probe("managed-w1")                             # the operator's way back
    assert env["reg"].load().target("managed-w1").status == T.ACTIVE
    assert load_renew_state(env["ops"].state_path)["targets"]["managed-w1"]["result"] == "activated"


# ---------------------------------------------------------------------- C13: due and pinned time
@pytest.mark.parametrize("assurance,due_h", [(T.OPERATOR_MANAGED, 78 + 10 / 60), (T.CUSTOMER_OBSERVED, 18 + 10 / 60)])
def test_the_due_boundary_is_inclusive(assurance, due_h):
    """CC14: exactly at `expires - due` it is due; one second before it is not."""
    assert RENEW_POLICY[assurance]["due"] == round(due_h * H)
    t = T.Target.from_doc(dict(_entry(), attestation=dict(BASE, assurance=assurance, expires_at=100_000.0)))
    at = due_at(t)
    assert at == 100_000.0 - RENEW_POLICY[assurance]["due"]
    assert is_due(t, at) and not is_due(t, at - 1)


def test_reserve_and_warning_numbers():
    assert RENEW_POLICY[T.OPERATOR_MANAGED]["reserve"] == 72 * H and RENEW_POLICY[T.OPERATOR_MANAGED]["warn"] == 48 * H
    assert RENEW_POLICY[T.CUSTOMER_OBSERVED]["reserve"] == 12 * H and RENEW_POLICY[T.CUSTOMER_OBSERVED]["warn"] == 6 * H


def test_due_is_judged_on_the_pinned_time_not_the_wall_clock(env):
    """CC15: a clock set back does not postpone a renewal -- the register's high-water counts."""
    w = _managed(env)
    _establish(env)
    _left(env, "managed-w1", 80)                               # by the wall clock: not due (> 78 h 10 min)
    assert env["ops"].attest_renew()["results"] == {} and w.jobs == 5
    clock = Path(env["reg"].stand) / "targets" / "clock.json"
    clock.write_text(json.dumps({"high_water": time.time() + 2 * H}))   # effective time 2 h ahead
    report = env["ops"].attest_renew()
    assert report["results"] == {"managed-w1": "renewed"}


# ---------------------------------------------------------------------- C10: one runner, one probe per target
def _hold(path, ready, release):
    from agentnode_sdk.gateway.filelock import ProcessLock
    with ProcessLock(path, timeout=5):
        ready.set()
        release.wait(20)


def test_a_second_renewal_runner_starts_nothing(env):
    """CC18."""
    from agentnode_sdk.remote_mcp.target_ops import RENEW_LOCK_NAME
    w = _managed(env)
    _left(env, "managed-w1", 1)
    ready, release = threading.Event(), threading.Event()
    th = threading.Thread(target=_hold, args=(env["ops"].ops_dir.parent / RENEW_LOCK_NAME, ready, release))
    th.start()
    try:
        assert ready.wait(10)
        with pytest.raises(OperationFailed) as busy:
            env["ops"].attest_renew()
        assert busy.value.code == "attest_renew.busy" and w.jobs == 0
    finally:
        release.set()
        th.join(10)


def test_a_second_probe_of_one_target_never_runs_a_job(env):
    """CC19: the timer's renewal and an operator's probe of the same target exclude each other."""
    from agentnode_sdk.remote_mcp.target_ops import PROBE_LOCK_PREFIX
    w = _managed(env)
    _left(env, "managed-w1", 1)
    ready, release = threading.Event(), threading.Event()
    th = threading.Thread(target=_hold, args=(env["ops"].ops_dir.parent / (PROBE_LOCK_PREFIX + "managed-w1"),
                                              ready, release))
    th.start()
    try:
        assert ready.wait(10)
        with pytest.raises(OperationFailed) as busy:
            env["ops"].renew("managed-w1", force=True)
        assert busy.value.code == "probe.busy"
        report = env["ops"].attest_renew()
        assert report["results"]["managed-w1"] == "probe.busy" and report["status"] == "degraded"
        assert w.jobs == 0 and env["ops"].open_operations() == []
    finally:
        release.set()
        th.join(10)


def _customer(env, tid, instance, hours):
    reg = env["reg"]
    reg.add_customer(customer_spec(tid, "acct-00000000000000a1", instance, port=8500 + int(instance[1:])), actor="t")
    for st in (T.ENROLLED, T.PROBING):
        reg.set_status(tid, st, actor="t")
    t = reg.load().target(tid)
    reg.set_attestation(tid, attestation(T.CUSTOMER, t.worker), actor="t")
    reg.set_status(tid, T.ACTIVE, actor="t")
    _left(env, tid, hours)
    _pair(env, instance)
    w = ProbeWorker(t.identity_uri, env["world"], None)
    env["workers"][tid] = w
    return w


def test_four_probes_a_run_earliest_expiry_first_one_at_a_time(env):
    """Bounded and fair: five due targets -> four this run in expiry order, the fifth next run; never two
    probes at once; equal expiry ordered by target id; resumed operations count against the four."""
    m = _managed(env)
    _left(env, "managed-w1", 17)
    ws = {"cust-c5": _customer(env, "cust-c5", "c5", 5), "cust-c2": _customer(env, "cust-c2", "c2", 2),
          "cust-c3": _customer(env, "cust-c3", "c3", 3), "cust-c4": _customer(env, "cust-c4", "c4", 3)}
    ws["managed-w1"] = m
    order = []
    for tid, w in ws.items():
        w.on_job = (lambda tid: (lambda n, job: order.append(tid) if n == 1 else None))(tid)
    live = {"n": 0, "max": 0}
    real = env["ops"]._observe

    def observe(t):
        live["n"] += 1
        live["max"] = max(live["max"], live["n"])
        try:
            return real(t)
        finally:
            live["n"] -= 1
    env["ops"]._observe = observe
    first = env["ops"].attest_renew()
    assert order == ["cust-c2", "cust-c3", "cust-c4", "cust-c5"] and first["probes"] == MAX_PROBES_PER_RUN
    assert first["results"]["managed-w1"] == "deferred_bound" and live["max"] == 1
    second = env["ops"].attest_renew()
    assert order[4:] == ["managed-w1"] and second["results"]["managed-w1"].startswith("renewed")


def test_resumed_operations_count_against_the_bound(env):
    _managed(env)
    _left(env, "managed-w1", 17)
    for i in range(2, 7):
        _customer(env, "cust-c%d" % i, "c%d" % i, i)

    def crash(name):
        if name == "observe":
            raise Crash(name)
    env["ops"]._crash_after = crash
    with pytest.raises(Crash):
        env["ops"].renew("cust-c6", force=True)
    env["ops"]._crash_after = lambda name: None
    report = env["ops"].attest_renew()
    assert report["probes"] == MAX_PROBES_PER_RUN
    assert report["results"]["cust-c6"].startswith("renewed")                  # the resumed one first
    assert sorted(k for k, v in report["results"].items() if v == "deferred_bound") == ["cust-c5", "managed-w1"]


# ---------------------------------------------------------------------- C9 / C15: no fallback, ever
def _stand_ops(st, worker):
    """Operations over the target stand's register: pair key present, an inventory with nothing withdrawn."""
    class Inventory:
        trust_dir = st.stand

        def inventory(self):
            return {"entries": {}, "ever_withdrawn": []}
    ring = st.root / "pair-keys.json"
    pairkeys.empty().add(gateway="g1", worker="w1", key=os.urandom(32)).write(ring)
    return Operations(stand=st.stand, register=st.register, acks=st.acks, issuer=Inventory(), keyring_path=ring,
                      gateway_instance="g1", deployment="rmcp-test", bundles_dir=st.root / "bundles",
                      worker_for=lambda t: worker, slot=None)


@pytest.fixture
def st(tmp_path):
    s = TargetStand(tmp_path)
    s.publish_starters()
    return s


def _run(st, token, args):
    first = rpc(st.app, token, "tools/call", {"name": REGEX, "arguments": args})[1]
    st.approve(hash_of(first), CAP_REGEX)
    return first, rpc(st.app, token, "tools/call", {"name": REGEX, "arguments": args})[1]


def test_lists_and_approved_runs_continue_during_a_renewal_of_the_default(st):
    """C5 / CC4 through the REAL transport: tools/list and approved sandbox runs on the managed default
    go on while it is renewed; not one fails; it is never `probing`."""
    alice, tok = st.account("alice")
    w = ProbeWorker(st.register.load().target("managed-w1").identity_uri, delay=0.3)
    ops = _stand_ops(st, w)
    seen = []
    w.on_job = lambda n, job: seen.append(st.register.load().target("managed-w1").status)
    stop, events = threading.Event(), []

    def traffic():
        i = 0
        while not stop.is_set():
            i += 1
            code, body = rpc(st.app, tok, "tools/list")
            events.append(("list", code == 200 and bool((body or {}).get("result", {}).get("tools"))))
            _first, ran = _run(st, tok, dict(REGEX_ARGS, test_string="during %d" % i))
            sc = (ran.get("result") or {}).get("structuredContent") or {}
            events.append(("run", ran["result"].get("isError") is False
                           and (sc.get("target") or {}).get("target_id") == "managed-w1"))
    th = threading.Thread(target=traffic)
    th.start()
    try:
        time.sleep(1.0)
        op = ops.renew("managed-w1", force=True)
        time.sleep(1.0)
    finally:
        stop.set()
        th.join(60)
    assert set(seen) == {T.ACTIVE}                             # first: never probing, whatever else
    assert op["params"]["result"] in ("renewed", "renewed_changed")
    runs = [ok for kind, ok in events if kind == "run"]
    assert events and all(ok for _k, ok in events) and len(runs) >= 3, events


def test_an_equivalent_renewal_keeps_an_outstanding_approval(st):
    """C3 / CC2: prepared and approved BEFORE the renewal, submitted AFTER it -- same hash, admitted."""
    alice, tok = st.account("alice")
    w = ProbeWorker(st.register.load().target("managed-w1").identity_uri)
    ops = _stand_ops(st, w)
    ops.renew("managed-w1", force=True)                        # the probe's own record (platform, bounds)
    args = dict(REGEX_ARGS, test_string="kept 1")
    first = rpc(st.app, tok, "tools/call", {"name": REGEX, "arguments": args})[1]
    st.approve(hash_of(first), CAP_REGEX)
    t0 = st.register.load().target("managed-w1")
    op = ops.renew("managed-w1", force=True)
    t1 = st.register.load().target("managed-w1")
    assert op["params"]["result"] == "renewed" and t1.attestation["record_digest"] != t0.attestation["record_digest"]
    ran = rpc(st.app, tok, "tools/call", {"name": REGEX, "arguments": args})[1]
    assert ran["result"]["isError"] is False and ran["result"]["structuredContent"]["invocation_hash"] == hash_of(first)


def test_a_non_equivalent_renewal_refuses_the_old_approval_and_never_redirects(st):
    """C4 / CC3: the platform changed -> the prepared binding is `target_changed`; re-prepare = new hash."""
    alice, tok = st.account("alice")
    w = ProbeWorker(st.register.load().target("managed-w1").identity_uri)
    ops = _stand_ops(st, w)
    ops.renew("managed-w1", force=True)
    args = dict(REGEX_ARGS, test_string="changed 2")
    first = rpc(st.app, tok, "tools/call", {"name": REGEX, "arguments": args})[1]
    st.approve(hash_of(first), CAP_REGEX)
    w.platform = "sha256:" + "9" * 64                          # a new sandbox image, still enforcing
    op = ops.renew("managed-w1", force=True)
    assert op["params"]["result"] == "renewed_changed" and "properties.reported_platform" in op["params"]["changed"]
    again = rpc(st.app, tok, "tools/call", {"name": REGEX, "arguments": args})[1]
    assert again["result"]["structuredContent"]["status"] == "approval_required"
    assert hash_of(again) != hash_of(first) and st.w("managed-w1").starts == 0


def test_a_blocked_default_refuses_every_run_and_selects_nothing_else(st):
    """C15 / CC13: suspended default -> refused, never another target, never a local worker."""
    alice, tok = st.account("alice")
    st.customer("cust-a", alice)
    w = ProbeWorker(st.register.load().target("managed-w1").identity_uri, leaky=True)
    ops = _stand_ops(st, w)
    op = ops.renew("managed-w1", force=True)
    assert op["params"]["result"] == "blocked"
    out = rpc(st.app, tok, "tools/call", {"name": REGEX, "arguments": REGEX_ARGS})[1]
    assert out["result"]["structuredContent"]["code"] == "target_inactive"
    assert st.w("managed-w1").starts == 0 and st.w("cust-a").starts == 0 and st.worker.dispatched == 0


def test_an_expired_record_is_refused_whatever_the_renewal_state_says(st):
    """C9: degraded/retrying is no licence: at expiry `target_properties_stale`, no fallback."""
    alice, tok = st.account("alice")
    st.customer("cust-a", alice)
    w = ProbeWorker(st.register.load().target("managed-w1").identity_uri, raise_exc=WorkerUnreachable("down"))
    ops = _stand_ops(st, w)
    _left(st.register, "managed-w1", 0.0005)                   # ~2 s left
    assert ops.renew("managed-w1", force=True)["params"]["result"] == "degraded"
    time.sleep(2.5)
    with pytest.raises(TargetRefusal) as refused:
        st.router.resolve(alice, None, st.catalog.load().resolve(alice, REGEX))
    assert refused.value.code == "target_properties_stale"
    out = rpc(st.app, tok, "tools/call", {"name": REGEX, "arguments": REGEX_ARGS})[1]
    assert out["result"]["structuredContent"]["code"] == "target_properties_stale"
    assert st.w("cust-a").starts == 0 and st.worker.dispatched == 0


# ---------------------------------------------------------------------- C16: status without secrets
def test_status_and_state_carry_no_secret(env):
    _managed(env)
    _establish(env)
    status = env["ops"].attest_status()
    text = json.dumps(status) + Path(env["ops"].state_path).read_text()
    rec = _record(env, "managed-w1")
    for forbidden in ("tcps://", "key_slot", "challenge_nonces", "pair", rec["challenge_nonces"][0],
                      env["reg"]._private.private_bytes_raw().hex()):
        assert forbidden not in text, forbidden
    t = status["targets"]["managed-w1"]
    assert t["status"] == T.ACTIVE and t["hours_left"] > 160 and status["effective_time_is_pinned"] is True
    if os.name == "posix":
        assert (Path(env["ops"].state_path).stat().st_mode & 0o777) == 0o600


def test_cli_exit_codes(monkeypatch, capsys):
    class Ops:
        status = "ok"

        def attest_renew(self, target_id=None, force=False):
            return {"status": Ops.status, "results": {}}
    monkeypatch.setattr(targets_cli, "build", lambda stand, canary, **kw: (Ops(), None, None))
    for status, code in (("ok", 0), ("degraded", 3), ("blocked", 4)):
        Ops.status = status
        assert targets_cli.main(["--stand", "S", "attest-renew"]) == code
    with pytest.raises(SystemExit):
        targets_cli.main(["--stand", "S", "attest-renew", "--force"])           # --force names one target
