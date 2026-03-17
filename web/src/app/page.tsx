import Link from "next/link";
import TerminalAnimation from "@/components/TerminalAnimation";
import PackageCard from "@/components/PackageCard";
import CopyInstallButton from "./CopyInstallButton";

const starterPacks = [
  {
    slug: "pdf-reader-pack",
    name: "pdf-reader-pack",
    summary: "Extract text, tables, and metadata from PDF documents with high fidelity.",
    trust_level: "trusted" as const,
    frameworks: ["langchain", "crewai"],
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
    frameworks: ["langchain", "crewai", "generic"],
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
                Your AI agent upgrades itself
              </h1>
              <p className="animate-fade-in-delay-1 mt-6 text-lg leading-relaxed text-muted">
                Agents find, verify and install capabilities automatically.
                One package. Any agent.
              </p>
              <p className="animate-fade-in-delay-1 mt-3 text-sm text-muted/70">
                Powered by{" "}
                <span className="text-foreground/80 font-medium">ANP</span>{" "}
                (AgentNode Package)
              </p>

              <div className="animate-fade-in-delay-2 mt-8 flex flex-col gap-3 sm:flex-row sm:items-center">
                <CopyInstallButton />
                <Link
                  href="/search"
                  className="inline-flex h-11 items-center justify-center rounded-lg border border-border px-6 text-sm font-medium text-foreground transition-colors hover:bg-card"
                >
                  Browse Capabilities
                </Link>
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
                Every ANP package exposes the same{" "}
                <code className="rounded bg-background px-1 py-0.5 font-mono text-xs">run()</code>{" "}
                interface. Same contract, consistent behavior, no surprises.
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
            capability, verifies it, and installs it securely.
          </p>
          <div className="grid gap-6 sm:grid-cols-3">
            <div className="rounded-xl border border-border bg-card p-6 transition-all hover:border-primary/30">
              <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-lg font-bold text-primary">
                1
              </div>
              <h3 className="mb-2 text-lg font-semibold text-foreground">
                Finds the right skill
              </h3>
              <p className="text-sm leading-relaxed text-muted">
                Search by capability, not keywords. Your agent describes what
                it needs and gets ranked, trust-verified results.
              </p>
            </div>
            <div className="rounded-xl border border-border bg-card p-6 transition-all hover:border-primary/30">
              <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-lg font-bold text-primary">
                2
              </div>
              <h3 className="mb-2 text-lg font-semibold text-foreground">
                Verifies before installing
              </h3>
              <p className="text-sm leading-relaxed text-muted">
                Every package declares permissions, trust level, and
                compatibility. Your agent checks all of it before install.
              </p>
            </div>
            <div className="rounded-xl border border-border bg-card p-6 transition-all hover:border-primary/30">
              <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-lg font-bold text-primary">
                3
              </div>
              <h3 className="mb-2 text-lg font-semibold text-foreground">
                Installs instantly
              </h3>
              <p className="text-sm leading-relaxed text-muted">
                One call. Hash-verified download, dependency resolution, and
                lockfile — the capability is ready to use immediately.
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
                {["No PDF support", "No web access", "No data analysis", "Limited, static capabilities"].map((item) => (
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
                {["Reads and extracts PDFs", "Searches the web", "Analyzes structured data", "Adds new skills on demand"].map((item) => (
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
            From missing capability to working tool
          </h2>
          <p className="mx-auto mb-14 max-w-2xl text-center text-muted">
            Install the SDK once. Your agent handles the rest.
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
                  Resolve the gap
                </h3>
                <p className="mb-3 text-sm leading-relaxed text-muted">
                  Your agent describes what it needs — PDF extraction, web
                  search, data analysis — and gets ranked, trust-verified
                  results.
                </p>
                <code className="block overflow-x-auto rounded-lg border border-border bg-[#0d1117] px-4 py-3 font-mono text-sm text-gray-300">
                  matches = client.resolve([&quot;pdf_extraction&quot;])
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
                  Evaluate trust and permissions
                </h3>
                <p className="mb-3 text-sm leading-relaxed text-muted">
                  Review trust level, security permissions, and framework
                  compatibility — then pick the best match.
                </p>
                <code className="block overflow-x-auto rounded-lg border border-border bg-[#0d1117] px-4 py-3 font-mono text-sm text-gray-300">
                  client.can_install(matches[0].slug, require_trusted=True)
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
                  Install and use
                </h3>
                <p className="mb-3 text-sm leading-relaxed text-muted">
                  Install the package and call it through the ANP
                  interface. Every package works the same way.
                </p>
                <code className="block overflow-x-auto rounded-lg border border-border bg-[#0d1117] px-4 py-3 font-mono text-sm text-gray-300">
                  client.install(&quot;pdf-reader-pack&quot;)
                </code>
              </div>
            </div>
          </div>

          <p className="mt-8 text-center text-sm text-muted">
            Every capability follows the{" "}
            <span className="text-foreground font-medium">ANP interface</span>,
            ensuring consistent behavior across agents and frameworks.
          </p>
        </div>
      </section>

      {/* ============================================================ */}
      {/*  CODE EXAMPLE                                                */}
      {/* ============================================================ */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-20">
          <h2 className="mb-4 text-center text-2xl font-bold text-foreground sm:text-3xl">
            Four lines to autonomous upgrades
          </h2>
          <p className="mx-auto mb-12 max-w-2xl text-center text-muted">
            Your agent resolves, installs, and uses new capabilities without
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

# Agent finds and installs what it needs
result = client.resolve_and_install(["pdf_extraction"])

# Same interface across all packages
tool = client.load_tool("pdf-reader-pack")
data = tool.run("report.pdf")
print(data["text"])`}</code>
            </pre>
          </div>
          <p className="mt-4 text-center text-sm text-muted">
            Every package follows the{" "}
            <span className="text-foreground font-medium">ANP contract</span>.
            No custom adapters, no framework-specific code.
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
                Trust-first security
              </h3>
              <p className="text-sm leading-relaxed text-muted">
                Every package declares permissions, gets security-scanned, and
                earns trust tiers. Agents enforce policies before any
                install happens.
              </p>
            </div>
            <div className="rounded-xl border border-primary/30 bg-primary/5 p-6">
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
                Install in one call
              </h3>
              <p className="text-sm leading-relaxed text-muted">
                <code className="rounded bg-background px-1 py-0.5 font-mono text-xs">client.install()</code> handles
                download, hash verification, pip install, and lockfile — in a
                single function call.
              </p>
            </div>
            <div className="rounded-xl border border-border bg-card p-6 transition-all hover:border-primary/30">
              <h3 className="mb-2 text-base font-semibold text-foreground">
                Works with any framework
              </h3>
              <p className="text-sm leading-relaxed text-muted">
                LangChain, CrewAI, or your own stack. One package format,
                one consistent interface. No lock-in, no boilerplate
                rewrites.
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
              Agents that evolve on a shared package format
            </h2>
            <p className="mt-6 text-lg leading-relaxed text-muted">
              Today, agents resolve and install capabilities on demand.
              With ANP, those capabilities are portable, reusable, and
              designed to work across agent systems.
            </p>
            <p className="mt-4 text-base leading-relaxed text-muted">
              Tomorrow, agents won&apos;t just upgrade themselves. They&apos;ll
              build on a shared ecosystem of interoperable capabilities.
              AgentNode is the infrastructure that makes that possible.
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
              Start upgrading your agents
            </h2>
            <p className="mt-4 max-w-xl text-muted">
              Install the SDK, resolve your first capability gap, and let your
              agents handle the rest.
            </p>
            <div className="mt-10 flex flex-col items-center gap-4 sm:flex-row">
              <Link
                href="/docs"
                className="inline-flex h-12 items-center justify-center rounded-lg bg-primary px-8 text-sm font-medium text-white transition-colors hover:bg-primary/90"
              >
                Install SDK
              </Link>
              <Link
                href="/search"
                className="inline-flex h-12 items-center justify-center rounded-lg border border-border px-8 text-sm font-medium text-foreground transition-colors hover:bg-card"
              >
                Explore Capabilities
              </Link>
            </div>
            <div className="mt-8 flex flex-wrap items-center justify-center gap-x-3 gap-y-2 text-sm text-muted">
              <code className="rounded border border-border bg-card px-3 py-1.5 font-mono text-xs text-foreground">
                pip install agentnode-sdk
              </code>
              <span>·</span>
              <code className="rounded border border-border bg-card px-3 py-1.5 font-mono text-xs text-foreground">
                npm install -g agentnode-cli
              </code>
              <span>·</span>
              <code className="rounded border border-border bg-card px-3 py-1.5 font-mono text-xs text-foreground">
                pip install agentnode-langchain
              </code>
              <span>·</span>
              <code className="rounded border border-border bg-card px-3 py-1.5 font-mono text-xs text-foreground">
                pip install agentnode-mcp
              </code>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
