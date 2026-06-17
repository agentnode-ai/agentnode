"""Generate compatibility artifacts from the central snapshot.

Source of truth: sdk/data/compatibility/current.json  (built by merge_batches.py)
Outputs:
  - backend/data/compatibility_matrix.json  (API data)
  - web/src/app/compatibility/data.ts       (Frontend TypeScript)
  - sdk/agentnode_sdk/compatibility.py       (SDK recommend_model)
  - web/public/llms.txt, web/public/llms-full.txt  (marker-injected compat line)

Dates (LAST_UPDATED / generated_at) come from the snapshot's `tested_at` — never now() —
so old data can never be re-stamped as fresh.

Usage:
    python sdk/scripts/generate_compatibility_artifacts.py
    python sdk/scripts/generate_compatibility_artifacts.py --target frontend|backend|sdk|llms
    python sdk/scripts/generate_compatibility_artifacts.py --target frontend --output-dir /tmp
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
SOURCE = ROOT / "sdk" / "data" / "compatibility" / "current.json"

TARGETS = {
    "backend": ROOT / "backend" / "data" / "compatibility_matrix.json",
    "frontend": ROOT / "web" / "src" / "app" / "compatibility" / "data.ts",
    "sdk": ROOT / "sdk" / "agentnode_sdk" / "compatibility.py",
}

# Static public files: the compat line lives between these markers and is regenerated.
LLMS_TARGETS = {
    "llms": ROOT / "web" / "public" / "llms.txt",
    "llms-full": ROOT / "web" / "public" / "llms-full.txt",
}
_COMPAT_START = "<!-- compat:start (auto-generated from current.json — do not edit by hand) -->"
_COMPAT_END = "<!-- compat:end -->"

# Curated recommendations (provider -> {best, cheapest})
_RECOMMENDED = {
    None: {"best": "gpt-4o", "cheapest": "gpt-4o-mini"},
    "openai": {"best": "gpt-4o", "cheapest": "gpt-4o-mini"},
    "anthropic": {"best": "claude-sonnet-4.6", "cheapest": "claude-haiku-4.5"},
    "google": {"best": "gemini-2.5-flash", "cheapest": "gemini-2.0-flash-001"},
    "mistralai": {"best": "mistral-large", "cheapest": "mistral-nemo"},
    "meta-llama": {"best": "llama-4-maverick", "cheapest": "llama-3.1-8b-instruct"},
    "deepseek": {"best": "deepseek-chat", "cheapest": "deepseek-chat"},
    "qwen": {"best": "qwen3-235b-a22b", "cheapest": "qwen3-30b-a3b"},
    "x-ai": {"best": "grok-4", "cheapest": "grok-3-mini"},
    "cohere": {"best": "command-r-plus-08-2024", "cheapest": "command-r-08-2024"},
    "nvidia": {"best": "llama-3.3-nemotron-super-49b-v1.5", "cheapest": "nemotron-nano-9b-v2"},
    "amazon": {"best": "nova-pro-v1", "cheapest": "nova-micro-v1"},
    "minimax": {"best": "minimax-m2.7", "cheapest": "minimax-m1"},
    "z-ai": {"best": "glm-5", "cheapest": "glm-4.7-flash"},
    "inception": {"best": "mercury-2", "cheapest": "mercury"},
    "moonshotai": {"best": "kimi-k2.5", "cheapest": "kimi-k2"},
    "xiaomi": {"best": "mimo-v2-pro", "cheapest": "mimo-v2-flash"},
    "bytedance-seed": {"best": "seed-1.6", "cheapest": "seed-2.0-mini"},
}

SCENARIO_KEYS = {
    "1. Capabilities List": "s1",
    "2. Search + Install": "s2",
    "3. Run Tool (word counter)": "s3",
    "4. Multi-step Autonomous": "s4",
}


def load_snapshot() -> dict[str, Any]:
    if not SOURCE.exists():
        print(f"ERROR: Source file not found: {SOURCE}", file=sys.stderr)
        sys.exit(1)
    with open(SOURCE) as f:
        data = json.load(f)
    if not isinstance(data, dict) or "models" not in data:
        print("ERROR: current.json must be an object with a 'models' array", file=sys.stderr)
        sys.exit(1)
    required = ("tested_at", "run_id", "total_models", "s_tier_count", "pass_rate",
               "provider_count_total", "provider_count_visible", "tier_counts")
    missing = [k for k in required if k not in data]
    if missing:
        print(f"ERROR: current.json missing fields: {missing}", file=sys.stderr)
        sys.exit(1)
    return data


def group_by_provider(models: list[dict]) -> dict[str, list[dict]]:
    providers: dict[str, list[dict]] = defaultdict(list)
    for m in models:
        parts = m["model"].split("/", 1)
        if len(parts) == 2:
            provider, model_name = parts
        else:
            provider, model_name = "unknown", parts[0]
        providers[provider].append({**m, "_short_model": model_name})
    return dict(sorted(providers.items()))


def compute_stats(models: list[dict]) -> dict[str, Any]:
    tier_counts: dict[str, int] = defaultdict(int)
    for m in models:
        tier_counts[m["tier"]] = tier_counts.get(m["tier"], 0) + 1
    # Exclude X-tier from totals (provider errors, not real results)
    countable = [m for m in models if m["tier"] != "X"]
    s_tier = sum(1 for m in countable if m["tier"] == "S")
    total = len(countable)
    pass_rate = round(s_tier / total * 100) if total > 0 else 0
    return {
        "total_models": total,
        "s_tier_count": s_tier,
        "pass_rate": pass_rate,
        "tier_counts": dict(sorted(tier_counts.items())),
    }


def atomic_write(target: Path, content: str) -> None:
    """Write to a temp file then move atomically."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(target.parent), suffix=".tmp", prefix=target.name
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        shutil.move(tmp_path, str(target))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ── Backend JSON ──────────────────────────────────────────────────────────────

