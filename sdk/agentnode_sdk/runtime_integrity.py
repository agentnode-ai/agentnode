"""Runtime-neutral, read-only lockfile-integrity gate for execution paths.

One fail-closed snapshot in, one ``(lock, entry, report)`` out — so a live path
(``run_tool``, ``cmd_mcp_doctor``) resolves the package entry AND evaluates the
two-stage (per-entry + structure) integrity decision from the SAME object, never
a second read. The decision matrix is the merged core helper's
(:func:`evaluate_lock_integrity`), NOT a second policy.

Imports no CLI or runtime module and NEVER writes (no seal / update / mutate /
atomic write / audit) — audit + warning translation is the caller's surface job.
"""
from __future__ import annotations

from pathlib import Path

from agentnode_sdk.exceptions import LockfileFormatError
from agentnode_sdk.installer import read_lockfile_strict
from agentnode_sdk.lock_integrity import (
    LockIntegrityDenied,
    LockIntegrityReport,
    evaluate_lock_integrity,
)


def gate_lock_integrity(
    slug: str, lockfile_path: Path | None, *, strict: bool
) -> tuple[dict, dict, LockIntegrityReport]:
    """Read ONE fail-closed lockfile snapshot, resolve *slug*'s entry from it, and
    evaluate + enforce the two-stage integrity decision.

    Returns ``(lock, entry, report)`` when execution is ALLOWED. ``report.reason``
    is ``"verified"`` for a fully-verified decision, otherwise ``"warn: ..."`` for
    an allowed-but-not-verified migration state (normal mode only — strict denies).
    The returned ``entry`` is the exact object from the same snapshot; the caller
    dispatches on it and must NOT re-read.

    Raises:
        LockfileFormatError: the lockfile cannot be SAFELY read/parsed — missing
            file, invalid JSON, duplicate key, OSError, non-object lockfile,
            non-object ``packages``, or a missing/unsupported ``lockfile_version``.
            No integrity report is invented for an unreadable snapshot; the caller
            translates this via its existing lockfile-error surface.
        LockIntegrityDenied: the snapshot is readable but the combined decision
            denies *slug* — ``entry_status="absent"`` (the requested package is not
            an object in ``packages``) in either mode, or any other non-verified
            entry/structure status under strict.

    Strictly read-only: never seals, writes, restamps, or audits.
    """
    lock = read_lockfile_strict(lockfile_path)
    if lock is None:
        # A missing lockfile is a hard read error at runtime: nothing to run.
        raise LockfileFormatError("lockfile_missing", "agentnode.lock could not be found")
    entry = lock["packages"].get(slug, {})
    report = evaluate_lock_integrity(slug, lock, strict=strict)
    if not report.allowed:
        raise LockIntegrityDenied(report)
    return lock, entry, report
