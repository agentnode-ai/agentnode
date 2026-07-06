"""1A of the 0.18.0 user-controlled host-trust-policy bow: the PURE policy core.

Only the decision table is exercised here — no config key, no runtime routing,
no installer change. The central property is the regression guarantee that the
``"default"`` policy is byte-for-byte today's behavior, so shipping 1A changes
nothing for anyone until a stricter value is explicitly wired + set later.
"""
import pytest

from agentnode_sdk.sandbox.policy import (
    host_allowed_tiers,
    requires_sandbox,
    requires_sandbox_for_policy,
)

# Every trust value the routing can see, plus edge inputs.
ALL_TIERS = ["curated", "trusted", "verified", "unverified", "unknown", "", None]


# --- host_allowed_tiers: the decision table --------------------------------

def test_default_allows_curated_and_trusted():
    assert host_allowed_tiers("default") == frozenset({"curated", "trusted"})


def test_curated_only_allows_only_curated():
    assert host_allowed_tiers("curated_only") == frozenset({"curated"})


def test_none_allows_nobody():
    assert host_allowed_tiers("none") == frozenset()


@pytest.mark.parametrize("policy", [None, "", "  ", "default", "garbage", "DEFAULT"])
def test_unknown_or_absent_policy_falls_back_to_default(policy):
    # Never silently over-restrict: only an explicitly recognized stricter value
    # narrows host access. Value validation is the config layer's job.
    assert host_allowed_tiers(policy) == frozenset({"curated", "trusted"})


@pytest.mark.parametrize("policy,expected", [
    (" Curated_Only ", frozenset({"curated"})),
    ("NONE", frozenset()),
    ("None", frozenset()),  # the string "None", not the value None
])
def test_policy_is_case_and_whitespace_insensitive(policy, expected):
    assert host_allowed_tiers(policy) == expected


# --- requires_sandbox_for_policy: policy x tier matrix ---------------------

# True = must be sandboxed; False = may run on host.
MATRIX = {
    "default": {
        "curated": False, "trusted": False,
        "verified": True, "unverified": True, "unknown": True, "": True, None: True,
    },
    "curated_only": {
        "curated": False, "trusted": True,
        "verified": True, "unverified": True, "unknown": True, "": True, None: True,
    },
    "none": {
        "curated": True, "trusted": True,
        "verified": True, "unverified": True, "unknown": True, "": True, None: True,
    },
}


@pytest.mark.parametrize("policy", list(MATRIX))
@pytest.mark.parametrize("tier", ALL_TIERS)
def test_requires_sandbox_matrix(policy, tier):
    assert requires_sandbox_for_policy(tier, policy) is MATRIX[policy][tier]


def test_missing_trust_is_always_sandboxed_under_every_policy():
    for policy in MATRIX:
        assert requires_sandbox_for_policy(None, policy) is True
        assert requires_sandbox_for_policy("", policy) is True


# --- REGRESSION: default == today's behavior (the non-breaking guarantee) ---

@pytest.mark.parametrize("tier", ALL_TIERS)
def test_default_policy_equals_legacy_requires_sandbox(tier):
    assert requires_sandbox_for_policy(tier, "default") == requires_sandbox(tier)


@pytest.mark.parametrize("tier", ALL_TIERS)
def test_absent_policy_equals_legacy_requires_sandbox(tier):
    # A config without the key must resolve to today's behavior.
    assert requires_sandbox_for_policy(tier, None) == requires_sandbox(tier)
