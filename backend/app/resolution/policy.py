"""Shared policy evaluation — used by both resolve-upgrade and check-policy.
Spec §8.5.1"""

from __future__ import annotations

from dataclasses import dataclass

TRUST_SCORES = {
    "curated": 1.0, "trusted": 0.8, "verified": 0.6, "unverified": 0.3,
}


@dataclass
class PolicyInput:
    """Package attributes needed for policy evaluation."""
    trust_level: str
    network_level: str
    filesystem_level: str
    code_execution_level: str
    data_access_level: str
    user_approval_level: str
    is_yanked: bool = False
    is_quarantined: bool = False
    requires_approval: bool = False


@dataclass
class PolicyConstraints:
    """Caller-specified policy constraints."""
    min_trust: str | None = None
    allow_shell: bool = True
    allow_network: bool = True


@dataclass
class PolicyResult:
    result: str  # "allowed" | "blocked" | "requires_approval"
    reasons: list[str]


def evaluate_policy(pkg: PolicyInput, policy: PolicyConstraints) -> PolicyResult:
    """Evaluate package against policy constraints.

    Returns:
        PolicyResult with result and reasons.

    Spec §8.5.1:
    - "blocked": violates hard constraints
    - "requires_approval": compatible but needs approval
    - "allowed": satisfies all constraints
    """
    reasons: list[str] = []

    # Hard blocks
    if pkg.is_yanked:
        reasons.append("Package version is yanked")
        return PolicyResult(result="blocked", reasons=reasons)

    if pkg.is_quarantined:
        reasons.append("Package version is quarantined")
        return PolicyResult(result="blocked", reasons=reasons)

    if policy.min_trust:
        pkg_score = TRUST_SCORES.get(pkg.trust_level, 0)
        min_score = TRUST_SCORES.get(policy.min_trust, 0)
        if pkg_score < min_score:
            reasons.append(
                f"Trust level '{pkg.trust_level}' below minimum '{policy.min_trust}'"
            )
            return PolicyResult(result="blocked", reasons=reasons)

    if not policy.allow_shell and pkg.code_execution_level == "shell":
        reasons.append("Package requires shell execution but policy forbids it")
        return PolicyResult(result="blocked", reasons=reasons)

    if not policy.allow_network and pkg.network_level == "unrestricted":
        reasons.append("Package requires unrestricted network but policy forbids it")
        return PolicyResult(result="blocked", reasons=reasons)

    # Requires approval checks
    approval_reasons: list[str] = []

    if pkg.user_approval_level == "always":
        approval_reasons.append("Package requires user approval for all operations")

    if pkg.user_approval_level == "high_risk_only":
        if pkg.code_execution_level in ("limited_subprocess", "shell"):
            approval_reasons.append("High-risk: code execution requires approval")
        if pkg.network_level == "unrestricted":
            approval_reasons.append("High-risk: unrestricted network requires approval")

    if pkg.requires_approval:
        approval_reasons.append("Package manifest requires approval")

    if approval_reasons:
        return PolicyResult(result="requires_approval", reasons=approval_reasons)

    return PolicyResult(result="allowed", reasons=[])


def evaluate_policy_inline(
    trust_level: str,
    permissions,
    quarantine_status: str,
    is_yanked: bool,
    policy: dict,
) -> str:
    """Simplified policy check for use inside Resolution Engine.

    Args:
        trust_level: Publisher trust level string.
        permissions: Permission model object (or None).
        quarantine_status: Version quarantine status.
        is_yanked: Whether version is yanked.
        policy: Dict with min_trust, allow_shell, allow_network.

    Returns:
        "allowed", "blocked", or "requires_approval".
    """
    pkg_input = PolicyInput(
        trust_level=trust_level,
        network_level=permissions.network_level if permissions else "none",
        filesystem_level=permissions.filesystem_level if permissions else "none",
        code_execution_level=permissions.code_execution_level if permissions else "none",
        data_access_level=permissions.data_access_level if permissions else "input_only",
        user_approval_level=permissions.user_approval_level if permissions else "never",
        is_yanked=is_yanked,
        is_quarantined=quarantine_status not in ("none", "cleared"),
    )
    constraints = PolicyConstraints(
        min_trust=policy.get("min_trust"),
        allow_shell=policy.get("allow_shell", True),
        allow_network=policy.get("allow_network", True),
    )
    result = evaluate_policy(pkg_input, constraints)
    return result.result
