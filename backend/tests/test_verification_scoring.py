"""Tests for the verification score engine (Phase 4A)."""

from unittest.mock import MagicMock

from app.verification.scoring import compute_tool_score


def _make_vr(**kwargs):
    """Create a mock VerificationResult with defaults."""
    vr = MagicMock()
    defaults = {
        "install_status": "passed",
        "import_status": "passed",
        "smoke_status": "passed",
        "smoke_reason": "ok",
        "tests_status": "not_present",
        "tests_auto_generated": False,
        "reliability": None,
        "determinism_score": None,
        "contract_valid": None,
        "warnings_count": 0,
        # Phase 5-6 new fields
        "contract_details": None,
        "verification_mode": "real",
        "stability_log": None,
        "install_duration_ms": None,
        "smoke_confidence": None,
    }
    defaults.update(kwargs)
    for k, v in defaults.items():
        setattr(vr, k, v)
    return vr


class TestComputeToolScore:

    def test_perfect_score(self):
        vr = _make_vr(
            smoke_status="passed",
            tests_status="passed",
            tests_auto_generated=False,
            reliability=1.0,
            determinism_score=1.0,
            contract_valid=True,
        )
        score, tier, breakdown = compute_tool_score(vr)
        # Max: install(15) + import(15) + smoke(25) + tests(15) + reliability(10) + determinism(5) + contract(10) = 95
        assert score == 95
        assert tier == "gold"
        assert breakdown["install"] == 15
        assert breakdown["import"] == 15
        assert breakdown["smoke"] == 25
        assert breakdown["tests"] == 15
        assert breakdown["reliability"] == 10
        assert breakdown["determinism"] == 5
        assert breakdown["contract"] == 10

    def test_install_import_only(self):
        vr = _make_vr(smoke_status="failed", tests_status="failed")
        score, tier, breakdown = compute_tool_score(vr)
        assert score == 30
        assert tier == "unverified"

    def test_credential_tool_partial(self):
        """Tools needing credentials should get partial smoke credit."""
        vr = _make_vr(
            smoke_status="inconclusive",
            smoke_reason="needs_credentials",
        )
        score, tier, breakdown = compute_tool_score(vr)
        assert breakdown["smoke"] == 12
        assert score >= 45  # install(15) + import(15) + smoke(12) + tests(3)

    def test_system_dependency_partial(self):
        vr = _make_vr(
            smoke_status="inconclusive",
            smoke_reason="missing_system_dependency",
        )
        score, tier, breakdown = compute_tool_score(vr)
        assert breakdown["smoke"] == 12

    def test_not_implemented_no_credit(self):
        vr = _make_vr(
            smoke_status="inconclusive",
            smoke_reason="not_implemented",
        )
        score, tier, breakdown = compute_tool_score(vr)
        assert breakdown["smoke"] == 0

    def test_ambiguous_inconclusive(self):
        vr = _make_vr(
            smoke_status="inconclusive",
            smoke_reason="unknown_smoke_condition",
        )
        score, tier, breakdown = compute_tool_score(vr)
        assert breakdown["smoke"] == 8

    def test_auto_generated_tests_less_credit(self):
        vr = _make_vr(tests_status="passed", tests_auto_generated=True)
        score, tier, breakdown = compute_tool_score(vr)
        assert breakdown["tests"] == 8

    def test_real_tests_full_credit(self):
        vr = _make_vr(tests_status="passed", tests_auto_generated=False)
        score, tier, breakdown = compute_tool_score(vr)
        assert breakdown["tests"] == 15

    def test_warnings_deduction(self):
        vr = _make_vr(warnings_count=3)
        score1, _, _ = compute_tool_score(vr)
        vr_no_warn = _make_vr(warnings_count=0)
        score2, _, _ = compute_tool_score(vr_no_warn)
        assert score2 - score1 == 6  # 3 * 2

    def test_warnings_capped_at_10(self):
        vr = _make_vr(warnings_count=20)
        _, _, breakdown = compute_tool_score(vr)
        assert breakdown["warnings"] == -10

    def test_score_clamped_0_100(self):
        vr = _make_vr(
            install_status="failed",
            import_status="failed",
            smoke_status="failed",
            tests_status="failed",
            warnings_count=20,
        )
        score, tier, _ = compute_tool_score(vr)
        assert score >= 0
        assert tier == "unverified"

    def test_tier_gold(self):
        vr = _make_vr(
            reliability=1.0, determinism_score=1.0, contract_valid=True,
            tests_status="passed", tests_auto_generated=False,
        )
        score, tier, _ = compute_tool_score(vr)
        assert tier == "gold"
        assert score >= 90

    def test_tier_verified(self):
        vr = _make_vr(
            reliability=1.0, contract_valid=True,
            tests_status="passed", tests_auto_generated=True,
        )
        score, tier, _ = compute_tool_score(vr)
        # install(15) + import(15) + smoke(25) + tests(8 auto) + reliability(10) + contract(10) = 83
        assert 70 <= score < 90
        assert tier == "verified"

    def test_binary_input_partial(self):
        vr = _make_vr(
            smoke_status="inconclusive",
            smoke_reason="needs_binary_input",
        )
        _, _, breakdown = compute_tool_score(vr)
        assert breakdown["smoke"] == 12

    def test_network_blocked_partial(self):
        vr = _make_vr(
            smoke_status="inconclusive",
            smoke_reason="external_network_blocked",
        )
        _, _, breakdown = compute_tool_score(vr)
        assert breakdown["smoke"] == 12

    def test_credential_boundary_high_confidence(self):
        """Phase 7A: credential_boundary_reached with high confidence → 15 smoke points."""
        vr = _make_vr(
            smoke_status="inconclusive",
            smoke_reason="credential_boundary_reached",
            smoke_confidence="high",
        )
        _, _, breakdown = compute_tool_score(vr)
        assert breakdown["smoke"] == 15

    def test_credential_boundary_medium_confidence(self):
        """Phase 7A: credential_boundary_reached with medium confidence → 12 smoke points."""
        vr = _make_vr(
            smoke_status="inconclusive",
            smoke_reason="credential_boundary_reached",
            smoke_confidence="medium",
        )
        _, _, breakdown = compute_tool_score(vr)
        assert breakdown["smoke"] == 12

    def test_credential_boundary_tier_cap(self):
        """credential_boundary_reached → max partial tier."""
        vr = _make_vr(
            smoke_status="inconclusive",
            smoke_reason="credential_boundary_reached",
            smoke_confidence="high",
            tests_status="passed",
            tests_auto_generated=False,
            contract_valid=True,
        )
        _, tier, _ = compute_tool_score(vr)
        assert tier == "partial"

    def test_mock_mode_capped_to_partial(self):
        """Phase 6E: mock verification mode → max partial."""
        vr = _make_vr(verification_mode="mock")
        _, tier, _ = compute_tool_score(vr)
        assert tier == "partial"

    def test_limited_mode_capped_to_verified(self):
        """Phase 6E: limited verification mode → max verified, never gold."""
        vr = _make_vr(
            reliability=1.0, determinism_score=1.0, contract_valid=True,
            tests_status="passed", tests_auto_generated=False,
            verification_mode="limited",
        )
        _, tier, _ = compute_tool_score(vr)
        assert tier == "verified"

    def test_contract_details_used_for_points(self):
        """Phase 6A: contract_details overrides contract_valid for scoring."""
        vr = _make_vr(
            contract_valid=True,
            contract_details={"valid": True, "points": 7, "reason": "1 sanity issue"},
        )
        _, _, breakdown = compute_tool_score(vr)
        assert breakdown["contract"] == 7

    def test_credential_boundary_floor_to_partial(self):
        """Credential boundary with install+import passed → at least partial tier."""
        vr = _make_vr(
            smoke_status="inconclusive",
            smoke_reason="credential_boundary_reached",
            tests_status="failed",
        )
        score, tier, _ = compute_tool_score(vr)
        assert score < 50  # Score is below partial threshold
        assert tier == "partial"  # But tier floor kicks in

    def test_sandbox_limitation_floor_to_partial(self):
        """Missing system dep with install+import passed → at least partial."""
        vr = _make_vr(
            smoke_status="inconclusive",
            smoke_reason="missing_system_dependency",
            tests_status="failed",
        )
        _, tier, _ = compute_tool_score(vr)
        assert tier == "partial"

    def test_no_floor_when_install_failed(self):
        """No tier floor when install itself failed."""
        vr = _make_vr(
            install_status="failed",
            import_status="failed",
            smoke_status="failed",
            smoke_reason="credential_boundary_reached",
        )
        _, tier, _ = compute_tool_score(vr)
        assert tier == "unverified"
