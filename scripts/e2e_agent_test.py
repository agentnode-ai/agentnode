"""E2E test: install and run all 30 agents on production server.

Usage:
    OPENROUTER_API_KEY=sk-... python scripts/e2e_agent_test.py

Requires agentnode-sdk>=0.5.0 and openai package (for OpenRouter).
"""
import json
import os
import sys
import time
import traceback
from pathlib import Path

# Ensure ~/.agentnode/.env is loaded
env_path = Path.home() / ".agentnode" / ".env"
if env_path.is_file():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        eq = line.find("=")
        if eq > 0:
            key, val = line[:eq].strip(), line[eq + 1:].strip()
            if key and key not in os.environ:
                os.environ[key] = val

OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
if not OPENROUTER_KEY:
    print("ERROR: Set OPENROUTER_API_KEY")
    sys.exit(1)

# Create OpenRouter LLM client
try:
    from openai import OpenAI
    llm_client = OpenAI(
        api_key=OPENROUTER_KEY,
        base_url="https://openrouter.ai/api/v1",
    )
    LLM = {"client": llm_client, "provider": "openai", "model": "anthropic/claude-sonnet-4-6"}
except ImportError:
    print("ERROR: pip install openai")
    sys.exit(1)

from agentnode_sdk.client import AgentNodeClient
from agentnode_sdk.runtimes.agent_runner import run_agent
from agentnode_sdk.installer import read_lockfile

# Bypass policy checks for E2E testing (all packages are first-party)
from agentnode_sdk import policy as _policy
from agentnode_sdk.policy import PolicyResult
_orig_check_run = _policy.check_run
_orig_check_install = _policy.check_install
def _allow_all_run(*a, **kw):
    return PolicyResult(action="allow", reason="e2e_test_bypass", source="test")
def _allow_all_install(*a, **kw):
    return PolicyResult(action="allow", reason="e2e_test_bypass", source="test")
_policy.check_run = _allow_all_run
_policy.check_install = _allow_all_install

client = AgentNodeClient()

# Test goals per agent
GOALS = {
    "academic-research-agent": "Find papers about transformer architectures published in 2025",
    "api-design-agent": "Design a REST API for a todo list application with CRUD operations",
    "blog-writer-agent": "Write a short blog post about the benefits of remote work",
    "ci-cd-agent": "Analyze a Python Flask project and suggest a GitHub Actions CI pipeline",
    "cloud-cost-agent": "Analyze cloud spending patterns and suggest 3 optimization strategies",
    "code-review-agent": "Review this Python code: def add(a,b): return a+b",
    "competitive-intel-agent": "Compare Notion vs Obsidian as note-taking tools",
    "contract-review-agent": "Review a standard NDA agreement and flag any unusual clauses",
    "crm-enrichment-agent": "Enrich contact info for John Smith at Acme Corp",
    "csv-analyst-agent": "Describe the structure and key patterns in a sales dataset",
    "data-pipeline-agent": "Design a pipeline to clean and transform CSV sales data",
    "deep-research-agent": "Research the current state of quantum computing in 2026",
    "dependency-audit-agent": "Audit Python dependencies: requests==2.28.0, flask==2.3.0",
    "deployment-agent": "Create a deployment checklist for a Node.js application",
    "email-triage-agent": "Triage this email: Subject: Urgent server outage, Body: Production DB is down since 3am",
    "fact-check-agent": "Fact check: Python is the most popular programming language in 2026",
    "log-investigator-agent": "Analyze these logs: ERROR 2026-04-01 DB connection timeout, WARN 2026-04-01 High memory usage",
    "meeting-prep-agent": "Prepare for a meeting about Q2 product roadmap with the engineering team",
    "news-digest-agent": "Summarize the latest news about artificial intelligence",
    "newsletter-agent": "Write a weekly tech newsletter about AI agent developments",
    "project-planner-agent": "Plan a 2-week sprint for building a user authentication system",
    "report-generator-agent": "Generate a summary report about team productivity metrics",
    "security-scanner-agent": "Scan this code for vulnerabilities: import os; os.system(input())",
    "seo-research-agent": "Analyze SEO opportunities for a SaaS product landing page",
    "social-media-agent": "Create 3 social media posts about a new AI product launch",
    "spreadsheet-auditor-agent": "Audit a spreadsheet with 100 rows of financial data for errors",
    "sql-report-agent": "Generate SQL to find the top 10 customers by revenue",
    "technical-docs-agent": "Generate API docs for a Python function: def calculate_price(items, tax_rate)",
    "test-writer-agent": "Write unit tests for: def fibonacci(n): return n if n<=1 else fibonacci(n-1)+fibonacci(n-2)",
    "website-monitor-agent": "Check if example.com is accessible and extract its title",
}

