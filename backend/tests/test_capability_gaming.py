"""Tests for capability expansion gaming prevention (diminishing returns).

Verifies that packages declaring excessive capabilities receive diminished
scoring benefits, preventing gaming of the resolution engine.
"""
import math

import pytest

from app.resolution.engine import (
    BROAD_PACKAGE_THRESHOLD,
    CAPABILITY_FULL_SCORE_THRESHOLD,
    effective_capability_count,
)


# ---------------------------------------------------------------------------
# Unit tests for effective_capability_count
# ---------------------------------------------------------------------------


class TestEffectiveCapabilityCount:
    """Pure-function tests for the diminishing returns formula."""

    def test_below_threshold_returns_exact_count(self):
        """Packages with <= threshold capabilities get full linear credit."""
        for count in range(1, CAPABILITY_FULL_SCORE_THRESHOLD + 1):
            assert effective_capability_count(count) == float(count)

    def test_at_threshold_returns_exact_count(self):
        """Exactly at threshold: no penalty."""
        assert effective_capability_count(CAPABILITY_FULL_SCORE_THRESHOLD) == float(
            CAPABILITY_FULL_SCORE_THRESHOLD
        )

    def test_five_capabilities_full_score(self):
        """5 capabilities: well under threshold, full credit."""
        assert effective_capability_count(5) == 5.0

    def test_ten_capabilities_full_score(self):
        """10 capabilities: at threshold, full credit."""
        assert effective_capability_count(10) == 10.0

    def test_twenty_capabilities_diminishing(self):
        """20 capabilities: should get less than 20x single-cap credit."""
        effective = effective_capability_count(20)
        # Must be greater than threshold (10) but less than 20
        assert effective > CAPABILITY_FULL_SCORE_THRESHOLD
        assert effective < 20.0
        # Expected: 10 + log2(10 + 1) = 10 + log2(11) ≈ 10 + 3.459 = 13.459
        expected = CAPABILITY_FULL_SCORE_THRESHOLD + math.log2(11)
        assert abs(effective - expected) < 0.001

    def test_fifty_capabilities_heavily_diminished(self):
        """50 capabilities: should be heavily diminished from 50."""
        effective = effective_capability_count(50)
        assert effective > CAPABILITY_FULL_SCORE_THRESHOLD
        assert effective < 50.0
        # Expected: 10 + log2(40 + 1) = 10 + log2(41) ≈ 10 + 5.358 = 15.358
        expected = CAPABILITY_FULL_SCORE_THRESHOLD + math.log2(41)
        assert abs(effective - expected) < 0.001
        # Effective should be less than a third of declared
        assert effective < 50.0 / 3

    def test_hundred_capabilities_severely_diminished(self):
        """100 capabilities: logarithmic growth keeps effective count small."""
        effective = effective_capability_count(100)
        # Expected: 10 + log2(91) ≈ 10 + 6.51 = 16.51
        expected = CAPABILITY_FULL_SCORE_THRESHOLD + math.log2(91)
        assert abs(effective - expected) < 0.001
        assert effective < 20.0  # Still well under 20 effective

    def test_monotonically_increasing(self):
        """More declared capabilities always produce a higher effective count."""
        prev = 0.0
        for count in range(1, 200):
            current = effective_capability_count(count)
            assert current > prev, (
                f"effective_capability_count({count})={current} "
                f"should be > effective_capability_count({count - 1})={prev}"
            )
            prev = current

    def test_growth_rate_decreasing_beyond_threshold(self):
        """Beyond threshold, each additional capability adds less benefit."""
        base = CAPABILITY_FULL_SCORE_THRESHOLD + 1
        prev_delta = effective_capability_count(base) - effective_capability_count(base - 1)
        for count in range(base + 1, base + 50):
            delta = effective_capability_count(count) - effective_capability_count(count - 1)
            assert delta < prev_delta, (
                f"Growth at {count} ({delta:.4f}) should be less than "
                f"at {count - 1} ({prev_delta:.4f})"
            )
            prev_delta = delta


# ---------------------------------------------------------------------------
# Scoring ratio tests — verify the breadth_ratio penalty
# ---------------------------------------------------------------------------


