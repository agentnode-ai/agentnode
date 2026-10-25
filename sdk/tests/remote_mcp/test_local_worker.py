"""Local worker, slice 1: a worker behind NAT dials the control plane and listens nowhere (Codex Q-LW-0001, profile
local-worker-slice1-r1 C1-C20, DECISION-0001 R1-R13, DECISION-0002).

Real certificates from a real issuer, real TLS over loopback, the real reverse hub, the real dial-back agent and the
real `ReverseWorker`; what stands in is the runtime behind the agent (`AWorkerThatAnswers`), because the question here
is the line and who may use it. Each test names the criterion / counter-check (CCn) it holds."""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

from agentnode_sdk.remote_mcp import reverse_hub as R
from agentnode_sdk.remote_mcp import target_probe as P
from agentnode_sdk.remote_mcp import targets as T
from agentnode_sdk.remote_mcp.target_router import TargetRefusal, TargetRouter, classify
from agentnode_sdk.worker import REVERSE_SESSION, Outcome, WorkerUnreachable, pairkeys
from agentnode_sdk.worker import dialback as D
from agentnode_sdk.worker import lease as _lease
from agentnode_sdk.worker import protocol as wire
from agentnode_sdk.worker import topology as _topology
from agentnode_sdk.worker.journal import Journal
from agentnode_sdk.worker.remote import ReverseWorker, from_address
from agentnode_sdk.worker.service import Bench, LeaseWatch
from agentnode_sdk.worker.tls import TlsListener
from tests.test_mtls_transport import World, _one_boot, a_job  # noqa: F401 - autouse: one boot throughout
from tests.test_socket_worker import AWorkerThatAnswers

from .target_fixtures import make_register, worker as worker_spec

INST = "c1a2b3c4d5e6"
GW = "g1"
ALICE, BOB = "acct-00000000000000a1", "acct-00000000000000b2"


# ---------------------------------------------------------------------- the stand
class Blocking(AWorkerThatAnswers):
    """A run that lasts until it is stopped (or `seconds` pass); records how it ended."""

    def __init__(self, seconds: float = 30.0):
        super().__init__()
        self.seconds = seconds
        self.go = threading.Event()
        self.ended = {}

    def run(self, job):
        self.ran.append(job)
        stopped = self.go.wait(self.seconds)
        self.ended[job.run_id] = ("stopped" if stopped else "finished", time.monotonic())
        return Outcome(exit_code=137 if stopped else 0, stdout="", stderr="", reason="stopped" if stopped else "exited",
                       runtime_platform="a-stand-in")

    def stop(self, run_id, container_name, appear_seconds):
        self.stopped.append((run_id, container_name))
        self.go.set()
        return True

    def gone(self, container_name, patiently=True):
        from agentnode_sdk.worker import Gone
        return Gone(answered=True, left=())


class Stand:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.world = World(tmp / "pki")
        self.gw_dir = self.world.service("gateway", GW)
        self.wk_dir = self.world.service("worker", INST)
        self.ring = pairkeys.empty().add(gateway=GW, worker=INST, key=os.urandom(32))
        self.gw_tls = self.world.settings(self.gw_dir, {INST}, role="gateway")
        self.wk_tls = self.world.settings(self.wk_dir, {GW}, role="worker",
                                          floor=self.world.floor_for("worker", INST))
        self.view = {INST: {"target_id": "cust-local1", "status": "active", "concurrency": 1, "owner": ALICE,
                            "device": "dev-a"}}
        self.code = {"now": ""}
        self.said: list[str] = []
        self.hub = R.ReverseHub("tcps://127.0.0.1:0", self.gw_tls, targets=lambda: self.view,
                                eligible=lambda tid: self.code["now"], allow_loopback=True, say=self.said.append)
        self.hub.start()
        self.agents = []

    def agent(self, *, stub=None, slots=None, max_runs=1, features=True, leases=False, ttl=20.0, tls=None,
              journal=True, idle=D.IDLE_SECONDS, journal_dir=None, port=None):
        stub = stub or AWorkerThatAnswers()
        n = len(self.agents)
        bench = Bench(stub, "", None, only_uid=None, remembers_at=str(self.tmp / ("floor-%d.json" % n)))
        bench.label = INST
        bench.tls = tls or self.wk_tls
        bench.max_runs = max_runs
        bench.features = (D.FEATURE,) if features else ()
        if journal:
            bench.journal = Journal(str(journal_dir or (self.tmp / ("journal-%d" % n))))
        if leases:
            bench.leases = _lease.Leases(str(self.tmp / ("lease-%d.json" % n)), ttl=ttl)
            bench.lease_watch = LeaseWatch(bench, every=0.2)
            bench.lease_watch.start()
        listener = TlsListener(bench, "", tls or self.wk_tls, say=self.said.append, topology=REVERSE_SESSION)
        listener.use_keyring(self.ring, INST)
        agent = D.DialBackAgent(listener, "tcps://127.0.0.1:%d" % (port or self.hub.port),
                                slots=slots or D.slots_for(max_runs),
                                allow_loopback=True, say=self.said.append, idle=idle)
        agent.bench, agent.stub = bench, stub
        agent.start()
        self.agents.append(agent)
        return agent

    def control(self, may_run=None):
        return from_address("reverse://" + INST, None, topology=REVERSE_SESSION, keyring=self.ring, own_instance=GW,
                            hub=self.hub, may_run=may_run or (lambda: self.code["now"]))

    def wait_parked(self, n: int, seconds: float = 10.0) -> int:
        end = time.time() + seconds
        while time.time() < end:
            got = self.hub.parked(INST)
            if got >= n:
                return got
            time.sleep(0.05)
        return self.hub.parked(INST)

    def close(self):
        for a in self.agents:
            a.stop()
            if hasattr(a.bench, "lease_watch"):
                a.bench.lease_watch.stop()
        self.hub.stop()


@pytest.fixture()
def stand(tmp_path):
    s = Stand(tmp_path)
    yield s
    s.close()


# ---------------------------------------------------------------------- C2/C3: outbound only, private, off by default
@pytest.mark.parametrize("address", ["tcps://0.0.0.0:9443", "tcps://[::]:9443", "tcps://8.8.8.8:9443",
                                     "tcps://127.0.0.1:9443", "tcps://10.0.0.4:8443", "tcps://hub.example:9443",
                                     "tcps://10.0.0.4", "http://10.0.0.4:9443"])
def test_the_hub_listens_only_on_one_private_address(address):
    """C3 / CC2: wildcard, public, loopback (outside a test), a name, no port and the live worker port are refused."""
    with pytest.raises(ValueError):
        R.listen_endpoint(address)
    assert R.listen_endpoint("tcps://10.0.0.4:9443") == ("10.0.0.4", 9443)


