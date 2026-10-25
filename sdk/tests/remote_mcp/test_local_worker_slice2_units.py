"""Local worker slice 2: the pieces the stand drives but no other unit test holds -- the activation export and its
offline verifier (D13), the start-time check of the installed pins (Q4), the root mirror of what the agent-facing
service verifies for itself (Q-LW2-0003), the update drain (D10), the operator's limits file and the worker CLI on
native Windows (D9). Real register, real operator key, real certificates; each test names what it holds."""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import threading
import time
import types

import pytest

from agentnode_sdk.remote_mcp import activation_export as AE
from agentnode_sdk.remote_mcp import agent_authority as AA
from agentnode_sdk.remote_mcp import target_probe as P
from agentnode_sdk.remote_mcp import targets as T
from agentnode_sdk.worker import pins as PN
from agentnode_sdk.worker import protocol as wire
from agentnode_sdk.worker.service import Bench, serve_dialback
from tests.test_mtls_transport import _one_boot  # noqa: F401 - autouse

from .target_fixtures import raw_pub
from .test_local_worker import Canned, _Canary
from .test_local_worker_pairing import CONTROL, PairStand, _paired


@pytest.fixture()
def stand(tmp_path):
    return PairStand(tmp_path)


# ---------------------------------------------------------------------- D13: the activation export
class _Observed(Canned):
    """The probe's jobs as an isolating worker answers them, run by THIS paired target's own identity."""

    def __init__(self, target, **over):
        super().__init__(**over)
        self.target = target

    def who_ran(self, run_id):
        return ("reverse-mtls", self.target.identity_uri, self.target.worker["instance"])


def _activated(stand, **over):
    target, _ = _paired(stand)
    tid = target["target_id"]
    stand.reg.set_status(tid, T.PROBING, actor="t")
    t = stand.reg.load().target(tid)
    record = P.probe(t, _Observed(t, **over), _Canary())
    assert P.passed(record) == []
    stand.reg.set_attestation(tid, record, actor="t")
    stand.reg.set_status(tid, T.ACTIVE, actor="t")
    return tid


def _head(bundle):
    last = bundle["generations"][-1]
    return "%d:%s" % (last["generation"], T.digest(last))


def _refused(bundle, pub, head, **kw):
    with pytest.raises(AE.ActivationRefused) as got:
        AE.verify(bundle, pub, head, **kw)
    return got.value.code


def test_an_exported_activation_verifies_offline_with_observations_and_claim_apart(stand):
    tid = _activated(stand)
    bundle = AE.export(stand.reg.stand, tid)
    pub = raw_pub(stand.op_key)
    out = AE.verify(json.loads(json.dumps(bundle)), pub, _head(bundle))
    assert out["target_id"] == tid and out["owner_account"] == stand.reg.load().target(tid).owner_account
    assert out["activated_in_generation"] == bundle["generations"][-1]["generation"]
    assert out["claim"]["runtime_version_verified"] is False and out["claim"]["runtime_version_source"] == "worker_claim"
    assert not set(out["observed"]["platform"]) & {"runtime_version", "runtime_version_raw", "runtime_version_source",
                                                    "runtime_version_verified"}
    text = AE.render(out)
    assert text.index("OBSERVED") < text.index("CLAIM") and "NOT verified" in text


def test_a_truncated_reordered_tampered_or_foreign_chain_is_refused(stand):
    tid = _activated(stand)
    bundle = AE.export(stand.reg.stand, tid)
    pub = raw_pub(stand.op_key)
    head = _head(bundle)
    truncated = dict(bundle, generations=bundle["generations"][:-1])
    assert _refused(truncated, pub, head) == "activation_head_untrusted"
    assert _refused(truncated, pub, _head(truncated)) != ""                 # a self-declared head is only the input
    assert _refused(dict(bundle, generations=bundle["generations"][1:]), pub, head) == "activation_chain_invalid"
    tampered = copy.deepcopy(bundle)
    tampered["generations"][1]["targets"]["managed-w1"]["limits"]["queue"] = 99
    assert _refused(tampered, pub, head) == "activation_signature_invalid"
    assert _refused(bundle, raw_pub(PN_other_key()), head) == "activation_signature_invalid"
    assert _refused(bundle, pub, head[:-1] + ("0" if head[-1] != "0" else "1")) == "activation_head_untrusted"
    assert _refused(bundle, pub, "17") == "activation_head_untrusted"
    assert _refused(dict(bundle, extra=1), pub, head) == "activation_export_invalid"


