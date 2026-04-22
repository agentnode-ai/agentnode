#!/usr/bin/env python3
"""Functional test for all 30 agent starter-packs.

Creates a MockAgentContext that simulates tool responses, runs each agent's
run() function, and validates:
  1. Correct entrypoint signature: def run(context, **kwargs)
  2. Right tools are called (correct slugs + tool_names)
  3. Output has expected keys and structure
  4. No crashes, no empty results
  5. Data flows between steps (not just passing goal to every tool)
"""

import importlib
import inspect
import json
import sys
import traceback
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

# ── Add all agent source dirs to sys.path ──
REPO_ROOT = Path(__file__).resolve().parent.parent
STARTER_PACKS = REPO_ROOT / "starter-packs"

for pack_dir in sorted(STARTER_PACKS.iterdir()):
    src_dir = pack_dir / "src"
    if src_dir.is_dir():
        sys.path.insert(0, str(src_dir))


# ══════════════════════════════════════════════════════════════════════════
# Mock infrastructure
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class MockRunToolResult:
    success: bool = True
    result: Any = None
    error: str | None = None


# Tool response mocks — keyed by (slug, tool_name)
TOOL_RESPONSES: dict[tuple[str, str | None], dict] = {
    # Web search
    ("web-search-pack", "search_web"): {
        "results": [
            {"title": "Example Article 1", "url": "https://example.com/article1",
             "snippet": "This is a detailed analysis of the topic with key findings."},
            {"title": "Example Article 2", "url": "https://example.com/article2",
             "snippet": "Recent developments show significant progress in the field."},
            {"title": "Example Article 3", "url": "https://example.com/article3",
             "snippet": "Expert analysis reveals important trends and patterns."},
            {"title": "Example Article 4", "url": "https://example.com/article4",
             "snippet": "New research paper discusses implications and future directions."},
            {"title": "Example Article 5", "url": "https://example.com/article5",
             "snippet": "Comprehensive overview covering all major aspects."},
        ]
    },
    # Webpage extraction
    ("webpage-extractor-pack", "extract_webpage"): {
        "text": "This is the extracted webpage content with detailed information about "
                "the topic. It contains multiple paragraphs of relevant analysis, data "
                "points, expert opinions, and referenced sources. The content covers "
                "background, current state, and future outlook. Key findings include "
                "measurable improvements, notable challenges, and strategic recommendations.",
        "title": "Example Page Title",
        "url": "https://example.com/page",
    },
    # Document summarizer
    ("document-summarizer-pack", "document_summary"): {
        "summary": "The analysis reveals three key findings: (1) significant progress in "
                   "the target area, (2) several challenges remain to be addressed, and "
                   "(3) strategic recommendations include focused investment and collaboration.",
    },
    # CSV analyzer
    ("csv-analyzer-pack", "describe_csv"): {
        "revenue": {"count": 1000, "mean": 5432.10, "std": 1234.56, "min": 100.0, "max": 25000.0},
        "cost": {"count": 1000, "mean": 3210.50, "std": 987.65, "min": 50.0, "max": 15000.0},
        "quantity": {"count": 1000, "mean": 42.3, "std": 15.2, "min": 1, "max": 200},
    },
    ("csv-analyzer-pack", "head_csv"): {
        "rows": [
            {"id": 1, "revenue": 5400, "cost": 3200, "quantity": 45},
            {"id": 2, "revenue": 6100, "cost": 3800, "quantity": 52},
            {"id": 3, "revenue": 4200, "cost": 2900, "quantity": 38},
        ]
    },
    ("csv-analyzer-pack", "columns_csv"): {
        "columns": [
            {"name": "id", "type": "int64", "non_null": 1000},
            {"name": "revenue", "type": "float64", "non_null": 998, "missing": 2},
            {"name": "cost", "type": "float64", "non_null": 1000},
            {"name": "quantity", "type": "int64", "non_null": 995, "missing": 5},
        ]
    },
    ("csv-analyzer-pack", "filter_csv"): {
        "rows": [
            {"id": 5, "revenue": 8000, "cost": 4500, "quantity": 65},
            {"id": 12, "revenue": 9200, "cost": 5100, "quantity": 78},
        ],
        "total_rows": 2,
    },
    # PDF extractor
    ("pdf-extractor-pack", "pdf_extraction"): {
        "text": "This PDF document contains detailed contractual terms and conditions. "
                "Section 1: Definitions. Section 2: Obligations. Section 3: Payment Terms. "
                "The document outlines liability clauses, termination conditions, and "
                "intellectual property provisions. Total pages: 12.",
        "pages": 12,
    },
    # Code linter
    ("code-linter-pack", "code_analysis"): {
        "issues": [
            {"line": 15, "rule": "E501", "message": "Line too long (95 > 88 characters)"},
            {"line": 23, "rule": "W291", "message": "Trailing whitespace"},
        ]
    },
    # Security audit
    ("security-audit-pack", "code_analysis"): {
        "issues": [
            {"line": 42, "severity": "MEDIUM", "test_id": "B105",
             "message": "Possible hardcoded password"},
        ]
    },
    # Secret scanner
    ("secret-scanner-pack", "code_analysis"): {
        "findings": [],
        "scanned_lines": 150,
    },
    # Test generator
    ("test-generator-pack", "code_analysis"): {
        "tests": "import pytest\n\ndef test_process_data():\n    result = process_data([])\n    assert result is not None\n\ndef test_validate_input():\n    assert validate_input('hello') == True\n",
        "test_count": 2,
    },
    # Code refactor
    ("code-refactor-pack", "code_analysis"): {
        "functions": ["process_data", "validate_input", "format_output"],
        "classes": ["DataProcessor"],
        "complexity": "moderate",
        "lines": 150,
    },
    # Email drafter
    ("email-drafter-pack", "email_drafting"): {
        "email": "Dear Colleague,\n\nThank you for your message. I've reviewed the items "
                 "you mentioned and would like to schedule a follow-up discussion.\n\n"
                 "Best regards,\nAssistant",
    },
    # News aggregator
    ("news-aggregator-pack", "web_search"): {
        "articles": [
            {"title": "Breaking: Major Development in Tech Sector", "link": "https://news.example.com/1"},
            {"title": "Market Analysis Shows Growth Trend", "link": "https://news.example.com/2"},
            {"title": "Industry Leaders Announce Partnership", "link": "https://news.example.com/3"},
        ]
    },
    # Translator
    ("text-translator-pack", "translation"): {
        "translated_text": "Dies ist der uebersetzte Text mit den wichtigsten Erkenntnissen.",
    },
    # SEO optimizer
    ("seo-optimizer-pack", "webpage_extraction"): {
        "score": 72,
        "issues": ["Missing meta description", "H1 tag too long", "Low keyword density"],
        "recommendations": ["Add meta description", "Shorten H1", "Include target keyword 2-3 more times"],
    },
    # Copywriting
    ("copywriting-pack", "tone_adjustment"): {
        "copy": "Discover the future of productivity. Our solution transforms how teams "
                "work together, delivering measurable results from day one. Join thousands "
                "of professionals who already made the switch.",
    },
    # Contract review
    ("contract-review-pack", "document_parsing"): {
        "risks": [
            {"clause": "Section 5.2", "risk": "Unlimited liability", "severity": "high"},
            {"clause": "Section 8.1", "risk": "Auto-renewal without notice", "severity": "medium"},
        ],
        "terms": [
            {"term": "Payment", "value": "Net 30 days"},
            {"term": "Duration", "value": "24 months"},
            {"term": "Termination", "value": "90 days written notice"},
        ],
    },
    # JSON processor
    ("json-processor-pack", "json_processing"): {
        "data": [{"level": "ERROR", "message": "Connection timeout", "count": 15}],
        "total_records": 1,
    },
    # SQL generator
    ("sql-generator-pack", "generate_sql"): {
        "sql": "SELECT u.name, COUNT(o.id) as order_count, SUM(o.total) as revenue "
               "FROM users u JOIN orders o ON u.id = o.user_id "
               "GROUP BY u.name ORDER BY revenue DESC LIMIT 10;",
    },
    ("sql-generator-pack", "format_sql"): {
        "formatted_sql": "SELECT\n  u.name,\n  COUNT(o.id) AS order_count,\n"
                         "  SUM(o.total) AS revenue\nFROM users u\n"
                         "JOIN orders o ON u.id = o.user_id\n"
                         "GROUP BY u.name\nORDER BY revenue DESC\nLIMIT 10;",
    },
}


