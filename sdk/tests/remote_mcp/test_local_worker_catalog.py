"""Local worker, slice 1, through the WHOLE call path: the real catalog adapter, router, approvals, ledger and
transport over a real reverse hub and a real dial-back agent (Codex Q-LW-0001 C9/C11/C14/C15, D7).

prepare -> operator approval -> submit reaches a dial-back target only when it is wished for by its owner, the approval
is bound to it, and every admitted dispatch gets exactly one `catalog_terminal`; a refused local target never falls
back to the managed one."""
from __future__ import annotations

import subprocess
import sys

import pytest

from agentnode_sdk.remote_mcp import targets as T
from agentnode_sdk.worker import REVERSE_SESSION, Outcome
from agentnode_sdk.worker.remote import from_address
from tests.test_mtls_transport import _one_boot  # noqa: F401 - autouse: one boot throughout
from tests.test_socket_worker import AWorkerThatAnswers

from .catalog_fixtures import hash_of
from .target_fixtures import attestation, worker as worker_spec
from .target_stand import CAP_REGEX, REGEX, REGEX_ARGS, TargetStand
from .test_local_worker import GW, INST, Stand as WireStand
from .test_target_routing import _bodies, _call, _sc

LOCAL = "cust-local1"


class Runs(AWorkerThatAnswers):
    """The runtime behind the agent: runs the job's program (a TEST DOUBLE, as `TargetWorker` does)."""

    def run(self, job):
        self.ran.append(job)
        proc = subprocess.run([sys.executable, "-c", job.command[2]], input=job.stdin, text=True,
                              capture_output=True, timeout=60)
        return Outcome(exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr, reason="exited",
                       runtime_platform="a-stand-in")


@pytest.fixture
def both(tmp_path):
    st = TargetStand(tmp_path / "cat")
    st.publish_starters()
    alice, ta = st.account("alice")
    bob, tb = st.account("bob")
    wire_stand = WireStand(tmp_path / "wire")
    # the dial-back target in the catalog's register, owned by alice and her device, attested, acknowledged
    w = worker_spec(INST)
    dep = wire_stand.world.deployment                         # the deployment the agent's certificate names
    w.update(address="reverse://" + INST, topology=T.REVERSE_SESSION, device_id="dev-alice", deployment=dep,
             identity_uri="agentnode://%s/worker/%s" % (dep, INST), pair={"gateway": GW, "worker": INST})
    st.register.add_customer({"target_id": LOCAL, "type": T.CUSTOMER, "owner_account": alice, "status": T.INVITED,
                              "expires_at": None, "worker": w, "limits": {"concurrency": 1, "queue": 2},
                              "attestation": None}, actor="t")
    for s in (T.ENROLLED, T.PROBING):
        st.register.set_status(LOCAL, s, actor="t")
    t = st.register.load().target(LOCAL)
    a = attestation(T.CUSTOMER, t.worker)
    a["properties"].update(cpu_quota_enforced=True, isolation_observed=True,
                           platform={"kernel": "6.12", "machine": "x86_64", "runtime_engine": "podman",
                                     "runtime_engine_source": "/run/.containerenv:marker"},
                           reported_platform={"runtime_version_raw": "podman 5.8.2", "runtime_version": "5.8.2",
                                              "runtime_version_source": "worker_claim",
                                              "runtime_version_verified": False})
    body = {k: a[k] for k in sorted(a) if k != "record_digest"}
    a["record_digest"] = T.digest(body)
    st.register.set_attestation(LOCAL, a, actor="t")
    st.register.set_status(LOCAL, T.ACTIVE, actor="t")
    st.ack(LOCAL)
    devices = {"dev-alice": ""}
    st.router.reverse_authority = lambda account, device: devices.get(device, "target_device_revoked")
    wire_stand.view[INST].update(target_id=LOCAL, owner=alice, device="dev-alice")
    real = st._worker_for

    def worker_for(target):
        if target.is_reverse:
            return from_address(target.worker["address"], None, topology=REVERSE_SESSION, keyring=wire_stand.ring,
                                own_instance=GW, hub=wire_stand.hub, may_run=lambda: wire_stand.code["now"])
        return real(target)
    st.router._worker_for = worker_for
    yield {"st": st, "wire": wire_stand, "alice": ta, "bob": tb, "devices": devices}
    wire_stand.close()


def _terminals(st, dispatch_ids=None):
    return [b for b in _bodies(st) if b.get("kind") == "catalog_terminal"
            and (dispatch_ids is None or b.get("dispatch_id") in dispatch_ids)]