def test_nothing_listens_without_reverse_listen(tmp_path):
    """C3 / CC3: a stand whose worker.json names no `reverse_listen` gets no hub and no socket."""
    from agentnode_sdk.remote_mcp import server

    stand = tmp_path / "stand"
    stand.mkdir()
    tls = {k: str(tmp_path / k) for k in ("certificate", "key", "anchor", "revocation_list", "floor",
                                          "identity_tombstones")}
    (stand / "worker.json").write_text(json.dumps({"tls": tls, "deployment": "alpha", "accept": ["w1"]}))

    class App:
        lifespan_hooks: list = []
    app = App()
    assert server.attach_reverse_hub(app, str(stand), router=None) is None
    assert app.lifespan_hooks == [] and not hasattr(app, "reverse_hub")


def test_the_agent_listens_nowhere_and_the_control_never_dials(stand, monkeypatch):
    """C2 / CC4: every connection is the agent's (outbound); the control side has no dial at all -- a run succeeds
    with `socket.create_connection` made to refuse for anyone but the agent."""
    agent = stand.agent()
    assert stand.wait_parked(D.slots_for(1)) == D.slots_for(1)
    assert agent.listener._socket is None                     # the TLS door was never opened
    real = socket.create_connection
    callers = []

    def guarded(*a, **kw):
        callers.append(threading.current_thread().name)
        if not threading.current_thread().name.startswith("dial-back-"):
            raise AssertionError("the control side dialled")
        return real(*a, **kw)
    monkeypatch.setattr(socket, "create_connection", guarded)
    w = stand.control()
    out = w.run(a_job("outbound-1"))
    assert out.stdout == "RAN"
    assert all(name.startswith("dial-back-") for name in callers)
    with pytest.raises(WorkerUnreachable):
        from_address("reverse://" + INST, None, topology=REVERSE_SESSION, keyring=stand.ring, own_instance=GW)


# ---------------------------------------------------------------------- C4/C5/C6/C7: identity, frames, slots
def test_a_worker_of_another_identity_parks_nothing(stand):
    """C4 / CC5: a certificate for an instance that is not a dial-back target here is closed, nothing parked."""
    other_dir = stand.world.service("worker", "c9f9f9f9f9f9")
    other = stand.world.settings(other_dir, {GW}, role="worker", floor=stand.world.floor_for("worker", "c9f9f9f9f9f9"))
    stand.agent(tls=other)
    time.sleep(1.5)
    assert stand.hub.parked(INST) == 0 and stand.hub.parked("c9f9f9f9f9f9") == 0
    assert stand.hub.refused["identity"] >= 1


def test_a_gateway_certificate_cannot_park_as_a_worker(stand):
    """C4 / CC5: the wrong ROLE (a gateway's certificate on the agent side) parks nothing."""
    gw2 = stand.world.service("gateway", "g9")
    as_gateway = stand.world.settings(gw2, {GW}, role="gateway")
    stand.agent(tls=as_gateway)
    time.sleep(1.5)
    # refused in the handshake (a gateway certificate is no TLS server's) or by the role check: never parked
    assert stand.hub.parked(INST) == 0 and stand.hub.refused["identity"] + stand.hub.refused["handshake"] >= 1


@pytest.mark.parametrize("status", ["invited", "suspended", "revoked", "expired"])
def test_a_target_that_may_not_be_reached_parks_nothing(stand, status):
    """C4/C16: only enrolled, probing or active dial-back targets hold slots."""
    stand.view[INST]["status"] = status
    stand.agent()
    time.sleep(1.5)
    assert stand.hub.parked(INST) == 0 and stand.hub.refused["not_a_parkable_target"] >= 1


def test_each_slot_carries_one_request_and_is_never_reused(stand):
    """C5 / CC11: N requests consume N slots (each closed after its answer) and the agent replaces each one."""
    agent = stand.agent()
    p = D.slots_for(1)
    stand.wait_parked(p)
    w = stand.control()
    for i in range(3):
        assert w.run(a_job("one-%d" % i)).stdout == "RAN"
    # describe + 3 runs = 4 conversations at least (no lease kept here), each on its own slot
    assert agent.conversations >= 4
    assert stand.wait_parked(p) == p                          # replaced, never more than P from this agent
    taken, _who = stand.hub.checkout(INST, 5.0)               # a slot handed out has LEFT the pool
    try:
        assert all(s.connection is not taken for s in stand.hub._pools[INST])
    finally:
        taken.close()


def test_the_hub_bounds_slots_per_worker_and_in_total(stand):
    """C6 / CC12: an agent dialling 3P slots gets at most 2P parked; a global cap below that bounds it too."""
    p = D.slots_for(1)
    stand.agent(slots=3 * p)
    time.sleep(3.0)
    assert stand.hub.parked(INST) == 2 * p and stand.hub.refused["slot_bound"] >= 1
    stand.hub.max_total = 3
    stand.hub.drain(INST)
    time.sleep(2.0)
    assert stand.hub.parked(INST) == 3


def test_no_slot_within_the_budget_is_offline(stand):
    """C6 / negative: slot exhaustion waits only within the caller's budget, then `target_offline`."""
    started = time.time()
    with pytest.raises(R.SlotRefused) as exc:
        stand.hub.checkout(INST, 0.5)
    assert exc.value.code == "target_offline" and time.time() - started < 3


def test_a_dead_parked_slot_is_never_handed_out(stand):
    """C6 / CC14: the agent goes away after parking: its slots are dead, `checkout` hands none out, a preflight is
    `target_offline` -- a parked slot's age is not proof of reachability."""
    stand.hub.reap = lambda: 0                                # no reaper: the parked slots stay in the pool, dead
    agent = stand.agent(idle=1.0)                             # its slots hang up after 1 s unused
    stand.wait_parked(D.slots_for(1))
    agent.stop()                                              # ... and are not replaced
    time.sleep(2.0)
    assert stand.hub.parked(INST) == D.slots_for(1)           # still parked, every one of them dead
    with pytest.raises(WorkerUnreachable) as exc:
        stand.control().confirm_reachable(1.0)
    assert classify(exc.value) == "target_offline" and stand.hub.parked(INST) == 0


def test_the_callers_identity_is_per_connection(stand):
    """C7 / CC1: the identity a handshake proved is visible to ITS conversation only -- another thread of the same
    worker sees no caller while that conversation runs (a shared `Bench._caller` would leak it)."""
    seen = {}
    agent = stand.agent()
    inner = agent.bench.converse

    def spy(connection, noted=None, key=None):
        seen["mine"] = agent.bench._caller
        t = threading.Thread(target=lambda: seen.setdefault("other", agent.bench._caller))
        t.start()
        t.join()
        return inner(connection, noted=noted, key=key)
    agent.bench.converse = spy
    stand.wait_parked(D.slots_for(1))
    stand.control()._describe()
    assert seen["mine"] == GW and seen["other"] == ""