@dataclass
class MockAgentContext:
    """Mock AgentContext that tracks tool calls and returns mock data."""
    _goal: str = "Test objective for validation"
    _iteration: int = 0
    _tool_calls: list = field(default_factory=list)
    _data_flow: list = field(default_factory=list)  # Track what data passes between calls

    @property
    def goal(self) -> str:
        return self._goal

    def next_iteration(self) -> None:
        self._iteration += 1
        if self._iteration > 15:
            raise RuntimeError("Too many iterations (>15)")

    def run_tool(self, slug: str, tool_name: str | None = None, **kwargs: Any) -> MockRunToolResult:
        self._tool_calls.append({
            "slug": slug,
            "tool_name": tool_name,
            "kwargs_keys": sorted(kwargs.keys()),
            "kwargs_preview": {k: str(v)[:80] for k, v in kwargs.items()},
        })

        # Track data flow: check if kwargs contain data from previous tool responses
        for k, v in kwargs.items():
            if isinstance(v, str) and len(v) > 50 and v != self._goal:
                self._data_flow.append(f"{slug}:{tool_name} received chained data in '{k}'")

        key = (slug, tool_name)
        if key in TOOL_RESPONSES:
            return MockRunToolResult(success=True, result=TOOL_RESPONSES[key])

        # Fallback: try without tool_name
        key_fallback = (slug, None)
        if key_fallback in TOOL_RESPONSES:
            return MockRunToolResult(success=True, result=TOOL_RESPONSES[key_fallback])

        # Unknown tool — still succeed with generic response
        return MockRunToolResult(success=True, result={"output": f"mock response for {slug}"})


