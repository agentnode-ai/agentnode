"""Real-world import conversion test suite.

Tests the importer against 20 realistic tool snippets across 5 buckets:
- Simple happy path (high confidence expected)
- BaseTool cases (medium-high expected)
- Helpers / complex structure (high expected if self-contained)
- Edge cases (medium expected)
- Hard blockers (low expected, draft_ready=False)

Run with: pytest tests/import_real_world/ -v
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.import_.schemas import ConvertRequest
from app.import_.service import convert

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(filename: str) -> str:
    return (FIXTURES_DIR / filename).read_text(encoding="utf-8")


# ── Expectation mapping ──────────────────────────────────────────────
# Each entry: (filename, platform, expected)
# Expected keys:
#   confidence: "high" | "medium" | "low"
#   draft_ready: bool
#   min_tools: int (minimum number of detected tools)
#   max_tools: int | None
#   warning_contains: list[str] (substrings that must appear in at least one warning)
#   warning_absent: list[str] (substrings that must NOT appear in any warning)
#   changes_min: int (minimum number of changes)
#   no_framework_imports: bool (tool.py must not contain framework imports)
#   code_valid: bool (tool.py must parse with ast.parse)
#   deps_contain: list[str] (must be in detected_dependencies)
#   unknown_contain: list[str] (must be in unknown_imports)

EXPECTATIONS = [
    # ── Bucket 1: Simple happy path ──
    (
        "lc01_simple_word_count.py", "langchain", {
            "confidence": "high",
            "draft_ready": True,
            "min_tools": 1,
            "no_framework_imports": True,
            "code_valid": True,
            "changes_min": 1,
        }
    ),
    (
        "lc02_tool_with_defaults.py", "langchain", {
            "confidence": "high",
            "draft_ready": True,
            "min_tools": 1,
            "deps_contain": ["requests"],
            "no_framework_imports": True,
            "code_valid": True,
        }
    ),
    (
        "lc03_two_tools.py", "langchain", {
            "confidence": "high",
            "draft_ready": True,
            "min_tools": 2,
            "max_tools": 2,
            "no_framework_imports": True,
            "code_valid": True,
        }
    ),

    # ── Bucket 2: BaseTool cases ──
    (
        "lc04_basetool_dict_return.py", "langchain", {
            "confidence": "high",
            "draft_ready": True,
            "min_tools": 1,
            "deps_contain": ["requests"],
            "no_framework_imports": True,
            "code_valid": True,
        }
    ),
    (
        "lc05_basetool_str_return.py", "langchain", {
            "confidence": "medium",
            "draft_ready": True,
            "min_tools": 1,
            "warning_contains": ["str", "wrapped"],
            "no_framework_imports": True,
            "code_valid": True,
        }
    ),
    (
        "lc06_basetool_args_schema.py", "langchain", {
            "confidence": "high",
            "draft_ready": True,
            "min_tools": 1,
            "warning_contains": ["args_schema"],
            "no_framework_imports": True,
            "code_valid": True,
        }
    ),

    # ── Bucket 3: Helpers / complex structure ──
    (
        "lc07_helper_functions.py", "langchain", {
            "confidence": "high",
            "draft_ready": True,
            "min_tools": 1,
            "no_framework_imports": True,
            "code_valid": True,
            "warning_absent": ["_clean_text", "_remove_stop_words"],  # helpers should NOT be unresolved
        }
    ),
    (
        "lc08_helper_class.py", "langchain", {
            "confidence": "high",
            "draft_ready": True,
            "min_tools": 1,
            "no_framework_imports": True,
            "code_valid": True,
        }
    ),

    # ── Bucket 4: Edge cases (medium expected) ──
    (
        "lc09_env_vars.py", "langchain", {
            "confidence": "high",  # code is valid, env vars are just a warning
            "draft_ready": True,
            "min_tools": 1,
            "warning_contains": ["environment"],
            "deps_contain": ["requests"],
            "no_framework_imports": True,
            "code_valid": True,
        }
    ),
    (
        "lc10_unresolved_symbols.py", "langchain", {
            "confidence": "medium",
            "draft_ready": True,
            "min_tools": 1,
            "warning_contains": ["parse_record"],
            "code_valid": True,
        }
    ),
    (
        "lc11_missing_type_hints.py", "langchain", {
            "confidence": "medium",
            "draft_ready": True,
            "min_tools": 1,
            "warning_contains": ["type hint"],
            "code_valid": True,
        }
    ),
    (
        "lc12_try_except_import.py", "langchain", {
            "confidence": "high",
            "draft_ready": True,
            "min_tools": 1,
            "warning_contains": ["try/except"],
            "deps_contain": ["PyPDF2"],
            "code_valid": True,
        }
    ),

    # ── Bucket 5: Hard blockers (low, draft_ready=False) ──
    (
        "lc13_async_tool.py", "langchain", {
            "confidence": "low",
            "draft_ready": False,
            "min_tools": 1,
            "warning_contains": ["async"],
        }
    ),
    (
        "lc14_self_reference.py", "langchain", {
            "confidence": "low",
            "draft_ready": False,
            "min_tools": 1,
            "warning_contains": ["self.connection_string"],
        }
    ),
    (
        "lc15_relative_import.py", "langchain", {
            "confidence": "low",
            "draft_ready": False,
            "warning_contains": ["relative import"],
        }
    ),
    (
        "lc16_structured_tool.py", "langchain", {
            "confidence": "low",
            "draft_ready": False,
            "warning_contains": ["StructuredTool.from_function"],
        }
    ),
    (
        "lc17_unknown_import_active.py", "langchain", {
            "confidence": "low",
            "draft_ready": False,
            "min_tools": 1,
            "unknown_contain": ["company_internal"],
            "warning_contains": ["company_internal"],
        }
    ),

    # ── CrewAI ──
    (
        "cr01_simple_named.py", "crewai", {
            "confidence": "high",
            "draft_ready": True,
            "min_tools": 1,
            "no_framework_imports": True,
            "code_valid": True,
            "changes_min": 1,
        }
    ),
    (
        "cr02_no_arg_decorator.py", "crewai", {
            "confidence": "high",
            "draft_ready": True,
            "min_tools": 1,
            "deps_contain": ["pandas"],
            "no_framework_imports": True,
            "code_valid": True,
        }
    ),
    (
        "cr03_with_helpers.py", "crewai", {
            "confidence": "high",
            "draft_ready": True,
            "min_tools": 1,
            "deps_contain": ["requests"],
            "no_framework_imports": True,
            "code_valid": True,
        }
    ),
]


# ── Parametrized test ─────────────────────────────────────────────────

@pytest.mark.parametrize(
    "filename,platform,expected",
    EXPECTATIONS,
    ids=[e[0].replace(".py", "") for e in EXPECTATIONS],
)
def test_real_world_fixture(filename: str, platform: str, expected: dict):
    source = _load_fixture(filename)
    req = ConvertRequest(platform=platform, content=source)
    resp = convert(req)

    # Confidence level
    assert resp.confidence.level == expected["confidence"], (
        f"Expected confidence={expected['confidence']}, got {resp.confidence.level}. "
        f"Reasons: {resp.confidence.reasons}"
    )

    # Draft ready
    assert resp.draft_ready == expected["draft_ready"], (
        f"Expected draft_ready={expected['draft_ready']}, got {resp.draft_ready}. "
        f"Warnings: {resp.warnings}"
    )

    # Tool count
    min_tools = expected.get("min_tools", 0)
    if min_tools:
        assert len(resp.detected_tools) >= min_tools, (
            f"Expected >= {min_tools} tools, got {len(resp.detected_tools)}"
        )
    max_tools = expected.get("max_tools")
    if max_tools is not None:
        assert len(resp.detected_tools) <= max_tools, (
            f"Expected <= {max_tools} tools, got {len(resp.detected_tools)}"
        )

    # Warning contains
    for substr in expected.get("warning_contains", []):
        assert any(substr.lower() in w.lower() for w in resp.warnings), (
            f"Expected warning containing '{substr}'. Warnings: {resp.warnings}"
        )

    # Warning absent
    for substr in expected.get("warning_absent", []):
        assert not any(substr in w for w in resp.warnings), (
            f"Warning should NOT contain '{substr}'. Warnings: {resp.warnings}"
        )

    # Changes minimum
    changes_min = expected.get("changes_min", 0)
    if changes_min:
        assert len(resp.changes) >= changes_min, (
            f"Expected >= {changes_min} changes, got {len(resp.changes)}"
        )

    # No framework imports in generated code
    if expected.get("no_framework_imports"):
        tool_py = next((f for f in resp.code_files if f.path.endswith("tool.py")), None)
        if tool_py:
            assert "langchain" not in tool_py.content, "Framework import 'langchain' in generated tool.py"
            assert "crewai" not in tool_py.content, "Framework import 'crewai' in generated tool.py"

    # Generated code is syntactically valid
    if expected.get("code_valid"):
        tool_py = next((f for f in resp.code_files if f.path.endswith("tool.py")), None)
        if tool_py:
            try:
                ast.parse(tool_py.content)
            except SyntaxError as e:
                pytest.fail(f"Generated tool.py has syntax error: {e}")

    # Dependencies contain
    for dep in expected.get("deps_contain", []):
        assert dep in resp.detected_dependencies, (
            f"Expected '{dep}' in dependencies. Got: {resp.detected_dependencies}"
        )

    # Unknown imports contain
    for unk in expected.get("unknown_contain", []):
        assert unk in resp.unknown_imports, (
            f"Expected '{unk}' in unknown_imports. Got: {resp.unknown_imports}"
        )


# ── Summary report (runs after all parametrized tests) ────────────────

def test_summary_report(capsys):
    """Generate a distribution summary across all fixtures."""
    results = {"high": 0, "medium": 0, "low": 0}
    draft_ready_count = 0
    total = 0
    bucket_stats: dict[str, dict] = {
        "Simple": {"total": 0, "high": 0, "medium": 0, "low": 0, "draft_ready": 0},
        "BaseTool": {"total": 0, "high": 0, "medium": 0, "low": 0, "draft_ready": 0},
        "Helpers": {"total": 0, "high": 0, "medium": 0, "low": 0, "draft_ready": 0},
        "Edge": {"total": 0, "high": 0, "medium": 0, "low": 0, "draft_ready": 0},
        "Blocker": {"total": 0, "high": 0, "medium": 0, "low": 0, "draft_ready": 0},
        "CrewAI": {"total": 0, "high": 0, "medium": 0, "low": 0, "draft_ready": 0},
    }

    def _bucket(filename: str) -> str:
        if filename.startswith("cr"):
            return "CrewAI"
        num = int(filename.split("_")[0][2:])
        if num <= 3:
            return "Simple"
        if num <= 6:
            return "BaseTool"
        if num <= 8:
            return "Helpers"
        if num <= 12:
            return "Edge"
        return "Blocker"

    for filename, platform, _ in EXPECTATIONS:
        source = _load_fixture(filename)
        req = ConvertRequest(platform=platform, content=source)
        resp = convert(req)

        total += 1
        level = resp.confidence.level
        results[level] += 1
        if resp.draft_ready:
            draft_ready_count += 1

        bucket = _bucket(filename)
        bucket_stats[bucket]["total"] += 1
        bucket_stats[bucket][level] += 1
        if resp.draft_ready:
            bucket_stats[bucket]["draft_ready"] += 1

    with capsys.disabled():
        print("\n")
        print("=" * 70)
        print("  IMPORT REAL-WORLD TEST SUMMARY")
        print("=" * 70)
        print(f"  Total fixtures: {total}")
        print(f"  High:   {results['high']:>3}  ({results['high']*100//total}%)")
        print(f"  Medium: {results['medium']:>3}  ({results['medium']*100//total}%)")
        print(f"  Low:    {results['low']:>3}  ({results['low']*100//total}%)")
        print(f"  Draft ready: {draft_ready_count}/{total}")
        print("-" * 70)
        print(f"  {'Bucket':<12} {'Total':>6} {'High':>6} {'Med':>6} {'Low':>6} {'Ready':>6}")
        print("-" * 70)
        for bucket, stats in bucket_stats.items():
            if stats["total"] > 0:
                print(
                    f"  {bucket:<12} {stats['total']:>6} "
                    f"{stats['high']:>6} {stats['medium']:>6} {stats['low']:>6} "
                    f"{stats['draft_ready']:>6}"
                )
        print("=" * 70)
