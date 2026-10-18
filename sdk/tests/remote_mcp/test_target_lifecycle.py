"""The target lifecycle and trust distribution (target-router T8-T11, T21; Codex D4/D5/D11).

Real issuer, real register, real keyring, real canary listener. Workers are test doubles that
behave like a sandbox (or, for the negative, like a host that lets egress through)."""
from __future__ import annotations

import dataclasses
import json
import shutil
import socket
import time

import pytest

from agentnode_sdk.pki.issuer import make_request
from agentnode_sdk.remote_mcp import target_probe as P
from agentnode_sdk.remote_mcp import targets as T
from agentnode_sdk.remote_mcp.target_ops import (ALERT_BEFORE_SECONDS, OperationFailed, Operations,
                                                 deadline_alerts)
from agentnode_sdk.worker import Outcome, pairkeys
from agentnode_sdk.worker.trust_install import install
from tests.test_mtls_transport import _one_boot  # noqa: F401 - autouse: one boot throughout
from tests.test_mtls_transport import World

from .target_fixtures import make_register

ALICE = "acct-00000000000000a1"


class SandboxLikeWorker:
    """Answers probe jobs like an enforcing sandbox; `leaky=True` lets the egress probe through."""

    def __init__(self, uri, world, folder, *, leaky=False, no_limits=False):
        self.uri, self.leaky, self.no_limits = uri, leaky, no_limits
        self.world, self.folder = world, folder
        self.ran = []

    def run(self, job):
        self.ran.append(job.run_id)
        prog = job.artifact.decode("utf-8")
        if "create_connection" in prog:                       # the egress probe
            if self.leaky:
                ns: dict = {}
                exec(compile(prog, "probe", "exec"), ns)      # noqa: S102 - the "host" runs it for real
                return Outcome(exit_code=0, stdout="", stderr="")
            return Outcome(exit_code=0, stdout=P.SENTINEL + json.dumps({"ipv4": "OSError", "ipv6": "OSError",
                                                                        "dns": "gaierror"}), stderr="")
        if "bytearray" in prog:
            mb = int(prog.split("range(")[1].split(")")[0])
            if mb > job.limits.memory_mb and not self.no_limits:
                return Outcome(exit_code=137, stdout="", stderr="killed")
            return Outcome(exit_code=0, stdout=P.SENTINEL + json.dumps({"allocated_mb": mb}), stderr="")
        if "_t.sleep" in prog:
            s = int(prog.split("_t.sleep(")[1].split(")")[0])
            if s > job.limits.wall_clock_s and not self.no_limits:
                return Outcome(exit_code=137, stdout="", stderr="killed")
            return Outcome(exit_code=0, stdout=P.SENTINEL + json.dumps({"slept": s}), stderr="")
        raise AssertionError("unexpected job")

    def who_ran(self, run_id):
        return "mtls", self.uri if run_id in self.ran else "", ""

    def install_trust(self, crl, tombs):
        if not (self.folder / "cert.pem").exists():             # the delivered certificate, installed
            shutil.copy(self.bundle / "cert.pem", self.folder / "cert.pem")
        settings = dataclasses.replace(self.world.settings(self.folder, {"g1"}, role="worker", floor=self.floor),
                                       revocation_list=str(self.folder / "revoked.crl"),
                                       identity_tombstones=str(self.folder / "revoked-identities.json"))
        return install(settings, crl, tombs)

    def runtime_version(self):
        return "podman 5 (reported)"

    def image_digest(self):
        return "sha256:" + "0" * 64


@pytest.fixture
def env(tmp_path):
    world = World(tmp_path / "pki")
    reg, _op = make_register(tmp_path / "stand")
    acks = T.TrustAcks(reg.stand, reg._public.public_bytes_raw(), operator_private=reg._private)
    keyring = tmp_path / "pair-keys.json"
    workers: dict[str, SandboxLikeWorker] = {}

    def worker_for(t):
        return workers[t.target_id]

    ops = Operations(stand=reg.stand, register=reg, acks=acks, issuer=world.issuer, keyring_path=keyring,
                     gateway_instance="g1", deployment=world.deployment, bundles_dir=tmp_path / "bundles",
                     worker_for=worker_for)
    # the managed worker, with its own copies of the lists, so trust propagation can reach it
    from agentnode_sdk.pki import floor as floors
    managed = world.service("worker", "w1")
    for name in ("revoked.crl", "revoked-identities.json"):
        shutil.copy(world.root / "trust" / name, managed / name)
    mw = SandboxLikeWorker(reg.load().target("managed-w1").identity_uri, world, managed)
    mw.floor = floors.path_for(world.floor_dir, "worker")
    workers["managed-w1"] = mw
    return {"world": world, "reg": reg, "acks": acks, "ops": ops, "workers": workers, "keyring": keyring,
            "tmp": tmp_path}