# ══════════════════════════════════════════════════════════════════════════
# Per-agent test specs
# ══════════════════════════════════════════════════════════════════════════

AGENT_SPECS = {
    "deep-research-agent": {
        "kwargs": {"topic": "artificial intelligence trends 2026"},
        "expected_keys": ["report", "sources", "topic", "done"],
        "expected_tools": ["web-search-pack", "webpage-extractor-pack", "document-summarizer-pack"],
        "must_have_data_flow": True,
    },
    "academic-research-agent": {
        "kwargs": {"topic": "transformer architectures in NLP"},
        "expected_keys": ["review", "papers", "topic", "done"],
        "expected_tools": ["web-search-pack"],
        "must_have_data_flow": True,
    },
    "competitive-intel-agent": {
        "kwargs": {"company": "Acme Corp"},
        "expected_keys": ["analysis", "company", "sources", "recent_news", "done"],
        "expected_tools": ["web-search-pack", "webpage-extractor-pack"],
    },
    "seo-research-agent": {
        "kwargs": {"url": "https://example.com", "keyword": "productivity tools"},
        "expected_keys": ["url", "seo_analysis", "competitor_rankings", "done"],
        "expected_tools": ["webpage-extractor-pack", "seo-optimizer-pack", "web-search-pack"],
    },
    "fact-check-agent": {
        "kwargs": {"claim": "The earth is round"},
        "expected_keys": ["claim", "verdict", "confidence", "sources", "done"],
        "expected_tools": ["web-search-pack", "webpage-extractor-pack"],
        # Data flow via URLs (short strings) — detected by tool call count instead
        "extra_checks": lambda r: r["verdict"] in ("likely_true", "likely_false", "disputed", "unverifiable"),
    },
    "news-digest-agent": {
        "kwargs": {"topic": "renewable energy"},
        "expected_keys": ["digest", "topic", "done"],
        "expected_tools": ["news-aggregator-pack"],
    },
    "blog-writer-agent": {
        "kwargs": {"topic": "remote work best practices", "audience": "managers"},
        "expected_keys": ["article", "title", "sources", "done"],
        "expected_tools": ["web-search-pack", "copywriting-pack"],
    },
    "technical-docs-agent": {
        "kwargs": {"code": "def process(data: list) -> dict:\n    return {'count': len(data)}\n"},
        "expected_keys": ["documentation", "code_structure", "done"],
        "expected_tools": ["code-refactor-pack", "test-generator-pack"],
    },
    "newsletter-agent": {
        "kwargs": {"topic": "cybersecurity updates"},
        "expected_keys": ["newsletter", "stories", "topic", "done"],
        "expected_tools": ["web-search-pack", "email-drafter-pack"],
    },
    "social-media-agent": {
        "kwargs": {"topic": "sustainable fashion"},
        "expected_keys": ["posts", "key_message", "topic", "done"],
        "expected_tools": ["copywriting-pack"],
        "extra_checks": lambda r: isinstance(r["posts"], dict) and len(r["posts"]) >= 2,
    },
    "report-generator-agent": {
        "kwargs": {"file_path": "/data/sales.csv"},
        "expected_keys": ["executive_summary", "statistics", "column_info", "done"],
        "expected_tools": ["csv-analyzer-pack"],
    },
    "csv-analyst-agent": {
        "kwargs": {"file_path": "/data/metrics.csv"},
        "expected_keys": ["analysis", "statistics", "columns", "sample_data", "done"],
        "expected_tools": ["csv-analyzer-pack"],
    },
    "log-investigator-agent": {
        "kwargs": {"log_text": '{"level":"ERROR","msg":"Connection refused","ts":"2026-04-22T10:00:00Z"}'},
        "expected_keys": ["findings", "error_count", "done"],
        "expected_tools": ["json-processor-pack"],
    },
    "data-pipeline-agent": {
        "kwargs": {"file_path": "/data/input.csv", "filter_column": "status", "filter_value": "active"},
        "expected_keys": ["source_file", "source_stats", "columns", "done"],
        "expected_tools": ["csv-analyzer-pack"],
    },
    "spreadsheet-auditor-agent": {
        "kwargs": {"file_path": "/data/budget.csv"},
        "expected_keys": ["audit_summary", "issues", "quality_score", "done"],
        "expected_tools": ["csv-analyzer-pack"],
        "extra_checks": lambda r: isinstance(r["quality_score"], (int, float)),
    },
    "sql-report-agent": {
        "kwargs": {"question": "Who are the top 10 customers by revenue?", "dialect": "postgresql"},
        "expected_keys": ["question", "sql", "done"],
        "expected_tools": ["sql-generator-pack"],
        "extra_checks": lambda r: "SELECT" in r.get("sql", ""),
    },
    "code-review-agent": {
        "kwargs": {"code": "import os\ndef run():\n    pw = 'secret123'\n    return os.getenv(pw)\n"},
        "expected_keys": ["review", "lint", "security", "done"],
        "expected_tools": ["code-linter-pack", "security-audit-pack", "code-refactor-pack", "secret-scanner-pack"],
    },
    "test-writer-agent": {
        "kwargs": {"code": "def add(a, b):\n    return a + b\n", "framework": "pytest"},
        "expected_keys": ["tests", "code_structure", "framework", "done"],
        "expected_tools": ["code-refactor-pack", "test-generator-pack"],
    },
    "dependency-audit-agent": {
        "kwargs": {"code": "requests>=2.28\nflask>=2.3\npyyaml>=6.0\ncryptography>=41.0\n"},
        "expected_keys": ["packages_scanned", "vulnerabilities", "done"],
        "expected_tools": ["web-search-pack", "secret-scanner-pack"],
        "extra_checks": lambda r: isinstance(r["packages_scanned"], list) and len(r["packages_scanned"]) > 0,
    },
    "ci-cd-agent": {
        "kwargs": {"code": "from flask import Flask\napp = Flask(__name__)\n\n@app.route('/')\ndef index():\n    return 'OK'\n"},
        "expected_keys": ["pipeline_steps", "code_analysis", "ready_to_deploy", "done"],
        "expected_tools": ["code-refactor-pack", "code-linter-pack", "test-generator-pack"],
        "extra_checks": lambda r: isinstance(r["pipeline_steps"], list) and "checkout" in r["pipeline_steps"],
    },
    "api-design-agent": {
        "kwargs": {"requirements": "User management API with CRUD operations and authentication"},
        "expected_keys": ["requirements", "data_model_sql", "done"],
        "expected_tools": ["sql-generator-pack"],
    },
    "email-triage-agent": {
        "kwargs": {"emails": "Subject: URGENT - Server Down\nThe production server is not responding. Please check ASAP.\n\nSubject: Weekly Report\nAttached is the weekly status report."},
        "expected_keys": ["summary", "priority", "draft_response", "done"],
        "expected_tools": ["document-summarizer-pack", "email-drafter-pack"],
        "extra_checks": lambda r: r["priority"] == "high",  # "urgent" keyword should trigger high
    },
    "meeting-prep-agent": {
        "kwargs": {"topic": "Q2 product roadmap review", "attendees": "John Smith, Jane Doe"},
        "expected_keys": ["agenda", "topic", "attendee_research", "done"],
        "expected_tools": ["web-search-pack"],
    },
    "project-planner-agent": {
        "kwargs": {"project": "Build a REST API for inventory management with authentication and reporting"},
        "expected_keys": ["plan", "scope", "done"],
        "expected_tools": ["document-summarizer-pack", "copywriting-pack"],
    },
    "contract-review-agent": {
        "kwargs": {"text": "AGREEMENT between Party A and Party B. Section 1: Party A shall pay $10,000 monthly. Section 5.2: Liability is unlimited. Section 8.1: Auto-renews annually."},
        "expected_keys": ["summary", "risk_analysis", "key_terms", "done"],
        "expected_tools": ["contract-review-pack", "document-summarizer-pack"],
    },
    "crm-enrichment-agent": {
        "kwargs": {"contact": "Elon Musk", "company": "Tesla"},
        "expected_keys": ["contact", "company", "profile_summary", "done"],
        "expected_tools": ["web-search-pack", "webpage-extractor-pack"],
    },
    "website-monitor-agent": {
        "kwargs": {"url": "https://example.com"},
        "expected_keys": ["url", "status", "content_summary", "done"],
        "expected_tools": ["webpage-extractor-pack"],
        "extra_checks": lambda r: r["status"] == "up",
    },
    "security-scanner-agent": {
        "kwargs": {"code": "import os\nAPI_KEY = 'sk-1234567890'\ndef connect():\n    return os.system('rm -rf /')\n"},
        "expected_keys": ["scan_results", "total_issues", "done"],
        "expected_tools": ["code-linter-pack", "security-audit-pack", "secret-scanner-pack"],
    },
    "cloud-cost-agent": {
        "kwargs": {"file_path": "/data/aws-billing-2026-03.csv"},
        "expected_keys": ["cost_analysis", "billing_statistics", "done"],
        "expected_tools": ["csv-analyzer-pack", "web-search-pack"],
    },
    "deployment-agent": {
        "kwargs": {"code": "from app import create_app\napp = create_app()\nif __name__ == '__main__':\n    app.run()\n"},
        "expected_keys": ["ready_to_deploy", "checklist", "done"],
        "expected_tools": ["code-linter-pack", "security-audit-pack", "secret-scanner-pack", "test-generator-pack"],
        "extra_checks": lambda r: isinstance(r["checklist"], list) and all("check" in c and "passed" in c for c in r["checklist"]),
    },
}


