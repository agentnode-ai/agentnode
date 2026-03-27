"""Analyze import smoke test results.

Usage:
    python scripts/import_smoke_test/analyze.py [results_file]

If no file is specified, reads the most recent run from results/.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


RESULTS_DIR = Path(__file__).parent / "results"


def load_results(path: Path | None = None) -> list[dict]:
    if path:
        return json.loads(path.read_text())

    results_files = sorted(RESULTS_DIR.glob("run_*.json"))
    if not results_files:
        print("No results found. Run runner.py first.")
        sys.exit(1)
    return json.loads(results_files[-1].read_text())


def analyze(results: list[dict]) -> None:
    total = len(results)
    errors = [r for r in results if "error" in r]
    success = [r for r in results if "error" not in r]

    print("=" * 60)
    print("  IMPORTER REALITY CHECK")
    print("=" * 60)
    print(f"  Total files:   {total}")
    print(f"  Successful:    {len(success)}")
    print(f"  Errors:        {len(errors)}")
    print()

    if not success:
        return

    # Confidence distribution
    confidence = Counter(r["confidence"] for r in success)
    print("  Confidence distribution:")
    for level in ["high", "medium", "low"]:
        count = confidence.get(level, 0)
        pct = count * 100 // len(success)
        bar = "#" * (pct // 2)
        print(f"    {level:<8} {count:>3} ({pct:>2}%)  {bar}")
    print()

    # Draft ready
    draft_ready = sum(1 for r in success if r["draft_ready"])
    print(f"  Draft ready:   {draft_ready}/{len(success)} ({draft_ready * 100 // len(success)}%)")
    print()

    # Platform breakdown
    platforms = Counter(r["platform"] for r in success)
    print("  Platform breakdown:")
    for platform, count in platforms.most_common():
        p_results = [r for r in success if r["platform"] == platform]
        p_high = sum(1 for r in p_results if r["confidence"] == "high")
        p_med = sum(1 for r in p_results if r["confidence"] == "medium")
        p_low = sum(1 for r in p_results if r["confidence"] == "low")
        print(f"    {platform:<12} total={count}  high={p_high}  med={p_med}  low={p_low}")
    print()

    # Top warnings (by blocking count)
    all_blocking = []
    for r in success:
        all_blocking.extend(r.get("blocking_warnings", []))
    if all_blocking:
        print("  Top blocking warnings:")
        for msg, count in Counter(all_blocking).most_common(5):
            print(f"    [{count}x] {msg[:80]}")
        print()

    # Unknown imports
    all_unknown = []
    for r in success:
        all_unknown.extend(r.get("unknown_imports", []))
    if all_unknown:
        print("  Unknown imports seen:")
        for imp, count in Counter(all_unknown).most_common(10):
            print(f"    [{count}x] {imp}")
        print()

    # Timing
    durations = [r["duration_ms"] for r in success]
    avg_ms = sum(durations) // len(durations)
    max_ms = max(durations)
    print(f"  Avg duration:  {avg_ms}ms")
    print(f"  Max duration:  {max_ms}ms")

    print("=" * 60)

    # Error details
    if errors:
        print()
        print("  ERRORS:")
        for e in errors:
            print(f"    {e['file']}: {e['error']}")


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    results = load_results(path)
    analyze(results)


if __name__ == "__main__":
    main()
