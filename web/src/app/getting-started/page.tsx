import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  // The root layout title template appends "| AgentNode" — no brand suffix here.
  title: "Getting Started",
  description:
    "Get started with AgentNode — run your first capability, set up providers and keys, publish a package, or review the security model.",
  alternates: {
    canonical: "/getting-started",
  },
  openGraph: {
    title: "Getting Started | AgentNode",
    description:
      "Get started with AgentNode — run your first capability, set up providers and keys, publish a package, or review the security model.",
    type: "website",
    url: "https://agentnode.net/getting-started",
    siteName: "AgentNode",
  },
  twitter: {
    card: "summary_large_image",
    site: "@AgentNodenet",
  },
};

interface PathLink {
  href: string;
  label: string;
}
interface PathCard {
  title: string;
  body: string;
  links: PathLink[];
}

const paths: PathCard[] = [
  {
    title: "Run your first capability",
    body: "Install the SDK, then search, install, and run a verified package — the hands-on walkthrough.",
    links: [{ href: "/docs/quickstart", label: "Quick Start" }],
  },
  {
    title: "Set up providers & keys",
    body: "Connect an LLM provider and store credentials in your OS keychain.",
    links: [
      { href: "/docs/llm-providers", label: "LLM Providers" },
      { href: "/docs/credentials", label: "Credentials" },
    ],
  },
  {
    title: "Publish a package",
    body: "Package a Tool Pack, Skill, or Agent and publish it to the registry.",
    links: [
      { href: "/publish", label: "Publish" },
      { href: "/for-developers", label: "Developer overview" },
    ],
  },
  {
    title: "Review security",
    body: "See how trust tiers, policy checks, and the sandbox govern what runs.",
    links: [
      { href: "/security", label: "Security model" },
      { href: "/docs/sandbox", label: "Execution sandbox" },
    ],
  },
];

const policies: { name: string; tag: string | null; body: string }[] = [
  { name: "safe", tag: "default", body: "Auto-installs only verified-or-higher packages." },
  { name: "strict", tag: null, body: "Auto-installs only trusted or curated packages — the tightest bar." },
  { name: "off", tag: null, body: "Detection only — never installs automatically; you decide what to add." },
];

