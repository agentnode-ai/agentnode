"""The single submit door for the multi-file (vendored-tree) capability (Slice 2, mechanism b).

Mirrors slice-1 ``pipeline.submit`` exactly -- consume a single-use, invocation-bound
operator approval, compute the effective grant (never exceeding requested), execute on the
worker with NO local fallback, bind one signed chain-bound audit entry -- but dispatches to
``tree_execution.execute_tree`` for a real multi-file server. Approval, grant and audit are
the slice-1 primitives, reused unchanged.
"""
from __future__ import annotations

from typing import Any

from agentnode_sdk.capability import tree_execution
from agentnode_sdk.capability.approval import ApprovalStore, effective_grant
from agentnode_sdk.capability.audit import AuditLedger
from agentnode_sdk.capability.contract import Invocation
from agentnode_sdk.capability.index import LocalIndex
from agentnode_sdk.capability.pipeline import SubmitResult


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
    # 3. execute the vendored tree on the worker -- refuses rather than running locally
    exec_result = tree_execution.execute_tree(invocation, index, worker, effective_grant=eff)
    # 4. bind one signed audit entry to job + manifest + result
    entry = audit_ledger.record_result(invocation=invocation, approval=ap,
                                        exec_result=exec_result, worker_identity=worker_identity)
    return SubmitResult(exec_result=exec_result, audit_entry=entry, effective_permissions=eff)
