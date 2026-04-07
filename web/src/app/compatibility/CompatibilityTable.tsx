"use client";

import { useState, useMemo } from "react";
import type { ProviderData, ModelResult } from "./data";

const TIER_COLORS: Record<string, string> = {
  S: "bg-green-500/10 text-green-400 border-green-500/30",
  A: "bg-blue-500/10 text-blue-400 border-blue-500/30",
  B: "bg-yellow-500/10 text-yellow-400 border-yellow-500/30",
  C: "bg-orange-500/10 text-orange-400 border-orange-500/30",
  F: "bg-red-500/10 text-red-400 border-red-500/30",
};

function TierBadge({ tier }: { tier: string }) {
  return (
    <span
      className={`inline-flex h-6 w-6 items-center justify-center rounded text-xs font-bold border ${TIER_COLORS[tier] || ""}`}
    >
      {tier}
    </span>
  );
}

function ScenarioCell({ pass }: { pass: boolean }) {
  return (
    <td className="px-2 py-2 text-center text-sm">
      {pass ? (
        <span className="text-green-400">&#10004;</span>
      ) : (
        <span className="text-red-400/60">&#10006;</span>
      )}
    </td>
  );
}

interface Props {
  data: ProviderData[];
}

export default function CompatibilityTable({ data }: Props) {
  const [search, setSearch] = useState("");
  const [tierFilter, setTierFilter] = useState<string>("all");
  const [providerFilter, setProviderFilter] = useState<string>("all");

  // Flatten all models with provider info
  const allModels = useMemo(() => {
    const result: (ModelResult & { provider: string })[] = [];
    for (const p of data) {
      for (const m of p.models) {
        result.push({ ...m, provider: p.name });
      }
    }
    return result;
  }, [data]);

  // Get unique providers for filter dropdown
  const providers = useMemo(
    () => [...new Set(data.map((p) => p.name))].sort(),
    [data]
  );

  // Apply filters
  const filtered = useMemo(() => {
    let result = allModels;
    if (tierFilter !== "all") {
      result = result.filter((m) => m.tier === tierFilter);
    }
    if (providerFilter !== "all") {
      result = result.filter((m) => m.provider === providerFilter);
    }
    if (search.trim()) {
      const q = search.toLowerCase();
      result = result.filter(
        (m) =>
          m.model.toLowerCase().includes(q) ||
          m.provider.toLowerCase().includes(q)
      );
    }
    // Sort: tier (S first), then provider, then model
    const tierOrder: Record<string, number> = { S: 0, A: 1, B: 2, C: 3, F: 4 };
    result.sort(
      (a, b) =>
        (tierOrder[a.tier] ?? 9) - (tierOrder[b.tier] ?? 9) ||
        a.provider.localeCompare(b.provider) ||
        a.model.localeCompare(b.model)
    );
    return result;
  }, [allModels, tierFilter, providerFilter, search]);

  return (
    <div>
      {/* Filters */}
      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap items-center gap-2">
          <input
            type="text"
            placeholder="Search models..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="h-9 w-48 rounded-lg border border-border bg-card px-3 text-sm text-foreground placeholder:text-muted focus:border-primary focus:outline-none"
          />
          <select
            value={tierFilter}
            onChange={(e) => setTierFilter(e.target.value)}
            className="h-9 rounded-lg border border-border bg-card px-3 text-sm text-foreground focus:border-primary focus:outline-none"
          >
            <option value="all">All tiers</option>
            {["S", "A", "B", "C", "F"].map((t) => (
              <option key={t} value={t}>
                Tier {t}
              </option>
            ))}
          </select>
          <select
            value={providerFilter}
            onChange={(e) => setProviderFilter(e.target.value)}
            className="h-9 rounded-lg border border-border bg-card px-3 text-sm text-foreground focus:border-primary focus:outline-none"
          >
            <option value="all">All providers</option>
            {providers.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </div>
        <div className="text-sm text-muted">
          {filtered.length} of {allModels.length} models
        </div>
      </div>

      {/* Table */}
      <div className="overflow-x-auto rounded-xl border border-border">
        <table className="w-full text-left">
          <thead>
            <tr className="border-b border-border bg-card/50">
              <th className="px-4 py-3 text-xs font-medium uppercase tracking-wider text-muted">
                Provider
              </th>
              <th className="px-4 py-3 text-xs font-medium uppercase tracking-wider text-muted">
                Model
              </th>
              <th className="px-3 py-3 text-xs font-medium uppercase tracking-wider text-muted text-center">
                Tier
              </th>
              <th className="px-2 py-3 text-xs font-medium uppercase tracking-wider text-muted text-center" title="Capabilities List">
                S1
              </th>
              <th className="px-2 py-3 text-xs font-medium uppercase tracking-wider text-muted text-center" title="Search + Install">
                S2
              </th>
              <th className="px-2 py-3 text-xs font-medium uppercase tracking-wider text-muted text-center" title="Run Tool">
                S3
              </th>
              <th className="px-2 py-3 text-xs font-medium uppercase tracking-wider text-muted text-center" title="Multi-step Autonomous">
                S4
              </th>
              <th className="px-3 py-3 text-xs font-medium uppercase tracking-wider text-muted text-center">
                Score
              </th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((m) => (
              <tr
                key={`${m.provider}/${m.model}`}
                className="border-b border-border/50 hover:bg-card/30 transition-colors"
              >
                <td className="px-4 py-2 text-sm text-muted whitespace-nowrap">
                  {m.provider}
                </td>
                <td className="px-4 py-2 text-sm text-foreground font-mono whitespace-nowrap">
                  {m.model}
                </td>
                <td className="px-3 py-2 text-center">
                  <TierBadge tier={m.tier} />
                </td>
                <ScenarioCell pass={m.s1} />
                <ScenarioCell pass={m.s2} />
                <ScenarioCell pass={m.s3} />
                <ScenarioCell pass={m.s4} />
                <td className="px-3 py-2 text-center text-sm text-muted">
                  {m.passed}/{m.total}
                </td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr>
                <td
                  colSpan={8}
                  className="px-4 py-8 text-center text-sm text-muted"
                >
                  No models match your filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
