"""Verify tool-calling works end-to-end for a given model.

Runs 4 scenarios, prints every tool call with arguments and results.
Usage:
    OPENROUTER_API_KEY=sk-... python scripts/verify_toolcalls.py google/gemma-4-26b-a4b-it
    OPENROUTER_API_KEY=sk-... python scripts/verify_toolcalls.py google/gemma-4-31b-it
    OPENAI_API_KEY=sk-...     python scripts/verify_toolcalls.py gpt-4o --provider openai
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

# Ensure SDK is importable from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentnode_sdk.runtime import AgentNodeRuntime


# ---------------------------------------------------------------------------
# Intercept handle() to log every tool call
# ---------------------------------------------------------------------------

class ToolCallLogger:
    def __init__(self, runtime: AgentNodeRuntime):
        self.runtime = runtime
        self._original = runtime.handle
        self.log: list[dict] = []
        runtime.handle = self._intercept

    def _intercept(self, tool_name: str, arguments: dict | None = None) -> dict:
        from agentnode_sdk.runtime import AgentNodeRuntime
        clean_name = AgentNodeRuntime._sanitize_tool_name(tool_name)
        t0 = time.monotonic()
        result = self._original(tool_name, arguments)
        elapsed = int((time.monotonic() - t0) * 1000)
        entry = {
            "tool": clean_name,
            "arguments": arguments,
            "result": result,
            "elapsed_ms": elapsed,
        }
        self.log.append(entry)

        # Print live
        print(f"    TOOL CALL: {tool_name}")
        if arguments:
            for k, v in arguments.items():
                val = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
                if len(val) > 120:
                    val = val[:117] + "..."
                print(f"      {k}: {val}")
        success = result.get("success", "?")
        print(f"      -> success={success}, {elapsed}ms")
        # Show result snippet
        res_data = result.get("result") or result.get("error")
        if res_data:
            snippet = json.dumps(res_data, ensure_ascii=False)
            if len(snippet) > 200:
                snippet = snippet[:197] + "..."
            print(f"      -> {snippet}")
        print()
        return result

    def restore(self):
        self.runtime.handle = self._original


# ---------------------------------------------------------------------------
# Extract response text
# ---------------------------------------------------------------------------

def get_text(result: Any, provider: str) -> str:
    if isinstance(result, dict):
        return result.get("error", {}).get("message", str(result))
    if provider == "anthropic":
        if hasattr(result, "content"):
            return " ".join(b.text for b in result.content if hasattr(b, "text"))
    elif provider == "gemini":
        if hasattr(result, "text"):
            return result.text or ""
    else:  # openai
        if hasattr(result, "content") and result.content:
            return result.content
    return ""


# ---------------------------------------------------------------------------
# Scenarios
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
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Verify tool-calling for a model")
    parser.add_argument("model", help="Model ID (e.g. google/gemma-4-26b-a4b-it)")
    parser.add_argument("--provider", default="openai", help="Provider: openai, anthropic, gemini (default: openai)")
    parser.add_argument("--base-url", default="", help="Base URL override")
    parser.add_argument("--api-key-env", default="", help="Env var for API key")
    args = parser.parse_args()

    # Determine base URL and key
    base_url = args.base_url
    api_key = ""

    if not base_url and args.provider == "openai":
        # Auto-detect OpenRouter
        if os.environ.get("OPENROUTER_API_KEY"):
            base_url = "https://openrouter.ai/api/v1"
            api_key = os.environ["OPENROUTER_API_KEY"]
        elif os.environ.get("OPENAI_API_KEY"):
            api_key = os.environ["OPENAI_API_KEY"]
        else:
            print("ERROR: Set OPENROUTER_API_KEY or OPENAI_API_KEY")
            sys.exit(1)

    if args.api_key_env:
        api_key = os.environ.get(args.api_key_env, "")

    # Create client
    if args.provider == "openai":
        import httpx
        import openai
        kwargs: dict[str, Any] = {"timeout": httpx.Timeout(90.0, connect=10.0)}
        if base_url:
            kwargs["base_url"] = base_url
        if api_key:
            kwargs["api_key"] = api_key
        client = openai.OpenAI(**kwargs)
    elif args.provider == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
    elif args.provider == "gemini":
        from google import genai
        client = genai.Client(api_key=api_key or os.environ.get("GEMINI_API_KEY"))
    else:
        print(f"Unknown provider: {args.provider}")
        sys.exit(1)

    runtime = AgentNodeRuntime(minimum_trust_level="verified")

    print("=" * 70)
    print(f"  TOOL-CALL VERIFICATION: {args.model}")
    print(f"  Provider: {args.provider} | Base URL: {base_url or 'default'}")
    print("=" * 70)
    print()

    passed = 0
    failed = 0
    results_summary = []

    for scenario in SCENARIOS:
        print("-" * 70)
        print(f"  {scenario['name']}")
        print(f"  {scenario['desc']}")
        print("-" * 70)

        logger = ToolCallLogger(runtime)
        t0 = time.monotonic()

        try:
            result = runtime.run(
                provider=args.provider,
                client=client,
                messages=list(scenario["messages"]),  # copy
                model=args.model,
                max_tool_rounds=scenario["max_rounds"],
            )
            elapsed = int((time.monotonic() - t0) * 1000)

            # Extract response
            text = get_text(result, args.provider)

            # Check tool calls
            called_tools = [e["tool"] for e in logger.log]
            expected = scenario["expect_tools"]
            all_called = all(t in called_tools for t in expected)
            has_response = len(text.strip()) > 0 or all_called

            status = "PASS" if all_called and has_response else "FAIL"

            print(f"  Model response ({len(text)} chars):")
            # Print first 300 chars
            for line in text[:500].split("\n"):
                print(f"    {line}")
            if len(text) > 500:
                print(f"    ... ({len(text) - 500} more chars)")
            print()

            print(f"  Tool calls made: {called_tools}")
            print(f"  Expected tools:  {expected}")
            print(f"  All expected called: {all_called}")
            print(f"  Has response text:   {has_response}")
            print(f"  Duration: {elapsed}ms")
            print(f"  STATUS: {status}")
            print()

            if status == "PASS":
                passed += 1
            else:
                failed += 1

            results_summary.append({
                "scenario": scenario["name"],
                "status": status,
                "tools_called": called_tools,
                "tools_expected": expected,
                "response_length": len(text),
                "duration_ms": elapsed,
            })

        except Exception as exc:
            elapsed = int((time.monotonic() - t0) * 1000)
            print(f"  EXCEPTION: {exc}")
            print(f"  Duration: {elapsed}ms")
            print(f"  STATUS: ERROR")
            print()
            failed += 1
            results_summary.append({
                "scenario": scenario["name"],
                "status": "ERROR",
                "error": str(exc),
                "duration_ms": elapsed,
            })
        finally:
            logger.restore()

    # Final summary
    print("=" * 70)
    print(f"  SUMMARY: {args.model}")
    print(f"  {passed} PASSED / {failed} FAILED / {passed + failed} TOTAL")
    print("=" * 70)
    for r in results_summary:
        icon = "PASS" if r["status"] == "PASS" else "FAIL"
        print(f"  [{icon}] {r['scenario']} ({r['duration_ms']}ms)")
        if r.get("tools_called"):
            print(f"        calls: {r['tools_called']}")
    print("=" * 70)

    # Write JSON report
    report_dir = os.path.join(os.path.dirname(__file__), "..", ".artifacts", "verification_reports")
    os.makedirs(report_dir, exist_ok=True)
    model_slug = args.model.replace("/", "-").replace(":", "-")
    report_path = os.path.join(report_dir, f"{model_slug}_{int(time.time())}.json")
    report = {
        "model": args.model,
        "provider": args.provider,
        "base_url": base_url,
        "timestamp": int(time.time()),
        "passed": passed,
        "failed": failed,
        "total": passed + failed,
        "scenarios": results_summary,
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\n  Report saved: {report_path}")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
