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
from agentnode_sdk.capability.execution import ExecutionError
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
    worker_identity: Any,
    now: float | None = None,
    run_id: str | None = None,
) -> SubmitResult:
    """`worker_identity` is the string to audit, or -- target-router R2/D8 -- a callable given the
    run id that returns the identity the connection carrying THAT run proved. A run whose carrying
    identity cannot be named is an integrity failure: it raises, and no audit entry claims it.
    `run_id`, when given, is the caller's stable dispatch id (the same job every time it is sent)."""
    # 0. refuse an invocation whose mutable arguments were changed after prepare/approval
    invocation.assert_consistent()
    # 1. consume a single-use approval bound to THIS invocation + audience (refuses otherwise)
    ap = approval_store.consume(approval_id, invocation.invocation_hash,
                                trusted_operator_public_bytes=operator_public_bytes,
                                expected_audience=invocation.capability_id, now=now)
    # 2. effective grant never exceeds requested
    eff = effective_grant(invocation.requested_permissions, ap.granted_scope)
    # 3. execute the vendored tree on the worker -- refuses rather than running locally
    exec_result = tree_execution.execute_tree(invocation, index, worker, effective_grant=eff, run_id=run_id)
    if callable(worker_identity):
        identity = worker_identity(exec_result.run_id)
        if not identity:
            raise ExecutionError("the run has no proved worker identity; it is not audited as a run of any worker")
    else:
        identity = worker_identity                      # unchanged for every existing caller
    # 4. bind one signed audit entry to job + manifest + result
    entry = audit_ledger.record_result(invocation=invocation, approval=ap,
                                        exec_result=exec_result, worker_identity=identity)
    return SubmitResult(exec_result=exec_result, audit_entry=entry, effective_permissions=eff)
