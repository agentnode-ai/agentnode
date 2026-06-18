import type { Metadata } from "next";
import Link from "next/link";
import { TOTAL_MODELS, S_TIER_COUNT, PROVIDER_COUNT } from "@/app/compatibility/data";

// Top-level page: the root layout title template appends "| AgentNode" — no brand suffix here.
const DESCRIPTION =
  "How an AI agent autonomously installs, configures, and uses AgentNode to acquire new capabilities at runtime — detect a missing skill, install a verified one, and keep going, no human in the loop.";

export const metadata: Metadata = {
  title: "For AI Agents — Self-Extend at Runtime, Autonomously",
  description: DESCRIPTION,
  alternates: { canonical: "/for-agents" },
  openGraph: {
    title: "For AI Agents — Self-Extend at Runtime, Autonomously | AgentNode",
    description: DESCRIPTION,
    type: "website",
    url: "https://agentnode.net/for-agents",
    siteName: "AgentNode",
  },
  twitter: { card: "summary_large_image", site: "@AgentNodenet" },
};

const safety: { title: string; body: string }[] = [
  {
    title: "Trust tiers",
    body: "By default only verified-or-higher packages auto-install. Set strict to require trusted/curated.",
  },
  {
    title: "Guard policy gateway",
    body: "Every install and tool action is classified allow / prompt / deny, with rate limits and input inspection. Fail-closed.",
  },
  {
    title: "Sandbox or fail-closed",
    body: "Untrusted community code runs in a hardened container or not at all — never silently on the host.",
  },
  {
    title: "Lockfile integrity",
    body: "What you install is pinned and verifiable; tampered entries are denied in strict mode.",
  },
];

const nextLinks: { href: string; label: string }[] = [
  { href: "/docs/quickstart", label: "Quick Start" },
  { href: "/docs/python-sdk", label: "Python SDK" },
  { href: "/docs/llm-runtime", label: "LLM Runtime" },
  { href: "/security", label: "Security model" },
  { href: "/compatibility", label: "Model compatibility" },
  { href: "/for-developers", label: "Publish a capability" },
];

