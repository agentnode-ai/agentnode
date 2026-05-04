"""Normalize legacy verification config formats into unified cases structure.

Supports three legacy source formats:
  - verification.cases     (new unified format, pass through)
  - verification.fixtures  (Phase B VCR fixtures)
  - verification.test_input (single real-mode case)

All three set has_explicit_cases=True, protecting existing Gold packs.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class NormalizedVerification:
    cases: list[dict] = field(default_factory=list)
    system_requirements: list[str] = field(default_factory=list)
    source_format: str = "none"  # "cases" | "fixtures" | "test_input" | "none"
    has_explicit_cases: bool = False


def normalize_verification_config(verification_config: dict | None) -> NormalizedVerification:
    """Normalize any verification config format into NormalizedVerification.

    Priority order (first match wins):
      1. verification.cases (new unified format)
      2. verification.fixtures (Phase B VCR format)
      3. verification.test_input (single real-mode case)
      4. Nothing → empty, has_explicit_cases=False
    """
    if not verification_config or not isinstance(verification_config, dict):
        return NormalizedVerification()

    system_requirements = verification_config.get("system_requirements", [])
    if not isinstance(system_requirements, list):
        system_requirements = []

    # 1. New unified cases format
    cases = verification_config.get("cases")
    if cases and isinstance(cases, list):
        normalized_cases = [_normalize_case(c) for c in cases if isinstance(c, dict)]
        if normalized_cases:
            return NormalizedVerification(
                cases=normalized_cases,
                system_requirements=system_requirements,
                source_format="cases",
                has_explicit_cases=True,
            )

    # 2. Legacy fixtures format (Phase B)
    fixtures = verification_config.get("fixtures")
    if fixtures and isinstance(fixtures, list):
        normalized_cases = [_fixture_to_case(f) for f in fixtures if isinstance(f, dict)]
        if normalized_cases:
            return NormalizedVerification(
                cases=normalized_cases,
                system_requirements=system_requirements,
                source_format="fixtures",
                has_explicit_cases=True,
            )

    # 3. Legacy test_input (single real-mode case)
    test_input = verification_config.get("test_input")
    if test_input and isinstance(test_input, dict):
        return NormalizedVerification(
            cases=[{
                "name": "legacy_test_input",
                "input": test_input,
                "tool": None,
                "cassette": None,
                "expected": None,
                "mode": "real",
            }],
            system_requirements=system_requirements,
            source_format="test_input",
            has_explicit_cases=True,
        )

    return NormalizedVerification(system_requirements=system_requirements)


def _normalize_case(case: dict) -> dict:
    """Ensure a unified case dict has required fields."""
    cassette = case.get("cassette")
    return {
        "name": case.get("name", "unnamed"),
        "input": case.get("input", {}),
        "tool": case.get("tool"),
        "cassette": cassette,
        "expected": case.get("expected"),
        "mode": "fixture" if cassette else "real",
    }


def _fixture_to_case(fixture: dict) -> dict:
    """Convert a legacy fixture dict to unified case format."""
    cassette = fixture.get("cassette")
    return {
        "name": fixture.get("name", "unnamed"),
        "input": fixture.get("test_input", {}),
        "tool": fixture.get("tool"),
        "cassette": cassette,
        "expected": fixture.get("expected"),
        "mode": "fixture" if cassette else "real",
    }