def generate_backend(models: list[dict], snapshot: dict, output_dir: Path | None = None) -> None:
    grouped = group_by_provider(models)

    now = snapshot["tested_at"]
    providers = []
    for provider_name, provider_models in grouped.items():
        model_entries = []
        for m in provider_models:
            scenarios = {}
            for full_name, short_key in SCENARIO_KEYS.items():
                scenarios[short_key] = m.get("scenarios", {}).get(full_name) == "PASS"
            model_entries.append({
                "model": m["_short_model"],
                "tier": m["tier"],
                "passed": m["passed"],
                "total": m["total"],
                "scenarios": scenarios,
            })
        providers.append({"name": provider_name, "models": model_entries})

    result = {
        "generated_at": now,
        "source_version": snapshot["run_id"],
        "total_models": snapshot["total_models"],
        "s_tier_count": snapshot["s_tier_count"],
        "pass_rate": snapshot["pass_rate"],
        "tier_counts": snapshot["tier_counts"],
        "provider_count": snapshot["provider_count_total"],
        "provider_count_total": snapshot["provider_count_total"],
        "provider_count_visible": snapshot["provider_count_visible"],
        "providers": providers,
    }

    content = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    # Validate before writing
    json.loads(content)

    target = (output_dir / "compatibility_matrix.json") if output_dir else TARGETS["backend"]
    atomic_write(target, content)
    print(f"  [ok] backend: {target}")


# ── Frontend TypeScript ───────────────────────────────────────────────────────

