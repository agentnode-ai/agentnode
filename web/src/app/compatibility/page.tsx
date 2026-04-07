import type { Metadata } from "next";
import Link from "next/link";
import {
  COMPATIBILITY_DATA,
  LAST_UPDATED,
  TOTAL_MODELS,
  S_TIER_COUNT,
  PROVIDER_COUNT,
} from "./data";
import CompatibilityTable from "./CompatibilityTable";

export const metadata: Metadata = {
  title: "Model Compatibility — Verified with 175+ Models",
  description:
    "AgentNode Runtime is tested against 182 LLM models across 32 providers. See which models pass all 4 tool-calling scenarios.",
  openGraph: {
    title: "Model Compatibility — Verified with 175+ Models | AgentNode",
    description:
      "AgentNode Runtime is tested against 182 LLM models across 32 providers. Full compatibility matrix with per-scenario results.",
    type: "website",
    url: "https://agentnode.net/compatibility",
    siteName: "AgentNode",
  },
};

const TIER_INFO = [
  {
    tier: "S",
    label: "Perfect",
    desc: "4/4 scenarios passed",
    color: "border-green-500/30 bg-green-500/5 text-green-400",
  },
  {
    tier: "A",
    label: "Great",
    desc: "3/4 scenarios passed",
    color: "border-blue-500/30 bg-blue-500/5 text-blue-400",
  },
  {
    tier: "B",
    label: "Partial",
    desc: "2/4 scenarios passed",
    color: "border-yellow-500/30 bg-yellow-500/5 text-yellow-400",
  },
  {
    tier: "C",
    label: "Minimal",
    desc: "1/4 scenarios passed",
    color: "border-orange-500/30 bg-orange-500/5 text-orange-400",
  },
  {
    tier: "F",
    label: "Unsupported",
    desc: "0/4 scenarios passed",
    color: "border-red-500/30 bg-red-500/5 text-red-400",
  },
];

const SCENARIOS = [
  {
    id: "s1",
    name: "Capabilities",
    desc: "Model calls agentnode_capabilities to list installed tools",
  },
  {
    id: "s2",
    name: "Search + Install",
    desc: "Model searches for a package, then installs it",
  },
  {
    id: "s3",
    name: "Run Tool",
    desc: "Model executes a tool with specific arguments",
  },
  {
    id: "s4",
    name: "Multi-step",
    desc: "Model chains multiple tool calls autonomously",
  },
];

// Compute tier counts
const tierCounts: Record<string, number> = { S: 0, A: 0, B: 0, C: 0, F: 0 };
for (const provider of COMPATIBILITY_DATA) {
  for (const model of provider.models) {
    tierCounts[model.tier] = (tierCounts[model.tier] || 0) + 1;
  }
}

