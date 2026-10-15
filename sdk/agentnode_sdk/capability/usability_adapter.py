"""Reverse MCP adapter for the Slice-2 usability capability (real multi-file server).

A generic MCP client connects here (stdio) and sees the capability's tools; a tools/call
goes through the one canonical contract (``UsabilityClient.prepare`` -> ``submit``) and
executes the REAL vendored-tree server on the worker. The adapter holds NO operator
private key -- it can only CONSUME an approval an operator deposited out of band. So a
client/model driving the adapter can never approve its own call: an unapproved call returns
``approval_required`` WITH the exact, executable next approval step for a human (U6).

Stdlib-only, like the slice-1 adapter, so it runs on a bare worker host.
"""
from __future__ import annotations

import json
import sys
from typing import Any

from agentnode_sdk.capability.approval import ApprovalError
from agentnode_sdk.capability.usability import UsabilityClient

PROTOCOL = "2024-11-05"


class UsabilityMCPAdapter:
    def __init__(self, client: UsabilityClient, capability_id: str, *,
                 version: str | None = None, approvals_dir: str | None = None):
        self.client = client
        self.capability_id = capability_id
        self.version = version
        self.approvals_dir = approvals_dir
        self._resolved = client.index.resolve(capability_id, version)

    def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        mid = msg.get("id")
        method = msg.get("method")
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": PROTOCOL,
                "serverInfo": {"name": "agentnode-capability-adapter", "version": "2"},
                "capabilities": {"tools": {}}}}
        if method == "notifications/initialized":
            return None
        if method == "tools/list":
            tools = []
            for t in self._resolved.manifest.get("tools", []):
                tools.append({"name": t["name"], "description": t.get("description", ""),
                              "inputSchema": t.get("input_schema") or {"type": "object"}})
            return {"jsonrpc": "2.0", "id": mid, "result": {"tools": tools}}
        if method == "tools/call":
            return {"jsonrpc": "2.0", "id": mid, "result": self._call(msg.get("params") or {})}
        if mid is not None:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
        return None

    def _call(self, params: dict[str, Any]) -> dict[str, Any]:
        tool = params.get("name")
        arguments = params.get("arguments") or {}
        nonce = self.client.deterministic_nonce(self._resolved.capability_id,
                                                self._resolved.capability_version, tool, arguments)
        try:
            inv = self.client.prepare(self.capability_id, tool, arguments,
                                      version=self.version, nonce=nonce)
        except ValueError as exc:
            return {"isError": True, "content": [{"type": "text", "text": str(exc)}]}

        # Pull in any approvals the operator deposited (the human channel); never mint one.
        if self.approvals_dir and self.client.approval_store is not None:
            self.client.approval_store.load_dir(self.approvals_dir)
        approval_id = (self.client.approval_store.find_unconsumed_for(inv.invocation_hash)
                       if self.client.approval_store is not None else None)
        if approval_id is None:
            req = self.client.approval_required(inv)
            return {"isError": True,
                    "content": [{"type": "text",
                                 "text": f"approval required. Next step:\n  {req.next_step}"}],
                    "structuredContent": req.to_dict()}
        try:
            res = self.client.submit(inv, approval_id)
        except (ApprovalError, Exception) as exc:  # noqa: BLE001 -- surface as a tool error, never crash
            return {"isError": True, "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}]}

        call = res.exec_result.result.get("call") or {}
        text = ""
        if isinstance(call, dict):
            for item in call.get("content") or []:
                if item.get("type") == "text":
                    text = item.get("text", "")
                    break
        return {"isError": not res.exec_result.ok,
                "content": [{"type": "text", "text": text}],
                "structuredContent": {
                    "call": call,
                    "invocation_hash": inv.invocation_hash,
                    "audit_seq": res.audit_entry.seq,
                    "achieved_isolation": res.exec_result.achieved_isolation,
                }}

    def serve_stdio(self, stdin=None, stdout=None) -> None:
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        for raw in stdin:
            line = raw.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue
            resp = self.handle(msg)
            if resp is not None:
                stdout.write(json.dumps(resp) + "\n")
                stdout.flush()


def build_from_stand(stand: str) -> UsabilityMCPAdapter:
    """Reconstruct an adapter from a persisted stand directory (for `python -m ... usability_adapter <stand>`).

    stand/ holds: capability_id.txt, operator_pub.hex, audit_signer_priv.hex, index/ (LocalIndex
    root), approvals/ (operator-deposited approvals), consumed/ (durable single-use markers),
    audit.jsonl. A REAL podman worker is built. The adapter holds no operator private key.
    """
    import os

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    from agentnode_sdk.capability.approval import ApprovalStore
    from agentnode_sdk.capability.audit import AuditLedger
    from agentnode_sdk.capability.index import LocalIndex
    from agentnode_sdk.sandbox.container_backend import ContainerBackend
    from agentnode_sdk.worker.local import LocalWorker

    cap_id = open(os.path.join(stand, "capability_id.txt")).read().strip()
    op_pub = bytes.fromhex(open(os.path.join(stand, "operator_pub.hex")).read().strip())
    au_priv = Ed25519PrivateKey.from_private_bytes(
        bytes.fromhex(open(os.path.join(stand, "audit_signer_priv.hex")).read().strip()))
    au_pub = au_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    idx = LocalIndex(os.path.join(stand, "index"))
    ledger = AuditLedger(os.path.join(stand, "audit.jsonl"), ledger_id="adapter-L1",
                         signer_private=au_priv, signer_public_bytes=au_pub)
    worker = LocalWorker(ContainerBackend())
    client = UsabilityClient(index=idx, worker=worker, audit_ledger=ledger,
                             approval_store=ApprovalStore(consumed_dir=os.path.join(stand, "consumed")),
                             operator_public_bytes=op_pub,
                             worker_identity="host:" + worker.configuration_sha256()[:16],
                             stand=stand)
    return UsabilityMCPAdapter(client, cap_id, approvals_dir=os.path.join(stand, "approvals"))


if __name__ == "__main__":
    build_from_stand(sys.argv[1]).serve_stdio()
