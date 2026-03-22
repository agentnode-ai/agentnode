"""Unit tests for install resolution priority logic.

Tests the core business rule: which version does `agentnode install` resolve to?
These are pure unit tests — no DB, no HTTP client.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app.packages.version_queries import (
    TIER_PRIORITY,
    InstallResolution,
    _derive_install_reason,
    _tier_priority_for_version,
)


def _make_version(
    *,
    verification_tier: str | None = None,
    verification_status: str | None = None,
    published_at: datetime | None = None,
) -> MagicMock:
    """Create a mock PackageVersion with verification fields."""
    pv = MagicMock()
    pv.verification_tier = verification_tier
    pv.verification_status = verification_status
    pv.published_at = published_at or datetime.now(timezone.utc)
    return pv


# --- Scenario 1: Gold v1.0 + Failed v2.0 → installs v1.0 (verified) ---

class TestVerifiedBeatsFailedLatest:
    def test_gold_version_gets_priority_1(self):
        pv = _make_version(verification_tier="gold")
        assert _tier_priority_for_version(pv) == 1
        assert _derive_install_reason(pv) == InstallResolution.VERIFIED

    def test_verified_version_gets_priority_1(self):
        pv = _make_version(verification_tier="verified")
        assert _tier_priority_for_version(pv) == 1
        assert _derive_install_reason(pv) == InstallResolution.VERIFIED

    def test_failed_version_gets_priority_4(self):
        pv = _make_version(verification_tier=None, verification_status="failed")
        assert _tier_priority_for_version(pv) == 4
        assert _derive_install_reason(pv) == InstallResolution.FALLBACK


# --- Scenario 2: Partial v2.0 + Verified v1.0 → verified wins ---

class TestVerifiedBeatsPartial:
    def test_partial_gets_priority_2(self):
        pv = _make_version(verification_tier="partial")
        assert _tier_priority_for_version(pv) == 2
        assert _derive_install_reason(pv) == InstallResolution.PARTIAL

    def test_verified_outranks_partial(self):
        verified = _make_version(verification_tier="verified")
        partial = _make_version(verification_tier="partial")
        assert _tier_priority_for_version(verified) < _tier_priority_for_version(partial)


# --- Scenario 3: Recent pending v2.0 + Verified v1.0 → pending wins (< 24h) ---

class TestRecentPendingPriority:
    def test_recent_pending_gets_priority_3(self):
        pv = _make_version(
            verification_status="pending",
            published_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        assert _tier_priority_for_version(pv) == 3
        assert _derive_install_reason(pv) == InstallResolution.PENDING

    def test_just_published_pending_gets_priority_3(self):
        pv = _make_version(
            verification_status="pending",
            published_at=datetime.now(timezone.utc),
        )
        assert _tier_priority_for_version(pv) == 3

    def test_pending_at_23h_still_recent(self):
        pv = _make_version(
            verification_status="pending",
            published_at=datetime.now(timezone.utc) - timedelta(hours=23),
        )
        assert _tier_priority_for_version(pv) == 3


# --- Scenario 4: Stale pending v2.0 + Verified v1.0 → verified wins ---

class TestStalePendingFallback:
    def test_stale_pending_drops_to_fallback(self):
        pv = _make_version(
            verification_status="pending",
            published_at=datetime.now(timezone.utc) - timedelta(hours=25),
        )
        assert _tier_priority_for_version(pv) == 4
        assert _derive_install_reason(pv) == InstallResolution.FALLBACK

    def test_very_old_pending_is_fallback(self):
        pv = _make_version(
            verification_status="pending",
            published_at=datetime.now(timezone.utc) - timedelta(days=30),
        )
        assert _tier_priority_for_version(pv) == 4

    def test_verified_outranks_stale_pending(self):
        verified = _make_version(verification_tier="gold")
        stale = _make_version(
            verification_status="pending",
            published_at=datetime.now(timezone.utc) - timedelta(hours=48),
        )
        assert _tier_priority_for_version(verified) < _tier_priority_for_version(stale)


# --- Scenario 5: All failed → fallback on latest public ---

class TestAllFailedFallback:
    def test_failed_no_tier_is_fallback(self):
        pv = _make_version(verification_status="failed", verification_tier=None)
        assert _derive_install_reason(pv) == InstallResolution.FALLBACK

    def test_error_is_fallback(self):
        pv = _make_version(verification_status="error", verification_tier=None)
        assert _derive_install_reason(pv) == InstallResolution.FALLBACK

    def test_skipped_is_fallback(self):
        pv = _make_version(verification_status="skipped", verification_tier=None)
        assert _derive_install_reason(pv) == InstallResolution.FALLBACK


# --- Scenario 6: Pinned version ---

class TestPinnedResolution:
    def test_pinned_constant_exists(self):
        assert InstallResolution.PINNED == "pinned"


# --- Central mapping consistency ---

class TestTierPriorityMapping:
    def test_mapping_covers_all_buckets(self):
        assert set(TIER_PRIORITY.keys()) == {1, 2, 3, 4}

    def test_mapping_values_match_constants(self):
        assert TIER_PRIORITY[1] == InstallResolution.VERIFIED
        assert TIER_PRIORITY[2] == InstallResolution.PARTIAL
        assert TIER_PRIORITY[3] == InstallResolution.PENDING
        assert TIER_PRIORITY[4] == InstallResolution.FALLBACK

    def test_derive_uses_mapping(self):
        """Ensure _derive_install_reason goes through TIER_PRIORITY, not hardcoded."""
        for tier_name, priority in [("gold", 1), ("verified", 1), ("partial", 2)]:
            pv = _make_version(verification_tier=tier_name)
            assert _derive_install_reason(pv) == TIER_PRIORITY[_tier_priority_for_version(pv)]

    def test_priority_ordering_is_strict(self):
        """Lower number = higher priority."""
        versions = [
            _make_version(verification_tier="gold"),
            _make_version(verification_tier="partial"),
            _make_version(verification_status="pending", published_at=datetime.now(timezone.utc)),
            _make_version(verification_status="failed"),
        ]
        priorities = [_tier_priority_for_version(v) for v in versions]
        assert priorities == [1, 2, 3, 4]
        assert priorities == sorted(priorities)