def PN_other_key():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    return Ed25519PrivateKey.generate()


def test_a_target_no_longer_active_or_past_its_observation_is_refused(stand):
    tid = _activated(stand)
    pub = raw_pub(stand.op_key)
    bundle = AE.export(stand.reg.stand, tid)
    expires = bundle["generations"][-1]["targets"][tid]["attestation"]["expires_at"]
    assert bundle["generations"][-1]["targets"][tid]["expires_at"] is None   # a paired target has no end date
    assert _refused(bundle, pub, _head(bundle), now=float(expires) + 1) == "activation_expired"
    stand.reg.set_status(tid, T.SUSPENDED, actor="t")
    later = AE.export(stand.reg.stand, tid)
    assert _refused(later, pub, _head(later)) == "activation_not_active"


def test_a_managed_target_is_not_a_paired_activation(stand):
    _activated(stand)
    bundle = AE.export(stand.reg.stand, "managed-w1")
    assert _refused(bundle, raw_pub(stand.op_key), _head(bundle)) == "activation_binding_invalid"


@pytest.mark.parametrize("confuse", ["verified", "source", "raw", "in_observations"])
def test_a_version_claim_presented_as_an_observation_is_refused(stand, confuse):
    tid = _activated(stand)
    rec = copy.deepcopy(stand.reg.load().target(tid).attestation)
    props = rec["properties"]
    if confuse == "verified":
        props["reported_platform"]["runtime_version_verified"] = True
    elif confuse == "source":
        props["reported_platform"]["runtime_version_source"] = "observed"
    elif confuse == "raw":
        props["reported_platform"]["runtime_version"] = "9.9.9"           # not what its raw text says
    else:
        props["platform"]["runtime_version"] = "5.8.2"
    rec["record_digest"] = T.digest({k: rec[k] for k in sorted(set(rec) - {"record_digest"})})
    stand.reg.set_attestation(tid, rec, actor="t")
    bundle = AE.export(stand.reg.stand, tid)
    assert _refused(bundle, raw_pub(stand.op_key), _head(bundle)) == "activation_claim_confused"


def test_the_activation_cli_exports_verifies_and_refuses_by_name(stand, tmp_path, capsys):
    tid = _activated(stand)
    out = tmp_path / "activation.json"
    assert AE.main(["export", "--stand", str(stand.reg.stand), "--target", tid, "--out", str(out)]) == 0
    head = _head(json.loads(out.read_text()))
    pub = raw_pub(stand.op_key).hex()
    assert AE.main(["verify", str(out), "--operator-pub", pub, "--trusted-head", head]) == 0
    assert "ACTIVATION VERIFIED" in capsys.readouterr().out
    raw = bytearray(out.read_bytes())
    i = raw.index(b'"queue": ') + len(b'"queue": ')
    raw[i] = ord("7") if raw[i] != ord("7") else ord("6")
    out.write_bytes(bytes(raw))
    assert AE.main(["verify", str(out), "--operator-pub", pub, "--trusted-head", head]) == 2
    assert "REFUSED activation_signature_invalid" in capsys.readouterr().out
    assert AE.main(["export", "--stand", str(stand.reg.stand), "--target", "cust-nothere", "--out", str(out)]) == 2


# ---------------------------------------------------------------------- Q4: every start checks the installed pins
def _installed(stand, tmp_path):
    target, state_dir = _paired(stand)
    pins = tmp_path / "pins.json"
    pins.write_text(json.dumps(stand.pins))
    return target, state_dir, pins