def _worker_side(env, target_id, *, leaky=False, no_limits=False):
    """The customer's worker machine: enrol with the bundle's secret, keep its own trust copies."""
    t = env["reg"].load().target(target_id)
    folder = env["tmp"] / ("worker-" + t.worker["instance"])
    folder.mkdir()
    bundle = env["tmp"] / "bundles" / target_id
    for name in ("revoked.crl", "revoked-identities.json"):
        shutil.copy(bundle / name, folder / name)
    req = make_request(folder, (bundle / "enrolment-secret").read_text())
    w = SandboxLikeWorker(t.identity_uri, env["world"], folder, leaky=leaky, no_limits=no_limits)
    w.bundle = bundle
    w.floor = env["world"].floor_for("worker", t.worker["instance"])   # its own floor (one boot)
    env["workers"][target_id] = w
    return req


def _provision(env, **kw):
    op = env["ops"].invite(ALICE, address="tcps://10.0.0.2:8444")
    tid = op["target_id"]
    req = _worker_side(env, tid, **kw)
    env["ops"].enroll(tid, req)
    return tid


def test_full_lifecycle_reaches_active_only_with_consistent_material(env):
    tid = _provision(env)
    assert env["reg"].load().target(tid).status == T.ENROLLED
    env["ops"].probe(tid)
    t = env["reg"].load().target(tid)
    assert t.status == T.ACTIVE and t.assurance == T.CUSTOMER_OBSERVED
    assert t.properties["network_none_observed"] is True and t.properties["bounds"] == P.BOUNDS
    assert (("g1", t.worker["instance"]) in pairkeys.Keyring.read(env["keyring"]).pairs())


@pytest.mark.parametrize("crash_at", ["register_invited", "pki_entry", "pair_key", "bundle",
                                      "pki_enroll", "status_enrolled", "status_probing", "trust_ack",
                                      "observe"])
def test_partial_provisioning_never_active(env, crash_at):
    class Crash(BaseException):
        pass

    def crash(step):
        if step == crash_at:
            raise Crash(step)
    env["ops"]._crash_after = crash
    tid = None
    try:
        op = env["ops"].invite(ALICE, address="tcps://10.0.0.2:8444")
        tid = op["target_id"]
        req = _worker_side(env, tid)
        env["ops"].enroll(tid, req)
        env["ops"].probe(tid)
    except Crash:
        pass
    open_ops = env["ops"].open_operations()
    assert open_ops, "a crash must leave an open, resumable operation"
    tid = tid or open_ops[0]["target_id"]
    t = env["reg"].load().target(tid)
    assert t is None or t.status != T.ACTIVE                 # never active with work outstanding
    env["ops"]._crash_after = lambda step: None
    env["ops"].resume()                                     # complete what was open...
    if env["reg"].load().target(tid).status == T.INVITED:   # ...then carry on as an operator would
        req = _worker_side(env, tid) if tid not in env["workers"] else None
        if req is not None:
            env["ops"].enroll(tid, req)
    if env["reg"].load().target(tid).status in (T.ENROLLED, T.PROBING):
        env["ops"].probe(tid)
    assert env["reg"].load().target(tid).status == T.ACTIVE
    assert not env["ops"].open_operations()


def test_probe_claim_is_bounded(env):
    tid = _provision(env, leaky=True)                      # a host that lets the probe connect
    with pytest.raises(OperationFailed) as failed:
        env["ops"].probe(tid)
    assert "network" in failed.value.detail
    t = env["reg"].load().target(tid)
    assert t.status == T.PROBING and t.attestation is None  # never active, nothing claimed
    # and a passing record still claims only what was observed, in its own words
    tid2 = _provision(env)
    env["ops"].probe(tid2)
    rec = env["reg"].load().target(tid2).attestation
    assert rec["assurance"] == T.CUSTOMER_OBSERVED and rec["properties"]["bounds"] == P.BOUNDS
    assert set(rec["properties"]) == {"network_none_observed", "memory_ceiling_mb", "wall_clock_enforced",
                                      "storage_mb", "reported_platform", "bounds"}
    assert "isolation_backend" not in json.dumps(rec)       # no backend is ever claimed proven
    assert rec["expires_at"] - rec["measured_at"] <= T.ATTESTATION_MAX_SECONDS[T.CUSTOMER_OBSERVED]


