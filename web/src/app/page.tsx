import Link from "next/link";
import TerminalAnimation from "@/components/TerminalAnimation";
import FeatureCard from "@/components/FeatureCard";
import PackageCard from "@/components/PackageCard";
import CopyInstallButton from "./CopyInstallButton";

const features = [
  {
    icon: "\uD83D\uDD0D",
    title: "Capability Resolution",
    description:
      "Find the right upgrade based on what your agent needs, not just keywords. Describe a capability gap and get matched to the best package.",
  },
  {
    icon: "\uD83D\uDD12",
    title: "Trust & Policy",
    description:
      "Every package verified for security, permissions, and compatibility. Know exactly what a package can access before you install it.",
  },
  {
    icon: "\uD83D\uDD27",
    title: "Framework Agnostic",
    description:
      "Works with LangChain, CrewAI, or any Python agent framework. One registry, every ecosystem.",
  },
];

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
      {/* Hero */}
      <section className="relative overflow-hidden border-b border-border">
        {/* Gradient background */}
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-primary/10 via-transparent to-transparent" />

        <div className="relative mx-auto max-w-6xl px-6 pb-20 pt-24 sm:pt-32">
          <div className="flex flex-col items-center gap-12 lg:flex-row lg:items-start lg:gap-16">
            {/* Left: copy */}
            <div className="flex max-w-xl flex-col items-center text-center lg:items-start lg:text-left">
              <h1 className="animate-fade-in text-4xl font-bold leading-tight tracking-tight text-foreground sm:text-5xl">
                Find and install the capabilities your AI agent is missing
              </h1>
              <p className="animate-fade-in-delay-1 mt-6 text-lg leading-relaxed text-muted">
                AgentNode helps developers and AI hosts resolve capability gaps
                with trust-aware, framework-compatible upgrade recommendations.
              </p>

              <div className="animate-fade-in-delay-2 mt-8 flex flex-col gap-3 sm:flex-row sm:items-center">
                <CopyInstallButton />
                <Link
                  href="/search"
                  className="inline-flex h-11 items-center justify-center rounded-lg border border-border px-6 text-sm font-medium text-foreground transition-colors hover:bg-card"
                >
                  Browse Packages
                </Link>
              </div>
            </div>

            {/* Right: terminal */}
            <div className="animate-fade-in-delay-3 w-full max-w-2xl flex-shrink-0 lg:w-auto">
              <TerminalAnimation />
            </div>
          </div>
        </div>
      </section>

      {/* Features */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-6xl px-6 py-20">
          <h2 className="mb-4 text-center text-2xl font-bold text-foreground sm:text-3xl">
            Built for the agent ecosystem
          </h2>
          <p className="mb-12 text-center text-muted">
            Everything you need to extend your AI agents with confidence.
          </p>
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {features.map((feature) => (
              <FeatureCard key={feature.title} {...feature} />
            ))}
          </div>
        </div>
      </section>

      {/* Starter Packs */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-6xl px-6 py-20">
          <h2 className="mb-4 text-center text-2xl font-bold text-foreground sm:text-3xl">
            Starter Packs
          </h2>
          <p className="mb-12 text-center text-muted">
            Get started with curated packages for common agent capabilities.
          </p>
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {starterPacks.map((pack) => (
              <PackageCard key={pack.slug} {...pack} />
            ))}
          </div>
        </div>
      </section>

      {/* Bottom CTA */}
      <section>
        <div className="mx-auto max-w-6xl px-6 py-20">
          <div className="flex flex-col items-center text-center">
            <h2 className="text-2xl font-bold text-foreground sm:text-3xl">
              Ready to upgrade your agents?
            </h2>
            <p className="mt-4 text-muted">
              Install the SDK and start resolving capability gaps in minutes.
            </p>
            <div className="mt-8 flex flex-col items-center gap-4">
              <code className="rounded-lg border border-border bg-card px-6 py-3 font-mono text-sm text-foreground">
                pip install agentnode-sdk
              </code>
              <Link
                href="/search"
                className="text-sm text-primary transition-colors hover:text-primary-hover"
              >
                or browse the package registry &rarr;
              </Link>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
