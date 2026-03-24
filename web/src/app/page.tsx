import type { Metadata } from "next";
import Link from "next/link";
import TerminalAnimation from "@/components/TerminalAnimation";
import PackageCard from "@/components/PackageCard";
import CopyInstallButton from "./CopyInstallButton";

export const metadata: Metadata = {
  title: "Self-Extending AI Agents | Detect, Install & Run Skills with AgentNode",
  description:
    "Agents detect missing capabilities and safely acquire verified skills on demand. Discover, verify and run portable capabilities automatically with AgentNode and ANP.",
  openGraph: {
    title: "Self-Extending AI Agents | Detect, Install & Run Skills with AgentNode",
    description:
      "Agents detect missing capabilities and safely acquire verified skills on demand. Discover, verify and run portable capabilities automatically with AgentNode and ANP.",
    type: "website",
    url: "https://agentnode.net",
    siteName: "AgentNode",
  },
  twitter: {
    card: "summary_large_image",
    site: "@AgentNodenet",
  },
};

const starterPacks = [
  {
    slug: "pdf-reader-pack",
    name: "pdf-reader-pack",
    summary: "Extract text, tables, and metadata from PDF documents with high fidelity.",
    trust_level: "trusted" as const,
    frameworks: ["generic"],
    version: "1.2.0",
  },
  {
    slug: "web-search-pack",
    name: "web-search-pack",
    summary: "Search the web and retrieve structured results for your AI agent.",
    trust_level: "trusted" as const,
    frameworks: ["generic"],
    version: "1.0.0",
  },
  {
    slug: "webpage-extractor-pack",
    name: "webpage-extractor-pack",
    summary: "Extract clean text and metadata from any webpage for your AI agent.",
    trust_level: "trusted" as const,
    frameworks: ["generic"],
    version: "1.0.0",
  },
];

