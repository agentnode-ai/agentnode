"""End-to-end Slice-2 (mechanism b): import a REAL multi-file server, approve, run on the worker.

Uses a multi-file PYTHON stdio server fixture + the FakeWorker double so the whole flow runs
with no podman/node. The real npm/node server is proven separately on the kept worker host.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from _doubles import FakeWorker
from agentnode_sdk.capability import npm_import, tree_execution, tree_pack
from agentnode_sdk.capability.approval import ApprovalError, ApprovalStore, grant
from agentnode_sdk.capability.audit import AuditLedger
from agentnode_sdk.capability.execution import ExecutionError
from agentnode_sdk.capability.index import IndexError_, LocalIndex
from agentnode_sdk.capability.usability import UsabilityClient
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

FIXTURE = Path(__file__).parent / "fixtures" / "multifile_echo_server"
CAP_ID = "examplepub/multifile-echo"


def _raw_pub(priv):
    return priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def _import_into(index: LocalIndex, op_priv):
    imp = npm_import.import_vendored_tree(
        str(FIXTURE), publisher="examplepub", name="multifile-echo", version="1.0.0",
        entry="app/server.py", interpreter="python", private_key=op_priv,
        public_key_bytes=_raw_pub(op_priv),
        registry={"manager": "none", "name": "multifile-echo", "version": "1.0.0",
                  "license": "MIT", "upstream_publisher": "Example"})
    stored = index.put_blob(imp.tar_bytes)
    assert stored == imp.tar_sha256
    index.register(imp.manifest)
    return imp


def _client(tmp_path, *, worker):
    op_priv = Ed25519PrivateKey.generate()
    au_priv = Ed25519PrivateKey.generate()
    index = LocalIndex(str(tmp_path / "index"))
    _import_into(index, op_priv)
    ledger = AuditLedger(str(tmp_path / "audit.jsonl"), ledger_id="L1",
                         signer_private=au_priv, signer_public_bytes=_raw_pub(au_priv))
    client = UsabilityClient(index=index, worker=worker, audit_ledger=ledger,
                             approval_store=ApprovalStore(consumed_dir=str(tmp_path / "consumed")),
                             operator_public_bytes=_raw_pub(op_priv),
                             worker_identity="host:test", stand=str(tmp_path / "stand"))
    return client, op_priv


def _grant_and_put(client, op_priv, inv, actor="human@x"):
    ap = grant(inv.invocation_hash, audience=inv.capability_id, human_actor=actor,
               operator_private=op_priv, operator_public_bytes=_raw_pub(op_priv))
    client.approval_store.put(ap)
    return ap


def test_import_pins_the_tree_byte_unchanged(tmp_path):
    index = LocalIndex(str(tmp_path / "index"))
    op_priv = Ed25519PrivateKey.generate()
    imp = _import_into(index, op_priv)
    # the pinned tree-digest equals the digest of the untouched source fixture
    assert imp.tree_digest == tree_pack.tree_digest_of_dir(str(FIXTURE))
    # the delivered bytes round-trip back to the exact upstream files
    out = str(tmp_path / "unpacked")
    tree_pack.safe_extract(imp.tar_bytes, out)
    assert open(os.path.join(out, "app", "server.py"), "rb").read() == \
        open(FIXTURE / "app" / "server.py", "rb").read()
    man = imp.manifest
    assert man["source"]["package"]["kind"] == "vendored-tree"
    assert man["signatures"][0]["role"] == "importing_publisher"


def test_describe_full_shows_provenance_and_achievable_now(tmp_path):
    client, _ = _client(tmp_path, worker=FakeWorker(available=True))
    d = client.describe_full(CAP_ID)
    assert d["version"] == "1.0.0"
    assert d["signature"][0]["role"] == "importing_publisher"
    assert d["source_pin"]["kind"] == "vendored-tree"
    assert d["achievable_guarantees_now"]["computed"] is True
    assert d["achievable_guarantees_now"]["sandboxed_execution_available_now"] is True


def test_full_flow_runs_on_worker_and_binds_result(tmp_path):
    client, op_priv = _client(tmp_path, worker=FakeWorker(available=True))
    inv = client.prepare(CAP_ID, "echo_sum", {"a": 40, "b": 2})
    req = client.approval_required(inv)
    assert req.status == "approval_required"
    assert inv.invocation_hash in req.next_step and "approve" in req.next_step
    ap = _grant_and_put(client, op_priv, inv)
    res = client.submit(inv, ap.approval_id)
    bound = client.bound_result(inv, res)
    assert bound["result"]["ok"] is True
    assert bound["result"]["call"]["structuredContent"]["sum"] == 42
    assert bound["manifest"]["capability_id"] == CAP_ID
    assert bound["job"]["run_id"].startswith("cap-")
    assert bound["audit"]["body"]["invocation_hash"] == inv.invocation_hash
    client.audit_ledger.verify()  # chain intact


def test_consumed_approval_is_single_use(tmp_path):
    client, op_priv = _client(tmp_path, worker=FakeWorker(available=True))
    inv = client.prepare(CAP_ID, "echo_sum", {"a": 1, "b": 2})
    ap = _grant_and_put(client, op_priv, inv)
    client.submit(inv, ap.approval_id)
    with pytest.raises(ApprovalError):
        client.submit(inv, ap.approval_id)


def test_no_worker_refuses_no_local_fallback(tmp_path):
    client, op_priv = _client(tmp_path, worker=FakeWorker(available=False))
    inv = client.prepare(CAP_ID, "echo_sum", {"a": 1, "b": 1})
    ap = _grant_and_put(client, op_priv, inv)
    with pytest.raises(ExecutionError):
        client.submit(inv, ap.approval_id)


def test_tuple_changed_after_approval_is_refused(tmp_path):
    client, op_priv = _client(tmp_path, worker=FakeWorker(available=True))
    inv = client.prepare(CAP_ID, "echo_sum", {"a": 1, "b": 1})
    ap = _grant_and_put(client, op_priv, inv)
    inv.arguments["a"] = 999  # mutate the approved tuple in place
    with pytest.raises(ValueError):
        client.submit(inv, ap.approval_id)


def test_declared_egress_is_refused_not_weakened(tmp_path):
    client, op_priv = _client(tmp_path, worker=FakeWorker(available=True))
    inv = client.prepare(CAP_ID, "echo_sum", {"a": 1, "b": 1})
    with pytest.raises(ExecutionError):
        tree_execution.execute_tree(inv, client.index, client.worker,
                                    effective_grant={"network": {"allowed_domains": ["evil.example"]}})


def test_client_exposes_no_self_approval(tmp_path):
    client, _ = _client(tmp_path, worker=FakeWorker(available=True))
    assert not hasattr(client, "grant")
    inv = client.prepare(CAP_ID, "echo_sum", {"a": 1, "b": 1})
    with pytest.raises(ApprovalError):
        client.submit(inv, "no-such-approval-id")


def test_delivered_artifact_mismatch_is_refused_in_sandbox(tmp_path):
    # Build a driver whose embedded tree does not match the pinned tree-digest: the in-sandbox
    # re-verification must refuse rather than run (defence in depth beyond content-addressing).
    client, op_priv = _client(tmp_path, worker=FakeWorker(available=True))
    pkg = client.index.resolve(CAP_ID).manifest["source"]["package"]
    tar = client.index.get_blob(pkg["sha256"])
    program = tree_execution.build_tree_run_program(
        tar, interpreter="python", entry="app/server.py",
        expected_tree_digest="sha256:" + "0" * 64, tool="echo_sum",
        arguments={"a": 1, "b": 1})
    # run the driver exactly as the worker would (it reads nothing from stdin)
    import subprocess
    import sys
    done = subprocess.run([sys.executable, "-c", program.decode()], capture_output=True,
                          text=True, timeout=60)
    assert "tree-digest mismatch" in done.stdout


def test_corrupt_blob_is_detected(tmp_path):
    client, _ = _client(tmp_path, worker=FakeWorker(available=True))
    pkg = client.index.resolve(CAP_ID).manifest["source"]["package"]
    blob = Path(str(tmp_path / "index")) / "blobs" / pkg["sha256"]
    blob.write_bytes(b"corrupted")
    with pytest.raises(IndexError_):
        client.index.get_blob(pkg["sha256"])