export default function GettingStartedPage() {
  return (
    <main className="mx-auto max-w-4xl px-4 py-16 sm:px-6">
      {/* Hero */}
      <h1 className="text-3xl font-bold tracking-tight text-foreground sm:text-4xl">
        Get started with AgentNode
      </h1>
      <p className="mt-4 max-w-2xl text-lg leading-relaxed text-muted">
        AgentNode helps agents discover, install, and run verified capabilities
        under trust tiers and policy controls. Pick a path below, or run the
        install commands to start now.
      </p>
      <p className="mt-3 text-sm text-muted">
        Building an AI agent?{" "}
        <a
          href="/llms-full.txt"
          target="_blank"
          rel="noopener noreferrer"
          className="text-primary transition-colors hover:text-foreground"
        >
          Read the machine-readable guide (llms-full.txt) &rarr;
        </a>
      </p>

      {/* Choose your path */}
      <h2 className="mb-6 mt-14 text-2xl font-bold text-foreground">
        Choose your path
      </h2>
      <div className="grid gap-4 sm:grid-cols-2">
        {paths.map((card) => (
          <div key={card.title} className="rounded-xl border border-border bg-card p-6">
            <h3 className="mb-2 text-base font-semibold text-foreground">
              {card.title}
            </h3>
            <p className="mb-4 text-sm leading-relaxed text-muted">{card.body}</p>
            <div className="flex flex-wrap gap-x-4 gap-y-1.5 text-sm">
              {card.links.map((l) => (
                <Link
                  key={l.href}
                  href={l.href}
                  className="text-primary transition-colors hover:text-foreground"
                >
                  {l.label} &rarr;
                </Link>
              ))}
            </div>
          </div>
        ))}
      </div>

      {/* Install */}
      <h2 className="mb-4 mt-14 text-2xl font-bold text-foreground">
        Install and run
      </h2>
      <p className="mb-4 text-sm leading-relaxed text-muted">
        The CLI ships with the SDK. No account is needed to search, install, or
        run packages. Full walkthrough:{" "}
        <Link href="/docs/quickstart" className="text-primary hover:underline">
          Quick Start
        </Link>
        .
      </p>
      <pre className="overflow-x-auto rounded-lg bg-[#0d1117] px-4 py-3 text-sm leading-relaxed text-gray-300">
        <code>{`$ pip install agentnode-sdk
$ agentnode setup
$ agentnode search "pdf extraction"
$ agentnode install pdf-reader-pack
$ agentnode run pdf-reader-pack --input '{"file_path":"report.pdf"}'`}</code>
      </pre>
      <p className="mt-4 text-sm leading-relaxed text-muted">
        <code className="font-mono text-foreground">agentnode setup</code> is an
        interactive wizard covering every first-class setting as a
        multiple-choice prompt with a recommended default — installation
        behavior, trust level, permissions, guard posture, credentials, the
        sandbox, and{" "}
        <code className="font-mono text-foreground">
          sandbox.host_trust_policy
        </code>{" "}
        (which trust tiers may run directly on your host). Accepting the
        recommendations reproduces the default config; it is fully skippable and
        never blocks CI.
      </p>

      {/* Ollama / local */}
      <h2 className="mb-4 mt-14 text-2xl font-bold text-foreground">
        Prefer local, no key?
      </h2>
      <p className="text-sm leading-relaxed text-muted">
        Ollama can run models locally with no hosted API key &mdash; once Ollama
        is installed, running, and a model is pulled. AgentNode never installs or
        starts it for you. See{" "}
        <Link href="/docs/llm-providers" className="text-primary hover:underline">
          LLM Providers
        </Link>{" "}
        to select it.
      </p>

      {/* Auto-upgrade policies */}
      <h2 className="mb-4 mt-14 text-2xl font-bold text-foreground">
        Auto-upgrade policies
      </h2>
      <p className="mb-6 text-sm leading-relaxed text-muted">
        You control whether your agent installs missing capabilities on its own.
        Set the policy once; it applies to automatic installs.
      </p>
      <div className="grid gap-4 sm:grid-cols-3">
        {policies.map((p) => (
          <div key={p.name} className="rounded-xl border border-border bg-card p-5">
            <div className="mb-2 flex items-center gap-2">
              <code className="font-mono text-sm font-semibold text-foreground">
                {p.name}
              </code>
              {p.tag && (
                <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
                  {p.tag}
                </span>
              )}
            </div>
            <p className="text-sm leading-relaxed text-muted">{p.body}</p>
          </div>
        ))}
      </div>

      {/* Related */}
      <h2 className="mb-4 mt-14 text-2xl font-bold text-foreground">Related</h2>
      <ul className="grid gap-2 sm:grid-cols-2">
        {[
          { href: "/docs/quickstart", label: "Quick Start" },
          { href: "/docs/credentials", label: "Credentials & Connectors" },
          { href: "/docs/llm-providers", label: "LLM Providers" },
          { href: "/docs/cli", label: "CLI Reference" },
          { href: "/security", label: "Security & Trust" },
          { href: "/docs/sandbox", label: "Execution Sandbox" },
          { href: "/publish", label: "Publish a package" },
          { href: "/for-developers", label: "For developers" },
          { href: "/llms-full.txt", label: "For AI agents (llms-full.txt)" },
        ].map((l) =>
          l.href.endsWith(".txt") ? (
            <li key={l.href}>
              <a
                href={l.href}
                target="_blank"
                rel="noopener noreferrer"
                className="text-sm text-primary transition-colors hover:text-foreground"
              >
                {l.label} &rarr;
              </a>
            </li>
          ) : (
            <li key={l.href}>
              <Link
                href={l.href}
                className="text-sm text-primary transition-colors hover:text-foreground"
              >
                {l.label} &rarr;
              </Link>
            </li>
          )
        )}
      </ul>
    </main>
  );
}