def test_a_wished_local_target_runs_once_with_its_own_approval_and_one_terminal(both):
    """C11 / C14 / CC22: approval_required first; the approval for THIS target admits exactly one run on the agent and
    exactly one terminal is written; the managed worker started nothing."""
    st, ws = both["st"], both["wire"]
    agent = ws.agent(stub=Runs())
    ws.wait_parked(5)
    first = _call(st, both["alice"], REGEX, REGEX_ARGS, wish=LOCAL)
    assert _sc(first)["status"] == "approval_required"
    st.approve(hash_of(first), CAP_REGEX)
    ran = _call(st, both["alice"], REGEX, REGEX_ARGS, wish=LOCAL)
    assert _sc(ran)["target"]["target_id"] == LOCAL, _sc(ran)
    assert len(agent.stub.ran) == 1 and st.w("managed-w1").starts == 0
    admitted = [b["dispatch_id"] for b in _bodies(st) if b.get("decision") == "admitted"]
    assert len(admitted) == 1 and len(_terminals(st, set(admitted))) == 1


def test_an_approval_for_the_managed_target_does_not_admit_the_local_one(both):
    """C11 / negative: the binding names the target (and its device): the managed call's approval is no approval for
    the same tool and arguments on the dial-back target."""
    st, ws = both["st"], both["wire"]
    agent = ws.agent(stub=Runs())
    ws.wait_parked(5)
    managed = _call(st, both["alice"], REGEX, REGEX_ARGS)
    st.approve(hash_of(managed), CAP_REGEX)
    local = _call(st, both["alice"], REGEX, REGEX_ARGS, wish=LOCAL)
    assert _sc(local)["status"] == "approval_required" and hash_of(local) != hash_of(managed)
    assert agent.stub.ran == []


def test_a_revoked_device_refuses_after_approval_and_never_falls_back(both):
    """C9 / C16 / CC9: approved, then the device is withdrawn: refused by name, nothing runs anywhere."""
    st, ws = both["st"], both["wire"]
    agent = ws.agent(stub=Runs())
    ws.wait_parked(5)
    first = _call(st, both["alice"], REGEX, REGEX_ARGS, wish=LOCAL)
    st.approve(hash_of(first), CAP_REGEX)
    both["devices"]["dev-alice"] = "target_device_revoked"
    out = _call(st, both["alice"], REGEX, REGEX_ARGS, wish=LOCAL)
    assert _sc(out).get("code") == "target_device_revoked", _sc(out)
    assert agent.stub.ran == [] and st.w("managed-w1").starts == 0


def test_an_offline_local_target_is_refused_and_never_falls_back(both):
    """C9 / CC9: no agent connected: `target_offline`, and the managed target is not tried."""
    st = both["st"]
    first = _call(st, both["alice"], REGEX, REGEX_ARGS, wish=LOCAL)
    if _sc(first).get("status") == "approval_required":
        st.approve(hash_of(first), CAP_REGEX)
        first = _call(st, both["alice"], REGEX, REGEX_ARGS, wish=LOCAL)
    assert _sc(first).get("code") == "target_offline", _sc(first)
    assert st.w("managed-w1").starts == 0


def test_another_account_cannot_see_or_use_the_local_target(both):
    """C9 / CC8: bob wishing for alice's dial-back target: `target_unknown`, as for an id that does not exist."""
    st, ws = both["st"], both["wire"]
    agent = ws.agent(stub=Runs())
    ws.wait_parked(5)
    for wish in (LOCAL, "cust-nosuch1"):
        out = _call(st, both["bob"], REGEX, REGEX_ARGS, wish=wish)
        assert _sc(out) == {"status": "refused", "code": "target_unknown"}
    assert agent.stub.ran == []


def test_the_managed_target_stays_the_default_for_its_owner(both):
    """C9: without a wish, alice's call goes to the managed default even with a live local target."""
    st, ws = both["st"], both["wire"]
    agent = ws.agent(stub=Runs())
    ws.wait_parked(5)
    first = _call(st, both["alice"], REGEX, REGEX_ARGS)
    st.approve(hash_of(first), CAP_REGEX)
    ran = _call(st, both["alice"], REGEX, REGEX_ARGS)
    assert _sc(ran)["target"]["target_id"] == "managed-w1" and agent.stub.ran == []


def test_pairing_gives_the_agent_no_way_to_approve():
    """C10 / negative: the worker protocol has no method that issues or consumes an approval."""
    from agentnode_sdk.worker import protocol as wire

    assert not [m for m in wire.METHODS if "approv" in m or "grant" in m or "token" in m]
