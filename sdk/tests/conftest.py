"""Shared fixtures and utilities for E2E runtime tests.

Provides ToolUsageScore, ToolCallTracker, and score-writing infrastructure
for structured provider comparison and regression tracking.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# ToolUsageScore — structured logging for provider comparison
# ---------------------------------------------------------------------------

@dataclass
class ToolUsageScore:
    """Score object for tracking tool usage across providers and tests.

    Written as JSON to sdk/.artifacts/tool_usage_scores/ after each test.
    Enables: provider comparison, prompt version A/B testing, regression detection.
    """

    test_name: str
    provider: str
    capability_class: str
    model: str = ""
    prompt_version: str = "v1_basic"
    tool_calls: list[str] = field(default_factory=list)
    correct_sequence: bool = False
    expected_tool_path: bool = False
    hallucination: bool = False
    final_answer_present: bool = False
    success: bool = False
    duration_ms: int = 0

    @property
    def verdict(self) -> str:
        """PASS / WARN / FAIL based on fixed schema.

        PASS = success + expected_tool_path + no hallucination
        WARN = success but tool path inefficient
        FAIL = wrong sequence, hallucination, or incorrect result
        """
        if self.success and self.expected_tool_path and not self.hallucination:
            return "PASS"
        if self.success and not self.expected_tool_path:
            return "WARN"
        return "FAIL"


# ---------------------------------------------------------------------------
# ToolCallTracker — wraps runtime.handle() to record tool calls
# ---------------------------------------------------------------------------

class ToolCallTracker:
    """Monkey-patches runtime.handle() to record all tool calls.

    Works with both OpenAI and Anthropic loops since handle() is the
    single dispatch point for all tool execution.
    """

    def __init__(self, runtime: Any):
        self.runtime = runtime
        self.calls: list[str] = []
        self.call_details: list[dict] = []
        self._original_handle = runtime.handle
        self._start_time = time.monotonic()
        runtime.handle = self._tracking_handle

    def _tracking_handle(self, tool_name: str, arguments: dict | None = None) -> dict:
        self.calls.append(tool_name)
        result = self._original_handle(tool_name, arguments)
        self.call_details.append({
            "tool": tool_name,
            "arguments": arguments,
            "success": result.get("success"),
            "elapsed_ms": int((time.monotonic() - self._start_time) * 1000),
        })
        return result

    def restore(self) -> None:
        """Restore original handle() method."""
        self.runtime.handle = self._original_handle

    @property
    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self._start_time) * 1000)


# ---------------------------------------------------------------------------
# Score writer — JSON to .artifacts/tool_usage_scores/
# ---------------------------------------------------------------------------

_SCORES_DIR = Path(__file__).resolve().parent.parent / ".artifacts" / "tool_usage_scores"


def write_score(score: ToolUsageScore) -> Path:
    """Write a ToolUsageScore as JSON. Returns the file path."""
    _SCORES_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    model_slug = score.model.replace("/", "-").replace(":", "-") if score.model else "default"
    filename = f"{score.provider}_{model_slug}_{score.test_name}_{ts}.json"
    path = _SCORES_DIR / filename
    data = asdict(score)
    data["verdict"] = score.verdict
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Helper: extract text from provider response
# ---------------------------------------------------------------------------

def extract_response_text(result: Any, provider: str) -> str:
    """Extract text content from an OpenAI, Anthropic, or Gemini response.

    Accepts both plain provider names ("openai", "anthropic", "gemini") and
    prefixed IDs ("openai_gpt4o_mini", "anthropic_sonnet", "gemini_flash").
    """
    if isinstance(result, dict):
        # Error dict from runtime.run()
        return result.get("error", {}).get("message", "")

    # Normalize provider IDs like "anthropic_haiku" → "anthropic"
    family = provider.split("_")[0] if "_" in provider else provider
    # Compat/third-party providers all use OpenAI response format
    if family in ("compat", "nvidia", "openrouter"):
        family = "openai"

    if family == "openai":
        if hasattr(result, "content") and result.content:
            return result.content
        return ""

    if family == "anthropic":
        if hasattr(result, "content"):
            return " ".join(
                b.text for b in result.content if hasattr(b, "text")
            )
        return ""

    if family == "gemini":
        # Gemini GenerateContentResponse: .text or .candidates[0].content.parts
        if hasattr(result, "text") and result.text:
            return result.text
        if hasattr(result, "candidates") and result.candidates:
            parts = result.candidates[0].content.parts or []
            texts = [p.text for p in parts if hasattr(p, "text") and p.text]
            return " ".join(texts)
        return ""

    return ""


# ---------------------------------------------------------------------------
# Helper: check tool call sequence
# ---------------------------------------------------------------------------

def check_sequence(calls: list[str], expected_order: list[str]) -> bool:
    """Check that expected tools appear in the correct order within calls.

    Only checks relative order of tools that are present.
    Returns False if any expected tool is missing.
    """
    indices = []
    for tool in expected_order:
        if tool not in calls:
            return False
        indices.append(calls.index(tool))
    return indices == sorted(indices)


# ---------------------------------------------------------------------------
# Helper: build score from tracker + result
# ---------------------------------------------------------------------------

def build_score(
    *,
    test_name: str,
    provider: str,
    capability_class: str,
    tracker: ToolCallTracker,
    result: Any,
    expected_tools: list[str],
    expected_sequence: list[str] | None = None,
    prompt_version: str = "v1_basic",
    model: str = "",
) -> ToolUsageScore:
    """Build a ToolUsageScore from test execution data and write to disk."""
    response_text = extract_response_text(result, provider)

    # Check sequence
    correct_sequence = True
    if expected_sequence:
        correct_sequence = check_sequence(tracker.calls, expected_sequence)

    # Check expected tool path
    expected_tool_path = all(t in tracker.calls for t in expected_tools)

    # Check hallucination: expected tool calls but none were made
    hallucination = len(expected_tools) > 0 and len(tracker.calls) == 0

    # Check final answer
    final_answer_present = len(response_text.strip()) > 0

    # Overall success
    success = (
        expected_tool_path
        and correct_sequence
        and final_answer_present
        and not hallucination
    )

    score = ToolUsageScore(
        test_name=test_name,
        provider=provider,
        capability_class=capability_class,
        model=model,
        prompt_version=prompt_version,
        tool_calls=list(tracker.calls),
        correct_sequence=correct_sequence,
        expected_tool_path=expected_tool_path,
        hallucination=hallucination,
        final_answer_present=final_answer_present,
        success=success,
        duration_ms=tracker.elapsed_ms,
    )
    write_score(score)
    return score
