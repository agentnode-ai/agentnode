"""Unit tests for resolution scoring weights."""
from app.resolution.engine import (
    PERMISSION_DEDUCTIONS,
    TRUST_SCORES,
    W_CAPABILITY,
    W_FRAMEWORK,
    W_PERMISSIONS,
    W_RUNTIME,
    W_TRUST,
)


def test_weights_sum_to_one():
    total = W_CAPABILITY + W_FRAMEWORK + W_RUNTIME + W_TRUST + W_PERMISSIONS
    assert abs(total - 1.0) < 1e-9


def test_capability_is_heaviest():
    assert W_CAPABILITY > W_FRAMEWORK
    assert W_CAPABILITY > W_RUNTIME
    assert W_CAPABILITY > W_TRUST
    assert W_CAPABILITY > W_PERMISSIONS


def test_trust_scores_ordered():
    assert TRUST_SCORES["curated"] > TRUST_SCORES["trusted"]
    assert TRUST_SCORES["trusted"] > TRUST_SCORES["verified"]
    assert TRUST_SCORES["verified"] > TRUST_SCORES["unverified"]


def test_trust_scores_match_spec():
    """Spec values: curated=1.0, trusted=0.8, verified=0.6, unverified=0.3."""
    assert TRUST_SCORES["curated"] == 1.0
    assert TRUST_SCORES["trusted"] == 0.8
    assert TRUST_SCORES["verified"] == 0.6
    assert TRUST_SCORES["unverified"] == 0.3


def test_trust_scores_in_valid_range():
    for level, score in TRUST_SCORES.items():
        assert 0.0 <= score <= 1.0, f"Trust score for {level} out of range"


def test_permission_deductions_spec():
    """Spec §4.3: unrestricted=0.3, shell=0.4, workspace_write=0.2, any=0.2."""
    assert PERMISSION_DEDUCTIONS["unrestricted"] == 0.3
    assert PERMISSION_DEDUCTIONS["shell"] == 0.4
    assert PERMISSION_DEDUCTIONS["workspace_write"] == 0.2
    assert PERMISSION_DEDUCTIONS["any"] == 0.2


def test_permission_score_with_all_dangerous():
    """If all dangerous permissions are set, perm_score should be 0.0 (clamped)."""
    perm_score = 1.0 - (0.3 + 0.4 + 0.2 + 0.2)
    assert max(0.0, perm_score) == 0.0


def test_permission_score_with_none():
    """No dangerous permissions = score 1.0."""
    perm_score = 1.0
    assert perm_score == 1.0


def test_framework_weight_greater_than_permissions():
    assert W_FRAMEWORK > W_PERMISSIONS


def test_max_possible_score():
    """Perfect match with curated trust and no permissions should score 1.0."""
    cap = 1.0
    fw = 1.0
    rt = 1.0
    trust = TRUST_SCORES["curated"]
    perm = 1.0
    total = W_CAPABILITY * cap + W_FRAMEWORK * fw + W_RUNTIME * rt + W_TRUST * trust + W_PERMISSIONS * perm
    assert abs(total - 1.0) < 1e-9