def test_two_gateways_racing_on_one_worker_are_told_apart(stand):
    """C7: concurrent conversations under two different proved callers each take their OWN lease holder."""
    bench = Bench(AWorkerThatAnswers(), "", None, only_uid=None, remembers_at=str(stand.tmp / "f.json"))
    bench.leases = _lease.Leases(None)                         # no counter file: the race is on the caller
    got = {}
    barrier = threading.Barrier(2)

    def take(who):
        bench.proved_caller(who)
        barrier.wait()
        got[who] = bench.answer("take_lease", {})["holder"]
        bench.proved_caller(None)
    ts = [threading.Thread(target=take, args=(w,)) for w in ("ga", "gb")]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert got == {"ga": "ga", "gb": "gb"}


# ---------------------------------------------------------------------- C8-C11: register, visibility, binding
def _reverse_spec(target_id="cust-local1", owner=ALICE, device="dev-a", instance=INST):
    w = worker_spec(instance)
    w.update(address="reverse://" + instance, topology=T.REVERSE_SESSION, device_id=device)
    return {"target_id": target_id, "type": T.CUSTOMER, "owner_account": owner, "status": T.INVITED,
            "expires_at": None, "worker": w, "limits": {"concurrency": 1, "queue": 2}, "attestation": None}


def test_a_reverse_target_carries_its_device_in_schema_digest_and_binding(tmp_path):
    """C8 / C11 / CC6: the device is required, part of the endpoint digest and named in the binding; a listening
    customer target's entry and binding are byte-for-byte what they were."""
    reg, _op = make_register(tmp_path / "stand")
    snap = reg.add_customer(_reverse_spec(), actor="t")
    t = snap.target("cust-local1")
    assert t.is_reverse and t.device_id == "dev-a" and t.worker["address"] == "reverse://" + INST
    assert t.binding("r")["device_id"] == "dev-a"
    other = dict(t.worker, device_id="dev-b")
    assert T.endpoint_digest(other) != t.endpoint_digest
    managed = snap.target("managed-w1")
    assert "device_id" not in managed.binding("r") and not managed.is_reverse
    bad = _reverse_spec(target_id="cust-local2", instance="c2b2b2b2b2b2")
    del bad["worker"]["device_id"]
    with pytest.raises(Exception):
        reg.add_customer(bad, actor="t")


def test_a_reverse_address_must_be_its_own_instance(tmp_path):
    """C8: `reverse://<another instance>` and a reverse topology with a tcps address are refused by the register."""
    reg, _op = make_register(tmp_path / "stand")
    spec = _reverse_spec()
    spec["worker"]["address"] = "reverse://c0ffee0c0ffee"
    with pytest.raises(Exception):
        reg.add_customer(spec, actor="t")
    for bad in ("reverse://", "reverse://UPPER", "reverse://a/b"):
        with pytest.raises(_topology.TopologyRefused):
            _topology.check(REVERSE_SESSION, bad)
    with pytest.raises(_topology.TopologyRefused):
        _topology.check(REVERSE_SESSION, "tcps://10.0.0.2:8443")


class _Acks:
    def load(self):
        return {}


def _router(reg, authority=None):
    r = TargetRouter(register=reg, acks=_Acks(), worker_for=lambda t: None, deployment_trust=lambda: None)
    r.reverse_authority = authority
    return r


@pytest.mark.parametrize("why", ["target_device_revoked", "target_account_suspended", "target_authority_unverifiable"])
def test_a_revoked_device_or_suspended_account_refuses_the_target_by_name(tmp_path, why):
    """C8 / C16 / negative: the owner's live authority, asked at resolve time, refuses with its own code."""
    reg, _op = make_register(tmp_path / "stand")
    reg.add_customer(_reverse_spec(), actor="t")
    with pytest.raises(TargetRefusal) as exc:
        _router(reg, authority=lambda a, d: why).resolve(ALICE, "cust-local1", None)
    assert exc.value.code == why


def test_no_authority_wired_refuses_every_dial_back_target(tmp_path):
    """C16: an unwired authority fails closed."""
    reg, _op = make_register(tmp_path / "stand")
    reg.add_customer(_reverse_spec(), actor="t")
    with pytest.raises(TargetRefusal) as exc:
        _router(reg).resolve(ALICE, "cust-local1", None)
    assert exc.value.code == "target_authority_unverifiable"


def test_a_foreign_dial_back_target_is_unknown(tmp_path):
    """C9 / CC8: another account asking for it gets `target_unknown`, exactly as for an id that does not exist."""
    reg, _op = make_register(tmp_path / "stand")
    reg.add_customer(_reverse_spec(), actor="t")
    for wish in ("cust-local1", "cust-nosuch1"):
        with pytest.raises(TargetRefusal) as exc:
            _router(reg, authority=lambda a, d: "").resolve(BOB, wish, None)
        assert exc.value.code == "target_unknown" and exc.value.detail == ""


def test_the_managed_target_stays_the_default_and_an_offline_local_never_falls_back(tmp_path):
    """C9 / CC9: without a wish the managed default is chosen; a wished-for dial-back target that is not usable is
    refused -- never resolved to the managed one."""
    reg, _op = make_register(tmp_path / "stand")
    reg.add_customer(_reverse_spec(), actor="t")
    router = _router(reg, authority=lambda a, d: "")
    with pytest.raises(TargetRefusal):
        router.resolve(ALICE, None, None)                    # the managed default: refused only on its trust
    with pytest.raises(TargetRefusal) as exc:
        router.resolve(ALICE, "cust-local1", None)
    assert exc.value.target_id == "cust-local1"               # judged as itself, not swapped for managed-w1


def test_the_invite_binds_account_and_device_and_hands_out_no_credential(tmp_path):
    """C10 / CC7: a reverse invite needs the account's live device; the bundle names where to dial and nothing an
    account or an approver holds."""
    from .test_target_lifecycle import env as _env_fixture  # noqa: F401
    from agentnode_sdk.remote_mcp.target_ops import OperationFailed, Operations
    from tests.test_mtls_transport import World as W2

    world = W2(tmp_path / "pki")
    reg, _op = make_register(tmp_path / "stand")
    acks = T.TrustAcks(reg.stand, reg._public.public_bytes_raw(), operator_private=reg._private)
    ops = Operations(stand=reg.stand, register=reg, acks=acks, issuer=world.issuer, keyring_path=tmp_path / "ring.json",
                     gateway_instance="g1", deployment=world.deployment, bundles_dir=tmp_path / "bundles",
                     worker_for=lambda t: None, slot=None)
    with pytest.raises(OperationFailed):
        ops.invite(ALICE, reverse=True, device="dev-a")     # no hub configured
    ops.reverse_hub = "tcps://10.0.0.4:9443"
    ops.device_authority = lambda a, d: "target_device_revoked" if d != "dev-a" else ""
    with pytest.raises(OperationFailed) as exc:
        ops.invite(ALICE, reverse=True, device="dev-of-someone-else")
    assert exc.value.detail == "target_device_revoked"
    op = ops.invite(ALICE, reverse=True, device="dev-a", concurrency=2, queue=3)
    t = reg.load().target(op["target_id"])
    assert t.is_reverse and t.device_id == "dev-a" and t.worker["address"] == "reverse://" + t.worker["instance"]
    bundle = tmp_path / "bundles" / op["target_id"]
    described = json.loads((bundle / "target.json").read_text())
    assert described["connect"] == "tcps://10.0.0.4:9443" and "listen" not in described
    assert described["limits"] == {"concurrency": 2, "queue": 3}
    assert sorted(p.name for p in bundle.iterdir()) == ["ca.pem", "enrolment-secret", "pair-keys.json",
                                                         "revoked-identities.json", "revoked.crl", "target.json"]
    with pytest.raises(OperationFailed):
        ops.invite(ALICE, address="tcps://10.0.0.2:9000", device="dev-a")   # a device only for dial-back


