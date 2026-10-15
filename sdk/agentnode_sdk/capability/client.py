"""CapabilityClient: the one contract object the CLI, the SDK and the MCP adapter share.

Every surface resolves / describes / prepares / submits through this one object, so
there is a single canonical invocation contract rather than three parallel paths.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from agentnode_sdk.capability import contract, pipeline
from agentnode_sdk.capability.approval import ApprovalStore
from agentnode_sdk.capability.audit import AuditLedger
from agentnode_sdk.capability.contract import Invocation
from agentnode_sdk.capability.index import LocalIndex
from agentnode_sdk.capability.pipeline import SubmitResult


class CapabilityClient:
    def __init__(self, *, index: LocalIndex, worker: Any = None,
                 audit_ledger: AuditLedger | None = None,
                 approval_store: ApprovalStore | None = None,
                 operator_public_bytes: bytes | None = None,
                 worker_identity: str = ""):
        self.index = index
        self.worker = worker
        self.audit_ledger = audit_ledger
        self.approval_store = approval_store
        self.operator_public_bytes = operator_public_bytes
        self.worker_identity = worker_identity

    # read ---------------------------------------------------------------- #
    def search(self, query: str) -> list[dict[str, str]]:
        return self.index.search(query)

    def list(self) -> list[dict[str, str]]:
        return self.index.list()

    def describe(self, capability_id: str, version: str | None = None) -> dict[str, Any]:
        return self.index.describe(capability_id, version)

    # the one canonical contract ------------------------------------------ #
    @staticmethod
    def deterministic_nonce(capability_id: str, version: str, tool: str, arguments: dict[str, Any]) -> str:
        body = json.dumps({"c": capability_id, "v": version, "t": tool, "a": arguments},
                          sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(b"agentnode.capability.v1/idempotency\n" + body).hexdigest()

    def prepare(self, capability_id: str, tool: str, arguments: dict[str, Any],
                version: str | None = None, *, nonce: str | None = None) -> Invocation:
        resolved = self.index.resolve(capability_id, version)
        return contract.prepare(resolved, tool, arguments, nonce=nonce)

    def submit(self, invocation: Invocation, approval_id: str) -> SubmitResult:
        if self.approval_store is None or self.operator_public_bytes is None or self.audit_ledger is None:
            raise RuntimeError("this client is read-only (no worker/approval/audit configured)")
        return pipeline.submit(invocation, approval_id=approval_id, approval_store=self.approval_store,
                               operator_public_bytes=self.operator_public_bytes, worker=self.worker,
                               index=self.index, audit_ledger=self.audit_ledger,
                               worker_identity=self.worker_identity)