export default function CompatibilityPage() {
  const passRate = Math.round((S_TIER_COUNT / TOTAL_MODELS) * 100);

  return (
    <div className="flex flex-col">
      {/* Hero */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-16 sm:py-20">
          <div className="mx-auto max-w-3xl text-center">
            <h1 className="text-3xl font-bold tracking-tight text-foreground sm:text-4xl">
              Model Compatibility
            </h1>
            <p className="mt-4 text-lg text-muted">
              AgentNode Runtime tested against{" "}
              <span className="text-foreground font-semibold">{TOTAL_MODELS} models</span> from{" "}
              <span className="text-foreground font-semibold">{PROVIDER_COUNT} providers</span>.{" "}
              <span className="text-green-400 font-semibold">{S_TIER_COUNT}</span> pass all 4
              scenarios ({passRate}%).
            </p>
            <p className="mt-2 text-sm text-muted">
              Last tested: {LAST_UPDATED}
            </p>
          </div>

          {/* Stats row */}
          <div className="mt-10 mx-auto grid max-w-4xl grid-cols-2 gap-3 sm:grid-cols-4">
            <div className="rounded-xl border border-border bg-card p-4 text-center">
              <div className="text-2xl font-bold text-foreground">{TOTAL_MODELS}</div>
              <div className="text-xs text-muted mt-1">Models Tested</div>
            </div>
            <div className="rounded-xl border border-border bg-card p-4 text-center">
              <div className="text-2xl font-bold text-green-400">{S_TIER_COUNT}</div>
              <div className="text-xs text-muted mt-1">Perfect Score (S-Tier)</div>
            </div>
            <div className="rounded-xl border border-border bg-card p-4 text-center">
              <div className="text-2xl font-bold text-foreground">{PROVIDER_COUNT}</div>
              <div className="text-xs text-muted mt-1">Providers</div>
            </div>
            <div className="rounded-xl border border-border bg-card p-4 text-center">
              <div className="text-2xl font-bold text-primary">{passRate}%</div>
              <div className="text-xs text-muted mt-1">S-Tier Rate</div>
            </div>
          </div>
        </div>
      </section>

      {/* Tier legend */}
      <section className="border-b border-border bg-card/30">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-10">
          <h2 className="mb-6 text-lg font-semibold text-foreground text-center">Tier System</h2>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
            {TIER_INFO.map((t) => (
              <div
                key={t.tier}
                className={`rounded-xl border p-4 text-center ${t.color}`}
              >
                <div className="text-xl font-bold">{t.tier}</div>
                <div className="text-sm font-medium text-foreground mt-1">{t.label}</div>
                <div className="text-xs text-muted mt-0.5">{t.desc}</div>
                <div className="text-lg font-semibold text-foreground mt-2">
                  {tierCounts[t.tier] || 0}
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Scenarios explanation */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-10">
          <h2 className="mb-6 text-lg font-semibold text-foreground text-center">
            Test Scenarios
          </h2>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {SCENARIOS.map((s, i) => (
              <div key={s.id} className="rounded-xl border border-border bg-card p-4">
                <div className="flex items-center gap-2 mb-2">
                  <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-bold text-primary">
                    {i + 1}
                  </span>
                  <span className="text-sm font-semibold text-foreground">{s.name}</span>
                </div>
                <p className="text-xs text-muted leading-relaxed">{s.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Full matrix */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-10">
          <CompatibilityTable data={COMPATIBILITY_DATA} />
        </div>
      </section>

      {/* Methodology */}
      <section className="border-b border-border bg-card/30">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-12">
          <div className="mx-auto max-w-3xl">
            <h2 className="mb-4 text-lg font-semibold text-foreground text-center">
              Methodology
            </h2>
            <div className="space-y-3 text-sm text-muted leading-relaxed">
              <p>
                Every model is tested through OpenRouter using the OpenAI-compatible
                API format. Each model runs 4 real tool-calling scenarios against a
                live AgentNode Runtime instance with actual registered tools.
              </p>
              <p>
                A scenario passes when the model calls all expected tools with
                correct arguments and produces a coherent response. No mocking,
                no simulated tool calls. The model must generate valid function
                calls that the runtime can execute.
              </p>
              <p>
                Models that return API errors (rate limits, unavailable, etc.)
                are excluded from the matrix and not counted in any tier.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* CTA */}
      <section>
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-16">
          <div className="flex flex-col items-center text-center">
            <h2 className="text-2xl font-bold text-foreground">
              Works with your model
            </h2>
            <p className="mt-3 text-muted max-w-lg">
              {passRate}% of all tested models achieve perfect compatibility. Pick any
              model from your provider and start building.
            </p>
            <div className="mt-8 flex flex-wrap justify-center gap-3">
              <Link
                href="/docs#llm-runtime"
                className="inline-flex h-11 items-center justify-center rounded-lg bg-primary px-6 text-sm font-medium text-white transition-colors hover:bg-primary/90"
              >
                Runtime Docs
              </Link>
              <Link
                href="/getting-started"
                className="inline-flex h-11 items-center justify-center rounded-lg border border-border px-6 text-sm font-medium text-foreground transition-colors hover:bg-card"
              >
                Get Started
              </Link>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