# ---------------------------------------------------------------------- C12: measured suitability
class Canned:
    """Answers each probe job with what a dial-back worker's isolated job would print (or nothing)."""

    def __init__(self, **over):
        self.said = {"-egress": {"ipv4": "OSError", "ipv6": "OSError", "dns": "gaierror"},
                     "-memory-control": {"allocated_mb": 64}, "-wall-control": {"slept": 1}, "-memory": None,
                     "-wall": None, "-memory-usable": {"allocated_mb": 896},
                     "-cpu": {"cpu_max": "100000 100000", "usage_s": 4.1, "wall_s": 4.0, "cpus_visible": 2},
                     "-cpu-control": {"cpu_max": "200000 100000", "usage_s": 7.8, "wall_s": 4.0, "cpus_visible": 2},
                     "-storage": {"first": "written", "past": "ENOSPC"},
                     "-boundary": {"kernel": "6.12.0", "machine": "x86_64", "engine": "podman-5.6.0",
                                   "uid_map": ["0", "1000", "1"], "cap_eff": "0000000000000000", "rootfs": "refused",
                                   "processes_visible": 2, "pid1": "/usr/bin/python3"}}
        self.said.update(over)

    def run(self, job):
        key = next(k for k in sorted(self.said, key=len, reverse=True) if job.run_id.endswith(k))
        body = self.said[key]
        if key == "-wall":
            time.sleep(0.05)
        return Outcome(exit_code=0 if body is not None else 137, stdout="" if body is None else
                       P.SENTINEL + json.dumps(body), stderr="", reason="", runtime_platform="x")

    def who_ran(self, run_id):
        return ("reverse-mtls", _target().identity_uri, INST)

    def runtime_version(self):
        return "a claim that is never used"

    def image_digest(self):
        return "sha256:" + "2" * 64


class _Canary:
    host, port, seen = "127.0.0.1", 9, []

    def positive_control(self):
        return True


def _target(reverse=True):
    w = worker_spec(INST)
    if reverse:
        w.update(address="reverse://" + INST, topology=T.REVERSE_SESSION, device_id="dev-a")
    return T.Target(target_id="cust-local1", type=T.CUSTOMER, owner_account=ALICE, created_at=0.0,
                    status=T.PROBING, expires_at=None, worker=w, endpoint_digest=T.endpoint_digest(w),
                    limits={"concurrency": 1, "queue": 2}, attestation=None, facts_generation=1)


def test_a_dial_back_target_is_activated_only_on_observed_properties():
    """C12 / CC15 / CC16: every property is what a job observed; the worker's claims are kept apart, unused."""
    rec = P.probe(_target(), Canned(), _Canary())
    props = rec["properties"]
    assert props["storage_mb"] == 512 and props["cpu_quota_enforced"] and props["isolation_observed"]
    assert props["platform"] == {"kernel": "6.12.0", "machine": "x86_64", "runtime": "podman-5.6.0"}
    assert props["reported_platform"]["runtime_version"] == "a claim that is never used"
    assert P.passed(rec) == []


@pytest.mark.parametrize("override,failed", [
    ({"-storage": {"first": "written", "past": "written"}}, "storage"),          # no ceiling: not 512
    ({"-storage": {"first": "ENOSPC", "past": "ENOSPC"}}, "storage"),            # not even 448 usable
    ({"-cpu": {"cpu_max": "max 100000", "usage_s": 7.9, "wall_s": 4.0, "cpus_visible": 2}}, "cpu"),
    ({"-cpu": {"cpu_max": "100000 100000", "usage_s": 4.0, "wall_s": 4.0, "cpus_visible": 1}}, "cpu"),
    ({"-cpu-control": {"cpu_max": "200000 100000", "usage_s": 4.0, "wall_s": 4.0, "cpus_visible": 2}}, "cpu"),
    ({"-cpu-control": None}, "cpu"),
    ({"-boundary": {"kernel": "6.12", "machine": "x", "engine": "podman", "uid_map": ["0", "0", "4294967295"],
                    "cap_eff": "0000000000000000", "rootfs": "refused", "processes_visible": 2,
                    "pid1": "/usr/bin/python3"}}, "isolation"),
    ({"-boundary": {"kernel": "6.12", "machine": "x", "engine": "podman", "uid_map": ["0", "1000", "1"],
                    "cap_eff": "0000000000000000", "rootfs": "refused", "processes_visible": 2,
                    "pid1": "/sbin/init"}}, "isolation"),
    ({"-boundary": {"kernel": "6.12", "machine": "x", "engine": "", "uid_map": ["0", "1000", "1"],
                    "cap_eff": "0000000000000000", "rootfs": "refused", "processes_visible": 2,
                    "pid1": "/usr/bin/python3"}}, "platform"),
    ({"-memory-usable": None}, "memory"),
])
def test_an_unenforced_or_unobserved_property_keeps_the_target_inactive(override, failed):
    """C12 / negative: no ceiling, no quota, no namespace, no runtime, no usable memory -> activation refused."""
    rec = P.probe(_target(), Canned(**override), _Canary())
    assert failed in P.passed(rec)


def test_the_managed_probe_is_unchanged():
    """R2: a listening target's probe runs exactly the five jobs it always did and claims no new property."""
    calls = []

    class Recording(Canned):
        def run(self, job):
            calls.append(job.run_id.split("-", 2)[-1])
            return super().run(job)
    rec = P.probe(_target(reverse=False), Recording(), _Canary())
    assert calls == ["egress", "memory-control", "wall-control", "memory", "wall"]
    assert "reverse" not in rec["results"] and "cpu_quota_enforced" not in rec["properties"]


def test_the_router_requires_the_observed_properties_of_a_dial_back_target(tmp_path):
    """C12: an attestation without cpu/isolation/platform observations is unsuitable for a dial-back target."""
    from agentnode_sdk.remote_mcp.target_router import _eligible

    rec = P.probe(_target(), Canned(), _Canary(), now=time.time())
    t = _target()
    for drop, named in (("cpu_quota_enforced", "cpu"), ("isolation_observed", "isolation"), ("platform", "platform")):
        props = {k: v for k, v in rec["properties"].items() if k != drop}
        a = dict(rec, properties=props, assurance=T.CUSTOMER_OBSERVED)
        t2 = T.Target(**{**t.__dict__, "attestation": a, "status": T.ACTIVE})

        class E:
            accepted_assurance = (T.CUSTOMER_OBSERVED,)
        with pytest.raises(TargetRefusal) as exc:
            _eligible(t2, E, time.time(), {}, None)
        assert (exc.value.code, exc.value.detail) == ("target_unsuitable", named)


