"""UsabilityClient: the one contract for the Slice-2 closed-beta usability flow.

Like slice-1 ``CapabilityClient`` it resolves / describes / prepares / submits through one
object, so CLI, SDK and the reverse MCP adapter share a single canonical invocation
contract. It differs from ``CapabilityClient`` only in the execution door it submits
through (``tree_pipeline`` for a real multi-file vendored-tree server) and in two usability
additions the brief asks for: ``describe_full`` (provenance + version + signature/digest +
execution model + requested permissions + the achievable guarantees COMPUTED from the
configured worker right now) and ``approval_required`` (a structured refusal carrying the
EXACT, executable next approval step for a human).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from agentnode_sdk.capability import contract, tree_pipeline
from agentnode_sdk.capability.approval import ApprovalStore
from agentnode_sdk.capability.audit import AuditLedger
from agentnode_sdk.capability.contract import Invocation
from agentnode_sdk.capability.index import LocalIndex
from agentnode_sdk.capability.pipeline import SubmitResult

#: The documented default command a human runs to grant one single-use approval. ``{stand}``
#: and ``{invocation_hash}`` are filled in. An operator can override the template so no
#: vendor- or deployment-specific path is baked into the product core.
DEFAULT_APPROVAL_COMMAND = (
    "python -m agentnode_sdk.capability.usability_cli approve "
    "--stand {stand} --invocation-hash {invocation_hash} --actor <your-name>"
)


@dataclass(frozen=True)
class ApprovalRequired:
    status: str
    invocation_hash: str
    capability_id: str
    tool: str
    next_step: str
    note: str

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "invocation_hash": self.invocation_hash,
                "capability_id": self.capability_id, "tool": self.tool,
                "next_step": self.next_step, "note": self.note}


class UsabilityClient:
    def __init__(self, *, index: LocalIndex, worker: Any = None,
                 audit_ledger: AuditLedger | None = None,
                 approval_store: ApprovalStore | None = None,
                 operator_public_bytes: bytes | None = None,
                 worker_identity: str = "",
                 stand: str = "<stand>",
                 approval_command_template: str = DEFAULT_APPROVAL_COMMAND):
        self.index = index
        self.worker = worker
        self.audit_ledger = audit_ledger
        self.approval_store = approval_store
        self.operator_public_bytes = operator_public_bytes
        self.worker_identity = worker_identity
        self.stand = stand
        self.approval_command_template = approval_command_template

    # read ---------------------------------------------------------------- #
    def search(self, query: str) -> list[dict[str, str]]:
        return self.index.search(query)

    def list(self) -> list[dict[str, str]]:
        return self.index.list()

    def describe(self, capability_id: str, version: str | None = None) -> dict[str, Any]:
        return self.index.describe(capability_id, version)

    def describe_full(self, capability_id: str, version: str | None = None) -> dict[str, Any]:
        """The slice-1 honest describe, augmented with version, a signature summary, and the
        achievable guarantees COMPUTED from the configured worker right now (never asserted)."""
        base = self.index.describe(capability_id, version)
        resolved = self.index.resolve(capability_id, version)
        man = resolved.manifest
        sigs = man.get("signatures", [])
        base["version"] = man["capability_version"]
        base["signature"] = [{"role": s.get("role"), "algorithm": "ed25519",
                              "key_id": s.get("key_id"),
                              "canonicalization_version": s.get("canonicalization_version")}
                             for s in sigs]
        base["source_pin"] = man.get("source", {}).get("package", {})
        base["achievable_guarantees_now"] = self._achievable_now()
        return base

    def _achievable_now(self) -> dict[str, Any]:
        if self.worker is None:
            return {"computed": False,
                    "note": "no worker configured; isolation is computed only when a run executes"}
        try:
            iso = self.worker.can_it_isolate()
        except Exception as exc:  # noqa: BLE001
            return {"computed": False, "note": f"worker could not be queried: {type(exc).__name__}"}
        available = bool(getattr(iso, "available", False))
        return {
            "computed": True,
            "backend": getattr(iso, "backend", "none"),
            "sandboxed_execution_available_now": available,
            "model2_isolation": ("the configured worker reports it can isolate this run"
                                 if available else
                                 "the configured worker CANNOT isolate now; a run would be REFUSED, "
                                 "never executed weakened/unsandboxed"),
            "caveat": "This is what the configured worker reports now; a run re-measures it. A "
                      "local/single-host worker shares a kernel with the control plane -- not a "
                      "multi-tenant boundary. Only measured properties are ever claimed.",
        }

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

    def approval_required(self, invocation: Invocation) -> ApprovalRequired:
        """The structured refusal a human sees before approving: the exact next executable step."""
        next_step = self.approval_command_template.format(
            stand=self.stand, invocation_hash=invocation.invocation_hash)
        return ApprovalRequired(
            status="approval_required",
            invocation_hash=invocation.invocation_hash,
            capability_id=invocation.capability_id,
            tool=invocation.tool_name,
            next_step=next_step,
            note="No model can approve this. A human runs the command above (it needs the "
                 "operator key in the stand), which deposits a single-use approval bound to this "
                 "exact invocation_hash; then the same call succeeds once.",
        )

    def submit(self, invocation: Invocation, approval_id: str) -> SubmitResult:
        if self.approval_store is None or self.operator_public_bytes is None or self.audit_ledger is None:
            raise RuntimeError("this client is read-only (no worker/approval/audit configured)")
        return tree_pipeline.submit(invocation, approval_id=approval_id,
                                    approval_store=self.approval_store,
                                    operator_public_bytes=self.operator_public_bytes,
                                    worker=self.worker, index=self.index,
                                    audit_ledger=self.audit_ledger,
                                    worker_identity=self.worker_identity)

    def bound_result(self, invocation: Invocation, submit_result: SubmitResult) -> dict[str, Any]:
        """Result + manifest + job + signed audit, bound together (U9)."""
        resolved = self.index.resolve(invocation.capability_id, invocation.capability_version)
        er = submit_result.exec_result
        return {
            "result": er.result,
            "manifest": resolved.manifest,
            "job": {"run_id": er.run_id, "achieved_isolation": er.achieved_isolation,
                    "exit_code": er.exit_code, "terminal_status": "ok" if er.ok else "error"},
            "audit": {"seq": submit_result.audit_entry.seq,
                      "ledger_id": submit_result.audit_entry.ledger_id,
                      "body": submit_result.audit_entry.body,
                      "body_digest": submit_result.audit_entry.body_digest,
                      "signature": submit_result.audit_entry.signature,
                      "integrity": AuditLedger.integrity_statement()},
            "effective_permissions": submit_result.effective_permissions,
        }
