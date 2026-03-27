"""Standalone smoke runner for import conversion.

Runs all .py files in sources/{langchain,crewai}/ through the converter
and writes results to results/run_{timestamp}.json.

Usage:
    cd backend
    python scripts/import_smoke_test/runner.py
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure backend app is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.import_.schemas import ConvertRequest
from app.import_.service import convert


BASE = Path(__file__).parent / "sources"
RESULTS_DIR = Path(__file__).parent / "results"


def run_file(path: Path, platform: str) -> dict:
    """Run a single file through the converter."""
    source = path.read_text(encoding="utf-8")
    start = time.monotonic()

    try:
        resp = convert(ConvertRequest(platform=platform, content=source))
    except Exception as e:
        return {
            "file": path.name,
            "platform": platform,
            "error": f"{type(e).__name__}: {e}",
            "duration_ms": int((time.monotonic() - start) * 1000),
        }

    duration_ms = int((time.monotonic() - start) * 1000)

    blocking = [w for w in resp.grouped_warnings if w.category == "blocking"]
    review = [w for w in resp.grouped_warnings if w.category == "review"]

    return {
        "file": path.name,
        "platform": platform,
        "confidence": resp.confidence.level,
        "draft_ready": resp.draft_ready,
        "tools_detected": len(resp.detected_tools),
        "warning_count": len(resp.warnings),
        "blocking_count": len(blocking),
        "review_count": len(review),
        "unknown_imports": resp.unknown_imports,
        "detected_dependencies": resp.detected_dependencies,
        "duration_ms": duration_ms,
        "package_id": resp.package_id,
        "confidence_reasons": resp.confidence.reasons,
        "blocking_warnings": [w.message for w in blocking],
    }


def run_all() -> list[dict]:
    """Run all source files and return results."""
    results = []

    for platform in ["langchain", "crewai"]:
        source_dir = BASE / platform
        if not source_dir.exists():
            continue
        for filepath in sorted(source_dir.glob("*.py")):
            results.append(run_file(filepath, platform))

    return results


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    results = run_all()

    if not results:
        print("No source files found. Add .py files to:")
        print(f"  {BASE / 'langchain'}/")
        print(f"  {BASE / 'crewai'}/")
        return

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    out_path = RESULTS_DIR / f"run_{timestamp}.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"Results written to {out_path}")
    print(f"Total files: {len(results)}")

    # Quick summary
    from collections import Counter
    confidence = Counter(r.get("confidence", "error") for r in results)
    draft_ready = sum(1 for r in results if r.get("draft_ready"))
    errors = sum(1 for r in results if "error" in r)

    print(f"\nConfidence: {dict(confidence)}")
    print(f"Draft ready: {draft_ready}/{len(results)}")
    if errors:
        print(f"Errors: {errors}")


if __name__ == "__main__":
    main()
