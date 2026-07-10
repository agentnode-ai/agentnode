"""Single source of truth for MCP submission statuses + convergence onto the
shared package lifecycle.

Slice 1 of the MCP auto-publish arc (2026-07-10): this PREPARES the status model
so admin review becomes structurally the exception path. It does NOT activate any
auto-publish and does NOT let any MCP go live that could not before — a clean
submission still lands in a review-hold state and requires the same admin
approve → publish steps. `derive_status` never returns PUBLISHED; only the
admin publish endpoint sets it.

`McpSubmission.status` is a free-form VARCHAR(40) (no DB enum), so adding the
QUARANTINED_REVIEW and PUBLISHED values needs no migration.
"""

from __future__ import annotations

# --- raw statuses stored in McpSubmission.status -----------------------------
PENDING = "pending"  # legacy name of the review-hold state (kept as an alias)
QUARANTINED_REVIEW = "quarantined_review"  # converged name: held for review, NOT live
ACTION_REQUIRED = "action_required"  # hard failure: publisher must fix + resubmit
NEEDS_CHANGES = "needs_changes"  # admin asked for changes
APPROVED = "approved"  # admin cleared, pre-publish — still NOT live
REJECTED = "rejected"
PUBLISHED = "published"  # live in the catalog (only the admin publish path sets this)

# pending ≡ quarantined_review — same meaning (held for human review, not live).
REVIEW_HOLD = frozenset({PENDING, QUARANTINED_REVIEW})

# A submission still in the pipeline — blocks a duplicate submit of the same pkg.
OPEN_STATUSES = frozenset(
    {PENDING, QUARANTINED_REVIEW, ACTION_REQUIRED, NEEDS_CHANGES, APPROVED}
)

# Statuses a publisher may update by re-submitting a verification report.
REPORT_UPDATABLE = frozenset(
    {PENDING, QUARANTINED_REVIEW, ACTION_REQUIRED, NEEDS_CHANGES}
)

# Statuses an admin may set via the review endpoint.
ADMIN_SETTABLE = frozenset(
    {APPROVED, REJECTED, NEEDS_CHANGES, PENDING, QUARANTINED_REVIEW}
)

# Statuses a re-verify may refresh (recover from unavailable / pre-approval).
REVERIFY_SOURCE = frozenset({PENDING, QUARANTINED_REVIEW, ACTION_REQUIRED})

# --- converged lifecycle buckets (shared vocabulary with the package model) ---
LIFECYCLE_NEEDS_FIX = "needs_fix"
LIFECYCLE_IN_REVIEW = "in_review"
LIFECYCLE_PUBLISHED = "published"
LIFECYCLE_REJECTED = "rejected"


def lifecycle_of(status: str, published_package_id: object | None = None) -> str:
    """Map a raw MCP submission status onto the converged lifecycle bucket.

    A submission is only ``published`` (live) if it actually produced a catalog
    package. Everything in the review-hold or approved-pre-publish state is
    ``in_review`` — never live. Unknown statuses default to ``in_review`` so a
    stray value can never read as live.
    """
    if published_package_id is not None or status == PUBLISHED:
        return LIFECYCLE_PUBLISHED
    if status in (ACTION_REQUIRED, NEEDS_CHANGES):
        return LIFECYCLE_NEEDS_FIX
    if status == REJECTED:
        return LIFECYCLE_REJECTED
    return LIFECYCLE_IN_REVIEW


def is_live(status: str, published_package_id: object | None = None) -> bool:
    """True only when the submission is actually live in the catalog."""
    return lifecycle_of(status, published_package_id) == LIFECYCLE_PUBLISHED