export default function HomePage() {
  return (
    <div className="flex flex-col">
      {/* ============================================================ */}
      {/*  HERO                                                        */}
      {/* ============================================================ */}
      <section className="relative overflow-hidden border-b border-border">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-primary/10 via-transparent to-transparent" />

        <div className="relative mx-auto max-w-6xl px-4 sm:px-6 pb-20 pt-24 sm:pt-32">
          <div className="flex flex-col items-center gap-12 lg:flex-row lg:items-start lg:gap-16">
            {/* Left: copy */}
            <div className="flex max-w-xl flex-col items-center text-center lg:items-start lg:text-left">
              <h1 className="animate-fade-in text-4xl font-bold leading-tight tracking-tight text-foreground sm:text-5xl">
                Agents That <span className="text-primary">Extend Themselves</span> — Safely
              </h1>
              <p className="animate-fade-in-delay-1 mt-6 text-lg leading-relaxed text-muted">
                Detect missing capabilities. Acquire verified skills on demand. No human intervention.
              </p>
              <p className="animate-fade-in-delay-1 mt-3 text-sm text-muted/70">
                Powered by{" "}
                <span className="text-foreground/80 font-medium">ANP</span>{" "}
                (AgentNode Package)
              </p>

              <div className="animate-fade-in-delay-2 mt-8 flex flex-col gap-3">
                <CopyInstallButton />
                <div className="flex flex-wrap gap-3">
                  <Link
                    href="/docs#installation"
                    className="inline-flex h-11 items-center justify-center rounded-lg bg-primary px-6 text-sm font-medium text-white transition-colors hover:bg-primary/90"
                  >
                    Get Started
                  </Link>
                  <Link
                    href="/search"
                    className="inline-flex h-11 items-center justify-center rounded-lg border border-border px-6 text-sm font-medium text-foreground transition-colors hover:bg-card"
                  >
                    Browse Capabilities
                  </Link>
                </div>
              </div>
            </div>

            {/* Right: terminal */}
            <div className="animate-fade-in-delay-3 w-full max-w-2xl flex-shrink-0 lg:w-[580px]">
              <TerminalAnimation />
            </div>
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/*  ANP STANDARD — Core differentiator                          */}
      {/* ============================================================ */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-20">
          <div className="mx-auto max-w-3xl text-center">
            <h2 className="mb-4 text-2xl font-bold text-foreground sm:text-3xl">
              One package. Any agent.
            </h2>
            <p className="mb-12 text-lg leading-relaxed text-muted">
              AgentNode is built on{" "}
              <span className="text-foreground font-medium">ANP</span>{" "}
              (AgentNode Package), a portable package format for AI agent
              capabilities. Build a capability once and use it across LangChain,
              CrewAI, and custom agents.
            </p>
          </div>
          <div className="grid gap-6 sm:grid-cols-3">
            <div className="rounded-xl border border-border bg-card p-6 text-center">
              <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-lg bg-primary/10 text-xl">
                🔗
              </div>
              <h3 className="mb-2 text-base font-semibold text-foreground">
                Cross-framework
              </h3>
              <p className="text-sm leading-relaxed text-muted">
                Works across LangChain, CrewAI, and custom agents. One package
                format, multiple ecosystems.
              </p>
            </div>
            <div className="rounded-xl border border-border bg-card p-6 text-center">
              <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-lg bg-primary/10 text-xl">
                📐
              </div>
              <h3 className="mb-2 text-base font-semibold text-foreground">
                Same interface everywhere
              </h3>
              <p className="text-sm leading-relaxed text-muted">
                Every ANP package exposes typed tool functions via{" "}
                <code className="rounded bg-background px-1 py-0.5 font-mono text-xs">load_tool()</code>{" "}
                — one pack can provide multiple tools, each individually addressable.
              </p>
            </div>
            <div className="rounded-xl border border-border bg-card p-6 text-center">
              <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-lg bg-primary/10 text-xl">
                🚫
              </div>
              <h3 className="mb-2 text-base font-semibold text-foreground">
                No rewrites, no adapters
              </h3>
              <p className="text-sm leading-relaxed text-muted">
                Install once and use across your stack. No framework
                lock-in, no custom adapters.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/*  AUTONOMY — Agents don't wait                                */}
      {/* ============================================================ */}
      <section className="border-b border-border bg-card/30">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-20">
          <h2 className="mb-4 text-center text-2xl font-bold text-foreground sm:text-3xl">
            Agents don&apos;t wait for developers
          </h2>
          <p className="mx-auto mb-12 max-w-2xl text-center text-muted">
            Your agent detects what it&apos;s missing, finds the best matching
            capability, verifies it, installs it, and retries — automatically.
          </p>
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
            <div className="rounded-xl border border-border bg-card p-6 transition-all hover:border-primary/30">
              <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-lg font-bold text-primary">
                1
              </div>
              <h3 className="mb-2 text-lg font-semibold text-foreground">
                Detects the gap
              </h3>
              <p className="text-sm leading-relaxed text-muted">
                When your agent fails, AgentNode analyzes the error and
                identifies the missing capability — with confidence levels.
              </p>
            </div>
            <div className="rounded-xl border border-border bg-card p-6 transition-all hover:border-primary/30">
              <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-lg font-bold text-primary">
                2
              </div>
              <h3 className="mb-2 text-lg font-semibold text-foreground">
                Resolves the best match
              </h3>
              <p className="text-sm leading-relaxed text-muted">
                Search by capability, not keywords. Your agent gets ranked,
                trust-verified results automatically.
              </p>
            </div>
            <div className="rounded-xl border border-border bg-card p-6 transition-all hover:border-primary/30">
              <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-lg font-bold text-primary">
                3
              </div>
              <h3 className="mb-2 text-lg font-semibold text-foreground">
                Installs with trust
              </h3>
              <p className="text-sm leading-relaxed text-muted">
                Auto-upgrade policies control what gets installed.
                Only verified or trusted packages — your rules.
              </p>
            </div>
            <div className="rounded-xl border border-border bg-card p-6 transition-all hover:border-primary/30">
              <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-lg font-bold text-primary">
                4
              </div>
              <h3 className="mb-2 text-lg font-semibold text-foreground">
                Retries once
              </h3>
              <p className="text-sm leading-relaxed text-muted">
                After installing the missing skill, your agent retries
                exactly once. No loops, no surprises.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/*  BEFORE / AFTER                                              */}
      {/* ============================================================ */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-20">
          <h2 className="mb-4 text-center text-2xl font-bold text-foreground sm:text-3xl">
            Close capability gaps instantly
          </h2>
          <p className="mx-auto mb-12 max-w-2xl text-center text-muted">
            Turn limited agents into modular systems that grow with every task.
          </p>
          <div className="mx-auto grid max-w-3xl gap-6 sm:grid-cols-2">
            {/* Before */}
            <div className="rounded-xl border border-border bg-card p-6">
              <div className="mb-4 text-sm font-semibold uppercase tracking-wider text-muted">
                Before
              </div>
              <ul className="space-y-3">
                {["Fails on missing libraries", "No idea what's missing", "Manual installs for every gap", "Limited, static capabilities"].map((item) => (
                  <li key={item} className="flex items-center gap-3 text-sm text-muted">
                    <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-red-500/10 text-xs text-red-400">✕</span>
                    {item}
                  </li>
                ))}
              </ul>
            </div>
            {/* After */}
            <div className="rounded-xl border border-primary/30 bg-primary/5 p-6">
              <div className="mb-4 text-sm font-semibold uppercase tracking-wider text-primary">
                After AgentNode
              </div>
              <ul className="space-y-3">
                {["Detects missing capabilities", "Installs verified skills automatically", "Retries with the new capability", "Extends itself under your control"].map((item) => (
                  <li key={item} className="flex items-center gap-3 text-sm text-foreground">
                    <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-green-500/10 text-xs text-green-400">✓</span>
                    {item}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/*  HOW IT WORKS — Agent autonomy flow                          */}
      {/* ============================================================ */}
      <section className="border-b border-border bg-card/30">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-20">
          <h2 className="mb-4 text-center text-2xl font-bold text-foreground sm:text-3xl">
            From runtime failure to working capability
          </h2>
          <p className="mx-auto mb-14 max-w-2xl text-center text-muted">
            Wrap your logic once. AgentNode handles detection, installation, and retry.
          </p>

          <div className="mx-auto max-w-3xl space-y-10">
            {/* Step 1 */}
            <div className="flex gap-4 sm:gap-6">
              <div className="flex flex-col items-center">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary/10 text-lg font-bold text-primary">
                  1
                </div>
                <div className="mt-2 w-px flex-1 bg-border" />
              </div>
              <div className="flex-1 min-w-0 pb-4">
                <h3 className="mb-1 text-lg font-semibold text-foreground">
                  Wrap your agent logic
                </h3>
                <p className="mb-3 text-sm leading-relaxed text-muted">
                  Use <code className="rounded bg-background px-1 py-0.5 font-mono text-xs">smart_run()</code> to
                  wrap any callable. If it fails due to a missing capability,
                  AgentNode takes over.
                </p>
                <code className="block overflow-x-auto rounded-lg border border-border bg-[#0d1117] px-4 py-3 font-mono text-sm text-gray-300">
                  result = client.smart_run(fn, auto_upgrade_policy=&quot;safe&quot;)
                </code>
              </div>
            </div>

            {/* Step 2 */}
            <div className="flex gap-4 sm:gap-6">
              <div className="flex flex-col items-center">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary/10 text-lg font-bold text-primary">
                  2
                </div>
                <div className="mt-2 w-px flex-1 bg-border" />
              </div>
              <div className="flex-1 min-w-0 pb-4">
                <h3 className="mb-1 text-lg font-semibold text-foreground">
                  Detect and install automatically
                </h3>
                <p className="mb-3 text-sm leading-relaxed text-muted">
                  AgentNode analyzes the error, detects the missing capability
                  with confidence scoring, and installs the best verified match.
                </p>
                <code className="block overflow-x-auto rounded-lg border border-border bg-[#0d1117] px-4 py-3 font-mono text-sm text-gray-300">
                  # Automatic: detect → resolve → trust check → install
                </code>
              </div>
            </div>

            {/* Step 3 */}
            <div className="flex gap-4 sm:gap-6">
              <div className="flex flex-col items-center">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary/10 text-lg font-bold text-primary">
                  3
                </div>
              </div>
              <div className="flex-1 min-w-0 pb-4">
                <h3 className="mb-1 text-lg font-semibold text-foreground">
                  Retry and succeed
                </h3>
                <p className="mb-3 text-sm leading-relaxed text-muted">
                  After installing the missing skill, your function is retried
                  exactly once. The result includes full transparency: what was
                  detected, what was installed, and timing.
                </p>
                <code className="block overflow-x-auto rounded-lg border border-border bg-[#0d1117] px-4 py-3 font-mono text-sm text-gray-300">
                  print(result.success, result.installed_slug, result.duration_ms)
                </code>
              </div>
            </div>
          </div>

          <p className="mt-8 text-center text-sm text-muted">
            Or use{" "}
            <code className="rounded bg-background px-1 py-0.5 font-mono text-xs">detect_and_install()</code>{" "}
            for fine-grained control over detection and installation separately.
          </p>
        </div>
      </section>

      {/* ============================================================ */}
      {/*  CODE EXAMPLE                                                */}
      {/* ============================================================ */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-20">
          <h2 className="mb-4 text-center text-2xl font-bold text-foreground sm:text-3xl">
            Three lines to self-extending agents
          </h2>
          <p className="mx-auto mb-12 max-w-2xl text-center text-muted">
            Your agent detects, installs, and retries — without
            human intervention.
          </p>
          <div className="mx-auto max-w-2xl overflow-hidden rounded-lg border border-border bg-[#0d1117]">
            <div className="flex items-center gap-2 border-b border-border/50 px-4 py-2">
              <div className="h-3 w-3 rounded-full bg-red-500/60" />
              <div className="h-3 w-3 rounded-full bg-yellow-500/60" />
              <div className="h-3 w-3 rounded-full bg-green-500/60" />
              <span className="ml-2 font-mono text-xs text-muted">agent.py</span>
            </div>
            <pre className="overflow-x-auto p-4 font-mono text-sm leading-relaxed text-gray-300">
              <code>{`from agentnode_sdk import AgentNodeClient

client = AgentNodeClient(api_key="ank_...")

# Agent runs logic — if a capability is missing, AgentNode
# detects the gap, installs the skill, and retries once
result = client.smart_run(
    lambda: process_pdf("report.pdf"),
    auto_upgrade_policy="safe",  # only verified+ skills
)

print(result.success)             # True
print(result.detected_capability) # "pdf_extraction"
print(result.installed_slug)      # "pdf-reader-pack"

# Or use detect_and_install() for fine-grained control
try:
    data = analyze_csv("data.csv")
except Exception as exc:
    upgrade = client.detect_and_install(exc, auto_upgrade_policy="safe")
    if upgrade.installed:
        data = analyze_csv("data.csv")`}</code>
            </pre>
          </div>
          <p className="mt-4 text-center text-sm text-muted">
            Policies control what gets installed:{" "}
            <code className="rounded bg-background/50 px-1 py-0.5 font-mono text-xs">&quot;off&quot;</code>{" "}
            <code className="rounded bg-background/50 px-1 py-0.5 font-mono text-xs">&quot;safe&quot;</code>{" "}
            <code className="rounded bg-background/50 px-1 py-0.5 font-mono text-xs">&quot;strict&quot;</code>
            . No hidden behavior.
          </p>
        </div>
      </section>

      {/* ============================================================ */}
      {/*  FEATURES                                                    */}
      {/* ============================================================ */}
      <section className="border-b border-border bg-card/30">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-20">
          <h2 className="mb-4 text-center text-2xl font-bold text-foreground sm:text-3xl">
            Everything your agent needs to evolve
          </h2>
          <p className="mx-auto mb-12 max-w-2xl text-center text-muted">
            Not just a package registry. A trust and capability layer built
            for autonomous agents.
          </p>
          <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
            <div className="rounded-xl border border-primary/30 bg-primary/5 p-6">
              <h3 className="mb-2 text-base font-semibold text-foreground">
                Capability gap detection
              </h3>
              <p className="text-sm leading-relaxed text-muted">
                Agents analyze runtime errors to detect missing capabilities
                — with high, medium, and low confidence levels. No LLM, fully
                deterministic.
              </p>
            </div>
            <div className="rounded-xl border border-primary/30 bg-primary/5 p-6">
              <h3 className="mb-2 text-base font-semibold text-foreground">
                Auto-upgrade policies
              </h3>
              <p className="text-sm leading-relaxed text-muted">
                Three named policies control what gets installed:{" "}
                <code className="rounded bg-background px-1 py-0.5 font-mono text-xs">off</code>{" "}
                <code className="rounded bg-background px-1 py-0.5 font-mono text-xs">safe</code>{" "}
                <code className="rounded bg-background px-1 py-0.5 font-mono text-xs">strict</code>.
                Your rules, enforced at runtime.
              </p>
            </div>
            <div className="rounded-xl border border-border bg-card p-6 transition-all hover:border-primary/30">
              <h3 className="mb-2 text-base font-semibold text-foreground">
                Capability-first discovery
              </h3>
              <p className="text-sm leading-relaxed text-muted">
                Agents search by what they need, not by package name. The
                resolution engine matches capabilities to the best trusted
                packages automatically.
              </p>
            </div>
            <div className="rounded-xl border border-border bg-card p-6 transition-all hover:border-primary/30">
              <h3 className="mb-2 text-base font-semibold text-foreground">
                Scored on publish
              </h3>
              <p className="text-sm leading-relaxed text-muted">
                Every package is installed, smoke-tested, and scored 0&ndash;100
                in a real sandbox. Broken tools are quarantined. Working tools
                earn a verification tier.
              </p>
            </div>
            <div className="rounded-xl border border-border bg-card p-6 transition-all hover:border-primary/30">
              <h3 className="mb-2 text-base font-semibold text-foreground">
                Portable package format (ANP)
              </h3>
              <p className="text-sm leading-relaxed text-muted">
                Build once, run on any agent. Every ANP package follows the
                same interface across LangChain, CrewAI, and custom agents.
              </p>
            </div>
            <div className="rounded-xl border border-border bg-card p-6 transition-all hover:border-primary/30">
              <h3 className="mb-2 text-base font-semibold text-foreground">
                Permission declarations
              </h3>
              <p className="text-sm leading-relaxed text-muted">
                Every package explicitly declares network, filesystem, code
                execution, and data access levels. No hidden behavior.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/*  VERIFICATION — Every tool verified, every score earned      */}
      {/* ============================================================ */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-20">
          <h2 className="mb-4 text-center text-2xl font-bold text-foreground sm:text-3xl">
            Every tool verified. Every score earned.
          </h2>
          <p className="mx-auto mb-12 max-w-2xl text-center text-muted">
            We don&apos;t just list tools &mdash; we install them in a sandbox, run them, and
            score them 0&ndash;100. Broken packages are quarantined. Working packages earn
            their verification tier.
          </p>

          {/* 4-step visual */}
          <div className="mx-auto max-w-3xl grid grid-cols-4 gap-3 mb-10">
            {[
              { icon: "\u2714", label: "Install", desc: "Clean sandbox" },
              { icon: "\u2714", label: "Import", desc: "Entrypoints load" },
              { icon: "\u2714", label: "Smoke Test", desc: "Tools called" },
              { icon: "\u2714", label: "Score", desc: "Reliability proven" },
            ].map((step) => (
              <div key={step.label} className="flex flex-col items-center gap-2 rounded-xl border border-green-500/20 bg-green-500/5 p-4">
                <span className="text-lg text-green-400">{step.icon}</span>
                <span className="text-sm font-medium text-foreground">{step.label}</span>
                <span className="text-[11px] text-muted text-center">{step.desc}</span>
              </div>
            ))}
          </div>

          {/* Tier cards */}
          <div className="mx-auto max-w-3xl grid grid-cols-2 sm:grid-cols-4 gap-3 mb-10">
            <div className="rounded-xl border border-yellow-500/20 bg-yellow-500/5 p-4 text-center">
              <div className="text-lg text-yellow-400 mb-1">{"\u2605"}</div>
              <div className="text-sm font-semibold text-foreground">Gold</div>
              <div className="text-[11px] text-muted">Score 90+</div>
            </div>
            <div className="rounded-xl border border-green-500/20 bg-green-500/5 p-4 text-center">
              <div className="text-lg text-green-400 mb-1">{"\u2714"}</div>
              <div className="text-sm font-semibold text-foreground">Verified</div>
              <div className="text-[11px] text-muted">Score 70&ndash;89</div>
            </div>
            <div className="rounded-xl border border-yellow-500/20 bg-yellow-500/5 p-4 text-center">
              <div className="text-lg text-yellow-400 mb-1">{"\u25CB"}</div>
              <div className="text-sm font-semibold text-foreground">Partial</div>
              <div className="text-[11px] text-muted">Score 50&ndash;69</div>
            </div>
            <div className="rounded-xl border border-zinc-500/20 bg-zinc-500/5 p-4 text-center">
              <div className="text-lg text-zinc-500 mb-1">&mdash;</div>
              <div className="text-sm font-semibold text-foreground">Unverified</div>
              <div className="text-[11px] text-muted">Score &lt;50</div>
            </div>
          </div>

          <div className="mx-auto max-w-3xl grid gap-6 sm:grid-cols-2">
            <div className="rounded-xl border border-border bg-card p-6">
              <h3 className="mb-3 text-base font-semibold text-foreground">
                What we test
              </h3>
              <ul className="space-y-2">
                <li className="flex items-start gap-2 text-sm text-muted">
                  <span className="text-green-400 mt-0.5 shrink-0">&#10004;</span>
                  Real pip install in isolated environment
                </li>
                <li className="flex items-start gap-2 text-sm text-muted">
                  <span className="text-green-400 mt-0.5 shrink-0">&#10004;</span>
                  Every declared entrypoint imports
                </li>
                <li className="flex items-start gap-2 text-sm text-muted">
                  <span className="text-green-400 mt-0.5 shrink-0">&#10004;</span>
                  Tools called with schema-generated inputs
                </li>
                <li className="flex items-start gap-2 text-sm text-muted">
                  <span className="text-green-400 mt-0.5 shrink-0">&#10004;</span>
                  Multi-run reliability and determinism
                </li>
              </ul>
            </div>
            <div className="rounded-xl border border-border bg-card p-6">
              <h3 className="mb-3 text-base font-semibold text-foreground">
                What we don&apos;t fake
              </h3>
              <ul className="space-y-2">
                <li className="flex items-start gap-2 text-sm text-muted">
                  <span className="text-primary mt-0.5 shrink-0">&bull;</span>
                  No mocking &mdash; real sandbox execution
                </li>
                <li className="flex items-start gap-2 text-sm text-muted">
                  <span className="text-primary mt-0.5 shrink-0">&bull;</span>
                  No self-reported badges &mdash; scores from evidence
                </li>
                <li className="flex items-start gap-2 text-sm text-muted">
                  <span className="text-primary mt-0.5 shrink-0">&bull;</span>
                  No pass/fail binary &mdash; nuanced tiers
                </li>
                <li className="flex items-start gap-2 text-sm text-muted">
                  <span className="text-primary mt-0.5 shrink-0">&bull;</span>
                  No stale results &mdash; re-verified every 30 days
                </li>
              </ul>
            </div>
          </div>

          <p className="mt-8 text-center text-sm text-muted">
            A registry is only useful if the tools inside it work.{" "}
            <span className="text-foreground font-medium">AgentNode doesn&apos;t just check &mdash; it proves it, scores it, and keeps checking.</span>
          </p>
        </div>
      </section>

      {/* ============================================================ */}
      {/*  POPULAR CAPABILITIES                                        */}
      {/* ============================================================ */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-20">
          <h2 className="mb-4 text-center text-2xl font-bold text-foreground sm:text-3xl">
            Popular capabilities
          </h2>
          <p className="mx-auto mb-12 max-w-2xl text-center text-muted">
            Trusted ANP packages ready to extend your agents.
          </p>
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {starterPacks.map((pack) => (
              <PackageCard key={pack.slug} {...pack} />
            ))}
          </div>
          <div className="mt-8 text-center">
            <Link
              href="/search"
              className="text-sm text-primary transition-colors hover:text-foreground"
            >
              Browse all capabilities &rarr;
            </Link>
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/*  VISION                                                      */}
      {/* ============================================================ */}
      <section className="border-b border-border bg-card/30">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-20">
          <div className="mx-auto max-w-2xl text-center">
            <h2 className="text-2xl font-bold text-foreground sm:text-3xl">
              A runtime that extends itself
            </h2>
            <p className="mt-6 text-lg leading-relaxed text-muted">
              Today, agents detect missing capabilities and safely acquire
              verified skills on demand. With ANP, those capabilities are
              portable, reusable, and designed to work across agent systems.
            </p>
            <p className="mt-4 text-base leading-relaxed text-muted">
              AgentNode is no longer just a tool registry. It&apos;s a runtime
              that can extend itself — safely, predictably, and under your
              control.
            </p>
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/*  FINAL CTA                                                   */}
      {/* ============================================================ */}
      <section>
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-24">
          <div className="flex flex-col items-center text-center">
            <h2 className="text-2xl font-bold text-foreground sm:text-3xl">
              Start building self-extending agents
            </h2>
            <p className="mt-4 max-w-xl text-muted">
              Detect your first capability gap and let your agents handle the rest.
            </p>
            <div className="mt-10 flex flex-col items-center gap-4 sm:flex-row">
              <Link
                href="/docs#installation"
                className="inline-flex h-12 items-center justify-center rounded-lg bg-primary px-8 text-sm font-medium text-white transition-colors hover:bg-primary/90"
              >
                Get Started
              </Link>
              <Link
                href="/search"
                className="inline-flex h-12 items-center justify-center rounded-lg border border-border px-8 text-sm font-medium text-foreground transition-colors hover:bg-card"
              >
                Explore Capabilities
              </Link>
            </div>
            <div className="mt-10 mx-auto grid max-w-4xl gap-3 sm:grid-cols-3">
              <div className="rounded-lg border border-border bg-card px-5 py-3 text-center">
                <div className="mb-1 text-xs font-medium uppercase tracking-wider text-muted">Python &middot; for agents &amp; apps</div>
                <code className="whitespace-nowrap font-mono text-xs text-foreground">pip install agentnode-sdk</code>
              </div>
              <div className="rounded-lg border border-border bg-card px-5 py-3 text-center">
                <div className="mb-1 text-xs font-medium uppercase tracking-wider text-muted">Terminal &middot; install &amp; publish</div>
                <code className="whitespace-nowrap font-mono text-xs text-foreground">npm install -g agentnode-cli</code>
              </div>
              <div className="rounded-lg border border-border bg-card px-5 py-3 text-center">
                <div className="mb-1 text-xs font-medium uppercase tracking-wider text-muted">Frameworks &middot; LangChain, MCP</div>
                <code className="whitespace-nowrap font-mono text-xs text-foreground">pip install agentnode-langchain</code>
                <div className="mt-0.5">
                  <code className="whitespace-nowrap font-mono text-xs text-foreground">pip install agentnode-mcp</code>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
