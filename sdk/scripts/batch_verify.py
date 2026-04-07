"""Batch-verify tool-calling for all OpenRouter models.

Reads model list from OpenRouter API, runs verify_toolcalls scenarios
for each tool-capable model, collects results into a single report.

Usage:
    OPENROUTER_API_KEY=sk-... python scripts/batch_verify.py
    OPENROUTER_API_KEY=sk-... python scripts/batch_verify.py --max-models 10
    OPENROUTER_API_KEY=sk-... python scripts/batch_verify.py --provider google
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

import httpx
import openai

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentnode_sdk.runtime import AgentNodeRuntime


# ---------------------------------------------------------------------------
# Reuse scenario definitions from verify_toolcalls
# ---------------------------------------------------------------------------

SCENARIOS = [
    {
        "name": "1. Capabilities List",
        "desc": "Model calls agentnode_capabilities to list installed tools",
        "messages": [
            {"role": "user", "content": (
                "Use the agentnode_capabilities tool to list installed tools. "
                "Tell me how many are installed and name them."
            )},
        ],
        "max_rounds": 3,
        "expect_tools": ["agentnode_capabilities"],
    },
    {
        "name": "2. Search + Install",
        "desc": "Model searches for PDF tools, then installs one",
        "messages": [
            {"role": "user", "content": (
                "Search AgentNode for PDF tools. Tell me the top result's slug. "
                "Then install that package."
            )},
        ],
        "max_rounds": 5,
        "expect_tools": ["agentnode_search", "agentnode_install"],
    },
    {
        "name": "3. Run Tool (word counter)",
        "desc": "Model runs word-counter-pack with specific input",
        "messages": [
            {"role": "user", "content": (
                "I need you to execute a tool right now. "
                "Call the agentnode_run tool with these exact parameters: "
                "slug='word-counter-pack', "
                "arguments={'inputs': {'text': 'The quick brown fox jumps over the lazy dog'}}. "
                "This is an installed package — do not search or install, just run it. "
                "Tell me the exact word count from the result."
            )},
        ],
        "max_rounds": 3,
        "expect_tools": ["agentnode_run"],
    },
    {
        "name": "4. Multi-step Autonomous",
        "desc": "Model checks capabilities, finds word counter, runs it",
        "messages": [
            {"role": "user", "content": (
                "First check what AgentNode capabilities are installed. "
                "If a word counter is available, use it to count the words in: "
                "'AgentNode is a verified runtime for AI agents'. "
                "Pass arguments as: {'inputs': {'text': 'AgentNode is a verified runtime for AI agents'}}. "
                "Tell me the word count."
            )},
        ],
        "max_rounds": 8,
        "expect_tools": ["agentnode_capabilities", "agentnode_run"],
    },
]


# ---------------------------------------------------------------------------
# Tool call logger (silent — no print, just collect)
# ---------------------------------------------------------------------------

class SilentToolLogger:
    def __init__(self, runtime: AgentNodeRuntime):
        self.runtime = runtime
        self._original = runtime.handle
        self.log: list[dict] = []
        runtime.handle = self._intercept

    def _intercept(self, tool_name: str, arguments: dict | None = None) -> dict:
        # Log the sanitized name so test grading matches runtime behavior
        clean_name = AgentNodeRuntime._sanitize_tool_name(tool_name)
        t0 = time.monotonic()
        result = self._original(tool_name, arguments)
        elapsed = int((time.monotonic() - t0) * 1000)
        self.log.append({
            "tool": clean_name,
            "arguments": arguments,
            "result_success": result.get("success", False),
            "elapsed_ms": elapsed,
        })
        return result

    def restore(self):
        self.runtime.handle = self._original


# ---------------------------------------------------------------------------
# Extract response text (OpenAI-compatible only)
# ---------------------------------------------------------------------------

def get_text(result: Any) -> str:
    if isinstance(result, dict):
        return result.get("error", {}).get("message", str(result))
    if hasattr(result, "content") and result.content:
        return result.content
    return ""


# ---------------------------------------------------------------------------
# Fetch models from OpenRouter API
# ---------------------------------------------------------------------------

def fetch_models(api_key: str) -> list[dict]:
    resp = httpx.get(
        "https://openrouter.ai/api/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", [])


def filter_tool_models(models: list[dict], provider_filter: str = "") -> list[dict]:
    """Filter to models that support tool calling."""
    result = []
    for m in models:
        sp = m.get("supported_parameters") or []
        if "tools" not in sp and "tool_choice" not in sp:
            continue
        mid = m["id"]
        # Skip variants
        if any(tag in mid for tag in [":free", ":extended", ":beta"]):
            continue
        # Skip non-chat models
        if any(tag in mid for tag in ["preview", "codex", "audio", "-vl-", "image", "thinking", "deep-research"]):
            continue
        # Provider filter
        if provider_filter and not mid.startswith(provider_filter + "/"):
            continue
        result.append(m)
    return result


# ---------------------------------------------------------------------------
# Run all scenarios for a single model
# ---------------------------------------------------------------------------

def test_model(
    client: openai.OpenAI,
    runtime: AgentNodeRuntime,
    model_id: str,
) -> dict:
    """Run all scenarios, return summary dict."""
    model_result = {
        "model": model_id,
        "timestamp": int(time.time()),
        "passed": 0,
        "failed": 0,
        "errors": 0,
        "scenarios": [],
    }

    for scenario in SCENARIOS:
        logger = SilentToolLogger(runtime)
        t0 = time.monotonic()

        try:
            result = runtime.run(
                provider="openai",
                client=client,
                messages=list(scenario["messages"]),
                model=model_id,
                max_tool_rounds=scenario["max_rounds"],
            )
            elapsed = int((time.monotonic() - t0) * 1000)
            text = get_text(result)
            called_tools = [e["tool"] for e in logger.log]
            expected = scenario["expect_tools"]
            all_called = all(t in called_tools for t in expected)
            # Accept empty response text if all expected tools were called
            # (some models execute tools correctly but don't produce a summary)
            has_response = len(text.strip()) > 0 or all_called
            status = "PASS" if all_called and has_response else "FAIL"

            scenario_result = {
                "name": scenario["name"],
                "status": status,
                "tools_called": called_tools,
                "tools_expected": expected,
                "response_length": len(text),
                "duration_ms": elapsed,
            }

            if status == "PASS":
                model_result["passed"] += 1
            else:
                model_result["failed"] += 1

        except Exception as exc:
            elapsed = int((time.monotonic() - t0) * 1000)
            scenario_result = {
                "name": scenario["name"],
                "status": "ERROR",
                "error": str(exc)[:200],
                "duration_ms": elapsed,
            }
            model_result["errors"] += 1

        finally:
            logger.restore()

        model_result["scenarios"].append(scenario_result)

    # Detect API errors: identical response lengths across all scenarios
    # with zero tool calls indicates a provider-level error, not a model failure
    scenarios = model_result["scenarios"]
    response_lengths = [s.get("response_length", -1) for s in scenarios]
    tools_called_counts = [len(s.get("tools_called", [])) for s in scenarios]
    all_same_length = (
        len(set(response_lengths)) == 1
        and response_lengths[0] >= 0
        and all(c == 0 for c in tools_called_counts)
    )
    # Detect timeout: any scenario took > 180s with no tools
    has_timeout = any(
        s.get("duration_ms", 0) > 180000 and len(s.get("tools_called", [])) == 0
        for s in scenarios
    )

    if all_same_length and model_result["passed"] == 0:
        model_result["tier"] = "X"  # API error / not supported
        model_result["note"] = f"API error: identical {response_lengths[0]}-char responses, no tools called"
    elif has_timeout and model_result["passed"] <= 1:
        model_result["tier"] = "X"
        model_result["note"] = "Timeout: scenarios exceeded 180s without tool calls"
    else:
        # Compute tier
        total = model_result["passed"] + model_result["failed"] + model_result["errors"]
        model_result["total"] = total
        model_result["score"] = model_result["passed"]
        if model_result["passed"] == 4:
            model_result["tier"] = "S"
        elif model_result["passed"] == 3:
            model_result["tier"] = "A"
        elif model_result["passed"] == 2:
            model_result["tier"] = "B"
        elif model_result["passed"] == 1:
            model_result["tier"] = "C"
        else:
            model_result["tier"] = "F"

    total = model_result["passed"] + model_result["failed"] + model_result["errors"]
    model_result["total"] = total
    model_result["score"] = model_result["passed"]

    return model_result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Batch-verify all OpenRouter models")
    parser.add_argument("--provider", default="", help="Filter by provider (e.g. google, meta-llama)")
    parser.add_argument("--max-models", type=int, default=0, help="Limit number of models (0=all)")
    parser.add_argument("--skip", default="", help="Comma-separated model IDs to skip")
    parser.add_argument("--only", default="", help="Comma-separated model IDs to test (overrides provider filter)")
    args = parser.parse_args()

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        print("ERROR: Set OPENROUTER_API_KEY")
        sys.exit(1)

    # Fetch and filter models
    print("Fetching model list from OpenRouter...")
    all_models = fetch_models(api_key)
    print(f"  Total models on OpenRouter: {len(all_models)}")

    if args.only:
        only_ids = set(args.only.split(","))
        models = [m for m in all_models if m["id"] in only_ids]
    else:
        models = filter_tool_models(all_models, args.provider)

    skip_ids = set(args.skip.split(",")) if args.skip else set()
    models = [m for m in models if m["id"] not in skip_ids]

    # Sort by provider, then by price (cheapest first)
    models.sort(key=lambda m: (
        m["id"].split("/")[0],
        float((m.get("pricing") or {}).get("prompt", "0")),
    ))

    if args.max_models > 0:
        models = models[:args.max_models]

    print(f"  Models to test: {len(models)}")
    print()

    # Create client
    client = openai.OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
        timeout=httpx.Timeout(120.0, connect=15.0),
    )
    runtime = AgentNodeRuntime(minimum_trust_level="verified")

    # Run tests
    results = []
    tier_counts = {"S": 0, "A": 0, "B": 0, "C": 0, "F": 0, "X": 0}
    total_start = time.monotonic()

    for i, m in enumerate(models):
        mid = m["id"]
        price_in = float((m.get("pricing") or {}).get("prompt", "0")) * 1e6
        print(f"[{i+1}/{len(models)}] {mid} (${price_in:.2f}/M input)")

        t0 = time.monotonic()
        model_result = test_model(client, runtime, mid)
        elapsed_total = int((time.monotonic() - t0) * 1000)
        model_result["total_duration_ms"] = elapsed_total

        tier = model_result["tier"]
        tier_counts[tier] += 1
        results.append(model_result)

        passed = model_result["passed"]
        failed = model_result["failed"]
        errors = model_result["errors"]
        print(f"  -> Tier {tier}: {passed} pass, {failed} fail, {errors} error ({elapsed_total}ms)")

        # Brief pause between models to avoid rate limits
        time.sleep(1)

    total_elapsed = int((time.monotonic() - total_start) * 1000)

    # Summary
    print()
    print("=" * 70)
    print("  BATCH VERIFICATION COMPLETE")
    print(f"  Models: {len(results)} | Duration: {total_elapsed // 1000}s")
    print(f"  Tiers: S={tier_counts['S']} A={tier_counts['A']} B={tier_counts['B']} C={tier_counts['C']} F={tier_counts['F']} X={tier_counts['X']}")
    print("=" * 70)

    # Print by tier
    for tier in ["S", "A", "B", "C", "F", "X"]:
        tier_models = [r for r in results if r["tier"] == tier]
        if not tier_models:
            continue
        print(f"\n  --- TIER {tier} ({len(tier_models)} models) ---")
        for r in tier_models:
            scenarios = " | ".join(
                f"{s['name'].split('.')[0].strip()}: {s['status']}"
                for s in r["scenarios"]
            )
            note = f"  ({r['note']})" if r.get("note") else ""
            print(f"  {r['model'].ljust(50)} {scenarios}{note}")

    # Save report
    report_dir = os.path.join(os.path.dirname(__file__), "..", ".artifacts", "batch_reports")
    os.makedirs(report_dir, exist_ok=True)
    report_path = os.path.join(report_dir, f"batch_{int(time.time())}.json")
    report = {
        "timestamp": int(time.time()),
        "total_models": len(results),
        "total_duration_ms": total_elapsed,
        "tier_counts": tier_counts,
        "results": results,
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\n  Report saved: {report_path}")

    # Also save a compact compatibility matrix
    matrix_path = os.path.join(report_dir, f"compatibility_matrix_{int(time.time())}.json")
    matrix = []
    for r in results:
        matrix.append({
            "model": r["model"],
            "tier": r["tier"],
            "score": f"{r['passed']}/{r['total']}",
            "passed": r["passed"],
            "scenarios": {
                s["name"]: s["status"] for s in r["scenarios"]
            },
        })
    matrix.sort(key=lambda x: (-x["passed"], x["model"]))
    with open(matrix_path, "w", encoding="utf-8") as f:
        json.dump(matrix, f, indent=2, ensure_ascii=False)
    print(f"  Matrix saved:  {matrix_path}")

    print()


if __name__ == "__main__":
    main()