def generate_frontend(models: list[dict], snapshot: dict, output_dir: Path | None = None) -> None:
    grouped = group_by_provider(models)
    stats = compute_stats(models)
    today = snapshot["tested_at"]

    lines = [
        "// Auto-generated from batch verification results",
        f"// Last updated: {today}",
        "",
        "export interface ModelResult {",
        '  model: string;',
        '  tier: "S" | "A" | "B" | "C" | "F";',
        "  passed: number;",
        "  total: number;",
        "  s1: boolean;",
        "  s2: boolean;",
        "  s3: boolean;",
        "  s4: boolean;",
        "}",
        "",
        "export interface ProviderData {",
        "  name: string;",
        "  models: ModelResult[];",
        "}",
        "",
        f'export const LAST_UPDATED = "{today}";',
        f"export const TOTAL_MODELS = {stats['total_models']};",
        f"export const S_TIER_COUNT = {stats['s_tier_count']};",
        f"export const PROVIDER_COUNT = {snapshot['provider_count_total']};",
        "",
        "export const COMPATIBILITY_DATA: ProviderData[] = [",
    ]

    for provider_name, provider_models in grouped.items():
        # Exclude X-tier from frontend
        visible = [m for m in provider_models if m["tier"] != "X"]
        if not visible:
            continue
        lines.append("  {")
        lines.append(f'    name: "{provider_name}",')
        lines.append("    models: [")
        for m in visible:
            s = m.get("scenarios", {})
            s1 = "true" if s.get("1. Capabilities List") == "PASS" else "false"
            s2 = "true" if s.get("2. Search + Install") == "PASS" else "false"
            s3 = "true" if s.get("3. Run Tool (word counter)") == "PASS" else "false"
            s4 = "true" if s.get("4. Multi-step Autonomous") == "PASS" else "false"
            tier = m["tier"]
            name = m["_short_model"]
            passed = m["passed"]
            total = m["total"]
            lines.append(
                f'      {{ model: "{name}", tier: "{tier}", passed: {passed}, '
                f"total: {total}, s1: {s1}, s2: {s2}, s3: {s3}, s4: {s4} }},"
            )
        lines.append("    ],")
        lines.append("  },")

    lines.append("];")
    lines.append("")

    content = "\n".join(lines)
    target = (output_dir / "data.ts") if output_dir else TARGETS["frontend"]
    atomic_write(target, content)
    print(f"  [ok] frontend: {target}")


# ── SDK compatibility.py ─────────────────────────────────────────────────────

def generate_sdk(models: list[dict], output_dir: Path | None = None) -> None:
    grouped = group_by_provider(models)

    # Build _TIER_DATA
    tier_data_lines = []
    for provider_name, provider_models in grouped.items():
        entries = []
        for m in provider_models:
            if m["tier"] == "X":
                continue
            entries.append(f'("{m["_short_model"]}", "{m["tier"]}")')
        if entries:
            joined = ", ".join(entries)
            tier_data_lines.append(f'    "{provider_name}": [{joined}],')

    # Build _RECOMMENDED
    rec_lines = ['    None: {"best": "gpt-4o", "cheapest": "gpt-4o-mini"},']
    for prov, recs in _RECOMMENDED.items():
        if prov is None:
            continue
        rec_lines.append(f'    "{prov}": {{"best": "{recs["best"]}", "cheapest": "{recs["cheapest"]}"}},')

    content = f'''"""AgentNode compatibility data and model recommendations.

Auto-generated by sdk/scripts/generate_compatibility_artifacts.py
Manual edits to _RECOMMENDED are preserved across regeneration.
"""
from __future__ import annotations


_TIER_ORDER = {{"S": 0, "A": 1, "B": 2, "C": 3, "F": 4}}


def _tier_passes(tier: str, minimum: str) -> bool:
    """Check if a tier meets the minimum requirement."""
    return _TIER_ORDER.get(tier, 99) <= _TIER_ORDER.get(minimum, 0)


# Generated from current.json — provider -> [(model, tier), ...]
_TIER_DATA: dict[str, list[tuple[str, str]]] = {{
{chr(10).join(tier_data_lines)}
}}

# Curated recommendations — override layer
_RECOMMENDED: dict[str | None, dict[str, str]] = {{
{chr(10).join(rec_lines)}
}}


def recommend_model(
    provider: str | None = None,
    *,
    prefer: str = "best",
    minimum_tier: str = "S",
) -> str | None:
    """Recommend a model for a given provider.

    Args:
        provider: Provider name (e.g. "openai", "anthropic"). None for overall best.
        prefer: "best" or "cheapest".
        minimum_tier: Minimum acceptable tier ("S", "A", "B", "C", "F").

    Returns:
        Model name string, or None if no model meets the criteria.

    Raises:
        ValueError: If prefer or minimum_tier is invalid.
    """
    if prefer not in ("best", "cheapest"):
        raise ValueError(f"Unknown prefer value: {{prefer!r}}. Use 'best' or 'cheapest'.")
    if minimum_tier not in _TIER_ORDER:
        raise ValueError(
            f"Unknown minimum_tier: {{minimum_tier!r}}. Use one of: {{', '.join(_TIER_ORDER)}}."
        )

    if provider is not None:
        provider = provider.lower()

    # Try curated recommendation first
    recs = _RECOMMENDED.get(provider)
    if recs:
        candidate = recs.get(prefer)
        if candidate:
            # Verify the candidate meets the minimum tier
            tier_list = _TIER_DATA.get(provider, [])
            for model_name, tier in tier_list:
                if model_name == candidate and _tier_passes(tier, minimum_tier):
                    return candidate

    # Fallback: find best model from tier data that meets minimum
    if provider is None:
        # Overall: check all providers
        for prov, models in _TIER_DATA.items():
            for model_name, tier in models:
                if _tier_passes(tier, minimum_tier):
                    # Return overall curated default if available
                    overall = _RECOMMENDED.get(None, {{}})
                    fallback = overall.get(prefer)
                    if fallback:
                        return fallback
                    return model_name
        return None

    tier_list = _TIER_DATA.get(provider, [])
    if not tier_list:
        return None

    # Find any model meeting the minimum tier
    for model_name, tier in tier_list:
        if _tier_passes(tier, minimum_tier):
            return model_name

    return None
'''

    target = (output_dir / "compatibility.py") if output_dir else TARGETS["sdk"]
    atomic_write(target, content)
    print(f"  [ok] sdk: {target}")