def test_a_start_with_the_installed_pins_target_and_anchor_is_accepted(stand, tmp_path):
    target, state_dir, pins = _installed(stand, tmp_path)
    doc = PN.check_installed(pins, state_dir / "target.json", state_dir / "ca.pem", control=CONTROL,
                             deployment=stand.world.deployment, accept=frozenset({"g1"}))
    assert doc["target_id"] == target["target_id"]


@pytest.mark.parametrize("change", ["control", "deployment", "accept", "anchor", "anchor_missing", "pins_shape",
                                    "pins_url", "target_signature", "target_missing"])
def test_a_start_that_disagrees_with_the_pins_is_untrusted(stand, tmp_path, change):
    target, state_dir, pins = _installed(stand, tmp_path)
    kw = dict(control=CONTROL, deployment=stand.world.deployment, accept=frozenset({"g1"}))
    target_path, anchor = state_dir / "target.json", state_dir / "ca.pem"
    if change == "control":
        kw["control"] = "https://mcp.agentnode.net:443/agent/v1/slot?x=1"
    elif change == "deployment":
        kw["deployment"] = "another"
    elif change == "accept":
        kw["accept"] = frozenset({"g1", "g2"})
    elif change == "anchor":
        anchor = tmp_path / "other-ca.pem"
        from tests.test_mtls_transport import World
        anchor.write_bytes(World(tmp_path / "other-pki").anchor.read_bytes())
    elif change == "anchor_missing":
        anchor = tmp_path / "nothing.pem"
    elif change == "pins_shape":
        pins.write_text(json.dumps(dict(stand.pins, extra=1)))
    elif change == "pins_url":
        pins.write_text(json.dumps(dict(stand.pins, control_url="https://MCP.agentnode.net/agent/v1/slot")))
    elif change == "target_signature":
        doc = json.loads(target_path.read_text())
        doc["limits"]["queue"] = 9
        target_path.write_text(json.dumps(doc))
    else:
        target_path = tmp_path / "no-target.json"
    with pytest.raises(PN.Untrusted) as got:
        PN.check_installed(pins, target_path, anchor, **kw)
    assert got.value.code == "pair_material_untrusted"


# ---------------------------------------------------------------------- Q-LW2-0003: the root mirror
def test_the_mirror_copies_only_signed_and_public_material(stand, tmp_path):
    import dataclasses
    _, state_dir = _paired(stand)
    (stand.reg.stand / "operator_pub.hex").write_text(raw_pub(stand.op_key).hex())
    unset = stand.world.settings(stand.world.service("gateway", "g1"), {"x"}, role="gateway")
    out = tmp_path / "mirror"
    with pytest.raises(ValueError):
        AA.mirror(stand.reg.stand, unset, out)                          # an unset trust file is never "."
    tls = dataclasses.replace(unset, identity_tombstones=str(state_dir / "revoked-identities.json"))
    written = AA.mirror(stand.reg.stand, tls, out)
    assert {w for w in written if not w.startswith("targets/history/")} >= {
        "targets/targets.json", "targets/head.json", "operator_pub.hex", "trust/ca.pem", "trust/revoked.crl",
        "trust/revoked-identities.json", "trust/gateway.floor"}
    names = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()}
    assert names == set(written)
    for p in out.rglob("*"):
        if p.is_file():
            assert b"PRIVATE KEY" not in p.read_bytes()
            if os.name == "posix":
                assert p.stat().st_mode & 0o777 == 0o640
    mirrored = T.Register(out, raw_pub(stand.op_key)).load()
    assert mirrored.generation == stand.reg.load().generation          # verified with the public key alone
    again = AA.mirror(stand.reg.stand, tls, out)
    assert not [w for w in again if w.startswith("targets/history/")]   # history is append-only


# ---------------------------------------------------------------------- D10: the update drain
class _Agent:
    conversations = 3
    last_admitted_at = 12.5
    last_refusal = ""

    def __init__(self):
        self.started = self.stopped = 0

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1