results = []
total = len(GOALS)

print(f"\n{'='*60}")
print(f" E2E Agent Test — {total} agents")
print(f" LLM: OpenRouter (claude-sonnet-4-6)")
print(f"{'='*60}\n")

for i, (slug, goal) in enumerate(sorted(GOALS.items()), 1):
    print(f"[{i}/{total}] {slug}")
    print(f"  Goal: {goal[:70]}...")

    t0 = time.time()
    status = "unknown"
    error_msg = ""
    result_summary = ""

    try:
        # Install
        install_result = client.install(slug)
        if not install_result.installed and not install_result.already_installed:
            status = "install_failed"
            error_msg = install_result.message
            print(f"  FAIL (install): {error_msg}")
            results.append({"slug": slug, "status": status, "error": error_msg, "duration": 0})
            continue

        # Read lockfile entry
        lockfile = read_lockfile()
        entry = lockfile.get("packages", {}).get(slug, {})

        if not entry:
            status = "no_lockfile_entry"
            error_msg = "Installed but not in lockfile"
            print(f"  FAIL: {error_msg}")
            results.append({"slug": slug, "status": status, "error": error_msg, "duration": 0})
            continue

        # Run
        run_result = run_agent(slug, entry=entry, goal=goal, llm=LLM, timeout=120)
        duration = round(time.time() - t0, 1)

        if run_result.success:
            status = "passed"
            if isinstance(run_result.result, dict):
                keys = list(run_result.result.keys())[:5]
                result_summary = f"keys={keys}"
            else:
                result_summary = str(run_result.result)[:100]
            print(f"  OK ({duration}s) — {result_summary}")
        else:
            status = "run_failed"
            error_msg = run_result.error or "unknown"
            print(f"  FAIL ({duration}s): {error_msg[:120]}")

    except Exception as exc:
        duration = round(time.time() - t0, 1)
        status = "exception"
        error_msg = f"{type(exc).__name__}: {exc}"
        print(f"  EXCEPTION ({duration}s): {error_msg[:120]}")
        if "--verbose" in sys.argv:
            traceback.print_exc()

    results.append({
        "slug": slug,
        "status": status,
        "error": error_msg,
        "result_summary": result_summary,
        "duration": duration,
    })

    # Small delay between agents
    time.sleep(2)

# Summary
print(f"\n{'='*60}")
print(f" RESULTS")
print(f"{'='*60}")

passed = [r for r in results if r["status"] == "passed"]
failed = [r for r in results if r["status"] != "passed"]

print(f"\n  Passed: {len(passed)}/{total}")
print(f"  Failed: {len(failed)}/{total}")

if failed:
    print(f"\n  Failures:")
    for r in failed:
        print(f"    {r['slug']}: {r['status']} — {r['error'][:100]}")

# Save results
out_path = Path.home() / ".agentnode" / "e2e-results.json"
out_path.write_text(json.dumps(results, indent=2))
print(f"\n  Full results: {out_path}")
print(f"{'='*60}\n")

sys.exit(1 if failed else 0)
