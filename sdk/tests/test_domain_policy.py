"""Stage 5: shared domain-policy canonicalizer (pure; no daemon, no DNS).

Drives the canonicalizer with the SHARED vectors used by the backend mirror too, so SDK
and backend decide identically (divergence = test failure)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentnode_sdk.sandbox.domain_policy import (
    DomainPolicyError,
    canonicalize_allowed_domains,
)

VECTORS = json.loads(
    (Path(__file__).parent / "data" / "allowed_domains_vectors.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", VECTORS["valid"])
def test_valid_vectors_canonicalize(case):
    assert list(canonicalize_allowed_domains(case["input"])) == case["output"]


@pytest.mark.parametrize("bad", VECTORS["invalid"])
def test_invalid_vectors_rejected(bad):
    with pytest.raises(DomainPolicyError):
        canonicalize_allowed_domains(bad)


def test_domain_policy_error_is_valueerror():
    # Stage-2 egress callers catch ValueError; the subclass must keep that working.
    assert issubclass(DomainPolicyError, ValueError)


def test_output_is_sorted_deduped_tuple():
    out = canonicalize_allowed_domains(["z.example.com", "a.example.com", "Z.EXAMPLE.COM"])
    assert out == ("a.example.com", "z.example.com")
    assert isinstance(out, tuple)


def test_egress_validate_delegates_to_shared():
    # behaviour-preserving delegation: egress.validate_allowed_domains == canonicalizer
    from agentnode_sdk.sandbox.egress import validate_allowed_domains
    assert validate_allowed_domains(["B.example.com", "a.example.com"]) == \
        canonicalize_allowed_domains(["B.example.com", "a.example.com"])
    with pytest.raises(ValueError):
        validate_allowed_domains([])