def _bench(max_runs=1):
    b = Bench.__new__(Bench)
    b._in_flight_lock = threading.Lock()
    b.max_runs = max_runs
    b._running = 0
    return b


def test_a_draining_worker_starts_no_new_run_and_waits_for_the_one_it_has(tmp_path):
    agent, bench = _Agent(), _bench(max_runs=2)
    status = str(tmp_path / "status.json")
    stopping = threading.Event()
    slot = Bench._a_run_slot(bench)
    slot.__enter__()                                                    # a run in progress
    t = threading.Thread(target=serve_dialback, args=(agent, bench),
                         kwargs={"status_path": status, "stopping": stopping, "every": 0.05})
    t.start()
    try:
        deadline = time.time() + 5
        while not os.path.exists(status) and time.time() < deadline:
            time.sleep(0.02)
        doc = json.loads(open(status).read())
        assert doc["running"] == 1 and doc["draining"] is False and doc["conversations"] == 3
        assert set(doc) == {"pid", "started_at", "conversations", "last_admitted_at", "last_refusal", "draining",
                            "running", "written_at"}
        stopping.set()
        deadline = time.time() + 5
        while not getattr(bench, "draining", False) and time.time() < deadline:
            time.sleep(0.02)
        with pytest.raises(wire.ProtocolError) as got:
            with Bench._a_run_slot(bench):
                pass
        assert got.value.code == wire.WORKER_BUSY and "draining" in str(got.value)
        time.sleep(0.2)
        assert t.is_alive() and agent.stopped == 0                      # the run here keeps its line
    finally:
        slot.__exit__(None, None, None)
    t.join(5)
    assert not t.is_alive() and agent.started == 1 and agent.stopped == 1
    final = json.loads(open(status).read())
    assert final["draining"] is True and final["running"] == 0


# ---------------------------------------------------------------------- the operator's limits file
def test_the_limits_file_overrides_named_limits_of_their_own_type(tmp_path):
    from agentnode_sdk.remote_mcp.production import read_limits_file
    f = tmp_path / "limits.json"
    f.write_text(json.dumps({"calls_per_minute_per_principal": 60, "max_queue_wait_seconds": 2.5}))
    limits, names = read_limits_file(f)
    assert (limits.calls_per_minute_per_principal, limits.max_queue_wait_seconds) == (60, 2.5)
    assert names == ["calls_per_minute_per_principal", "max_queue_wait_seconds"]
    assert limits.max_body_bytes == 256 * 1024                           # everything else: the decided default


@pytest.mark.parametrize("content", [{"no_such_limit": 1}, {"call_burst": 1.5}, {"call_burst": 0},
                                     {"call_burst": True}, {"call_burst": "3"}, {"max_queue_wait_seconds": -1},
                                     [1], "not json"])
def test_a_bad_limits_file_refuses_the_start(tmp_path, content):
    from agentnode_sdk.remote_mcp.production import read_limits_file
    f = tmp_path / "limits.json"
    f.write_text(content if isinstance(content, str) else json.dumps(content))
    with pytest.raises(SystemExit):
        read_limits_file(f)


# ---------------------------------------------------------------------- D9: no worker on native Windows
def test_pairing_on_native_windows_is_refused_before_any_key_or_request(tmp_path, monkeypatch, capsys):
    from agentnode_sdk.cli import worker_commands as W
    from agentnode_sdk.worker import pairing as WP
    called = []
    monkeypatch.setattr(WP, "pair", lambda *a, **k: called.append(a))
    monkeypatch.setattr(sys, "platform", "win32")
    state = tmp_path / "state"
    args = argparse.Namespace(pins=str(tmp_path / "pins.json"), state_dir=str(state), name="w", concurrency=1, queue=2)
    assert W.cmd_pair(args) == 1
    assert "runtime-absent" in capsys.readouterr().out
    assert called == [] and not state.exists()
    assert W.cmd_pair(types.SimpleNamespace(pins="", state_dir="")) == 2
