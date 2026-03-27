"""External smoke test suite — 40 fixtures across 5 categories.

Tests the importer against realistic tool snippets:
- LangChain happy path (8 fixtures, expect high)
- LangChain edge cases (8 fixtures, mix of high/medium)
- CrewAI (8 fixtures, expect high)
- Blockers / negative cases (6 fixtures, expect low)
- Real-world stress tests (10 fixtures, from GitHub issues/repos/docs)

Run with: pytest tests/import_smoke/ -v
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.import_.schemas import ConvertRequest
from app.import_.service import convert

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load(filename: str) -> str:
    return (FIXTURES_DIR / filename).read_text(encoding="utf-8")


def _get_tool_py(response) -> str:
    for f in response.code_files:
        if f.path.endswith("tool.py"):
            return f.content
    return ""


# ── Expectations ────────────────────────────────────────────────────
# Each entry: (filename, platform, expected_dict)
# Expected keys: confidence, draft_ready, min_tools (optional)

EXPECTATIONS = [
    # ── LangChain happy path ──
    ("lc01_simple_dict_tool.py", "langchain", {"confidence": "high", "draft_ready": True, "min_tools": 1}),
    ("lc02_tool_with_defaults.py", "langchain", {"confidence": "high", "draft_ready": True, "min_tools": 1}),
    ("lc03_two_tools_shared_file.py", "langchain", {"confidence": "high", "draft_ready": True, "min_tools": 2, "manifest_version": "0.2"}),
    ("lc04_helper_function_used.py", "langchain", {"confidence": "high", "draft_ready": True, "min_tools": 1}),
    ("lc05_module_constants_used.py", "langchain", {"confidence": "high", "draft_ready": True, "min_tools": 1}),
    ("lc06_nested_import_in_tool.py", "langchain", {"confidence": "high", "draft_ready": True, "min_tools": 1}),
    ("lc07_basetool_dict_return.py", "langchain", {"confidence": "high", "draft_ready": True, "min_tools": 1}),
    ("lc08_basetool_str_return.py", "langchain", {"confidence": "medium", "draft_ready": True, "min_tools": 1}),

    # ── LangChain edge cases ──
    ("lc09_args_schema_pydantic.py", "langchain", {"confidence": "high", "draft_ready": True, "min_tools": 1}),
    ("lc10_env_var_usage.py", "langchain", {"confidence": "high", "draft_ready": True, "min_tools": 1}),
    ("lc11_try_except_optional_import.py", "langchain", {"confidence": "high", "draft_ready": True, "min_tools": 1}),
    ("lc12_unknown_import_unused.py", "langchain", {"confidence": "medium", "draft_ready": True, "min_tools": 1}),
    ("lc13_missing_type_hints.py", "langchain", {"confidence": "medium", "draft_ready": True, "min_tools": 1}),
    ("lc14_single_unresolved_symbol.py", "langchain", {"confidence": "medium", "draft_ready": True, "min_tools": 1}),
    ("lc15_helper_class_used.py", "langchain", {"confidence": "high", "draft_ready": True, "min_tools": 1}),
    ("lc16_lambda_usage_in_helper.py", "langchain", {"confidence": "high", "draft_ready": True, "min_tools": 1}),

    # ── CrewAI ──
    ("cr01_simple_named_tool.py", "crewai", {"confidence": "high", "draft_ready": True, "min_tools": 1}),
    ("cr02_no_arg_decorator.py", "crewai", {"confidence": "high", "draft_ready": True, "min_tools": 1}),
    ("cr03_tool_with_defaults.py", "crewai", {"confidence": "high", "draft_ready": True, "min_tools": 1}),
    ("cr04_tool_with_helper_function.py", "crewai", {"confidence": "high", "draft_ready": True, "min_tools": 1}),
    ("cr05_tool_with_constants.py", "crewai", {"confidence": "high", "draft_ready": True, "min_tools": 1}),
    ("cr06_requests_dependency.py", "crewai", {"confidence": "high", "draft_ready": True, "min_tools": 1, "deps_contain": ["requests"]}),
    ("cr07_file_io_tool.py", "crewai", {"confidence": "high", "draft_ready": True, "min_tools": 1}),
    ("cr08_env_var_tool.py", "crewai", {"confidence": "high", "draft_ready": True, "min_tools": 1}),

    # ── Blockers ──
    ("blk01_langchain_async_tool.py", "langchain", {"confidence": "low", "draft_ready": False, "min_tools": 1}),
    ("blk02_crewai_async_tool.py", "crewai", {"confidence": "low", "draft_ready": False, "min_tools": 1}),
    ("blk03_basetool_self_usage.py", "langchain", {"confidence": "low", "draft_ready": False, "min_tools": 1}),
    ("blk04_relative_import_langchain.py", "langchain", {"confidence": "low", "draft_ready": False, "min_tools": 1}),
    ("blk05_structured_tool_from_function.py", "langchain", {"confidence": "low", "draft_ready": False}),
    ("blk06_unknown_import_active.py", "langchain", {"confidence": "low", "draft_ready": False, "min_tools": 1}),

    # ── Real-world stress tests (from GitHub issues/repos/docs) ──
    # rw01: StructuredTool.from_function() in loop — no extractable pattern
    ("rw01_structured_tool_dynamic.py", "langchain", {"confidence": "low", "draft_ready": False}),
    # rw02: BaseTool with List[str] return + BaseTool with str return (2 tools)
    ("rw02_basetool_list_return.py", "langchain", {"confidence": "medium", "draft_ready": True, "min_tools": 2}),
    # rw03: BaseTool with args_schema, clean _run body, requests dep
    ("rw03_basetool_args_schema.py", "langchain", {"confidence": "high", "draft_ready": True, "min_tools": 1, "deps_contain": ["requests"]}),
    # rw04: Tool() constructor pattern (not @tool, not BaseTool) — no extractable pattern
    ("rw04_tool_constructor_langchain.py", "langchain", {"confidence": "low", "draft_ready": False}),
    # rw05: CrewAI BaseTool with Field(default_factory) + self.draft_creator
    ("rw05_crewai_basetool_field_factory.py", "crewai", {"confidence": "low", "draft_ready": False, "min_tools": 1}),
    # rw06: CrewAI crew.py with Agent/Task/Crew — no tool definitions at all
    ("rw06_crewai_crew_no_tools.py", "crewai", {"confidence": "low", "draft_ready": False}),
    # rw07: CrewAI BaseTool wrapping external service via self.search
    ("rw07_crewai_basetool_self_service.py", "crewai", {"confidence": "low", "draft_ready": False, "min_tools": 1}),
    # rw08: Mixed file — CrewAI BaseTool tested as platform=langchain, self.search
    ("rw08_mixed_langchain_crewai.py", "langchain", {"confidence": "low", "draft_ready": False, "min_tools": 1}),
    # rw09: StructuredTool direct instantiation (not from_function) — no extractable pattern
    ("rw09_structured_tool_direct.py", "langchain", {"confidence": "low", "draft_ready": False}),
    # rw10: @tool function with Agent setup noise in same file, str return wrapped
    ("rw10_crewai_tool_with_agent.py", "crewai", {"confidence": "medium", "draft_ready": True, "min_tools": 1}),
]


# ── Parametrized test ───────────────────────────────────────────────

@pytest.mark.parametrize(
    "filename,platform,expected",
    EXPECTATIONS,
    ids=[e[0].replace(".py", "") for e in EXPECTATIONS],
)
def test_smoke_fixture(filename: str, platform: str, expected: dict):
    source = _load(filename)
    resp = convert(ConvertRequest(platform=platform, content=source))

    # Core assertions
    assert resp.confidence.level == expected["confidence"], (
        f"Expected confidence={expected['confidence']}, got {resp.confidence.level}. "
        f"Reasons: {resp.confidence.reasons}"
    )
    assert resp.draft_ready == expected["draft_ready"], (
        f"Expected draft_ready={expected['draft_ready']}, got {resp.draft_ready}. "
        f"Warnings: {resp.warnings}"
    )

    # Tool count
    min_tools = expected.get("min_tools", 0)
    if min_tools:
        assert len(resp.detected_tools) >= min_tools

    # Manifest version
    if "manifest_version" in expected:
        assert resp.manifest_json.get("manifest_version") == expected["manifest_version"]

    # Dependencies contain
    for dep in expected.get("deps_contain", []):
        assert dep in resp.detected_dependencies, (
            f"Expected '{dep}' in dependencies. Got: {resp.detected_dependencies}"
        )

    # For draft-ready fixtures: generated code must be valid
    if expected["draft_ready"]:
        tool_py = _get_tool_py(resp)
        assert tool_py, "No tool.py generated for a draft-ready fixture"
        try:
            ast.parse(tool_py)
        except SyntaxError as e:
            pytest.fail(f"Generated tool.py has syntax error: {e}")
        # No framework imports in generated code
        assert "langchain" not in tool_py, "Framework import 'langchain' in generated tool.py"
        assert "crewai" not in tool_py, "Framework import 'crewai' in generated tool.py"

    # For blockers: must have warnings
    if expected["confidence"] == "low":
        assert len(resp.warnings) > 0, "Low confidence but no warnings"
        blocking = [w for w in resp.grouped_warnings if w.category == "blocking"]
        assert len(blocking) > 0, "Low confidence but no blocking warnings"

    # Grouped warnings must exist
    assert resp.grouped_warnings is not None
    assert len(resp.grouped_warnings) == len(resp.warnings)


# ── Summary report ──────────────────────────────────────────────────

def test_smoke_summary(capsys):
    """Generate a distribution summary across all smoke fixtures."""
    results = {"high": 0, "medium": 0, "low": 0}
    draft_ready_count = 0
    total = 0

    bucket_stats: dict[str, dict] = {
        "LC Happy": {"total": 0, "high": 0, "medium": 0, "low": 0, "draft_ready": 0},
        "LC Edge": {"total": 0, "high": 0, "medium": 0, "low": 0, "draft_ready": 0},
        "CrewAI": {"total": 0, "high": 0, "medium": 0, "low": 0, "draft_ready": 0},
        "Blocker": {"total": 0, "high": 0, "medium": 0, "low": 0, "draft_ready": 0},
        "Real-World": {"total": 0, "high": 0, "medium": 0, "low": 0, "draft_ready": 0},
    }

    def _bucket(filename: str) -> str:
        if filename.startswith("rw"):
            return "Real-World"
        if filename.startswith("blk"):
            return "Blocker"
        if filename.startswith("cr"):
            return "CrewAI"
        num = int(filename.split("_")[0][2:])
        return "LC Happy" if num <= 8 else "LC Edge"

    for filename, platform, _ in EXPECTATIONS:
        source = _load(filename)
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
        print("  IMPORT SMOKE TEST SUMMARY")
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
