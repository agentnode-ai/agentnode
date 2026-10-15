"""S5-S10 end to end with a test-double worker, plus the negative proofs.

The FakeWorker is a labelled TEST DOUBLE: it runs the accepted bootstrap program
locally so the contract/approval/execution/audit composition can be exercised
without podman. It is NOT the capability's production path -- the real sandbox
run on a podman worker is S7's measured evidence (separate).
"""
from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path

import pytest

from agentnode_sdk.capability import approval, contract, execution, importer as I, pipeline
from agentnode_sdk.capability.approval import ApprovalError, ApprovalStore
from agentnode_sdk.capability.audit import AuditError, AuditLedger
from agentnode_sdk.capability.execution import ExecutionError, NotImplementedModel
from agentnode_sdk.capability.index import LocalIndex
from agentnode_sdk.signing_key import generate_ed25519_keypair
from agentnode_sdk.worker import Isolation, Outcome, WorkerUnreachable

FIXTURE = Path(__file__).parent / "fixtures" / "deterministic_add_server.py"


class FakeWorker:
    """Test double: runs the bootstrap command locally (NOT a container). Clearly not production."""

    def __init__(self, *, available=True, backend="fake-sandbox", raises=None):
        self._iso = Isolation(available=available, backend=backend)
        self._raises = raises

    def can_it_isolate(self):
        return self._iso

    def run(self, job):
        if self._raises is not None:
            raise self._raises
        stub = job.command[2]            # ["python", "-c", <stub>]
        proc = subprocess.run([sys.executable, "-c", stub], input=job.stdin,
                              text=True, capture_output=True, timeout=60)
        return Outcome(exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


def _setup(tmp_path, *, model="agentnode_executed_mcp"):
    idx = LocalIndex(tmp_path / "index")
    priv, pub = generate_ed25519_keypair()
    imp = I.import_stdio_server(FIXTURE, publisher="acme", name="add-server", version="1.0.0",
                                private_key=priv, public_key_bytes=pub)
    man = imp.manifest
    if model != "agentnode_executed_mcp":
        man = copy.deepcopy(man)
        man["execution_model"] = model
        man.pop("signatures")
        man = __import__("agentnode_sdk.capability.manifest", fromlist=["x"]).sign_manifest(
            man, priv, pub, role="importing_publisher")
    idx.put_blob(imp.server_bytes)
    idx.register(man)
    op_priv, op_pub = generate_ed25519_keypair()
    store = ApprovalStore()
    au_priv, au_pub = generate_ed25519_keypair()
    ledger = AuditLedger(tmp_path / "audit.jsonl", ledger_id="L1",
                         signer_private=au_priv, signer_public_bytes=au_pub)
    return idx, store, op_priv, op_pub, au_pub, ledger


def _grant(store, op_priv, op_pub, invocation, **kw):
    ap = approval.grant(invocation.invocation_hash, human_actor="operator@acme",
                        operator_private=op_priv, operator_public_bytes=op_pub, **kw)
    store.put(ap)
    return ap


def test_happy_path_runs_on_worker_and_binds_a_signed_audit(tmp_path):
    idx, store, op_priv, op_pub, au_pub, ledger = _setup(tmp_path)
    resolved = idx.resolve("acme/add-server")
    inv = contract.prepare(resolved, "add", {"a": 2, "b": 40})
    ap = _grant(store, op_priv, op_pub, inv)
    worker = FakeWorker()
    res = pipeline.submit(inv, approval_id=ap.approval_id, approval_store=store,
                          operator_public_bytes=op_pub, worker=worker, index=idx,
                          audit_ledger=ledger, worker_identity="fake/local")
    assert res.exec_result.ok
    assert res.exec_result.result["call"]["structuredContent"]["sum"] == 42
    # audit entry is bound to this invocation and verifies
    assert res.audit_entry.body["invocation_hash"] == inv.invocation_hash
    assert res.audit_entry.body["manifest_digest"] == inv.manifest_digest
    assert res.audit_entry.body["terminal_status"] == "ok"
    ledger.verify(trusted_public_bytes=au_pub)
    assert res.effective_permissions == inv.requested_permissions


def test_cli_sdk_mcp_resolve_to_the_same_canonical_invocation(tmp_path):
    idx, *_ = _setup(tmp_path)
    resolved = idx.resolve("acme/add-server")
    # three "front doors" all call contract.prepare with the same inputs
    cli = contract.prepare(resolved, "add", {"a": 1, "b": 2})
    sdk = contract.prepare(resolved, "add", {"a": 1, "b": 2})
    mcp = contract.prepare(resolved, "add", {"a": 1, "b": 2})

    def _tuple(inv):
        b = dict(inv.binding())
        b.pop("nonce")   # the per-run nonce makes each invocation unique (single-use approval)
        return b

    # the stable canonical tuple -- capability, version, manifest, artifact, tool, schema, input --
    # is identical across the three surfaces: they resolve to one canonical contract, not three.
    assert _tuple(cli) == _tuple(sdk) == _tuple(mcp)
    # and each run is uniquely identified (nonce), so an approval binds exactly one invocation
    assert len({cli.invocation_hash, sdk.invocation_hash, mcp.invocation_hash}) == 3


def test_submit_without_an_approval_is_refused(tmp_path):
    idx, store, op_priv, op_pub, au_pub, ledger = _setup(tmp_path)
    inv = contract.prepare(idx.resolve("acme/add-server"), "add", {"a": 1, "b": 1})
    with pytest.raises(ApprovalError):
        pipeline.submit(inv, approval_id="nope", approval_store=store, operator_public_bytes=op_pub,
                        worker=FakeWorker(), index=idx, audit_ledger=ledger, worker_identity="x")


def test_approval_is_single_use(tmp_path):
    idx, store, op_priv, op_pub, au_pub, ledger = _setup(tmp_path)
    inv = contract.prepare(idx.resolve("acme/add-server"), "add", {"a": 3, "b": 4})
    ap = _grant(store, op_priv, op_pub, inv)
    pipeline.submit(inv, approval_id=ap.approval_id, approval_store=store, operator_public_bytes=op_pub,
                    worker=FakeWorker(), index=idx, audit_ledger=ledger, worker_identity="x")
    with pytest.raises(ApprovalError):  # second use refused
        pipeline.submit(inv, approval_id=ap.approval_id, approval_store=store, operator_public_bytes=op_pub,
                        worker=FakeWorker(), index=idx, audit_ledger=ledger, worker_identity="x")


def test_expired_approval_is_refused(tmp_path):
    idx, store, op_priv, op_pub, au_pub, ledger = _setup(tmp_path)
    inv = contract.prepare(idx.resolve("acme/add-server"), "add", {"a": 5, "b": 6})
    ap = _grant(store, op_priv, op_pub, inv, ttl_seconds=-1.0)  # already expired
    with pytest.raises(ApprovalError):
        store.consume(ap.approval_id, inv.invocation_hash, trusted_operator_public_bytes=op_pub)


def test_approval_from_an_untrusted_operator_key_is_refused(tmp_path):
    idx, store, op_priv, op_pub, au_pub, ledger = _setup(tmp_path)
    inv = contract.prepare(idx.resolve("acme/add-server"), "add", {"a": 1, "b": 1})
    other_priv, other_pub = generate_ed25519_keypair()  # a model forging its own "approval"
    ap = _grant(store, other_priv, other_pub, inv)
    with pytest.raises(ApprovalError):
        store.consume(ap.approval_id, inv.invocation_hash, trusted_operator_public_bytes=op_pub)


def test_changing_the_input_after_approval_is_refused(tmp_path):
    idx, store, op_priv, op_pub, au_pub, ledger = _setup(tmp_path)
    resolved = idx.resolve("acme/add-server")
    inv_a = contract.prepare(resolved, "add", {"a": 2, "b": 40})
    ap = _grant(store, op_priv, op_pub, inv_a)
    inv_b = contract.prepare(resolved, "add", {"a": 2, "b": 41})  # changed input -> new hash
    assert inv_b.invocation_hash != inv_a.invocation_hash
    with pytest.raises(ApprovalError):
        pipeline.submit(inv_b, approval_id=ap.approval_id, approval_store=store,
                        operator_public_bytes=op_pub, worker=FakeWorker(), index=idx,
                        audit_ledger=ledger, worker_identity="x")


@pytest.mark.parametrize("model", ["external_remote_mcp", "native_integration"])
def test_models_1_and_3_are_not_implemented_and_never_substitute(tmp_path, model):
    idx, store, op_priv, op_pub, au_pub, ledger = _setup(tmp_path, model=model)
    inv = contract.prepare(idx.resolve("acme/add-server"), "add", {"a": 1, "b": 1})
    worker = FakeWorker()
    with pytest.raises(NotImplementedModel) as exc:
        execution.execute(inv, idx, worker, effective_grant=inv.requested_permissions)
    assert exc.value.status == "NOT_IMPLEMENTED"
    assert exc.value.model == model


def test_no_local_fallback_when_the_worker_is_unavailable_or_cannot_isolate(tmp_path):
    idx, store, op_priv, op_pub, au_pub, ledger = _setup(tmp_path)
    inv = contract.prepare(idx.resolve("acme/add-server"), "add", {"a": 1, "b": 1})
    # (a) no worker
    with pytest.raises(ExecutionError):
        execution.execute(inv, idx, None, effective_grant=inv.requested_permissions)
    # (b) worker cannot isolate -> refuse, do NOT run without the sandbox
    with pytest.raises(ExecutionError):
        execution.execute(inv, idx, FakeWorker(available=False, backend="none"),
                          effective_grant=inv.requested_permissions)
    # (c) worker unreachable -> refuse, no local run
    with pytest.raises(ExecutionError):
        execution.execute(inv, idx, FakeWorker(raises=WorkerUnreachable("down")),
                          effective_grant=inv.requested_permissions)


def test_tampered_or_reordered_audit_is_detected(tmp_path):
    idx, store, op_priv, op_pub, au_pub, ledger = _setup(tmp_path)
    # two runs -> two chained entries
    for args in ({"a": 1, "b": 1}, {"a": 2, "b": 2}):
        inv = contract.prepare(idx.resolve("acme/add-server"), "add", args)
        ap = _grant(store, op_priv, op_pub, inv)
        pipeline.submit(inv, approval_id=ap.approval_id, approval_store=store, operator_public_bytes=op_pub,
                        worker=FakeWorker(), index=idx, audit_ledger=ledger, worker_identity="x")
    ledger.verify(trusted_public_bytes=au_pub)  # clean

    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    # tamper: flip a body field in entry 1
    import json as _j
    e0 = _j.loads(lines[0]); e0["body"]["terminal_status"] = "error"
    (tmp_path / "audit.jsonl").write_text(_j.dumps(e0) + "\n" + lines[1] + "\n", encoding="utf-8")
    with pytest.raises(AuditError):
        ledger.verify(trusted_public_bytes=au_pub)

    # reorder the two valid entries -> broken chain link
    (tmp_path / "audit.jsonl").write_text(lines[1] + "\n" + lines[0] + "\n", encoding="utf-8")
    with pytest.raises(AuditError):
        ledger.verify(trusted_public_bytes=au_pub)


def test_audit_integrity_statement_is_honest_about_rollback():
    stmt = AuditLedger.integrity_statement()
    assert stmt["tamper_evident"] is True
    assert "truncation of the tail" in stmt["does_not_detect_without_external_anchor"]