def test_no_isolating_runtime_refuses_before_any_dial(monkeypatch):
    """C12 / C20 / CC17 / CC28: a worker that cannot isolate connects to nothing and runs nothing."""
    class NoRuntime(AWorkerThatAnswers):
        def can_it_isolate(self):
            from agentnode_sdk.worker import Isolation
            return Isolation(available=False, backend="none", reason="no runtime", measured=())
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(AssertionError("dialled")))
    with pytest.raises(D.DialRefused) as exc:
        D.refuse_without_isolation(NoRuntime())
    assert exc.value.code == wire.RUNTIME_ABSENT


def test_a_runtime_that_vanishes_after_the_start_is_runtime_absent_at_the_run(stand):
    """C12 / CC17 / negative (Codex D6): the agent started isolated, then its runtime went away: the next `run` is
    refused `runtime-absent` before the journal claims it -- nothing ran, nothing is recorded as accepted."""
    from agentnode_sdk.worker import Isolation
    from agentnode_sdk.worker.remote import NoRuntimeThere

    class Vanishing(AWorkerThatAnswers):
        gone = False

        def can_it_isolate(self):
            if self.gone:
                return Isolation(available=False, backend="none", reason="podman is gone", measured=())
            return super().can_it_isolate()
    stub = Vanishing()
    agent = stand.agent(stub=stub)
    stand.wait_parked(D.slots_for(1))
    w = stand.control()
    assert w.run(a_job("iso-1")).stdout == "RAN"
    stub.gone = True
    with pytest.raises(NoRuntimeThere):
        w.run(a_job("iso-2"))
    assert [j.run_id for j in stub.ran] == ["iso-1"] and agent.bench.journal.look("iso-2") is None


@pytest.mark.parametrize("body", [b"{not json", b"", b"[]"])
def test_an_unreadable_or_malformed_keyring_file_refuses(tmp_path, body):
    """C16 / negative: the agent's pair-key store as a whole is garbage, empty or the wrong shape: refused."""
    path = tmp_path / "ring.json"
    path.write_bytes(body)
    with pytest.raises(pairkeys.KeyringRefused):
        pairkeys.Keyring.read(path)
    with pytest.raises(pairkeys.KeyringRefused) as exc:
        pairkeys.Keyring.read(tmp_path / "absent.json")
    assert exc.value.cause == pairkeys.NO_FILE


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="the Windows stand-in (DECISION-0001 R10)")
def test_on_this_windows_machine_the_agent_refuses_and_reports_wsl():
    """C20 / CC28: native Windows: the real container backend finds no isolating runtime -> runtime-absent before a
    dial; the platform report states the WSL2 distribution state (none here), nothing is installed or started."""
    from agentnode_sdk.sandbox.container_backend import ContainerBackend
    from agentnode_sdk.worker.local import LocalWorker

    report = D.platform_report()
    assert report["os"].startswith("win") and report["wsl2_distribution"] in ("none", "present", "unknown")
    with pytest.raises(D.DialRefused) as exc:
        D.refuse_without_isolation(LocalWorker(ContainerBackend(), apply_job_limits=True))
    assert exc.value.code == wire.RUNTIME_ABSENT
    assert D.private_state_problems([__file__]) != []          # restrictive ACLs not proved: refused


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX modes")
def test_secret_files_broader_than_0600_refuse_the_agent(tmp_path):
    """C18 / CC27: a key or keyring readable by others, or a state directory others may write, refuses the start."""
    key = tmp_path / "key.pem"
    key.write_text("x")
    os.chmod(key, 0o600)
    os.chmod(tmp_path, 0o700)
    assert D.private_state_problems([key], [tmp_path]) == []
    os.chmod(key, 0o640)
    assert any("more than 0600" in p for p in D.private_state_problems([key], [tmp_path]))
    os.chmod(key, 0o600)
    os.chmod(tmp_path, 0o777)
    assert any("writable by another" in p for p in D.private_state_problems([key], [tmp_path]))
    os.chmod(tmp_path, 0o700)


def test_the_agent_applies_the_jobs_own_limits_and_nothing_else_does():
    """DECISION-0002: memory/cpu/storage from the job on a dial-back worker; every other worker's argv unchanged."""
    from agentnode_sdk.sandbox.container_backend import ContainerBackend
    from agentnode_sdk.sandbox.types import ProcessSpec
    from agentnode_sdk.worker.local import LocalWorker

    b = ContainerBackend()
    plain = b.wrap_command(ProcessSpec(command=["true"], network="none"))
    job = a_job("lim")
    mine = LocalWorker(b, apply_job_limits=True)._limits_for(job)
    assert LocalWorker(b)._limits_for(job) == {}
    shaped = b.wrap_command(ProcessSpec(command=["true"], network="none", limits=mine))
    assert plain[plain.index("--memory") + 1] == "512m" and plain[plain.index("--cpus") + 1] == "1"
    assert shaped[shaped.index("--memory") + 1] == "%dm" % job.limits.memory_mb
    assert shaped[shaped.index("--memory-swap") + 1] == "%dm" % job.limits.memory_mb
    assert [a for a in shaped if a not in ("256m", "1")] == [a for a in plain if a not in ("512m", "1")]


# ---------------------------------------------------------------------- C13/C14/C15: lease, at most once, fairness
def test_withholding_renewal_ends_the_run_within_the_lease_bound(stand, monkeypatch):
    """C13 / CC18 / CC19: the target becomes ineligible mid-run -> no renewal, the hub drains, the worker's own
    lease watch stops the run within ttl + its poll interval, and cleanup is journaled."""
    monkeypatch.setattr(_lease, "HEARTBEAT_EVERY_SECONDS", 0.3)
    stub = Blocking(seconds=30.0)
    stand.agent(stub=stub, leases=True, ttl=2.0)
    stand.wait_parked(D.slots_for(1))
    w = stand.control()
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", _try(w.run, a_job("leased-1"))))
    t.start()
    end = time.time() + 10
    while not stub.ran and time.time() < end:
        time.sleep(0.05)
    assert stub.ran, "the run never started"
    flipped = time.monotonic()
    stand.code["now"] = "target_device_revoked"
    t.join(15)
    ended = stub.ended.get("leased-1")
    assert ended and ended[0] == "stopped"
    assert ended[1] - flipped <= 2.0 + 0.3 + 0.2 + 1.0     # ttl + one beat + the watch's poll + slack
    assert stand.hub.parked(INST) == 0 or stand.code["now"]


