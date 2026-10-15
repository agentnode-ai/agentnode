"""S5/S6/S8/C12 wiring: the reverse MCP adapter over the one CapabilityClient contract."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agentnode_sdk.capability import approval, importer as I
from agentnode_sdk.capability.adapter import CapabilityMCPAdapter
from agentnode_sdk.capability.approval import ApprovalStore
from agentnode_sdk.capability.audit import AuditLedger
from agentnode_sdk.capability.client import CapabilityClient
from agentnode_sdk.capability.index import LocalIndex
from agentnode_sdk.signing_key import generate_ed25519_keypair

sys.path.insert(0, str(Path(__file__).parent))
from _doubles import FakeWorker  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "deterministic_add_server.py"


def _client(tmp_path):
    idx = LocalIndex(tmp_path / "index")
    priv, pub = generate_ed25519_keypair()
    imp = I.import_stdio_server(FIXTURE, publisher="acme", name="add-server", version="1.0.0",
                                private_key=priv, public_key_bytes=pub)
    idx.put_blob(imp.server_bytes)
    idx.register(imp.manifest)
    op_priv, op_pub = generate_ed25519_keypair()
    au_priv, au_pub = generate_ed25519_keypair()
    store = ApprovalStore()
    ledger = AuditLedger(tmp_path / "audit.jsonl", ledger_id="L1",
                         signer_private=au_priv, signer_public_bytes=au_pub)
    client = CapabilityClient(index=idx, worker=FakeWorker(), audit_ledger=ledger,
                              approval_store=store, operator_public_bytes=op_pub,
                              worker_identity="fake/local")
    return client, op_priv, op_pub, au_pub


def _req(mid, method, **params):
    m = {"jsonrpc": "2.0", "id": mid, "method": method}
    if params:
        m["params"] = params
    return m


def test_generic_mcp_shape_initialize_and_tools_list(tmp_path):
    client, *_ = _client(tmp_path)
    ad = CapabilityMCPAdapter(client, "acme/add-server")
    init = ad.handle(_req(1, "initialize"))
    assert init["result"]["protocolVersion"] == "2024-11-05"
    tl = ad.handle(_req(2, "tools/list"))
    names = [t["name"] for t in tl["result"]["tools"]]
    assert names == ["add"]
    assert tl["result"]["tools"][0]["inputSchema"]["required"] == ["a", "b"]


def test_tools_call_is_gated_until_an_operator_approves_then_runs(tmp_path):
    client, op_priv, op_pub, au_pub = _client(tmp_path)
    ad = CapabilityMCPAdapter(client, "acme/add-server")

    # 1) a client/model call with no approval cannot proceed -> approval_required + the hash
    r1 = ad.handle(_req(3, "tools/call", name="add", arguments={"a": 2, "b": 40}))
    assert r1["result"]["isError"] is True
    assert r1["result"]["structuredContent"]["status"] == "approval_required"
    inv_hash = r1["result"]["structuredContent"]["invocation_hash"]

    # 2) the operator (holding the key the adapter does NOT have) approves THAT invocation
    ap = approval.grant(inv_hash, audience="acme/add-server", human_actor="operator@acme",
                        operator_private=op_priv, operator_public_bytes=op_pub)
    client.approval_store.put(ap)

    # 3) the same call now runs on the worker and returns the result + an audit seq
    r2 = ad.handle(_req(4, "tools/call", name="add", arguments={"a": 2, "b": 40}))
    assert r2["result"]["isError"] is False
    assert r2["result"]["structuredContent"]["call"]["structuredContent"]["sum"] == 42
    assert r2["result"]["structuredContent"]["audit_seq"] == 1
    client.audit_ledger.verify(trusted_public_bytes=au_pub)


def test_the_adapter_cannot_self_approve(tmp_path):
    client, op_priv, op_pub, au_pub = _client(tmp_path)
    ad = CapabilityMCPAdapter(client, "acme/add-server")
    # structural: neither the client nor the adapter holds the operator private key
    assert not hasattr(client, "operator_private")
    assert not hasattr(ad, "operator_private")
    # and there is no code path on the adapter that mints an approval -- approval.grant
    # REQUIRES an Ed25519 private key, which the model-facing path does not possess.
    with pytest.raises(TypeError):
        approval.grant("deadbeef", human_actor="x", operator_public_bytes=op_pub)  # no private key


def test_all_surfaces_share_one_client_contract(tmp_path):
    client, *_ = _client(tmp_path)
    # CLI, SDK and adapter are all constructed over the SAME CapabilityClient instance
    ad = CapabilityMCPAdapter(client, "acme/add-server")
    inv_sdk = client.prepare("acme/add-server", "add", {"a": 1, "b": 2},
                             nonce=client.deterministic_nonce("acme/add-server", "1.0.0", "add", {"a": 1, "b": 2}))
    # the adapter's prepared invocation for the same inputs has the same hash (one contract)
    r = ad.handle(_req(9, "tools/call", name="add", arguments={"a": 1, "b": 2}))
    assert r["result"]["structuredContent"]["invocation_hash"] == inv_sdk.invocation_hash
