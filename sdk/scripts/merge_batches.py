"""Merge AgentNode batch-verification artifacts into the canonical compatibility snapshot.

Reads one or more EXPLICITLY listed batch artifacts (raw ``batch_*.json`` from
``batch_verify.py`` or an already-merged matrix array), deduplicates per model
(best result wins), normalizes scenarios, computes the public stats, and writes the
single source of truth:

    sdk/data/compatibility/current.json

Design rules:
- **No provider calls. Pure transform.** Costs nothing.
- ``tested_at`` / ``run_id`` describe the REAL test time and MUST be passed in — this
  script never invents "now()" for test data (that is how stale data looked fresh before).
- Inputs are listed **explicitly** — never blind-glob a directory, so April and a later
  run can never be silently mixed.

Usage (bootstrap the honest April snapshot from existing April artifacts):
    python sdk/scripts/merge_batches.py \
        --inputs sdk/.artifacts/batch_reports/batch_1775570755.json \
                 sdk/.artifacts/batch_reports/batch_1775577888.json \
                 sdk/.artifacts/batch_reports/batch_1775587489.json \
                 sdk/.artifacts/batch_reports/batch_1775591836.json \
                 sdk/.artifacts/batch_reports/batch_1775602911.json \
        --run-id batch-2026-04-08 --tested-at 2026-04-08 \
        --out sdk/data/compatibility/current.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

# Canonical scenario names (must match batch_verify.py SCENARIOS / generator SCENARIO_KEYS).
SCENARIO_NAMES = [
    "1. Capabilities List",
    "2. Search + Install",
    "3. Run Tool (word counter)",
    "4. Multi-step Autonomous",
]
_TIER_RANK = {"S": 0, "A": 1, "B": 2, "C": 3, "F": 4, "X": 9}


def _load(path: Path) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    # Raw batch report = {"results": [...]}; merged form = bare list.
    if isinstance(data, dict) and "results" in data:
        return data["results"]
    if isinstance(data, list):
        return data
    raise ValueError(f"{path}: unrecognized shape (expected list or {{results: [...]}})")


def _norm_scenarios(raw: Any) -> dict[str, str]:
    """Normalize scenarios to {full_name: STATUS}. Accepts list-of-dict or dict."""
    out: dict[str, str] = {}
    if isinstance(raw, dict):
        for name in SCENARIO_NAMES:
            out[name] = raw.get(name, "FAIL")
    elif isinstance(raw, list):
        by_name = {s.get("name"): s.get("status", "FAIL") for s in raw if isinstance(s, dict)}
        for name in SCENARIO_NAMES:
            out[name] = by_name.get(name, "FAIL")
    else:
        for name in SCENARIO_NAMES:
            out[name] = "FAIL"
    return out


def _norm_model(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": raw["model"],
        "tier": raw.get("tier", "X"),
        "passed": int(raw.get("passed", 0)),
        "total": int(raw.get("total", 4)) or 4,
        "scenarios": _norm_scenarios(raw.get("scenarios")),
        "note": raw.get("note", "") or "",
        "_ts": int(raw.get("timestamp", 0)),
    }


def _better(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Best-result-wins: non-X beats X; then more passed; then more recent timestamp."""
    ax, bx = a["tier"] == "X", b["tier"] == "X"
    if ax != bx:
        return b if ax else a
    if a["passed"] != b["passed"]:
        return a if a["passed"] > b["passed"] else b
    return a if a["_ts"] >= b["_ts"] else b


def merge(inputs: list[Path]) -> list[dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for path in inputs:
        for raw in _load(path):
            m = _norm_model(raw)
            cur = best.get(m["model"])
            best[m["model"]] = m if cur is None else _better(cur, m)
    models = sorted(best.values(), key=lambda m: m["model"])
    for m in models:
        m.pop("_ts", None)
    return models


def _provider(model_id: str) -> str:
    return model_id.split("/", 1)[0] if "/" in model_id else "unknown"


def build_snapshot(models: list[dict[str, Any]], run_id: str, tested_at: str,
                   generated_by: str) -> dict[str, Any]:
    scored = [m for m in models if m["tier"] != "X"]
    excluded = [m for m in models if m["tier"] == "X"]
    tier_counts = dict(sorted(Counter(m["tier"] for m in models).items()))

    total = len(scored)
    s_tier = sum(1 for m in scored if m["tier"] == "S")
    pass_rate = round(s_tier / total * 100) if total else 0

    all_providers = {_provider(m["model"]) for m in models}
    visible_providers = {_provider(m["model"]) for m in scored}

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for m in scored:
        grouped[_provider(m["model"])].append(m)
    providers = [
        {"name": name, "models": sorted(grouped[name], key=lambda m: m["model"])}
        for name in sorted(grouped)
    ]

    # source_hash over the canonical model data (order-independent, value-only).
    digest_payload = json.dumps(
        sorted(models, key=lambda m: m["model"]), sort_keys=True, ensure_ascii=False
    )
    source_hash = hashlib.sha256(digest_payload.encode("utf-8")).hexdigest()

    return {
        "run_id": run_id,
        "tested_at": tested_at,
        "generated_by": generated_by,
        "source_hash": source_hash,
        "total_models": total,
        "s_tier_count": s_tier,
        "pass_rate": pass_rate,
        "provider_count_total": len(all_providers),
        "provider_count_visible": len(visible_providers),
        "excluded_provider_count": len(all_providers) - len(visible_providers),
        "tier_counts": tier_counts,
        "models": scored,
        "providers": providers,
        "excluded_models": excluded,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Merge batch artifacts into current.json snapshot")
    ap.add_argument("--inputs", nargs="+", required=True,
                    help="Explicit list of batch_*.json files from a SINGLE run (never blind-glob)")
    ap.add_argument("--run-id", required=True, help="Run identifier, e.g. batch-2026-04-08")
    ap.add_argument("--tested-at", required=True,
                    help="REAL test date/time (e.g. 2026-04-08). Never 'now' — this is test provenance.")
    ap.add_argument("--generated-by", default="merge_batches.py",
                    help="Provenance note for generated_by")
    ap.add_argument("--out", required=True, help="Output path (e.g. sdk/data/compatibility/current.json)")
    args = ap.parse_args()

    inputs = [Path(p) for p in args.inputs]
    missing = [str(p) for p in inputs if not p.exists()]
    if missing:
        print("ERROR: missing input(s):\n  " + "\n  ".join(missing), file=sys.stderr)
        sys.exit(1)

    models = merge(inputs)
    snap = build_snapshot(models, args.run_id, args.tested_at, args.generated_by)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(snap, indent=2, ensure_ascii=False) + "\n"
    json.loads(content)  # validate
    out.write_text(content, encoding="utf-8")

    print(f"[ok] wrote {out}")
    print(f"  run_id={snap['run_id']} tested_at={snap['tested_at']}")
    print(f"  total_models={snap['total_models']} s_tier={snap['s_tier_count']} "
          f"pass_rate={snap['pass_rate']}%")
    print(f"  providers total={snap['provider_count_total']} visible={snap['provider_count_visible']} "
          f"excluded={snap['excluded_provider_count']}")
    print(f"  tier_counts={snap['tier_counts']}")
    print(f"  source_hash={snap['source_hash'][:16]}…")


if __name__ == "__main__":
    main()