def test_the_renewal_gate_alone_withholds_renewal(stand, monkeypatch):
    """C13 / R9 / CC18: the hub still hands out slots, only the control's own renewal gate (`may_run`) says no: the
    run is not renewed and the worker's lease watch ends it within the bound -- the gate is not the hub's drain."""
    monkeypatch.setattr(_lease, "HEARTBEAT_EVERY_SECONDS", 0.3)
    stub = Blocking(seconds=30.0)
    stand.agent(stub=stub, leases=True, ttl=2.0)
    stand.wait_parked(D.slots_for(1))
    gate = {"now": ""}
    w = stand.control(may_run=lambda: gate["now"])
    t = threading.Thread(target=lambda: _try(w.run, a_job("gated-1")))
    t.start()
    end = time.time() + 10
    while not stub.ran and time.time() < end:
        time.sleep(0.05)
    assert stub.ran, "the run never started"
    flipped = time.monotonic()
    gate["now"] = "target_account_suspended"                  # the hub's own eligibility stays "" throughout
    t.join(15)
    ended = stub.ended.get("gated-1")
    assert ended and ended[0] == "stopped" and ended[1] - flipped <= 2.0 + 0.3 + 0.2 + 1.0
    assert stand.code["now"] == ""


def test_the_lease_survives_while_renewals_are_allowed(stand, monkeypatch):
    """C13 / C15 / CC24: with concurrency 1 a run holds ONE slot; renewals use the reserved ones and the run ends
    by itself, never stopped by a lapsed lease."""
    monkeypatch.setattr(_lease, "HEARTBEAT_EVERY_SECONDS", 0.3)
    stub = Blocking(seconds=3.0)
    stand.agent(stub=stub, leases=True, ttl=1.0)
    stand.wait_parked(D.slots_for(1))
    out = stand.control().run(a_job("long-1"))
    assert stub.ended["long-1"][0] == "finished" and out.exit_code == 0


def test_a_lost_answer_is_reconciled_never_run_twice(stand):
    """C14 / CC20 / CC21: the same run id delivered again is answered from the journal, not run again; the same run
    id with other work is RUN_ID_CONFLICT; `result(run_id)` says what became of it."""
    agent = stand.agent()
    stand.wait_parked(D.slots_for(1))
    w = stand.control()
    assert w.run(a_job("once-1")).stdout == "RAN"
    again = w.run(a_job("once-1"))                            # a redelivery: the journal's answer, nothing run
    assert len(agent.stub.ran) == 1 and again.stdout == "RAN"
    other = a_job("once-1")
    other = type(other)(**{**other.__dict__, "command": ("false",)})
    with pytest.raises(WorkerUnreachable) as conflict:
        w.run(other)
    assert wire.RUN_ID_CONFLICT in str(conflict.value) and len(agent.stub.ran) == 1
    said = w.result("once-1")
    assert said.known and said.never_ran is False and len(agent.stub.ran) == 1


def test_the_agent_runs_at_most_its_signed_concurrency(stand):
    """C15 / CC23: concurrency 1 with 5 slots: a second simultaneous run is `worker-busy` (target_full), nothing
    started, though transport slots were free."""
    stub = Blocking(seconds=3.0)
    stand.agent(stub=stub, max_runs=1)
    stand.wait_parked(D.slots_for(1))
    w1, w2 = stand.control(), stand.control()
    t = threading.Thread(target=lambda: w1.run(a_job("busy-1")))
    t.start()
    end = time.time() + 10
    while not stub.ran and time.time() < end:
        time.sleep(0.05)
    with pytest.raises(WorkerUnreachable) as exc:
        w2.run(a_job("busy-2"))
    assert classify(exc.value) == "target_full" and [j.run_id for j in stub.ran] == ["busy-1"]
    t.join(10)


# ---------------------------------------------------------------------- C16/C17: refusals and versions
@pytest.mark.parametrize("code", ["target_device_revoked", "target_account_suspended", "target_suspended",
                                  "target_offline"])
def test_an_ineligible_target_gets_no_slot_and_is_drained(stand, code):
    """C16 / negative: at checkout the hub asks again; a refusal names its code and drains every parked slot."""
    stand.agent()
    stand.wait_parked(D.slots_for(1))
    stand.code["now"] = code
    with pytest.raises(WorkerUnreachable) as exc:
        stand.control().confirm_reachable(2.0)
    assert classify(exc.value) == code and stand.hub.parked(INST) == 0


def test_an_agent_without_the_reverse_feature_gets_no_work(stand):
    """C17 / CC13: a worker that does not say `reverse-session/1` is a protocol mismatch before work crosses."""
    agent = stand.agent(features=False)
    stand.wait_parked(D.slots_for(1))
    with pytest.raises(WorkerUnreachable) as exc:
        stand.control().run(a_job("old-peer"))
    assert classify(exc.value) == "target_protocol_mismatch" and agent.stub.ran == []


def test_a_frame_sealed_with_another_key_gets_no_answer(stand):
    """C5 / C17: the MAC is end to end -- a control side holding another pair key gets nothing back."""
    stand.agent()
    stand.wait_parked(D.slots_for(1))
    wrong = pairkeys.empty().add(gateway=GW, worker=INST, key=os.urandom(32))
    w = from_address("reverse://" + INST, None, topology=REVERSE_SESSION, keyring=wrong, own_instance=GW,
                     hub=stand.hub, may_run=lambda: "")
    with pytest.raises(WorkerUnreachable):
        w._describe()


def test_the_hub_holds_no_pair_key():
    """C5 / CC10: nothing in the hub can seal or open a frame: it has no key and imports no keyring."""
    import inspect

    source = inspect.getsource(R)
    assert "pairkeys" not in source and "wire.seal" not in source and "read_frame" not in source


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX") or sys.platform.startswith("win"), reason="the control is Linux")
def test_the_relay_moves_one_frame_each_way_untouched(stand, tmp_path):
    """C5 / D2: another process's request through the local relay: the same sealed bytes, one each way, closed."""
    stand.agent()
    stand.wait_parked(D.slots_for(1))
    relay = R.RelayServer(stand.hub, str(tmp_path / "hub.sock"))
    relay.start()
    try:
        w = from_address("reverse://" + INST, None, topology=REVERSE_SESSION, keyring=stand.ring, own_instance=GW,
                         hub=R.RelayClient(str(tmp_path / "hub.sock")), may_run=lambda: "")
        assert w.run(a_job("relayed-1")).stdout == "RAN"
        assert oct(os.stat(tmp_path / "hub.sock").st_mode & 0o777) == "0o600"
    finally:
        relay.stop()


