"""Detect new tool-capable OpenRouter models missing from the compatibility snapshot.

Costs nothing: one OpenRouter catalog request, no LLM calls. Prints a summary
and exits 1 when untested models exist, so a scheduled CI job surfaces drift
instead of the matrix silently aging (snapshot dates are never re-stamped).

Usage:
    python scripts/check_model_drift.py
    python scripts/check_model_drift.py --snapshot ../sdk/data/compatibility/current.json
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

DEFAULT_SNAPSHOT = Path(__file__).resolve().parent.parent / "data" / "compatibility" / "current.json"
CATALOG_URL = "https://openrouter.ai/api/v1/models"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", default=str(DEFAULT_SNAPSHOT))
    args = parser.parse_args()

    snap = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    tested = {
        m.get("model") or m.get("model_id") or m.get("id")
        for m in (snap.get("models") or snap.get("results") or [])
    }
    tested.discard(None)

    with urllib.request.urlopen(CATALOG_URL, timeout=30) as resp:
        catalog = json.load(resp)["data"]

    tool_capable = {
        m["id"] for m in catalog if "tools" in (m.get("supported_parameters") or [])
    }
    new = sorted(tool_capable - tested)
    gone = sorted(tested - {m["id"] for m in catalog})

    print(f"Snapshot tested_at: {snap.get('tested_at')}")
    print(f"Tested models: {len(tested)} | Tool-capable on OpenRouter: {len(tool_capable)}")
    print(f"NEW (untested): {len(new)}")
    for mid in new:
        print(f"  + {mid}")
    print(f"REMOVED from OpenRouter: {len(gone)}")
    for mid in gone:
        print(f"  - {mid}")

    if new:
        print(
            f"\n{len(new)} untested models — run batch_verify.py --only <ids>, "
            "merge_batches.py, then generate_compatibility_artifacts.py."
        )
        return 1
    print("\nNo drift — compatibility matrix covers all tool-capable models.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
