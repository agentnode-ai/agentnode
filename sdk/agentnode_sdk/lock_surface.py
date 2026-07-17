"""Import-neutral SURFACE helpers for the two-stage lock-integrity decision.

Shared by every enforcing surface — ``runner.run_tool``, ``cli.mcp_commands``
(doctor), and ``installer.load_tool`` — so none of them has to import another
(``installer`` must not import ``runner``). This module imports no runtime/CLI
module at top level; ``policy`` is imported lazily inside the audit helper.

These are TRANSLATION helpers only (one migration warning, one best-effort audit,
the safe report fields) — the decision itself is the merged core helper's
(:func:`agentnode_sdk.lock_integrity.evaluate_lock_integrity`); there is no second
policy here.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("agentnode_sdk.lock_integrity")


def integrity_report_fields(report: Any) -> dict:
    """The ONLY safe fields to expose from a :class:`LockIntegrityReport` — status
    names / flags, never hashes, entry content, signatures, paths, or tokens."""
    return {
        "entry_status": report.entry_status,
        "structure_status": report.structure_status,
        "strict": report.strict,
        "allowed": report.allowed,
        "reason": report.reason,
    }


def warn_lock_integrity(slug: str, report: Any) -> None:
    """Emit exactly ONE migration warning (normal mode). Names the entry and
    structure status, notes strict would deny, points to ``lock verify`` (and
    ``lock seal`` when something is unsealed). Never claims an automatic repair."""
    msg = (
        f"Lockfile integrity for '{slug}': entry={report.entry_status}, "
        f"structure={report.structure_status}. Allowed in normal mode; strict mode "
        "(AGENTNODE_GUARD_STRICT) would deny. Run 'agentnode lock verify'"
    )
    if report.entry_status == "missing" or report.structure_status == "missing":
        msg += "; seal with 'agentnode lock seal' if the state is intended"
    logger.warning(msg + ".")


def audit_lock_integrity(report: Any, slug: str) -> None:
    """Best-effort audit for a non-verified integrity decision: ``allow`` in normal
    mode, ``deny`` in strict. Never changes the decision, never raises, never emits
    a second event. Carries only status names + the strict flag (no sensitive
    values)."""
    try:
        from agentnode_sdk.policy import PolicyResult, audit_decision
        audit_decision(
            PolicyResult(
                action="deny" if not report.allowed else "allow",
                reason=report.reason,
                source="lock_integrity",
            ),
            "lock_integrity_check",
            slug,
            extra={
                "entry_status": report.entry_status,
                "structure_status": report.structure_status,
                "strict": report.strict,
            },
        )
    except Exception:
        logger.debug("Failed to audit lock integrity", exc_info=True)
