"""Slice 1 — MCP status convergence onto the shared package lifecycle.

Locks the status foundation and, critically, the safety line: this slice must
NOT let any MCP go live automatically. `derive_status` never returns published;
only the admin publish path does. A clean submission is held for review.
"""

from __future__ import annotations

from app.mcp import status as st
from app.mcp.registry_verify import derive_status


# --- lifecycle mapping (single source of truth) ------------------------------


def test_lifecycle_mapping_table():
    assert st.lifecycle_of(st.PENDING) == st.LIFECYCLE_IN_REVIEW
    assert st.lifecycle_of(st.QUARANTINED_REVIEW) == st.LIFECYCLE_IN_REVIEW
    assert st.lifecycle_of(st.APPROVED) == st.LIFECYCLE_IN_REVIEW
    assert st.lifecycle_of(st.ACTION_REQUIRED) == st.LIFECYCLE_NEEDS_FIX
    assert st.lifecycle_of(st.NEEDS_CHANGES) == st.LIFECYCLE_NEEDS_FIX
    assert st.lifecycle_of(st.REJECTED) == st.LIFECYCLE_REJECTED
    assert st.lifecycle_of(st.PUBLISHED) == st.LIFECYCLE_PUBLISHED


def test_pending_is_legacy_alias_of_quarantined_review():
    # Same lifecycle bucket, both open, both report-updatable, both reviewable.
    assert st.PENDING in st.REVIEW_HOLD and st.QUARANTINED_REVIEW in st.REVIEW_HOLD
    for s in (st.PENDING, st.QUARANTINED_REVIEW):
        assert s in st.OPEN_STATUSES
        assert s in st.REPORT_UPDATABLE
        assert s in st.ADMIN_SETTABLE
        assert s in st.REVERIFY_SOURCE
        assert st.lifecycle_of(s) == st.LIFECYCLE_IN_REVIEW


def test_published_only_via_published_package_id_or_status():
    assert (
        st.lifecycle_of(st.APPROVED, published_package_id="pkg-123")
        == st.LIFECYCLE_PUBLISHED
    )
    assert st.is_live(st.PUBLISHED) is True
    assert st.is_live(st.APPROVED, published_package_id="pkg-123") is True


def test_unknown_status_never_reads_as_live():
    assert st.lifecycle_of("some_future_state") == st.LIFECYCLE_IN_REVIEW
    assert st.is_live("some_future_state") is False


# --- safety line: derive_status never auto-publishes -------------------------


def test_clean_submission_goes_to_review_hold_not_live():
    """A clean report (server ok, TESTED, no high actions) is held for review —
    NEVER published. This is the core Slice-1 safety invariant."""
    server_ok = {"server_status": "resolved"}
    clean_report = {"status": "TESTED", "actions": []}
    out = derive_status(server_ok, clean_report)
    assert out == st.QUARANTINED_REVIEW
    assert st.is_live(out) is False


def test_derive_status_never_returns_published():
    """No combination of inputs may make derive_status emit a live state."""
    cases = [
        ({"server_status": "resolved"}, {"status": "TESTED", "actions": []}),
        ({"server_status": "resolved"}, {"status": "RESOLVED", "actions": []}),
        ({"server_status": "mismatch"}, {"status": "TESTED", "actions": []}),
        (
            {"server_status": "resolved"},
            {"status": "TESTED", "actions": [{"severity": "high"}]},
        ),
        ({}, {}),
    ]
    for sv, report in cases:
        out = derive_status(sv, report)
        assert out != st.PUBLISHED
        assert st.is_live(out) is False


def test_mismatch_or_high_action_requires_fix():
    assert (
        derive_status(
            {"server_status": "mismatch"}, {"status": "TESTED", "actions": []}
        )
        == st.ACTION_REQUIRED
    )
    assert (
        derive_status(
            {"server_status": "resolved"},
            {"status": "TESTED", "actions": [{"severity": "high"}]},
        )
        == st.ACTION_REQUIRED
    )
