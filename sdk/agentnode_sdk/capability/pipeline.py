"""The single submit door: consume an approval, execute on the worker, bind the audit.

CLI, SDK and the MCP adapter all reach execution ONLY through here, after
``contract.prepare``. ``submit`` never approves anything itself: it consumes an
approval an operator issued out of band (``approval.grant``), bound to this exact
invocation, single-use. Then it runs on the worker (no local fallback) and writes
one signed, chain-bound audit entry.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentnode_sdk.capability import execution
from agentnode_sdk.capability.approval import ApprovalStore, effective_grant
from agentnode_sdk.capability.audit import AuditEntry, AuditLedger
from agentnode_sdk.capability.contract import Invocation
from agentnode_sdk.capability.execution import ExecResult
from agentnode_sdk.capability.index import LocalIndex


@dataclass(frozen=True)
class SubmitResult:
    exec_result: ExecResult
    audit_entry: AuditEntry
    effective_permissions: dict[str, Any]


def submit(
    invocation: Invocation,
    *,
    approval_id: str,
    approval_store: ApprovalStore,
    operator_public_bytes: bytes,
    worker: Any,
    index: LocalIndex,
    audit_ledger: AuditLedger,
    worker_identity: str,
    now: float | None = None,
) -> SubmitResult:
    # 0. refuse an invocation whose mutable arguments were changed after prepare/approval
    invocation.assert_consistent()
    # 1. consume a single-use approval bound to THIS invocation + audience (refuses otherwise)
    ap = approval_store.consume(approval_id, invocation.invocation_hash,
                                trusted_operator_public_bytes=operator_public_bytes,
                                expected_audience=invocation.capability_id, now=now)
    # 2. effective grant never exceeds requested
    eff = effective_grant(invocation.requested_permissions, ap.granted_scope)
    # 3. execute on the worker -- refuses rather than running locally
    exec_result = execution.execute(invocation, index, worker, effective_grant=eff)
    # 4. bind one signed audit entry to job + manifest + result
    entry = audit_ledger.record_result(invocation=invocation, approval=ap,
                                        exec_result=exec_result, worker_identity=worker_identity)
    return SubmitResult(exec_result=exec_result, audit_entry=entry, effective_permissions=eff)