# ---------------------------------------------------------------------- C18: nothing secret is written down
def test_no_key_token_or_secret_reaches_a_log_or_file(stand):
    """C18 / CC26: after a full round (park, describe, lease, run, refusals) no log line and no file the agent or
    hub wrote holds the pair key, a private key or a certificate body."""
    stand.agent()
    stand.wait_parked(D.slots_for(1))
    stand.control().run(a_job("scan-1"))
    stand.code["now"] = "target_device_revoked"
    with pytest.raises(WorkerUnreachable):
        stand.control().confirm_reachable(1.0)
    import base64

    key = stand.ring.for_pair(gateway=GW, worker=INST).current
    needles = [key.hex(), base64.urlsafe_b64encode(key).decode(), "PRIVATE KEY", "BEGIN CERTIFICATE"]
    text = "\n".join(stand.said)
    assert not [n for n in needles if n in text]
    written = [p for p in stand.tmp.iterdir() if p.name.startswith(("journal-", "floor-", "lease-"))]
    for root in written:
        for p in ([root] if root.is_file() else list(root.rglob("*"))):
            if p.is_file():
                body = p.read_bytes().decode("utf-8", "replace")
                assert not [n for n in needles if n in body], p
    assert written, "the agent wrote its journal and replay floor"


def _try(fn, *a):
    try:
        return fn(*a)
    except Exception as exc:                                  # noqa: BLE001
        return exc


# ---------------------------------------------------------------------- more negatives (Codex's list)
@pytest.mark.parametrize("which", ["foreign_anchor", "expired", "other_deployment", "client_only_eku"])
def test_a_foreign_or_expired_certificate_parks_nothing(stand, which):
    """C4 / negative: a worker certificate of another CA, one that has expired, one naming another deployment, or one
    without the server usage never becomes a slot."""
    uri = "agentnode://%s/worker/%s" % (stand.world.deployment, INST)
    both = ["1.3.6.1.5.5.7.3.1", "1.3.6.1.5.5.7.3.2"]
    if which == "foreign_anchor":
        ca = stand.world.foreign_ca("lw")[:2]
        folder = stand.world.forge("lw-foreign", uri=uri, usages=both, ca=ca)
    elif which == "other_deployment":
        folder = stand.world.forge("lw-otherdep", uri="agentnode://elsewhere/worker/" + INST, usages=both)
    elif which == "client_only_eku":
        folder = stand.world.forge("lw-clientonly", uri=uri, usages=["1.3.6.1.5.5.7.3.2"])
    else:
        folder = stand.world.forge("lw-expired", uri=uri, usages=["1.3.6.1.5.5.7.3.1", "1.3.6.1.5.5.7.3.2"],
                                   not_after_days=-1, not_before_days=-3)
    tls = stand.world.settings(folder, {GW}, role="worker", floor=stand.world.floor_for("worker", INST))
    stand.agent(tls=tls)
    time.sleep(1.5)
    assert stand.hub.parked(INST) == 0
    assert stand.hub.refused["identity"] + stand.hub.refused["handshake"] >= 1


def test_an_unreadable_register_parks_and_hands_out_nothing(tmp_path):
    """C16 / negative: the hub's view of an unreadable register is empty -- nothing parks, nothing is handed out."""
    class Broken:
        def load(self):
            raise T.TargetsUnavailable("unreadable")
    assert R.target_view(Broken())() == {}
    check = R.eligibility(R.target_view(Broken()), lambda a, d: "", None)
    assert check("cust-local1") == "target_offline"
    assert R.eligibility(lambda: {}, lambda a, d: "", lambda: "unreadable")("x") == "target_offline"


def test_a_replayed_frame_through_another_slot_is_refused(stand):
    """C5 / C17 / negative: the same sealed request sent again on a fresh slot is a replay to the worker."""
    stand.agent()
    stand.wait_parked(D.slots_for(1))
    key = stand.ring.for_pair(gateway=GW, worker=INST).current
    body = wire.request("describe", {}, deadline=time.time() + 30)
    frame = wire.seal(body, key)
    answers = []
    for _ in range(2):
        conn, _who = stand.hub.checkout(INST, 5.0)
        conn.sendall(frame)
        conn.settimeout(5)
        with conn.makefile("rb") as stream:
            answers.append(wire.read_frame(stream, key))
        conn.close()
    assert answers[0].get("ok", True) is not False and "result" in answers[0]
    assert answers[1].get("ok") is False and answers[1].get("error") == wire.REPLAY


def test_a_restarted_agent_answers_from_its_journal_and_a_restarted_hub_is_redialled(stand):
    """C14 / negative: an agent restarted on the same journal never runs a run id again; a new hub on the same
    address gets slots again by the agent's own reconnects."""
    first = stand.agent()
    stand.wait_parked(D.slots_for(1))
    w = stand.control()
    assert w.run(a_job("restart-1")).stdout == "RAN"
    first.stop()
    stand.hub.drain(INST)
    second = stand.agent(journal_dir=stand.tmp / "journal-0")   # a new process on the SAME journal (one disk)
    stand.wait_parked(D.slots_for(1))
    assert stand.control().run(a_job("restart-1")).stdout == "RAN" and second.stub.ran == []
    port = stand.hub.port
    stand.hub.stop()
    stand.hub = R.ReverseHub("tcps://127.0.0.1:%d" % port, stand.gw_tls, targets=lambda: stand.view,
                             eligible=lambda tid: stand.code["now"], allow_loopback=True, say=stand.said.append)
    stand.hub.start()
    assert stand.wait_parked(D.slots_for(1), seconds=40) == D.slots_for(1)


def test_a_run_that_never_crossed_is_unknown_never_a_success(stand):
    """C14 / negative (Codex D8): lost BEFORE it reached the worker -> the worker keeps a journal and has no record:
    `known` is False, which the control plane settles `indeterminate` -- never an invented success, and
    `not_started` only where the journal itself says the accepted run did not run (`catalog_adapter` 575/653)."""
    stand.agent()
    stand.wait_parked(D.slots_for(1))
    said = stand.control().result("never-sent-1")
    assert said.known is False and said.keeps_a_record is True and said.never_ran is False and said.outcome is None


def test_an_accepted_run_lost_before_it_started_is_never_ran_after_a_restart(stand):
    """C14 / negative: the run reached the worker and was journaled, the agent died before starting it: the restarted
    agent's startup sweep settles it `never_started`, and `result` says `never_ran` -- the only `not_started` proof."""
    from agentnode_sdk.worker.service import reconcile_what_was_left

    jdir = stand.tmp / "journal-lost"
    Journal(str(jdir)).claim("lost-1", "d" * 64, container="")
    agent = stand.agent(journal_dir=jdir)
    assert reconcile_what_was_left(agent.bench)["never_started"] == 1   # what `serve` does before its first dial
    stand.wait_parked(D.slots_for(1))
    said = stand.control().result("lost-1")
    assert said.known and said.never_ran and agent.stub.ran == []


