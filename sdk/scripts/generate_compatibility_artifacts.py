"""Generate compatibility artifacts from merged_matrix.json.

Source of truth: sdk/.artifacts/batch_reports/merged_matrix.json
Outputs:
  - backend/data/compatibility_matrix.json  (API data)
  - web/src/app/compatibility/data.ts       (Frontend TypeScript)
  - sdk/agentnode_sdk/compatibility.py       (SDK recommend_model)

Usage:
    python sdk/scripts/generate_compatibility_artifacts.py
    python sdk/scripts/generate_compatibility_artifacts.py --target backend
    python sdk/scripts/generate_compatibility_artifacts.py --target frontend
    python sdk/scripts/generate_compatibility_artifacts.py --target sdk
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
SOURCE = ROOT / "sdk" / ".artifacts" / "batch_reports" / "merged_matrix.json"

TARGETS = {
    "backend": ROOT / "backend" / "data" / "compatibility_matrix.json",
    "frontend": ROOT / "web" / "src" / "app" / "compatibility" / "data.ts",
    "sdk": ROOT / "sdk" / "agentnode_sdk" / "compatibility.py",
}

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


def load_matrix() -> list[dict[str, Any]]:
    if not SOURCE.exists():
        print(f"ERROR: Source file not found: {SOURCE}", file=sys.stderr)
        sys.exit(1)
    with open(SOURCE) as f:
        data = json.load(f)
    if not isinstance(data, list):
        print("ERROR: merged_matrix.json must be a JSON array", file=sys.stderr)
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

def generate_backend(models: list[dict], output_dir: Path | None = None) -> None:
    grouped = group_by_provider(models)
    stats = compute_stats(models)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
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
        "source_version": f"batch-{now[:10]}T00:00Z",
        **stats,
        "provider_count": len(grouped),
        "providers": providers,
    }

    content = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    # Validate before writing
    json.loads(content)

    target = (output_dir / "compatibility_matrix.json") if output_dir else TARGETS["backend"]
    atomic_write(target, content)
    print(f"  [ok] backend: {target}")


# ── Frontend TypeScript ───────────────────────────────────────────────────────

def generate_frontend(models: list[dict], output_dir: Path | None = None) -> None:
    grouped = group_by_provider(models)
    stats = compute_stats(models)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

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
        f"export const PROVIDER_COUNT = {len(grouped)};",
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


# Generated from merged_matrix.json — provider -> [(model, tier), ...]
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


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate compatibility artifacts")
    parser.add_argument(
        "--target",
        choices=["all", "backend", "frontend", "sdk"],
        default="all",
        help="Which artifact to generate (default: all)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Override output directory (for backend target only)",
    )
    args = parser.parse_args()

    print(f"Loading {SOURCE} ...")
    models = load_matrix()
    grouped = group_by_provider(models)
    stats = compute_stats(models)
    print(
        f"  {stats['total_models']} models, {stats['s_tier_count']} S-tier, "
        f"{len(grouped)} providers"
    )

    output_dir = args.output_dir

    if args.target in ("all", "backend"):
        generate_backend(models, output_dir if args.target == "backend" else None)
    if args.target in ("all", "frontend"):
        generate_frontend(models)
    if args.target in ("all", "sdk"):
        generate_sdk(models)

    print("Done.")


if __name__ == "__main__":
    main()