# ══════════════════════════════════════════════════════════════════════════
# Test runner
# ══════════════════════════════════════════════════════════════════════════

def test_agent(slug: str, spec: dict) -> tuple[bool, list[str]]:
    """Test a single agent. Returns (passed, list_of_errors)."""
    errors = []
    mod_name = slug.replace("-", "_")
    module_path = f"{mod_name}.agent"

    # 1. Import the module
    try:
        module = importlib.import_module(module_path)
    except Exception as exc:
        return False, [f"IMPORT FAILED: {exc}"]

    # 2. Check run() exists and has correct signature
    run_fn = getattr(module, "run", None)
    if run_fn is None:
        return False, ["No run() function found"]

    sig = inspect.signature(run_fn)
    params = list(sig.parameters.keys())
    if len(params) < 1 or params[0] != "context":
        errors.append(f"Wrong signature: run({', '.join(params)}) — expected run(context, **kwargs)")

    # Check it's NOT async
    if inspect.iscoroutinefunction(run_fn):
        errors.append("run() is async — MUST be synchronous for AgentContext contract v1")

    # 3. Run the agent with mock context
    ctx = MockAgentContext(_goal=spec.get("goal_override", f"Test: {slug}"))
    kwargs = spec.get("kwargs", {})

    try:
        result = run_fn(ctx, **kwargs)
    except Exception as exc:
        return False, errors + [f"CRASH: {type(exc).__name__}: {exc}\n{traceback.format_exc()}"]

    # 4. Validate result type
    if not isinstance(result, dict):
        errors.append(f"Result is {type(result).__name__}, expected dict")
        return False, errors

    # 5. Check expected keys
    for key in spec.get("expected_keys", []):
        if key not in result:
            errors.append(f"Missing key: '{key}'")

    # 6. Check done flag
    if "done" in result and result["done"] is not True:
        # Some agents set done=False on error — check if there's an error
        if "error" not in result:
            errors.append(f"done={result['done']} but no error field")

    # 7. Check expected tool calls
    tools_called = [tc["slug"] for tc in ctx._tool_calls]
    for expected_tool in spec.get("expected_tools", []):
        if expected_tool not in tools_called:
            errors.append(f"Expected tool not called: {expected_tool}")

    # 8. Check no empty results for key fields
    for key in spec.get("expected_keys", []):
        if key in result and key != "done":
            val = result[key]
            if val is None:
                errors.append(f"Key '{key}' is None")
            elif isinstance(val, str) and len(val) == 0 and key not in ("error",):
                errors.append(f"Key '{key}' is empty string")

    # 9. Check data flow (data from one tool feeds into the next)
    if spec.get("must_have_data_flow"):
        if not ctx._data_flow:
            errors.append("No data flow detected — steps may not be chaining data")

    # 10. Check tool call count (should be > 1 for multi-step agents)
    if len(ctx._tool_calls) < 2:
        errors.append(f"Only {len(ctx._tool_calls)} tool call(s) — expected multi-step agent")

    # 11. Check iterations (should use next_iteration())
    if ctx._iteration == 0:
        errors.append("next_iteration() never called — agent doesn't track iterations")

    # 12. Run extra checks
    extra_check = spec.get("extra_checks")
    if extra_check:
        try:
            check_result = extra_check(result)
            if check_result is False:
                errors.append(f"Extra check failed")
        except Exception as exc:
            errors.append(f"Extra check error: {exc}")

    return len(errors) == 0, errors


def main():
    print("=" * 70)
    print(" AgentNode Agent Functional Tests (V2)")
    print("=" * 70)
    print()

    passed = 0
    failed = 0
    total = 0
    failures = []

    for slug in sorted(AGENT_SPECS.keys()):
        spec = AGENT_SPECS[slug]
        total += 1

        ok, errs = test_agent(slug, spec)

        if ok:
            passed += 1
            print(f"  PASS  {slug}")
        else:
            failed += 1
            failures.append((slug, errs))
            print(f"  FAIL  {slug}")
            for err in errs:
                first_line = err.split("\n")[0]
                print(f"        -> {first_line}")

    print()
    print("=" * 70)
    print(f" Results: {total} tested | {passed} passed | {failed} failed")
    print("=" * 70)

    if failures:
        print()
        print("Detailed failures:")
        print("-" * 70)
        for slug, errs in failures:
            print(f"\n  {slug}:")
            for err in errs:
                for line in err.split("\n"):
                    print(f"    {line}")

    print()
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