# ── Static public files (llms.txt / llms-full.txt) ───────────────────────────

def _llms_block(snapshot: dict, full: bool) -> str:
    n = snapshot["total_models"]
    p = snapshot["provider_count_total"]
    s = snapshot["s_tier_count"]
    if full:
        return (
            f"Compatibility: tested with {n} LLM models across {p} providers ({s} pass all\n"
            f"scenarios). Details: https://agentnode.net/compatibility and\n"
            f"https://agentnode.net/docs/llm-providers"
        )
    return (
        f"- Compatibility: Tested with {n} LLM models across {p} providers "
        f"({s} pass all scenarios) — plus any OpenAI-compatible endpoint, including "
        f"local Ollama without an API key"
    )


def generate_llms(snapshot: dict, output_dir: Path | None = None) -> None:
    for key, src in LLMS_TARGETS.items():
        text = src.read_text(encoding="utf-8")
        if _COMPAT_START not in text or _COMPAT_END not in text:
            print(f"ERROR: compat markers not found in {src}", file=sys.stderr)
            sys.exit(1)
        pre, rest = text.split(_COMPAT_START, 1)
        _, post = rest.split(_COMPAT_END, 1)
        block = _llms_block(snapshot, full=(key == "llms-full"))
        new = f"{pre}{_COMPAT_START}\n{block}\n{_COMPAT_END}{post}"
        target = (output_dir / src.name) if output_dir else src
        atomic_write(target, new)
        print(f"  [ok] {key}: {target}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate compatibility artifacts")
    parser.add_argument(
        "--target",
        choices=["all", "backend", "frontend", "sdk", "llms"],
        default="all",
        help="Which artifact to generate (default: all)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Override output directory (dry-run; writes copies there instead of the real files)",
    )
    args = parser.parse_args()

    print(f"Loading {SOURCE} ...")
    snapshot = load_snapshot()
    models = snapshot["models"]
    print(
        f"  {snapshot['total_models']} models, {snapshot['s_tier_count']} S-tier, "
        f"{snapshot['provider_count_total']} providers "
        f"(visible {snapshot['provider_count_visible']}, tested_at {snapshot['tested_at']})"
    )

    output_dir = args.output_dir

    if args.target in ("all", "backend"):
        generate_backend(models, snapshot, output_dir)
    if args.target in ("all", "frontend"):
        generate_frontend(models, snapshot, output_dir)
    if args.target in ("all", "sdk"):
        generate_sdk(models, output_dir)
    if args.target in ("all", "llms"):
        generate_llms(snapshot, output_dir)

    print("Done.")


if __name__ == "__main__":
    main()
