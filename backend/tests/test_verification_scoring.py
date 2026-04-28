"""Tests for the verification score engine (Phase 4A + Agent Scoring)."""

from unittest.mock import MagicMock

from app.verification.scoring import compute_tool_score, compute_score_result


def _make_vr(**kwargs):
    """Create a mock VerificationResult with defaults for TOOL-PACKS."""
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
        # Agent fields — False/None for tool-packs
        "is_agent_package": False,
        "manifest_completeness": None,
        "agent_cases_results": None,
        "agent_cases_passed": None,
        "agent_cases_total": None,
        "agent_gold_blockers": None,
    }
    defaults.update(kwargs)
    for k, v in defaults.items():
        setattr(vr, k, v)
    return vr


def _make_agent_vr(**kwargs):
    """Create a mock VerificationResult for AGENTS."""
    defaults = {
        "is_agent_package": True,
        "install_status": "passed",
        "import_status": "passed",
        "smoke_status": "passed",
        "smoke_reason": "ok",
        "tests_status": "skipped",
        "tests_auto_generated": False,
        "reliability": None,
        "determinism_score": None,
        "contract_valid": None,
        "warnings_count": 0,
        "contract_details": None,
        "verification_mode": "real",
        "stability_log": None,
        "install_duration_ms": None,
        "smoke_confidence": None,
        "manifest_completeness": None,
        "agent_cases_results": None,
        "agent_cases_passed": None,
        "agent_cases_total": None,
        "agent_gold_blockers": None,
    }
    defaults.update(kwargs)
    vr = MagicMock()
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


class TestToolPackRegression:
    """Regression suite: tool-pack scoring MUST NOT change with agent scoring addition."""

    def test_toolpack_perfect_exact_scores(self):
        vr = _make_vr(
            smoke_status="passed", tests_status="passed", tests_auto_generated=False,
            reliability=1.0, determinism_score=1.0, contract_valid=True,
        )
        result = compute_score_result(vr)
        assert result.score == 95
        assert result.tier == "gold"
        b = result.breakdown
        assert b["install"].points == 15
        assert b["import"].points == 15
        assert b["smoke"].points == 25
        assert b["tests"].points == 15
        assert b["reliability"].points == 10
        assert b["determinism"].points == 5
        assert b["contract"].points == 10

    def test_toolpack_no_tests_exact(self):
        vr = _make_vr(tests_status="not_present")
        result = compute_score_result(vr)
        assert result.breakdown["tests"].points == 3

    def test_toolpack_auto_tests_exact(self):
        vr = _make_vr(tests_status="passed", tests_auto_generated=True)
        result = compute_score_result(vr)
        assert result.breakdown["tests"].points == 8

    def test_toolpack_credential_boundary_exact(self):
        vr = _make_vr(
            smoke_status="inconclusive", smoke_reason="credential_boundary_reached",
            smoke_confidence="high",
        )
        result = compute_score_result(vr)
        assert result.breakdown["smoke"].points == 15
        assert result.tier == "partial"

    def test_toolpack_not_executed_tests(self):
        """Untrusted publisher tests present but not run get same credit as not_present."""
        vr = _make_vr(tests_status="not_executed")
        result = compute_score_result(vr)
        assert result.breakdown["tests"].points == 3
        assert "untrusted" in result.breakdown["tests"].reason.lower()

    def test_toolpack_is_not_agent(self):
        """Tool-pack VR must NOT be routed to agent scoring."""
        vr = _make_vr(is_agent_package=False)
        result = compute_score_result(vr)
        assert "manifest" not in result.breakdown
        assert "tests" in result.breakdown


class TestAgentScoring:
    """Agent-specific scoring (Phase 6: bifurcation)."""

    def test_agent_perfect_score(self):
        vr = _make_agent_vr(
            reliability=1.0, determinism_score=1.0, contract_valid=True,
            manifest_completeness={"score": 10},
            agent_cases_total=2, agent_cases_passed=2,
            agent_gold_blockers=[],
        )
        result = compute_score_result(vr)
        # install(15) + import(15) + smoke(20) + contract(15) + reliability(15) + determinism(10) + manifest(10) = 100
        assert result.score == 100
        assert result.tier == "gold"

    def test_agent_scoring_table(self):
        vr = _make_agent_vr(
            reliability=1.0, determinism_score=1.0, contract_valid=True,
            manifest_completeness={"score": 10},
            agent_cases_total=2, agent_cases_passed=2,
            agent_gold_blockers=[],
        )
        result = compute_score_result(vr)
        b = result.breakdown
        assert b["install"].max_points == 15
        assert b["import"].max_points == 15
        assert b["smoke"].max_points == 20
        assert b["contract"].max_points == 15
        assert b["reliability"].max_points == 15
        assert b["determinism"].max_points == 10
        assert b["manifest"].max_points == 10

    def test_agent_no_tests_category(self):
        """Agent scoring has 'manifest' instead of 'tests'."""
        vr = _make_agent_vr()
        result = compute_score_result(vr)
        assert "manifest" in result.breakdown
        assert "tests" not in result.breakdown

    def test_agent_without_cases_max_verified(self):
        vr = _make_agent_vr(
            reliability=1.0, determinism_score=1.0, contract_valid=True,
            manifest_completeness={"score": 10},
            agent_cases_total=0, agent_cases_passed=0,
        )
        result = compute_score_result(vr)
        assert result.score >= 90
        assert result.tier == "verified"

    def test_agent_gold_requires_cases(self):
        vr = _make_agent_vr(
            reliability=1.0, determinism_score=1.0, contract_valid=True,
            manifest_completeness={"score": 10},
            agent_cases_total=1, agent_cases_passed=1,
            agent_gold_blockers=[],
        )
        result = compute_score_result(vr)
        assert result.tier == "verified"

    def test_agent_gold_with_blockers(self):
        vr = _make_agent_vr(
            reliability=1.0, determinism_score=1.0, contract_valid=True,
            manifest_completeness={"score": 10},
            agent_cases_total=2, agent_cases_passed=2,
            agent_gold_blockers=["goal not passed to LLM prompt"],
        )
        result = compute_score_result(vr)
        assert result.tier == "verified"

    def test_agent_smoke_max_20(self):
        vr = _make_agent_vr(smoke_status="passed")
        result = compute_score_result(vr)
        assert result.breakdown["smoke"].points == 20

    def test_agent_manifest_completeness(self):
        vr = _make_agent_vr(manifest_completeness={"score": 6})
        result = compute_score_result(vr)
        assert result.breakdown["manifest"].points == 6

    def test_agent_failed_cases_no_gold(self):
        vr = _make_agent_vr(
            reliability=1.0, determinism_score=1.0, contract_valid=True,
            manifest_completeness={"score": 10},
            agent_cases_total=2, agent_cases_passed=1,
            agent_gold_blockers=["1/2 cases failed"],
        )
        result = compute_score_result(vr)
        assert result.tier == "verified"

    def test_agent_low_reliability_no_gold(self):
        vr = _make_agent_vr(
            reliability=0.5, determinism_score=1.0, contract_valid=True,
            manifest_completeness={"score": 10},
            agent_cases_total=2, agent_cases_passed=2,
            agent_gold_blockers=[],
        )
        result = compute_score_result(vr)
        assert result.tier != "gold"
