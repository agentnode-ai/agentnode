#!/usr/bin/env python3
"""Score Analyzer — turns ToolUsageScore JSON artifacts into actionable reports.

Reads all JSON files from sdk/.artifacts/tool_usage_scores/ and produces:
  1. Provider comparison (PASS/WARN/FAIL rates)
  2. Capability class breakdown (duration, success rate, common sequences)
  3. Hallucination detection
  4. Trend analysis (stability across runs)

Usage:
    python scripts/analyze_scores.py                    # full report
    python scripts/analyze_scores.py --latest           # only latest run per test
    python scripts/analyze_scores.py --provider openai  # filter by provider
    python scripts/analyze_scores.py --json             # machine-readable output
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median


SCORES_DIR = Path(__file__).resolve().parent.parent / ".artifacts" / "tool_usage_scores"


# ---------------------------------------------------------------------------
# Load scores
# ---------------------------------------------------------------------------

def load_scores(
    scores_dir: Path = SCORES_DIR,
    provider_filter: str | None = None,
) -> list[dict]:
    """Load all score JSON files, optionally filtered by provider."""
    if not scores_dir.exists():
        return []

    scores = []
    for path in sorted(scores_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["_file"] = path.name
            # Extract timestamp from filename: provider_testname_TIMESTAMP.json
            parts = path.stem.rsplit("_", 1)
            data["_timestamp"] = int(parts[-1]) if len(parts) > 1 else 0
            scores.append(data)
        except (json.JSONDecodeError, ValueError):
            continue

    if provider_filter:
        scores = [s for s in scores if s.get("provider") == provider_filter]

    return scores


def latest_per_test(scores: list[dict]) -> list[dict]:
    """Keep only the most recent score per (provider, test_name) pair."""
    latest: dict[tuple[str, str], dict] = {}
    for s in scores:
        key = (s["provider"], s["test_name"])
        if key not in latest or s["_timestamp"] > latest[key]["_timestamp"]:
            latest[key] = s
    return list(latest.values())


# ---------------------------------------------------------------------------
# Analysis functions
# ---------------------------------------------------------------------------

def verdict_summary(scores: list[dict]) -> dict:
    """Count PASS/WARN/FAIL per provider."""
    by_provider: dict[str, Counter] = defaultdict(Counter)
    for s in scores:
        by_provider[s["provider"]][s.get("verdict", "UNKNOWN")] += 1
    return dict(by_provider)


def capability_breakdown(scores: list[dict]) -> dict:
    """Per capability class: success rate, avg duration, common tool sequences."""
    by_class: dict[str, list[dict]] = defaultdict(list)
    for s in scores:
        by_class[s["capability_class"]].append(s)

    result = {}
    for cls, items in sorted(by_class.items()):
        durations = [s["duration_ms"] for s in items if s["duration_ms"] > 0]
        pass_count = sum(1 for s in items if s.get("verdict") == "PASS")
        sequences = Counter(
            " -> ".join(s["tool_calls"]) if s["tool_calls"] else "(no calls)"
            for s in items
        )

        result[cls] = {
            "total_runs": len(items),
            "pass_count": pass_count,
            "pass_rate": f"{pass_count / len(items) * 100:.0f}%" if items else "N/A",
            "avg_duration_ms": int(mean(durations)) if durations else 0,
            "median_duration_ms": int(median(durations)) if durations else 0,
            "top_sequences": sequences.most_common(3),
        }
    return result


def hallucination_report(scores: list[dict]) -> dict:
    """Hallucination rate per provider."""
    by_provider: dict[str, dict] = defaultdict(lambda: {"total": 0, "hallucinated": 0})
    for s in scores:
        by_provider[s["provider"]]["total"] += 1
        if s.get("hallucination"):
            by_provider[s["provider"]]["hallucinated"] += 1

    result = {}
    for provider, counts in sorted(by_provider.items()):
        rate = counts["hallucinated"] / counts["total"] * 100 if counts["total"] else 0
        result[provider] = {
            "total": counts["total"],
            "hallucinated": counts["hallucinated"],
            "rate": f"{rate:.1f}%",
        }
    return result


def provider_comparison(scores: list[dict]) -> dict:
    """Side-by-side comparison of providers per test."""
    by_test: dict[str, dict[str, dict]] = defaultdict(dict)
    for s in scores:
        key = s["test_name"]
        provider = s["provider"]
        # Keep latest per provider per test
        if provider not in by_test[key] or s["_timestamp"] > by_test[key][provider]["_timestamp"]:
            by_test[key][provider] = s

    return dict(by_test)


def sequence_analysis(scores: list[dict]) -> dict:
    """Most common tool call sequences across all tests."""
    sequences = Counter()
    for s in scores:
        if s["tool_calls"]:
            seq = " -> ".join(s["tool_calls"])
            sequences[seq] += 1
    return dict(sequences.most_common(10))


def stability_report(scores: list[dict]) -> dict:
    """For tests with multiple runs: how stable is the verdict?"""
    by_key: dict[tuple[str, str], list[str]] = defaultdict(list)
    for s in scores:
        key = (s["provider"], s["test_name"])
        by_key[key].append(s.get("verdict", "UNKNOWN"))

    unstable = {}
    for key, verdicts in sorted(by_key.items()):
        if len(verdicts) > 1 and len(set(verdicts)) > 1:
            unstable[f"{key[0]}:{key[1]}"] = {
                "runs": len(verdicts),
                "verdicts": verdicts,
                "pass_rate": f"{verdicts.count('PASS') / len(verdicts) * 100:.0f}%",
            }
    return unstable


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

def format_report(scores: list[dict], latest_only: bool = False) -> str:
    """Format a human-readable report from scores."""
    if not scores:
        return "No scores found in .artifacts/tool_usage_scores/"

    if latest_only:
        scores = latest_per_test(scores)

    lines: list[str] = []
    providers = sorted(set(s["provider"] for s in scores))

    lines.append("=" * 70)
    lines.append("  AgentNode Tool Usage Score Report")
    lines.append(f"  {len(scores)} scores | Providers: {', '.join(providers)}")
    lines.append(f"  Mode: {'latest per test' if latest_only else 'all runs'}")
    lines.append("=" * 70)

    # --- 1. Verdict summary ---
    lines.append("\n## Verdict Summary (PASS / WARN / FAIL)\n")
    verdicts = verdict_summary(scores)
    for provider in providers:
        counts = verdicts.get(provider, Counter())
        total = sum(counts.values())
        p, w, f = counts.get("PASS", 0), counts.get("WARN", 0), counts.get("FAIL", 0)
        bar = f"{'#' * p}{'~' * w}{'.' * f}"
        lines.append(f"  {provider:12s}  [{bar}]  PASS={p}  WARN={w}  FAIL={f}  (total={total})")

    # --- 2. Capability class breakdown ---
    lines.append("\n## Capability Class Breakdown\n")
    cap_data = capability_breakdown(scores)
    lines.append(f"  {'Class':<20s}  {'Runs':>5s}  {'Pass':>6s}  {'Avg ms':>7s}  {'Med ms':>7s}  Top Sequence")
    lines.append(f"  {'-' * 20}  {'-' * 5}  {'-' * 6}  {'-' * 7}  {'-' * 7}  {'-' * 30}")
    for cls, data in cap_data.items():
        top_seq = data["top_sequences"][0][0] if data["top_sequences"] else "--"
        if len(top_seq) > 40:
            top_seq = top_seq[:37] + "..."
        lines.append(
            f"  {cls:<20s}  {data['total_runs']:>5d}  {data['pass_rate']:>6s}  "
            f"{data['avg_duration_ms']:>7d}  {data['median_duration_ms']:>7d}  {top_seq}"
        )

    # --- 3. Hallucination report ---
    lines.append("\n## Hallucination Rate\n")
    hall = hallucination_report(scores)
    for provider, data in hall.items():
        status = "CLEAN" if data["hallucinated"] == 0 else "WARNING"
        lines.append(
            f"  {provider:12s}  {data['hallucinated']}/{data['total']} runs  "
            f"({data['rate']})  [{status}]"
        )

    # --- 4. Provider comparison (latest only) ---
    lines.append("\n## Provider Comparison (latest run per test)\n")
    comparison = provider_comparison(scores)
    lines.append(f"  {'Test':<42s}  ", )
    header_providers = []
    for p in providers:
        header_providers.append(f"{p:>12s}")
    lines[-1] += "  ".join(header_providers)
    lines.append(f"  {'-' * 42}  " + "  ".join("-" * 12 for _ in providers))

    for test_name in sorted(comparison.keys()):
        row = f"  {test_name:<42s}  "
        cells = []
        for p in providers:
            if p in comparison[test_name]:
                s = comparison[test_name][p]
                v = s.get("verdict", "?")
                d = s.get("duration_ms", 0)
                cells.append(f"{v:>4s} {d:>5d}ms")
            else:
                cells.append(f"{'—':>12s}")
        row += "  ".join(cells)
        lines.append(row)

    # --- 5. Stability report ---
    unstable = stability_report(scores)
    if unstable:
        lines.append("\n## Stability Issues (inconsistent verdicts across runs)\n")
        for key, data in unstable.items():
            verdict_str = " -> ".join(data["verdicts"])
            lines.append(
                f"  {key:<45s}  {data['runs']} runs  "
                f"pass_rate={data['pass_rate']}  [{verdict_str}]"
            )
    else:
        lines.append("\n## Stability: All tests consistent across runs OK\n")

    # --- 6. Top tool sequences ---
    lines.append("\n## Most Common Tool Sequences\n")
    seqs = sequence_analysis(scores)
    for seq, count in seqs.items():
        lines.append(f"  {count:>3d}x  {seq}")

    lines.append("\n" + "=" * 70)
    return "\n".join(lines)


def format_json_report(scores: list[dict], latest_only: bool = False) -> str:
    """Format a machine-readable JSON report."""
    if latest_only:
        scores = latest_per_test(scores)

    report = {
        "total_scores": len(scores),
        "providers": sorted(set(s["provider"] for s in scores)),
        "verdict_summary": {
            provider: dict(counts)
            for provider, counts in verdict_summary(scores).items()
        },
        "capability_breakdown": capability_breakdown(scores),
        "hallucination": hallucination_report(scores),
        "stability_issues": stability_report(scores),
        "top_sequences": sequence_analysis(scores),
    }
    return json.dumps(report, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze ToolUsageScore artifacts")
    parser.add_argument(
        "--latest", action="store_true",
        help="Only use the latest score per (provider, test) pair",
    )
    parser.add_argument(
        "--provider", type=str, default=None,
        help="Filter by provider (e.g., 'openai', 'anthropic')",
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON instead of human report",
    )
    parser.add_argument(
        "--dir", type=str, default=None,
        help="Custom scores directory (default: sdk/.artifacts/tool_usage_scores/)",
    )
    args = parser.parse_args()

    scores_dir = Path(args.dir) if args.dir else SCORES_DIR
    scores = load_scores(scores_dir, provider_filter=args.provider)

    if args.json_output:
        print(format_json_report(scores, latest_only=args.latest))
    else:
        print(format_report(scores, latest_only=args.latest))

    # Exit code: 1 if any latest scores have FAIL verdict
    latest = latest_per_test(scores)
    has_fails = any(s.get("verdict") == "FAIL" for s in latest)
    sys.exit(1 if has_fails else 0)


if __name__ == "__main__":
    main()