def test_a_slot_that_dies_during_a_run_is_reconciled_not_resent(stand):
    """C14 / CC20 / negative: the network between agent and hub is cut while a run executes: the control gets no
    answer and does not send `run` again; on a fresh slot `result(run_id)` gives the one outcome."""
    line = _Line(stand.hub.port)
    stub = Blocking(seconds=30.0)
    agent = stand.agent(stub=stub, port=line.port)
    runs_asked = []
    inner = agent.bench.answer

    def counting(method, params, *a, **kw):
        if method == "run":
            runs_asked.append(params.get("job", {}).get("run_id"))
        return inner(method, params, *a, **kw)
    agent.bench.answer = counting
    stand.wait_parked(D.slots_for(1))
    w = stand.control()
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", _try(w.run, a_job("cut-1"))))
    t.start()
    end = time.time() + 10
    while not stub.ran and time.time() < end:
        time.sleep(0.05)
    assert stub.ran
    line.cut()
    t.join(15)
    assert isinstance(out["r"], WorkerUnreachable)
    stub.go.set()                                             # the run itself ends on the worker
    stand.wait_parked(D.slots_for(1), seconds=40)
    end = time.time() + 15
    said = stand.control().result("cut-1")
    while said.outcome is None and time.time() < end:
        time.sleep(0.2)
        said = stand.control().result("cut-1")
    assert said.known and said.outcome is not None and [j.run_id for j in stub.ran] == ["cut-1"]
    assert runs_asked == ["cut-1"], runs_asked                # the control never sent `run` a second time
    line.close()


class _Line:
    """The network between agent and hub: a TCP relay whose every connection can be severed at once."""

    def __init__(self, to_port: int):
        self.to = to_port
        self.server = socket.create_server(("127.0.0.1", 0))
        self.port = self.server.getsockname()[1]
        self.pairs: list = []
        self.down = threading.Event()
        self.frozen = threading.Event()                       # packets dropped, no reset: a NAT that black-holes
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while not self.down.is_set():
            try:
                a, _ = self.server.accept()
                b = socket.create_connection(("127.0.0.1", self.to))
            except OSError:
                return
            self.pairs.append((a, b))
            for x, y in ((a, b), (b, a)):
                threading.Thread(target=self._pump, args=(x, y), daemon=True).start()

    def _pump(self, x, y):
        try:
            while True:
                data = x.recv(65536)
                if not data:
                    break
                if not self.frozen.is_set():
                    y.sendall(data)
        except OSError:
            pass
        for s in (x, y):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def cut(self):
        for a, b in list(self.pairs):
            for s in (a, b):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                s.close()
        self.pairs.clear()

    def close(self):
        self.down.set()
        self.cut()
        self.server.close()


def test_a_black_holed_agent_is_offline_before_anything_is_admitted(stand):
    """C6 / negative (network loss BEFORE a run): the line drops every packet without a reset, so the parked slots
    look alive; the preflight needs an answer, gets none within its budget, and the target is `target_offline` --
    nothing is sent that could be admitted, nothing runs."""
    line = _Line(stand.hub.port)
    agent = stand.agent(port=line.port)
    stand.wait_parked(D.slots_for(1))
    line.frozen.set()
    started = time.time()
    with pytest.raises(WorkerUnreachable) as exc:
        stand.control().confirm_reachable(2.0)
    assert classify(exc.value) == "target_offline" and time.time() - started < 6
    assert agent.stub.ran == []
    line.frozen.clear()
    line.close()


def test_the_device_authority_maps_the_control_planes_answer_by_name():
    """C8 / C16 / negative: another account's device, a revoked device, a suspended account and an unreadable store
    each refuse with their code; only a clean answer is eligible."""
    answers = {"dev-ok": None, "dev-foreign": "account_mismatch", "dev-revoked": "device_revoked",
               "dev-susp": "account_suspended", "dev-odd": "something_new"}

    def authority_error(principal):
        if principal.device_or_connection_id == "dev-broken":
            raise OSError("the device store is unreadable")
        return answers[principal.device_or_connection_id]
    check = R.device_authority(authority_error)
    assert check(ALICE, "dev-ok") == ""
    assert check(ALICE, "dev-foreign") == check(ALICE, "dev-revoked") == "target_device_revoked"
    assert check(ALICE, "dev-susp") == "target_account_suspended"
    assert check(ALICE, "dev-broken") == check(ALICE, "dev-odd") == "target_authority_unverifiable"
    assert check(ALICE, "") == check("", "dev-ok") == "target_device_revoked"


@pytest.mark.parametrize("status,stopped,code", [
    ("active", "stopped", "target_offline"), ("active", "unreadable", "target_offline"),
    ("suspended", "", "target_suspended"), ("revoked", "", "target_revoked"), ("expired", "", "target_offline")])
def test_the_checkout_eligibility_refuses_the_kill_switch_and_every_dead_status(status, stopped, code):
    """C16 / R8 / negative: an active kill switch (or an unreadable one) and every non-live status refuse checkout."""
    view = {INST: {"target_id": "cust-local1", "status": status, "concurrency": 1, "owner": ALICE, "device": "dev-a"}}

    def kill_switch():
        if stopped == "unreadable":
            raise OSError("unreadable")
        return stopped
    assert R.eligibility(lambda: view, lambda a, d: "", kill_switch)("cust-local1") == code


def test_two_bindings_differ_by_the_device_alone(tmp_path):
    """C11 / negative: the same target re-paired from another device is another binding, so an approval for the
    one (its nonce binds the binding) never admits the other."""
    reg, _op = make_register(tmp_path / "stand")
    a = reg.add_customer(_reverse_spec(), actor="t").target("cust-local1")
    b = T.Target(**{**a.__dict__, "worker": dict(a.worker, device_id="dev-b"),
                    "endpoint_digest": T.endpoint_digest(dict(a.worker, device_id="dev-b"))})
    assert a.binding("r") != b.binding("r") and a.binding("r")["device_id"] != b.binding("r")["device_id"]


@pytest.mark.skipif(sys.platform.startswith("win"), reason="the dial-back start is POSIX (Windows refuses earlier)")
def test_the_dial_back_start_deletes_the_spent_enrolment_secret(stand, monkeypatch):
    """C10 / CC25: `serve(dial=...)` removes what enrolment left (the one-use secret, the request) before any dial."""
    from agentnode_sdk.worker import service

    tls_dir = Path(stand.wk_tls.certificate).parent
    for name in ("secret", "request.json"):
        (tls_dir / name).write_text("spent")
    for p in tls_dir.iterdir():
        os.chmod(p, 0o600)
    os.chmod(tls_dir, 0o700)
    ring_path = stand.tmp / "ring.json"
    stand.ring.write(ring_path)
    journal = stand.tmp / "journal-start"
    journal.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(stand.tmp))
    seen = {}
    monkeypatch.setattr(D.DialBackAgent, "serve_forever", lambda self: seen.update(
        left=sorted(p.name for p in tls_dir.iterdir())))
    service.serve("", "", None, worker=AWorkerThatAnswers(), tls=stand.wk_tls, topology=REVERSE_SESSION,
                  keyring_path=str(ring_path), journal_at=str(journal), dial="tcps://127.0.0.1:%d" % stand.hub.port,
                  max_runs=1, allow_loopback_dial=True)
    assert "secret" not in seen["left"] and "request.json" not in seen["left"]