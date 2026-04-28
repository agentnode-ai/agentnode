"""Verification score engine (Phases 4A + 6B/6D).

Score = deterministic, evidence-based, explainable.
ScoreResult is a core API object — everything builds on it.

Tier caps enforce hard rules: credential-boundary → max Partial, mock → max Partial, etc.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ── Tier ordering ──

TIER_ORDER = {"unverified": 0, "partial": 1, "verified": 2, "gold": 3}


def cap_tier(current: str, max_allowed: str) -> str:
    """Cap tier to max_allowed. Never promote, only demote."""
    if TIER_ORDER.get(current, 0) > TIER_ORDER.get(max_allowed, 0):
        return max_allowed
    return current


# ── Smoke reason categories ──

_SANDBOX_LIMITATION_REASONS = frozenset({
    "needs_credentials",
    "missing_system_dependency",
    "needs_binary_input",
    "external_network_blocked",
})

_CREDENTIAL_BOUNDARY_REASONS = frozenset({
    "credential_boundary_reached",
    "needs_credentials",
})

_NO_CREDIT_REASONS = frozenset({
    "not_implemented",
})


# ── Score result dataclasses ──

@dataclass
class StepScore:
    points: int
    max_points: int
    reason: str


@dataclass
class ScoreResult:
    score: int                         # 0-100
    tier: str                          # gold/verified/partial/unverified
    confidence: str                    # high/medium/low
    breakdown: dict[str, StepScore] = field(default_factory=dict)
    explanation: str = ""              # Human-readable one-liner

    def to_dict(self) -> dict:
        """Convert to API-friendly dict."""
        return {
            "score": self.score,
            "tier": self.tier,
            "confidence": self.confidence,
            "breakdown": {
                k: {"points": v.points, "max": v.max_points, "reason": v.reason}
                for k, v in self.breakdown.items()
            },
            "explanation": self.explanation,
        }

    def breakdown_simple(self) -> dict[str, int]:
        """Simple {step: points} for backward compatibility."""
        return {k: v.points for k, v in self.breakdown.items()}


def compute_tool_score(vr) -> tuple[int, str, dict]:
    """Compute verification score from a VerificationResult.

    Returns (score, tier, breakdown_dict) for backward compatibility.
    Use compute_score_result() for the full ScoreResult with explanation.
    """
    result = compute_score_result(vr)
    return result.score, result.tier, result.breakdown_simple()


def compute_score_result(vr) -> ScoreResult:
    """Compute full ScoreResult with breakdown, confidence, and explanation."""
    if getattr(vr, "is_agent_package", False) is True:
        return _compute_agent_score(vr)

    breakdown: dict[str, StepScore] = {}
    score = 0

    # ── Basis (0-70): Functionality ──

    # Install (15 pts)
    if vr.install_status == "passed":
        install_ms = getattr(vr, "install_duration_ms", None)
        reason = f"Installed in {install_ms / 1000:.1f}s" if install_ms else "Installed successfully"
        breakdown["install"] = StepScore(15, 15, reason)
        score += 15
    else:
        breakdown["install"] = StepScore(0, 15, "Installation failed")

    # Import (15 pts)
    if vr.import_status == "passed":
        breakdown["import"] = StepScore(15, 15, "All tools imported successfully")
        score += 15
    else:
        breakdown["import"] = StepScore(0, 15, "Import verification failed")

    # Smoke (25 pts) — confidence-aware for credential boundary
    smoke_points, smoke_reason = _compute_smoke_points(vr)
    breakdown["smoke"] = StepScore(smoke_points, 25, smoke_reason)
    score += smoke_points

    # Tests (15 pts)
    if vr.tests_status == "passed" and not vr.tests_auto_generated:
        breakdown["tests"] = StepScore(15, 15, "Publisher-provided tests passed")
        score += 15
    elif vr.tests_status == "passed":
        breakdown["tests"] = StepScore(8, 15, "Auto-generated tests only")
        score += 8
    elif vr.tests_status == "not_present":
        breakdown["tests"] = StepScore(3, 15, "No tests provided")
        score += 3
    elif vr.tests_status == "not_executed":
        breakdown["tests"] = StepScore(5, 15, "Tests present but not executed (no container sandbox)")
        score += 5
    else:
        breakdown["tests"] = StepScore(0, 15, "Tests failed")

    # ── Quality (0-25): Multi-run metrics ──

    # Contract (10 pts) — use contract_details if available
    contract_details = getattr(vr, "contract_details", None)
    if contract_details and isinstance(contract_details, dict):
        contract_pts = contract_details.get("points", 0)
        contract_reason = contract_details.get("reason", "Contract validated")
        breakdown["contract"] = StepScore(contract_pts, 10, contract_reason)
        score += contract_pts
    elif vr.contract_valid:
        breakdown["contract"] = StepScore(10, 10, "Serializable, non-None return")
        score += 10
    else:
        breakdown["contract"] = StepScore(0, 10, "Contract not validated")

    # Reliability (10 pts)
    if vr.reliability is not None:
        rel_points = int(vr.reliability * 10)
        runs = 3  # default
        stability_log = getattr(vr, "stability_log", None)
        if stability_log and isinstance(stability_log, list):
            runs = len(stability_log)
        ok_runs = int(vr.reliability * runs)
        breakdown["reliability"] = StepScore(
            rel_points, 10, f"{ok_runs}/{runs} runs passed",
        )
        score += rel_points
    else:
        breakdown["reliability"] = StepScore(0, 10, "No stability data")

    # Determinism (5 pts)
    if vr.determinism_score is not None:
        det_points = int(vr.determinism_score * 5)
        breakdown["determinism"] = StepScore(det_points, 5, "Output consistency check")
        score += det_points
    else:
        breakdown["determinism"] = StepScore(0, 5, "No determinism data")

    # ── Deductions ──
    deduction = min((vr.warnings_count or 0) * 2, 10)
    if deduction > 0:
        count = vr.warnings_count or 0
        breakdown["warnings"] = StepScore(-deduction, 0, f"{count} deprecation/runtime warning(s)")
    else:
        breakdown["warnings"] = StepScore(0, 0, "No warnings")
    score -= deduction

    score = max(0, min(100, score))

    # ── Tier (score-based, before caps) ──
    if score >= 90:
        tier = "gold"
    elif score >= 70:
        tier = "verified"
    elif score >= 50:
        tier = "partial"
    else:
        tier = "unverified"

    # ── Hard tier caps (Phase 6D) ──
    tier = apply_tier_caps(score, tier, vr)

    # ── Confidence ──
    confidence = compute_confidence(vr)

    # ── Explanation ──
    explanation = _build_explanation(score, tier, vr)

    return ScoreResult(
        score=score,
        tier=tier,
        confidence=confidence,
        breakdown=breakdown,
        explanation=explanation,
    )


def _compute_smoke_points(vr) -> tuple[int, str]:
    """Compute smoke test points with credential boundary confidence."""
    if vr.smoke_status == "passed":
        return 25, "Returned valid result"

    if vr.smoke_status == "inconclusive":
        reason = vr.smoke_reason
        smoke_confidence = getattr(vr, "smoke_confidence", None)

        # Phase 7A: Credential boundary with confidence levels
        if reason == "credential_boundary_reached":
            if smoke_confidence == "high":
                return 15, "Credential boundary reached (high confidence)"
            return 12, "Credential boundary reached (medium confidence)"

        if reason in _SANDBOX_LIMITATION_REASONS:
            return 12, f"Sandbox limitation: {reason}"

        if reason in _NO_CREDIT_REASONS:
            return 0, "Package is a stub/placeholder"

        return 8, f"Inconclusive: {reason}"

    return 0, "Smoke test failed"


def apply_tier_caps(score: int, tier: str, vr) -> str:
    """Hard tier caps AND floors. Score can be high but tier gets capped.

    Floors: packages that install+import but hit sandbox limits get at least "partial".
    Caps: credential-boundary → max partial, mock → max partial, etc.
    """
    # ── Tier floors: install+import passed + legitimate limitation → at least partial ──
    if (vr.install_status == "passed" and vr.import_status == "passed"
            and tier == "unverified"):
        # Legitimate sandbox limitations → floor to partial
        if vr.smoke_reason in ("credential_boundary_reached", "needs_credentials",
                                "missing_system_dependency", "needs_binary_input",
                                "external_network_blocked"):
            tier = "partial"
        # mock mode also gets partial floor
        verification_mode = getattr(vr, "verification_mode", None)
        if verification_mode == "mock":
            tier = "partial"

    # ── Tier caps ──

    # Smoke not passed → max verified (never gold)
    if vr.smoke_status != "passed":
        tier = cap_tier(tier, "verified")

    # Contract invalid → max verified
    contract_valid = vr.contract_valid
    contract_details = getattr(vr, "contract_details", None)
    if contract_details and isinstance(contract_details, dict):
        contract_valid = contract_details.get("valid", contract_valid)
    if contract_valid is False:
        tier = cap_tier(tier, "verified")

    # Credential-boundary → max partial
    if vr.smoke_reason in ("credential_boundary_reached", "needs_credentials"):
        tier = cap_tier(tier, "partial")

    # verification_mode caps
    verification_mode = getattr(vr, "verification_mode", None)
    if verification_mode == "mock":
        tier = cap_tier(tier, "partial")
    elif verification_mode == "limited":
        tier = cap_tier(tier, "verified")

    # Gold requirements
    if tier == "gold":
        if not _qualifies_for_gold(score, vr):
            tier = "verified"

    return tier


def _qualifies_for_gold(score: int, vr) -> bool:
    """Check all Gold requirements."""
    if vr.smoke_status != "passed":
        return False
    if score < 90:
        return False

    contract_valid = vr.contract_valid
    contract_details = getattr(vr, "contract_details", None)
    if contract_details and isinstance(contract_details, dict):
        contract_valid = contract_details.get("valid", contract_valid)
    if not contract_valid:
        return False

    verification_mode = getattr(vr, "verification_mode", None)
    if verification_mode and verification_mode != "real":
        return False

    if vr.smoke_reason in ("credential_boundary_reached", "needs_credentials"):
        return False

    if vr.reliability is not None and vr.reliability < 0.9:
        return False

    return True


def compute_confidence(vr) -> str:
    """How much should you trust this score?"""
    signals = 0

    # Positive signals
    if vr.smoke_status == "passed":
        signals += 2
    contract_valid = vr.contract_valid
    contract_details = getattr(vr, "contract_details", None)
    if contract_details and isinstance(contract_details, dict):
        contract_valid = contract_details.get("valid", contract_valid)
    if contract_valid:
        signals += 1
    if vr.reliability is not None and vr.reliability >= 0.9:
        signals += 1
    if vr.tests_status == "passed" and not vr.tests_auto_generated:
        signals += 1

    # Negative signals
    if vr.smoke_status == "inconclusive":
        signals -= 2
    if vr.smoke_reason in ("credential_boundary_reached", "needs_credentials"):
        signals -= 1

    if signals >= 4:
        return "high"
    if signals >= 2:
        return "medium"
    return "low"


def _build_explanation(score: int, tier: str, vr) -> str:
    """Build a human-readable explanation one-liner."""
    parts = []

    if vr.install_status == "passed" and vr.import_status == "passed":
        parts.append("Package installs and imports correctly")
    elif vr.install_status == "passed":
        parts.append("Package installs but has import issues")
    else:
        parts.append("Package failed to install")

    if vr.smoke_status == "passed":
        parts.append("runtime checks passed")
    elif vr.smoke_reason in ("credential_boundary_reached", "needs_credentials"):
        parts.append("requires API credentials for full verification")
    elif vr.smoke_reason == "missing_system_dependency":
        parts.append("requires system dependencies not in sandbox")
    elif vr.smoke_reason == "needs_binary_input":
        parts.append("requires binary input files")
    elif vr.smoke_status == "inconclusive":
        parts.append("some runtime checks inconclusive")

    if vr.tests_status == "passed" and not vr.tests_auto_generated:
        parts.append("publisher tests passed")
    elif vr.tests_status == "not_executed":
        parts.append("publisher tests present but not executed (no container sandbox)")
    elif vr.tests_status == "not_present":
        parts.append("no custom tests provided")

    return ". ".join(parts) + "."


# ── Agent-specific scoring ──


def _compute_agent_score(vr) -> ScoreResult:
    """Compute score for agent packages.

    Agent scoring table (max 100):
      Install:       15 pts
      Import:        15 pts
      Smoke:         20 pts
      Contract:      15 pts
      Reliability:   15 pts
      Determinism:   10 pts
      Manifest:      10 pts
    """
    breakdown: dict[str, StepScore] = {}
    score = 0

    # Install (15 pts)
    if vr.install_status == "passed":
        install_ms = getattr(vr, "install_duration_ms", None)
        reason = f"Installed in {install_ms / 1000:.1f}s" if install_ms else "Installed successfully"
        breakdown["install"] = StepScore(15, 15, reason)
        score += 15
    else:
        breakdown["install"] = StepScore(0, 15, "Installation failed")

    # Import (15 pts)
    if vr.import_status == "passed":
        breakdown["import"] = StepScore(15, 15, "Agent entrypoint imported successfully")
        score += 15
    else:
        breakdown["import"] = StepScore(0, 15, "Import verification failed")

    # Smoke (20 pts)
    smoke_pts, smoke_reason = _compute_agent_smoke_points(vr)
    breakdown["smoke"] = StepScore(smoke_pts, 20, smoke_reason)
    score += smoke_pts

    # Contract (15 pts)
    contract_details = getattr(vr, "contract_details", None)
    if contract_details and isinstance(contract_details, dict):
        raw_pts = contract_details.get("points", 0)
        contract_pts = min(15, int(raw_pts * 1.5))
        contract_reason = contract_details.get("reason", "Contract validated")
        breakdown["contract"] = StepScore(contract_pts, 15, contract_reason)
        score += contract_pts
    elif vr.contract_valid:
        breakdown["contract"] = StepScore(15, 15, "Serializable, non-None return")
        score += 15
    else:
        breakdown["contract"] = StepScore(0, 15, "Contract not validated")

    # Reliability (15 pts)
    if vr.reliability is not None:
        rel_points = int(vr.reliability * 15)
        runs = 3
        stability_log = getattr(vr, "stability_log", None)
        if stability_log and isinstance(stability_log, list):
            runs = len(stability_log)
        ok_runs = int(vr.reliability * runs)
        breakdown["reliability"] = StepScore(rel_points, 15, f"{ok_runs}/{runs} runs passed")
        score += rel_points
    else:
        breakdown["reliability"] = StepScore(0, 15, "No stability data")

    # Determinism (10 pts)
    if vr.determinism_score is not None:
        det_points = int(vr.determinism_score * 10)
        breakdown["determinism"] = StepScore(det_points, 10, "Output consistency check")
        score += det_points
    else:
        breakdown["determinism"] = StepScore(0, 10, "No determinism data")

    # Manifest completeness (10 pts)
    manifest_data = getattr(vr, "manifest_completeness", None)
    if manifest_data and isinstance(manifest_data, dict):
        manifest_pts = manifest_data.get("score", 0)
        breakdown["manifest"] = StepScore(manifest_pts, 10, "Agent manifest completeness")
        score += manifest_pts
    else:
        breakdown["manifest"] = StepScore(0, 10, "No manifest data")

    # Deductions (warnings)
    deduction = min((vr.warnings_count or 0) * 2, 10)
    if deduction > 0:
        count = vr.warnings_count or 0
        breakdown["warnings"] = StepScore(-deduction, 0, f"{count} deprecation/runtime warning(s)")
    else:
        breakdown["warnings"] = StepScore(0, 0, "No warnings")
    score -= deduction

    score = max(0, min(100, score))

    # Tier
    if score >= 90:
        tier = "gold"
    elif score >= 70:
        tier = "verified"
    elif score >= 50:
        tier = "partial"
    else:
        tier = "unverified"

    # Agent tier caps
    tier = _apply_agent_tier_caps(score, tier, vr)

    # Confidence
    confidence = _compute_agent_confidence(vr)

    # Explanation
    explanation = _build_agent_explanation(score, tier, vr)

    return ScoreResult(
        score=score,
        tier=tier,
        confidence=confidence,
        breakdown=breakdown,
        explanation=explanation,
    )


def _compute_agent_smoke_points(vr) -> tuple[int, str]:
    """Agent smoke points (max 20)."""
    if vr.smoke_status == "passed":
        return 20, "Agent executed successfully"

    if vr.smoke_status == "inconclusive":
        reason = vr.smoke_reason
        smoke_confidence = getattr(vr, "smoke_confidence", None)
        if reason == "credential_boundary_reached":
            if smoke_confidence == "high":
                return 12, "Credential boundary reached (high confidence)"
            return 10, "Credential boundary reached (medium confidence)"
        if reason in _SANDBOX_LIMITATION_REASONS:
            return 10, f"Sandbox limitation: {reason}"
        if reason in _NO_CREDIT_REASONS:
            return 0, "Agent is a stub/placeholder"
        return 6, f"Inconclusive: {reason}"

    return 0, "Smoke test failed"


def _apply_agent_tier_caps(score: int, tier: str, vr) -> str:
    """Agent-specific tier caps."""
    # Tier floors
    if (vr.install_status == "passed" and vr.import_status == "passed"
            and tier == "unverified"):
        if vr.smoke_reason in ("credential_boundary_reached", "needs_credentials",
                                "missing_system_dependency", "needs_binary_input",
                                "external_network_blocked"):
            tier = "partial"

    # Smoke not passed → max verified
    if vr.smoke_status != "passed":
        tier = cap_tier(tier, "verified")

    # Contract invalid → max verified
    contract_valid = vr.contract_valid
    contract_details = getattr(vr, "contract_details", None)
    if contract_details and isinstance(contract_details, dict):
        contract_valid = contract_details.get("valid", contract_valid)
    if contract_valid is False:
        tier = cap_tier(tier, "verified")

    # Credential-boundary → max partial
    if vr.smoke_reason in ("credential_boundary_reached", "needs_credentials"):
        tier = cap_tier(tier, "partial")

    # No verification cases → max verified (never gold)
    cases_total = getattr(vr, "agent_cases_total", None)
    if not cases_total or cases_total < 2:
        tier = cap_tier(tier, "verified")

    # Gold gate
    if tier == "gold":
        if not _agent_qualifies_for_gold(score, vr):
            tier = "verified"

    return tier


def _agent_qualifies_for_gold(score: int, vr) -> bool:
    """Check all agent Gold requirements."""
    if score < 90:
        return False
    if vr.smoke_status != "passed":
        return False

    contract_valid = vr.contract_valid
    contract_details = getattr(vr, "contract_details", None)
    if contract_details and isinstance(contract_details, dict):
        contract_valid = contract_details.get("valid", contract_valid)
    if not contract_valid:
        return False

    if vr.reliability is not None and vr.reliability < 0.9:
        return False

    # Must have >= 2 verification cases, all passed
    cases_total = getattr(vr, "agent_cases_total", None)
    cases_passed = getattr(vr, "agent_cases_passed", None)
    if not cases_total or cases_total < 2:
        return False
    if cases_passed != cases_total:
        return False

    # Check gold blockers
    gold_blockers = getattr(vr, "agent_gold_blockers", None)
    if gold_blockers and len(gold_blockers) > 0:
        return False

    return True


def _compute_agent_confidence(vr) -> str:
    """Confidence level for agent scoring."""
    signals = 0

    if vr.smoke_status == "passed":
        signals += 2
    contract_valid = vr.contract_valid
    contract_details = getattr(vr, "contract_details", None)
    if contract_details and isinstance(contract_details, dict):
        contract_valid = contract_details.get("valid", contract_valid)
    if contract_valid:
        signals += 1
    if vr.reliability is not None and vr.reliability >= 0.9:
        signals += 1

    # Agent-specific: verification cases boost confidence
    cases_total = getattr(vr, "agent_cases_total", None)
    cases_passed = getattr(vr, "agent_cases_passed", None)
    if cases_total and cases_total >= 2 and cases_passed == cases_total:
        signals += 1

    # Manifest completeness boosts confidence
    manifest_data = getattr(vr, "manifest_completeness", None)
    if manifest_data and isinstance(manifest_data, dict) and manifest_data.get("score", 0) >= 8:
        signals += 1

    if vr.smoke_status == "inconclusive":
        signals -= 2
    if vr.smoke_reason in ("credential_boundary_reached", "needs_credentials"):
        signals -= 1

    if signals >= 4:
        return "high"
    if signals >= 2:
        return "medium"
    return "low"


def _build_agent_explanation(score: int, tier: str, vr) -> str:
    """Build explanation for agent scoring."""
    parts = []

    if vr.install_status == "passed" and vr.import_status == "passed":
        parts.append("Agent installs and imports correctly")
    elif vr.install_status == "passed":
        parts.append("Agent installs but has import issues")
    else:
        parts.append("Agent failed to install")

    if vr.smoke_status == "passed":
        parts.append("runtime checks passed")
    elif vr.smoke_status == "inconclusive":
        parts.append("some runtime checks inconclusive")

    cases_total = getattr(vr, "agent_cases_total", None)
    cases_passed = getattr(vr, "agent_cases_passed", None)
    if cases_total:
        parts.append(f"{cases_passed}/{cases_total} verification cases passed")
    else:
        parts.append("no verification cases defined")

    manifest_data = getattr(vr, "manifest_completeness", None)
    if manifest_data and isinstance(manifest_data, dict):
        parts.append(f"manifest completeness {manifest_data.get('score', 0)}/10")

    return ". ".join(parts) + "."
