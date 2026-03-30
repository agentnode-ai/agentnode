import type { Metadata } from "next";
import Link from "next/link";
import TerminalAnimation from "@/components/TerminalAnimation";
import PackageCard from "@/components/PackageCard";
import AiStackLogos from "@/components/AiStackLogos";
import CopyInstallButton from "./CopyInstallButton";

export const metadata: Metadata = {
  title: "Verified Agent Skills for AI Agents | Auto-Detect & Install | AgentNode",
  description:
    "AI agents detect missing capabilities and install verified skills on demand. Trust-gated auto-upgrades with confidence scoring. Portable ANP format for LangChain, CrewAI, MCP, and Python.",
  openGraph: {
    title: "Verified Agent Skills for AI Agents | Auto-Detect & Install | AgentNode",
    description:
      "AI agents detect missing capabilities and install verified skills on demand. Trust-gated auto-upgrades with confidence scoring. Portable ANP format for LangChain, CrewAI, MCP, and Python.",
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
                Verified <span className="text-primary">Agent Skills</span> for Every AI Agent
              </h1>
              <p className="animate-fade-in-delay-1 mt-6 text-lg leading-relaxed text-foreground/80">
                Your agent detects what&apos;s missing, installs verified skills, and keeps going. Works with LangChain, CrewAI, MCP, and plain Python.
              </p>
              <p className="animate-fade-in-delay-1 mt-3 text-sm text-muted">
                Powered by ANP. One portable package format for all AI agents.
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
              capabilities. Build a capability once and use it with any Python agent framework.
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
      {/*  WORKS WITH YOUR AI STACK                                    */}
      {/* ============================================================ */}
      <section className="border-b border-border bg-card/30">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-20">
          <h2 className="mb-4 text-center text-2xl font-bold text-foreground sm:text-3xl">
            Works with your AI stack
          </h2>
          <p className="mx-auto mb-12 max-w-2xl text-center text-muted">
            Native runtime integration for OpenAI, Anthropic, and Gemini.
            Framework adapters for LangChain, CrewAI, and MCP.
            Compatible with any system that runs Python.
          </p>

          <AiStackLogos />

          <p className="mx-auto mt-12 max-w-2xl text-center text-sm text-muted/70">
            AgentNode tools are standard Python packages.
            They work with any system that supports Python execution, tool calling, or API integration.
          </p>

          <div className="mt-8 text-center">
            <Link
              href="/getting-started"
              className="inline-flex items-center gap-2 text-sm font-medium text-primary hover:text-primary-hover transition-colors"
            >
              See how to use AgentNode
              <span aria-hidden="true">&rarr;</span>
            </Link>
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/*  AUTONOMY — Agents don't wait                                */}
      {/* ============================================================ */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-20">
          <h2 className="mb-4 text-center text-2xl font-bold text-foreground sm:text-3xl">
            Agents don&apos;t wait for developers
          </h2>
          <p className="mx-auto mb-12 max-w-2xl text-center text-muted">
            One loop. Fully automatic.
          </p>

          {/* Visual flow — scannable at a glance */}
          <div className="mx-auto max-w-4xl flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-0 mb-12">
            {[
              { icon: "\u2718", label: "Fails", desc: "Missing capability", color: "border-red-500/30 bg-red-500/5", iconColor: "text-red-400" },
              { icon: "\uD83D\uDD0D", label: "Detect", desc: "Analyze error", color: "border-primary/30 bg-primary/5", iconColor: "text-primary" },
              { icon: "\uD83D\uDCE6", label: "Install", desc: "Verified skill", color: "border-primary/30 bg-primary/5", iconColor: "text-primary" },
              { icon: "\uD83D\uDD01", label: "Retry", desc: "Exactly once", color: "border-primary/30 bg-primary/5", iconColor: "text-primary" },
              { icon: "\u2714", label: "Works", desc: "Capability added", color: "border-green-500/30 bg-green-500/5", iconColor: "text-green-400" },
            ].map((step, i) => (
              <div key={step.label} className="flex items-center gap-3 sm:gap-0">
                <div className={`flex flex-col items-center gap-1.5 rounded-xl border ${step.color} px-5 py-4 min-w-[100px]`}>
                  <span className={`text-xl ${step.iconColor}`}>{step.icon}</span>
                  <span className="text-sm font-semibold text-foreground">{step.label}</span>
                  <span className="text-[11px] text-muted">{step.desc}</span>
                </div>
                {i < 4 && (
                  <span className="hidden sm:block text-muted/50 text-lg px-2">{"\u2192"}</span>
                )}
              </div>
            ))}
          </div>

          {/* Detail cards below */}
          <div className="mx-auto max-w-4xl grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <div className="rounded-xl border border-border bg-card p-5">
              <h3 className="mb-1 text-sm font-semibold text-foreground">Gap Detection</h3>
              <p className="text-xs leading-relaxed text-muted">
                Analyzes ImportErrors, error messages, and context hints. Three confidence levels: high, medium, low.
              </p>
            </div>
            <div className="rounded-xl border border-border bg-card p-5">
              <h3 className="mb-1 text-sm font-semibold text-foreground">Smart Resolution</h3>
              <p className="text-xs leading-relaxed text-muted">
                Finds the best package by capability match, trust level, and compatibility score.
              </p>
            </div>
            <div className="rounded-xl border border-border bg-card p-5">
              <h3 className="mb-1 text-sm font-semibold text-foreground">Trust-Gated Install</h3>
              <p className="text-xs leading-relaxed text-muted">
                Policies control what gets installed. <code className="font-mono text-[10px]">off</code> / <code className="font-mono text-[10px]">safe</code> / <code className="font-mono text-[10px]">strict</code> — your rules.
              </p>
            </div>
            <div className="rounded-xl border border-border bg-card p-5">
              <h3 className="mb-1 text-sm font-semibold text-foreground">Single Retry</h3>
              <p className="text-xs leading-relaxed text-muted">
                After installing, your function is retried exactly once. No loops, no runaway installs.
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
            Three lines. That&apos;s it.
          </h2>
          <p className="mx-auto mb-12 max-w-2xl text-center text-muted">
            Wrap your logic. AgentNode handles the rest.
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

result = client.smart_run(
    lambda: process_pdf("report.pdf"),
    auto_upgrade_policy="safe",
)

# If pdfplumber was missing → detected, installed, retried
print(result.success)        # True
print(result.installed_slug) # "pdf-reader-pack"`}</code>
            </pre>
          </div>
          <p className="mt-6 text-center text-sm text-muted">
            Want more control?{" "}
            Use <code className="rounded bg-background/50 px-1 py-0.5 font-mono text-xs">detect_and_install()</code> to
            handle detection and installation separately.{" "}
            <Link href="/docs#python-sdk" className="text-primary hover:text-foreground transition-colors">See docs</Link>
          </p>
        </div>
      </section>

      {/* ============================================================ */}
      {/*  LLM RUNTIME — Direct OpenAI / Anthropic integration         */}
      {/* ============================================================ */}
      <section className="border-b border-border bg-card/30">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-20">
          <h2 className="mb-4 text-center text-2xl font-bold text-foreground sm:text-3xl">
            Or let the LLM decide
          </h2>
          <p className="mx-auto mb-12 max-w-2xl text-center text-muted">
            Connect any OpenAI or Anthropic agent to AgentNode. The Runtime registers tools,
            injects the system prompt, and runs the tool loop automatically.
          </p>
          <div className="mx-auto grid max-w-5xl gap-6 lg:grid-cols-2">
            {/* OpenAI */}
            <div className="overflow-hidden rounded-lg border border-border bg-[#0d1117]">
              <div className="flex items-center gap-2 border-b border-border/50 px-4 py-2">
                <div className="h-3 w-3 rounded-full bg-red-500/60" />
                <div className="h-3 w-3 rounded-full bg-yellow-500/60" />
                <div className="h-3 w-3 rounded-full bg-green-500/60" />
                <span className="ml-2 font-mono text-xs text-muted">openai_agent.py</span>
              </div>
              <pre className="overflow-x-auto p-4 font-mono text-sm leading-relaxed text-gray-300">
                <code>{`from openai import OpenAI
from agentnode_sdk import AgentNodeRuntime

runtime = AgentNodeRuntime()
client = OpenAI()

result = runtime.run(
    provider="openai",
    client=client,
    model="gpt-4o",
    messages=[{
        "role": "user",
        "content": "Extract text from report.pdf"
    }],
)
print(result.content)`}</code>
              </pre>
            </div>
            {/* Anthropic */}
            <div className="overflow-hidden rounded-lg border border-border bg-[#0d1117]">
              <div className="flex items-center gap-2 border-b border-border/50 px-4 py-2">
                <div className="h-3 w-3 rounded-full bg-red-500/60" />
                <div className="h-3 w-3 rounded-full bg-yellow-500/60" />
                <div className="h-3 w-3 rounded-full bg-green-500/60" />
                <span className="ml-2 font-mono text-xs text-muted">anthropic_agent.py</span>
              </div>
              <pre className="overflow-x-auto p-4 font-mono text-sm leading-relaxed text-gray-300">
                <code>{`from anthropic import Anthropic
from agentnode_sdk import AgentNodeRuntime

runtime = AgentNodeRuntime()
client = Anthropic()

result = runtime.run(
    provider="anthropic",
    client=client,
    model="claude-sonnet-4-6",
    messages=[{
        "role": "user",
        "content": "Search for PDF tools"
    }],
)`}</code>
              </pre>
            </div>
          </div>
          <p className="mt-8 text-center text-sm text-muted">
            The LLM discovers, installs, and runs tools autonomously &mdash; no hardcoded tool calls.{" "}
            <Link href="/docs#llm-runtime" className="text-primary hover:text-foreground transition-colors">
              Runtime docs
            </Link>
          </p>
        </div>
      </section>

      {/* ============================================================ */}
      {/*  FEATURES                                                    */}
      {/* ============================================================ */}
      <section className="border-b border-border bg-card/30">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-20">
          <h2 className="mb-4 text-center text-2xl font-bold text-foreground sm:text-3xl">
            Not just a registry. A policy-controlled runtime.
          </h2>
          <p className="mx-auto mb-12 max-w-2xl text-center text-muted">
            Everything your agent needs to grow — safely and autonomously.
          </p>
          <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {/* Row 1: Core — Self-extension */}
            <div className="rounded-xl border border-primary/30 bg-primary/5 p-6">
              <h3 className="mb-2 text-base font-semibold text-foreground">
                Capability gap detection
              </h3>
              <p className="text-sm leading-relaxed text-muted">
                Analyzes runtime errors to identify missing capabilities.
                Three confidence levels. No LLM — fully deterministic.
              </p>
            </div>
            <div className="rounded-xl border border-primary/30 bg-primary/5 p-6">
              <h3 className="mb-2 text-base font-semibold text-foreground">
                Auto-upgrade policies
              </h3>
              <p className="text-sm leading-relaxed text-muted">
                <code className="rounded bg-background px-1 py-0.5 font-mono text-xs">off</code>{" "}
                <code className="rounded bg-background px-1 py-0.5 font-mono text-xs">safe</code>{" "}
                <code className="rounded bg-background px-1 py-0.5 font-mono text-xs">strict</code>
                {" "}&mdash; you decide what gets installed, not the agent.
              </p>
            </div>
            {/* Row 2: Trust & Safety */}
            <div className="rounded-xl border border-primary/30 bg-primary/5 p-6">
              <h3 className="mb-2 text-base font-semibold text-foreground">
                Scored and verified
              </h3>
              <p className="text-sm leading-relaxed text-muted">
                Every package is sandbox-tested and scored 0&ndash;100 on publish.
                Broken tools are quarantined. Verified tools earn higher trust tiers.
              </p>
            </div>
            <div className="rounded-xl border border-border bg-card p-6 transition-all hover:border-primary/30">
              <h3 className="mb-2 text-base font-semibold text-foreground">
                Permission declarations
              </h3>
              <p className="text-sm leading-relaxed text-muted">
                Every package declares network, filesystem, code execution,
                and data access levels. No hidden behavior.
              </p>
            </div>
            {/* Row 3: Platform */}
            <div className="rounded-xl border border-border bg-card p-6 transition-all hover:border-primary/30">
              <h3 className="mb-2 text-base font-semibold text-foreground">
                Cross-framework (ANP)
              </h3>
              <p className="text-sm leading-relaxed text-muted">
                Build once, run on any agent. LangChain, CrewAI, or custom.
                One package format, no adapters.
              </p>
            </div>
            <div className="rounded-xl border border-border bg-card p-6 transition-all hover:border-primary/30">
              <h3 className="mb-2 text-base font-semibold text-foreground">
                Capability-first resolution
              </h3>
              <p className="text-sm leading-relaxed text-muted">
                Agents search by what they need, not by name. The engine
                scores and ranks matches automatically.
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
            score them 0&ndash;100. Broken packages are quarantined. Passing packages earn
            trust tiers based on their score.
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
                  Verification runs on every publish
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
              A policy-controlled runtime
            </h2>
            <p className="mt-6 text-lg leading-relaxed text-muted">
              Today, agents detect missing capabilities and safely acquire
              verified skills on demand. With ANP, those capabilities are
              portable, reusable, and designed to work across agent systems.
            </p>
            <p className="mt-4 text-base leading-relaxed text-muted">
              AgentNode is no longer just a tool registry. It&apos;s a runtime
              that installs and executes capabilities — safely, predictably,
              and governed by your policies.
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
              Start building smarter agents
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