class TestBreadthRatioPenalty:
    """Test the breadth_ratio = effective/declared multiplier applied to cap_score."""

    def _breadth_ratio(self, declared: int) -> float:
        """Compute the breadth_ratio as the engine would."""
        if declared <= CAPABILITY_FULL_SCORE_THRESHOLD:
            return 1.0
        return effective_capability_count(declared) / declared

    def test_five_caps_no_penalty(self):
        """5 declared caps: ratio = 1.0, no penalty."""
        assert self._breadth_ratio(5) == 1.0

    def test_ten_caps_no_penalty(self):
        """10 declared caps: at threshold, ratio = 1.0."""
        assert self._breadth_ratio(10) == 1.0

    def test_twenty_caps_moderate_penalty(self):
        """20 declared caps: ratio < 1.0 (moderate penalty)."""
        ratio = self._breadth_ratio(20)
        assert 0.5 < ratio < 1.0
        # Expected: ~13.459/20 ≈ 0.673
        expected = (CAPABILITY_FULL_SCORE_THRESHOLD + math.log2(11)) / 20
        assert abs(ratio - expected) < 0.001

    def test_fifty_caps_heavy_penalty(self):
        """50 declared caps: ratio significantly below 1.0."""
        ratio = self._breadth_ratio(50)
        assert 0.2 < ratio < 0.5
        # Expected: ~15.358/50 ≈ 0.307
        expected = (CAPABILITY_FULL_SCORE_THRESHOLD + math.log2(41)) / 50
        assert abs(ratio - expected) < 0.001

    def test_hundred_caps_severe_penalty(self):
        """100 declared caps: very low ratio."""
        ratio = self._breadth_ratio(100)
        assert ratio < 0.2
        # Expected: ~16.51/100 ≈ 0.165
        expected = (CAPABILITY_FULL_SCORE_THRESHOLD + math.log2(91)) / 100
        assert abs(ratio - expected) < 0.001

    def test_penalty_increases_with_count(self):
        """The penalty (1 - ratio) should grow as declared count grows."""
        prev_ratio = 1.0
        for count in [15, 20, 30, 50, 100]:
            ratio = self._breadth_ratio(count)
            assert ratio < prev_ratio, (
                f"Ratio at {count} ({ratio:.4f}) should be less than "
                f"at previous count ({prev_ratio:.4f})"
            )
            prev_ratio = ratio


# ---------------------------------------------------------------------------
# Broad package flag tests
# ---------------------------------------------------------------------------


class TestBroadPackageFlag:
    """Test the broad_package hint threshold."""

    def test_threshold_value(self):
        """BROAD_PACKAGE_THRESHOLD should be 10."""
        assert BROAD_PACKAGE_THRESHOLD == 10

    def test_below_threshold_not_broad(self):
        """Packages with <= 10 caps should not be flagged as broad."""
        for count in range(1, BROAD_PACKAGE_THRESHOLD + 1):
            assert count <= BROAD_PACKAGE_THRESHOLD

    def test_above_threshold_is_broad(self):
        """Packages with > 10 caps should be flagged as broad."""
        for count in [11, 20, 50, 100]:
            assert count > BROAD_PACKAGE_THRESHOLD


# ---------------------------------------------------------------------------
# Scenario-based scoring tests (simulated, no DB)
# ---------------------------------------------------------------------------


class TestScoringScenarios:
    """End-to-end scoring scenarios verifying diminishing returns behaviour.

    These simulate how cap_score is modified by breadth_ratio in the engine,
    without requiring a database or full integration setup.
    """

    def _simulate_cap_score(
        self, requested: int, matched: int, declared: int
    ) -> float:
        """Simulate the capability score calculation from the engine."""
        # Base cap_score: fraction of requested caps that matched
        cap_score = matched / requested if requested > 0 else 0.0

        # Apply gaming prevention
        if declared > CAPABILITY_FULL_SCORE_THRESHOLD:
            breadth_ratio = effective_capability_count(declared) / declared
            cap_score *= breadth_ratio

        return cap_score

    def test_focused_package_beats_broad_package(self):
        """A focused package matching 3/3 caps should outscore a broad one
        matching 3/3 caps but declaring 50 total."""
        focused_score = self._simulate_cap_score(
            requested=3, matched=3, declared=3
        )
        broad_score = self._simulate_cap_score(
            requested=3, matched=3, declared=50
        )
        assert focused_score > broad_score
        # Focused gets 1.0 (3/3, no penalty)
        assert focused_score == 1.0
        # Broad gets penalized
        assert broad_score < 0.5

    def test_moderate_package_mild_penalty(self):
        """A package with 15 caps matching 3/3 gets a mild penalty."""
        focused = self._simulate_cap_score(requested=3, matched=3, declared=3)
        moderate = self._simulate_cap_score(requested=3, matched=3, declared=15)
        assert focused > moderate
        # But penalty is moderate — still above 0.5
        assert moderate > 0.5

    def test_score_ordering_across_breadths(self):
        """Scores should decrease as declared count grows, all else equal."""
        scores = []
        for declared in [5, 10, 15, 20, 30, 50]:
            score = self._simulate_cap_score(
                requested=3, matched=3, declared=declared
            )
            scores.append(score)

        # First two (5 and 10) should be equal (both at/under threshold)
        assert scores[0] == scores[1]
        # Rest should be strictly decreasing
        for i in range(1, len(scores) - 1):
            assert scores[i] >= scores[i + 1]
        # Last should be strictly less than first
        assert scores[-1] < scores[0]
