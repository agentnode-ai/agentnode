"""The reverse MCP adapter: approval_required-with-exact-next-step, then a real worker run (U6)."""
from __future__ import annotations

import json
from pathlib import Path

from _doubles import FakeWorker
from agentnode_sdk.capability import npm_import
from agentnode_sdk.capability.approval import ApprovalStore, grant
from agentnode_sdk.capability.audit import AuditLedger
from agentnode_sdk.capability.index import LocalIndex
from agentnode_sdk.capability.usability import UsabilityClient
from agentnode_sdk.capability.usability_adapter import UsabilityMCPAdapter
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

FIXTURE = Path(__file__).parent / "fixtures" / "multifile_echo_server"
CAP_ID = "examplepub/multifile-echo"


def _raw_pub(priv):
    return priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def _adapter(tmp_path):
    op_priv = Ed25519PrivateKey.generate()
    au_priv = Ed25519PrivateKey.generate()
    index = LocalIndex(str(tmp_path / "index"))
    imp = npm_import.import_vendored_tree(
        str(FIXTURE), publisher="examplepub", name="multifile-echo", version="1.0.0",
        entry="app/server.py", interpreter="python", private_key=op_priv,
        public_key_bytes=_raw_pub(op_priv),
        registry={"manager": "none", "name": "multifile-echo", "version": "1.0.0"})
    index.put_blob(imp.tar_bytes)
    index.register(imp.manifest)
    ledger = AuditLedger(str(tmp_path / "audit.jsonl"), ledger_id="L1",
                         signer_private=au_priv, signer_public_bytes=_raw_pub(au_priv))
    approvals = tmp_path / "approvals"
    approvals.mkdir()
    client = UsabilityClient(index=index, worker=FakeWorker(available=True), audit_ledger=ledger,
                             approval_store=ApprovalStore(consumed_dir=str(tmp_path / "consumed")),
                             operator_public_bytes=_raw_pub(op_priv),
                             worker_identity="host:test", stand=str(tmp_path / "stand"))
    adapter = UsabilityMCPAdapter(client, CAP_ID, approvals_dir=str(approvals))
    return adapter, op_priv, approvals


def test_initialize_and_tools_list(tmp_path):
    adapter, _, _ = _adapter(tmp_path)
    init = adapter.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert init["result"]["protocolVersion"] == "2024-11-05"
    tl = adapter.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert [t["name"] for t in tl["result"]["tools"]] == ["echo_sum"]


def test_unapproved_call_returns_exact_next_step(tmp_path):
    adapter, _, _ = _adapter(tmp_path)
    resp = adapter.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                           "params": {"name": "echo_sum", "arguments": {"a": 2, "b": 3}}})
    sc = resp["result"]["structuredContent"]
    assert sc["status"] == "approval_required"
    assert sc["invocation_hash"] in sc["next_step"]
    assert "usability_cli approve" in sc["next_step"]


def test_after_operator_approval_runs_on_worker(tmp_path):
    adapter, op_priv, approvals = _adapter(tmp_path)
    # the model path gets the hash via approval_required; a human then deposits an approval
    first = adapter.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                            "params": {"name": "echo_sum", "arguments": {"a": 20, "b": 22}}})
    inv_hash = first["result"]["structuredContent"]["invocation_hash"]
    ap = grant(inv_hash, audience=CAP_ID, human_actor="human@x",
               operator_private=op_priv, operator_public_bytes=_raw_pub(op_priv))
    (approvals / f"{ap.approval_id}.json").write_text(json.dumps(ap.to_dict()), encoding="utf-8")
    second = adapter.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                             "params": {"name": "echo_sum", "arguments": {"a": 20, "b": 22}}})
    sc = second["result"]["structuredContent"]
    assert sc["call"]["structuredContent"]["sum"] == 42
    assert sc["audit_seq"] == 1
    # third identical call: the approval is spent -> refused again
    third = adapter.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                            "params": {"name": "echo_sum", "arguments": {"a": 20, "b": 22}}})
    assert third["result"]["structuredContent"]["status"] == "approval_required"