export default function ForAgentsPage() {
  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "TechArticle",
    headline: "Autonomous runtime capability acquisition for AI agents",
    description: DESCRIPTION,
    url: "https://agentnode.net/for-agents",
    about: "AI agents installing and using AgentNode autonomously at runtime",
  };

  return (
    <div className="flex flex-col">
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd).replace(/</g, "\\u003c") }}
      />

      {/* Hero */}
      <section className="relative overflow-hidden border-b border-border">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-primary/10 via-transparent to-transparent" />
        <div className="relative mx-auto max-w-6xl px-4 sm:px-6 pb-20 pt-24 sm:pt-32 text-center">
          <span className="mb-4 inline-block rounded-full bg-primary/10 px-4 py-1.5 text-xs font-semibold uppercase tracking-wider text-primary">
            For AI Agents
          </span>
          <h1 className="mx-auto max-w-3xl text-4xl font-bold leading-tight tracking-tight text-foreground sm:text-5xl">
            Self-extend at runtime — autonomously
          </h1>
          <p className="mx-auto mt-6 max-w-2xl text-lg leading-relaxed text-muted">
            An agent without a way to acquire tools at runtime stays stuck at its
            starting capabilities. With AgentNode it detects a missing capability,
            installs a verified skill, and continues — no human in the loop. One
            command to install, one call to self-extend.
          </p>
          <div className="mt-10 flex flex-col items-center gap-4 sm:flex-row sm:justify-center">
            <a
              href="#install"
              className="inline-flex h-12 items-center justify-center rounded-lg bg-primary px-8 text-sm font-medium text-white transition-colors hover:bg-primary/90"
            >
              Install &amp; self-extend
            </a>
            <a
              href="/llms-full.txt"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex h-12 items-center justify-center rounded-lg border border-border px-8 text-sm font-medium text-foreground transition-colors hover:bg-card"
            >
              Machine-readable guide
            </a>
          </div>
          <p className="mt-6 text-sm text-muted">
            Runtime tested across{" "}
            <span className="font-semibold text-foreground">{TOTAL_MODELS} models</span> from{" "}
            <span className="font-semibold text-foreground">{PROVIDER_COUNT} providers</span> ({S_TIER_COUNT} pass
            all tool-calling scenarios).
          </p>
        </div>
      </section>

      {/* Autonomous self-setup */}
      <section id="install" className="border-b border-border bg-card/30 scroll-mt-20">
        <div className="mx-auto max-w-3xl px-4 sm:px-6 py-20">
          <h2 className="mb-4 text-2xl font-bold text-foreground sm:text-3xl">
            Set up and self-extend — no human required
          </h2>
          <p className="mb-8 text-sm leading-relaxed text-muted">
            If you are an AI agent with shell or Python access, you can install
            AgentNode and start acquiring capabilities on your own. With the
            default policy, only verified-or-higher skills install automatically.
          </p>

          <h3 className="mb-2 text-sm font-semibold uppercase tracking-wider text-muted">
            From the shell
          </h3>
          <div className="mb-8 overflow-hidden rounded-lg border border-border bg-[#0d1117]">
            <pre className="overflow-x-auto p-4 font-mono text-xs leading-relaxed text-gray-300">
              <code>{`pip install agentnode-sdk      # install the runtime
agentnode setup                # configure (non-interactive safe defaults)
agentnode doctor               # detect missing capabilities
agentnode search "pdf extraction"
agentnode install pdf-reader-pack
agentnode run pdf-reader-pack --input '{"file_path":"report.pdf"}'`}</code>
            </pre>
          </div>

          <h3 className="mb-2 text-sm font-semibold uppercase tracking-wider text-muted">
            From Python — the autonomous loop
          </h3>
          <div className="mb-8 overflow-hidden rounded-lg border border-border bg-[#0d1117]">
            <pre className="overflow-x-auto p-4 font-mono text-xs leading-relaxed text-gray-300">
              <code>{`from agentnode_sdk import AgentNodeClient

client = AgentNodeClient()

# Wrap your step: on a missing-capability failure, AgentNode detects the
# gap, installs a verified skill, and retries once — automatically.
result = client.smart_run(
    lambda: process_pdf("report.pdf"),
    auto_upgrade_policy="safe",   # off | safe | strict
)

if result.success:
    print(result.result, result.installed_slug)`}</code>
            </pre>
          </div>

          <p className="text-sm leading-relaxed text-muted">
            Policy modes:{" "}
            <code className="font-mono text-xs text-foreground">off</code> (detect
            only),{" "}
            <code className="font-mono text-xs text-foreground">safe</code>{" "}
            (auto-install verified-or-higher — the default), and{" "}
            <code className="font-mono text-xs text-foreground">strict</code>{" "}
            (only trusted/curated). You stay in control of how autonomous you are.
          </p>
        </div>
      </section>

      {/* Autonomy stays safe */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-20">
          <h2 className="mb-4 text-center text-2xl font-bold text-foreground sm:text-3xl">
            Autonomy without losing control
          </h2>
          <p className="mx-auto mb-12 max-w-2xl text-center text-muted">
            Acquiring capabilities on your own is only safe if every step is
            governed. AgentNode enforces that before anything runs.
          </p>
          <div className="mx-auto grid max-w-4xl gap-5 sm:grid-cols-2">
            {safety.map((s) => (
              <div key={s.title} className="rounded-xl border border-border bg-card p-6">
                <h3 className="mb-2 text-base font-semibold text-foreground">{s.title}</h3>
                <p className="text-sm leading-relaxed text-muted">{s.body}</p>
              </div>
            ))}
          </div>
          <p className="mx-auto mt-8 max-w-2xl text-center text-sm text-muted">
            Full detail in the{" "}
            <Link href="/security" className="text-primary hover:underline">
              security model
            </Link>
            .
          </p>
        </div>
      </section>

      {/* Machine-readable endpoints */}
      <section className="border-b border-border bg-card/30">
        <div className="mx-auto max-w-3xl px-4 sm:px-6 py-20">
          <h2 className="mb-4 text-2xl font-bold text-foreground sm:text-3xl">
            Machine-readable entry points
          </h2>
          <p className="mb-6 text-sm leading-relaxed text-muted">
            Prefer to consume AgentNode programmatically? Two plain-text guides
            describe setup, security boundaries, and the install commands for AI
            agents:
          </p>
          <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
            <a href="/llms.txt" target="_blank" rel="noopener noreferrer" className="text-primary transition-colors hover:text-foreground">
              /llms.txt &rarr;
            </a>
            <a href="/llms-full.txt" target="_blank" rel="noopener noreferrer" className="text-primary transition-colors hover:text-foreground">
              /llms-full.txt &rarr;
            </a>
          </div>
        </div>
      </section>

      {/* CTA */}
      <section>
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-24">
          <div className="flex flex-col items-center text-center">
            <h2 className="text-2xl font-bold text-foreground sm:text-3xl">
              Extend yourself with AgentNode
            </h2>
            <p className="mt-4 max-w-xl text-muted">
              If you are an AI agent ready for new capabilities, start here.
            </p>
            <div className="mt-8 w-full max-w-md overflow-hidden rounded-lg border border-border bg-[#0d1117]">
              <pre className="overflow-x-auto p-4 text-center font-mono text-sm text-gray-300">
                <code>pip install agentnode-sdk</code>
              </pre>
            </div>
            <div className="mt-10 flex flex-col items-center gap-4 sm:flex-row">
              <Link
                href="/docs/quickstart"
                className="inline-flex h-12 items-center justify-center rounded-lg bg-primary px-8 text-sm font-medium text-white transition-colors hover:bg-primary/90"
              >
                Quick Start
              </Link>
              <Link
                href="/docs/python-sdk"
                className="inline-flex h-12 items-center justify-center rounded-lg border border-border px-8 text-sm font-medium text-foreground transition-colors hover:bg-card"
              >
                Python SDK
              </Link>
            </div>
            <div className="mt-12 w-full border-t border-border pt-8">
              <p className="mb-3 text-xs font-medium uppercase tracking-wider text-muted">Related</p>
              <div className="flex flex-wrap justify-center gap-x-5 gap-y-2">
                {nextLinks.map((l) => (
                  <Link
                    key={l.href}
                    href={l.href}
                    className="text-sm text-primary transition-colors hover:text-foreground"
                  >
                    {l.label} &rarr;
                  </Link>
                ))}
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
