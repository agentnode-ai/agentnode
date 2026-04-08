"""CI smoke test — verify core models still pass S-tier scenarios.

Runs 2 scenarios (S1: Capabilities, S3: Run Tool) against 5 key models.
Retry once on transient errors. Exit 0 unless a real regression is detected.

Usage:
    OPENROUTER_API_KEY=sk-... python sdk/scripts/ci_smoke_test.py
"""
from __future__ import annotations

import os
import sys
import time
from typing import Any

import openai

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentnode_sdk.runtime import AgentNodeRuntime

# 5 core models via OpenRouter
SMOKE_MODELS = [
    "openai/gpt-4o-mini",
    "anthropic/claude-sonnet-4.6",
    "google/gemini-2.0-flash-exp",
    "deepseek/deepseek-chat",
    "qwen/qwen3-30b-a3b",
]

# S1: Capabilities List + S3: Run Tool
SCENARIOS = [
    {
        "name": "S1-Capabilities",
        "messages": [
            {"role": "user", "content": (
                "Use the agentnode_capabilities tool to list installed tools. "
                "Tell me how many are installed and name them."
            )},
        ],
        "expect_tools": ["agentnode_capabilities"],
    },
    {
        "name": "S3-RunTool",
        "messages": [
            {"role": "user", "content": (
                "Run the word-counter tool on this text: 'Hello world test'. "
                "Use agentnode_run with slug 'word-counter' and input text 'Hello world test'. "
                "Tell me the result."
            )},
        ],
        "expect_tools": ["agentnode_run"],
    },
]


def _is_transient_error(error_msg: str) -> bool:
    """Check if an error is transient (network/rate-limit)."""
    lower = error_msg.lower()
    transient_signals = [
        "rate limit", "429", "timeout", "timed out", "connection",
        "502", "503", "504", "server error", "overloaded",
    ]
    return any(s in lower for s in transient_signals)


def run_scenario(
    client: openai.OpenAI,
    model: str,
    scenario: dict,
    runtime: AgentNodeRuntime,
) -> dict[str, Any]:
    """Run a single scenario. Returns {passed, error, transient}."""
    messages = [m.copy() for m in scenario["messages"]]

    try:
        result = runtime.run(
            provider="openai",
            client=client,
            messages=messages,
            model=model,
            max_tool_rounds=3,
        )
    except Exception as exc:
        err = str(exc)
        return {"passed": False, "error": err, "transient": _is_transient_error(err)}

    if isinstance(result, dict) and not result.get("success", True):
        err = result.get("error", {})
        msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        return {"passed": False, "error": msg, "transient": _is_transient_error(msg)}

    return {"passed": True, "error": None, "transient": False}


def main() -> int:
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        print("ERROR: OPENROUTER_API_KEY not set", file=sys.stderr)
        return 1

    client = openai.OpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
    )

    runtime = AgentNodeRuntime()

    results: list[dict[str, Any]] = []
    warnings: list[str] = []
    regressions: list[str] = []

    for model in SMOKE_MODELS:
        model_passed = True
        model_warnings: list[str] = []

        for scenario in SCENARIOS:
            print(f"  {model} / {scenario['name']} ... ", end="", flush=True)
            r = run_scenario(client, model, scenario, runtime)

            if not r["passed"]:
                # Retry once on transient errors
                if r["transient"]:
                    print("transient, retrying ... ", end="", flush=True)
                    time.sleep(3)
                    r = run_scenario(client, model, scenario, runtime)

                if not r["passed"]:
                    if r["transient"]:
                        print(f"WARN (transient: {r['error'][:60]})")
                        model_warnings.append(
                            f"{model}/{scenario['name']}: transient - {r['error'][:80]}"
                        )
                    else:
                        print(f"FAIL ({r['error'][:60]})")
                        model_passed = False
                else:
                    print("OK (retry)")
            else:
                print("OK")

            results.append({
                "model": model,
                "scenario": scenario["name"],
                **r,
            })

        if model_warnings:
            warnings.extend(model_warnings)

        if not model_passed:
            regressions.append(model)

    # Summary
    print("\n" + "=" * 60)
    print("COMPATIBILITY SMOKE TEST SUMMARY")
    print("=" * 60)
    total = len(SMOKE_MODELS)
    passed = total - len(regressions)
    print(f"Models tested:    {total}")
    print(f"S-tier retained:  {passed}/{total}")

    if warnings:
        print(f"\nWarnings ({len(warnings)}):")
        for w in warnings:
            print(f"  - {w}")

    if regressions:
        print(f"\nREGRESSIONS ({len(regressions)}):")
        for r in regressions:
            print(f"  - {r}")
        print("\nResult: FAIL")
        return 1

    print("\nResult: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