def test_a_worker_without_limits_is_never_activated(env):
    tid = _provision(env, no_limits=True)
    with pytest.raises(OperationFailed) as failed:
        env["ops"].probe(tid)
    assert "memory" in failed.value.detail and "wall_clock" in failed.value.detail


def test_canary_positive_control_is_real():
    c = P.Canary("127.0.0.1")
    try:
        assert c.positive_control() is True
        with socket.create_connection((c.host, c.port), timeout=2) as s:
            s.sendall(b"nonce-xyz")
        time.sleep(0.3)
        assert "nonce-xyz" in c.seen
    finally:
        c.close()


def test_pair_rotation_overlap_bounded(env):
    tid = _provision(env)
    env["ops"].probe(tid)
    inst = env["reg"].load().target(tid).worker["instance"]
    fg = env["reg"].load().target(tid).facts_generation
    env["ops"].rotate(tid)
    pair = pairkeys.Keyring.read(env["keyring"]).for_pair(gateway="g1", worker=inst)
    assert pair.previous and pair.generation == 2
    assert env["reg"].load().target(tid).worker["key_slot"] == "k2"
    assert env["reg"].load().target(tid).facts_generation == fg      # a key rotation keeps approvals
    assert env["ops"].retire_overdue_overlaps() == []                # inside the bound: kept
    env["ops"].clock = lambda: time.time() + 2 * 86400              # past the bound
    retired = env["ops"].retire_overdue_overlaps()
    assert len(retired) == 1
    assert not pairkeys.Keyring.read(env["keyring"]).for_pair(gateway="g1", worker=inst).previous


def test_revocation_is_final_and_withdraws_the_identity(env):
    tid = _provision(env)
    env["ops"].probe(tid)
    t = env["reg"].load().target(tid)
    env["ops"].revoke(tid)
    snap = env["reg"].load()
    assert snap.target(tid).status == T.REVOKED
    assert t.identity_uri in env["world"].issuer.inventory()["ever_withdrawn"]
    assert ("g1", t.worker["instance"]) not in pairkeys.Keyring.read(env["keyring"]).pairs()
    assert not (env["tmp"] / "bundles" / tid).exists()
    with pytest.raises(T.TargetsRefused):
        env["reg"].set_status(tid, T.ACTIVE, actor="t")
    with pytest.raises(OperationFailed):
        env["ops"].resume_target(tid)
    with pytest.raises(Exception):                                   # the same name never enrols again
        env["world"].issuer.add("worker", t.worker["instance"], secret_at=env["tmp"] / "s",
                                deliver_to=env["tmp"] / "c.pem")


def test_expiry_is_committed_and_revokes(env):
    tid = _provision(env)
    env["ops"].probe(tid)
    env["reg"].change(lambda doc, now: (doc["targets"][tid].update(expires_at=now + 0.3), "x")[1], actor="t")
    time.sleep(0.5)
    assert env["ops"].reconcile_expiry() == [tid]
    t = env["reg"].load().target(tid)
    assert t.status == T.EXPIRED and t.identity_uri in env["world"].issuer.inventory()["ever_withdrawn"]


def test_trust_refresh_pushes_and_records_acknowledgements(env):
    tid = _provision(env)
    env["ops"].probe(tid)
    before = env["acks"].load()[tid]["revocation_list_number"]
    report = env["ops"].refresh_trust(force=True)
    assert report["published"] and report["pushed"][tid] > before and report["lagging"] == []
    assert env["acks"].load()[tid]["revocation_list_number"] == report["pushed"][tid]


def test_trust_refresh_deadline_alert(env):
    trust = env["world"].root / "trust"
    assert deadline_alerts(trust, now=time.time(), lagging=[]) == []
    late = time.time() + 7 * 86400 - ALERT_BEFORE_SECONDS + 60       # within 48 h of nextUpdate
    alerts = deadline_alerts(trust, now=late, lagging=["cust-x"])
    assert any("revocation list expires" in a for a in alerts)
    assert any("tombstone list expires" in a for a in alerts)
    assert any("cust-x" in a for a in alerts)
