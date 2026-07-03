import type { Metadata } from "next";
import Link from "next/link";

const DESCRIPTION =
  "How AgentNode keeps AI agents safe: untrusted code runs sandboxed or fail-closed, keys stay in your OS keychain, every package is verified. Local-first.";

export const metadata: Metadata = {
  // Plain string (no brand suffix): the root layout title template
  // "%s | AgentNode" appends it, so the rendered <title> has exactly one
  // "| AgentNode". Top-level pages must NOT include the suffix themselves.
  title: "Security & Trust",
  description: DESCRIPTION,
  alternates: { canonical: "/security" },
  openGraph: {
    title: "Security & Trust for AgentNode",
    description: DESCRIPTION,
    type: "website",
    url: "https://agentnode.net/security",
    siteName: "AgentNode",
  },
  twitter: {
    card: "summary_large_image",
    site: "@AgentNodenet",
  },
};

const webPageLd = {
  "@context": "https://schema.org",
  "@type": "WebPage",
  name: "Security & Trust for AgentNode",
  description: DESCRIPTION,
  url: "https://agentnode.net/security",
};

const breadcrumbLd = {
  "@context": "https://schema.org",
  "@type": "BreadcrumbList",
  itemListElement: [
    { "@type": "ListItem", position: 1, name: "Home", item: "https://agentnode.net" },
    { "@type": "ListItem", position: 2, name: "Security & Trust", item: "https://agentnode.net/security" },
  ],
};

function H2({ id, children }: { id: string; children: React.ReactNode }) {
  return (
    <h2 id={id} className="mb-4 mt-14 scroll-mt-24 text-2xl font-bold text-foreground">
      {children}
    </h2>
  );
}

function DocLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <Link href={href} className="text-primary hover:underline">
      {children}
    </Link>
  );
}

export default function SecurityPage() {
  return (
    <main className="mx-auto max-w-4xl px-4 py-12 sm:px-6">
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(webPageLd) }}
      />
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(breadcrumbLd) }}
      />

      {/* Hero / answer-first */}
      <h1 className="text-3xl font-bold tracking-tight text-foreground sm:text-4xl">
        Security &amp; Trust for AgentNode
      </h1>
      <p className="mt-4 max-w-3xl text-lg leading-relaxed text-muted">
        AgentNode runs third-party tool code on your own machine, not ours.
        Untrusted, community-tier code runs inside a hardened container —{" "}
        <span className="font-medium text-foreground">or not at all</span>{" "}
        (fail-closed). Trusted and curated code runs host-side under policy
        checks. Your API keys stay in the OS keychain, and tool data never
        reaches our servers.
      </p>
      <div className="mt-6 flex flex-wrap gap-3">
        <Link
          href="/docs/security"
          className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-primary/90"
        >
          Read the security model
        </Link>
        <Link
          href="/docs/sandbox"
          className="rounded-md border border-border px-4 py-2 text-sm font-medium text-foreground transition-colors hover:bg-card"
        >
          Set up the sandbox
        </Link>
      </div>

      {/* Q&A */}
      <H2 id="is-agentnode-safe">Is AgentNode safe?</H2>
      <p className="text-muted">
        AgentNode is built so that untrusted code cannot quietly run with your
        privileges. Community-tier packages run inside a hardened container or
        are refused (fail-closed); trusted and curated code runs host-side under
        policy checks; and your data and credentials stay on your machine. How
        safe it is in practice depends on your setup — running the local sandbox
        and keeping a sensible minimum trust level.
      </p>

      <h3 className="mb-2 mt-8 text-lg font-semibold text-foreground">
        Does AgentNode sandbox community code?
      </h3>
      <p className="text-muted">
        Yes. Verified, unverified, and unknown-tier code runs in a hardened
        container — read-only filesystem, non-root user, dropped Linux
        capabilities, network off by default — or not at all. There is no silent
        fallback to host execution. It requires Docker or Podman plus the
        digest-pinned image; trusted and curated packages run host-side instead.
      </p>

      <h3 className="mb-2 mt-8 text-lg font-semibold text-foreground">
        Where do API keys go?
      </h3>
      <p className="text-muted">
        Into your operating-system keychain via <code className="rounded bg-card px-1.5 py-0.5 font-mono text-xs text-primary">agentnode auth</code>{" "}
        — never into sandboxed code. When a sandboxed agent needs an LLM, a
        host-side broker makes the call, so the provider key never enters the
        container, audit records, manifests, or lockfiles.
      </p>

      <h3 className="mb-2 mt-8 text-lg font-semibold text-foreground">
        Can teams enforce policies?
      </h3>
      <p className="text-muted">
        Yes. A minimum trust level, the Guard policy engine (allow / prompt /
        deny per action), input inspection, rate limits, and lockfile-integrity
        checks all run locally and fail closed. In CI or non-interactive
        contexts, any prompt escalates to a denial.
      </p>

      {/* Defense in depth */}
      <H2 id="defense-in-depth">Defense in depth</H2>
      <p className="text-muted">
        Trust is layered across the lifecycle of a package — from publish to
        runtime to your data.
      </p>
      <div className="mt-6 grid gap-4 sm:grid-cols-2">
        <div className="rounded-xl border border-border bg-card p-5">
          <p className="text-xs font-semibold uppercase tracking-wide text-primary">
            Before publish
          </p>
          <p className="mt-1 font-semibold text-foreground">Verification</p>
          <p className="mt-2 text-sm leading-relaxed text-muted">
            Every package version is tested — install, import, smoke test, unit
            tests — and scored before it earns a trust tier.
          </p>
          <p className="mt-3 text-sm">
            <DocLink href="/docs/verification">Package verification →</DocLink>
          </p>
        </div>

        <div className="rounded-xl border border-border bg-card p-5">
          <p className="text-xs font-semibold uppercase tracking-wide text-primary">
            At publish
          </p>
          <p className="mt-1 font-semibold text-foreground">Integrity</p>
          <p className="mt-2 text-sm leading-relaxed text-muted">
            Publisher Ed25519 signatures (an invalid signature blocks install),
            SHA-256 hashes, Bandit security scanning, and typosquatting
            detection.
          </p>
          <p className="mt-3 text-sm">
            <DocLink href="/docs/trust">Trust &amp; security →</DocLink>
          </p>
        </div>

        <div className="rounded-xl border border-border bg-card p-5">
          <p className="text-xs font-semibold uppercase tracking-wide text-primary">
            At runtime
          </p>
          <p className="mt-1 font-semibold text-foreground">
            Isolation &amp; policy
          </p>
          <p className="mt-2 text-sm leading-relaxed text-muted">
            Untrusted community code runs in a hardened container or not at all.
            Guard adds action policy, input inspection, and rate limits on every
            call.
          </p>
          <p className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-sm">
            <DocLink href="/docs/sandbox">Sandbox →</DocLink>
            <DocLink href="/docs/guard">Guard →</DocLink>
          </p>
        </div>

        <div className="rounded-xl border border-border bg-card p-5">
          <p className="text-xs font-semibold uppercase tracking-wide text-primary">
            Your data
          </p>
          <p className="mt-1 font-semibold text-foreground">Local-first</p>
          <p className="mt-2 text-sm leading-relaxed text-muted">
            Tools run on your machine; tool data, prompts, and telemetry never
            reach our servers. Credentials live in your OS keychain, and
            sandboxed agents use a host-side LLM broker.
          </p>
          <p className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-sm">
            <DocLink href="/docs/data-sovereignty">Data sovereignty →</DocLink>
            <DocLink href="/docs/credentials">Credentials →</DocLink>
          </p>
        </div>
      </div>

      {/* Trust tiers */}
      <H2 id="trust-tiers">Trust tiers</H2>
      <p className="text-muted">
        Every package version carries a trust level, and enforcement differs by
        tier: community tiers are sandboxed or refused, while vetted tiers run
        host-side under policy checks.
      </p>
      <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-lg border border-gray-500/30 bg-card p-4">
          <p className="font-mono text-sm font-bold text-gray-400">Unverified</p>
          <p className="mt-2 text-sm leading-relaxed text-muted">
            Metadata validated only. Sandboxed or fail-closed at runtime.
          </p>
        </div>
        <div className="rounded-lg border border-blue-500/30 bg-card p-4">
          <p className="font-mono text-sm font-bold text-blue-400">Verified</p>
          <p className="mt-2 text-sm leading-relaxed text-muted">
            Publisher confirmed, passes security scans. Sandboxed at runtime.
          </p>
        </div>
        <div className="rounded-lg border border-green-500/30 bg-card p-4">
          <p className="font-mono text-sm font-bold text-green-400">Trusted</p>
          <p className="mt-2 text-sm leading-relaxed text-muted">
            Proven reliability over time. Runs host-side under policy checks.
          </p>
        </div>
        <div className="rounded-lg border border-primary/30 bg-card p-4">
          <p className="font-mono text-sm font-bold text-primary">Curated</p>
          <p className="mt-2 text-sm leading-relaxed text-muted">
            Manually reviewed by AgentNode. Highest assurance; runs host-side.
          </p>
        </div>
      </div>
      <p className="mt-4 text-sm text-muted">
        Details per tier: <DocLink href="/docs/security">Security model</DocLink>{" "}
        and <DocLink href="/docs/sandbox">Execution sandbox</DocLink>.
      </p>

      {/* For teams & companies */}
      <H2 id="teams-and-companies">For teams and companies</H2>
      <p className="text-muted">
        AgentNode is designed to be governed before it is rolled out across
        agents:
      </p>
      <ul className="mt-4 list-disc space-y-2 pl-5 text-muted">
        <li>
          Set a <span className="text-foreground">minimum trust level</span> so
          packages below your bar are denied before execution.
        </li>
        <li>
          The <span className="text-foreground">Guard policy engine</span>{" "}
          classifies each action and returns allow, prompt, or deny — with rate
          limits and input inspection.
        </li>
        <li>
          <span className="text-foreground">Lockfile integrity</span> is
          verifiable in CI (<code className="rounded bg-card px-1.5 py-0.5 font-mono text-xs text-primary">agentnode lock verify</code>); tampered entries are denied in strict
          mode.
        </li>
        <li>
          In CI and non-interactive contexts, any{" "}
          <span className="text-foreground">prompt escalates to a denial</span> —
          no silent approvals in automation.
        </li>
        <li>
          A <span className="text-foreground">local-only audit trail</span>{" "}
          records every policy decision; it never leaves your machine.
        </li>
        <li>
          The whole runtime is{" "}
          <span className="text-foreground">fail-closed</span>: missing config or
          an unavailable sandbox blocks execution, it does not loosen it.
        </li>
      </ul>
      <p className="mt-4 text-sm text-muted">
        See <DocLink href="/docs/guard">AgentNode Guard</DocLink> and the{" "}
        <DocLink href="/docs/security">security model</DocLink>.
      </p>

      {/* Honest limits */}
      <H2 id="honest-limits">Honest limits</H2>
      <p className="text-muted">
        We document the boundaries plainly — trust is earned by being precise,
        not by overclaiming.
      </p>
      <ul className="mt-4 list-disc space-y-2 pl-5 text-muted">
        <li>
          Trusted and curated packages run host-side with policy checks and
          subprocess isolation, not OS-level filesystem or network sandboxing.
        </li>
        <li>
          Community sandboxing requires Docker or Podman plus the pinned image;
          without them, community code is blocked, not downgraded to the host.
        </li>
        <li>
          MCP servers must be pinned and preinstalled; they then run with no
          network by default, or a sealed egress allowlist when they declare
          allowed domains. Non-preinstalled (npx/uvx) servers are refused —
          filesystem, host, and secret isolation apply as before.
        </li>
        <li>
          The agent sandbox for community agents is opt-in and off by default;
          with it off, community agents are refused, not run on the host.
        </li>
      </ul>
      <p className="mt-4 text-sm text-muted">
        Full detail: <DocLink href="/docs/sandbox">Execution sandbox</DocLink>.
      </p>

      {/* CTA / next steps */}
      <H2 id="next-steps">Next steps</H2>
      <div className="grid gap-3 sm:grid-cols-2">
        <DocLink href="/docs/security">Security model</DocLink>
        <DocLink href="/docs/sandbox">Execution sandbox</DocLink>
        <DocLink href="/docs/verification">Package verification</DocLink>
        <DocLink href="/docs/trust">Trust &amp; security</DocLink>
        <DocLink href="/docs/guard">AgentNode Guard</DocLink>
        <DocLink href="/docs/data-sovereignty">Data sovereignty</DocLink>
        <DocLink href="/docs/credentials">Credentials &amp; connectors</DocLink>
        <DocLink href="/docs/llm-runtime">LLM runtime</DocLink>
      </div>
      <div className="mt-8 rounded-xl border border-border bg-card p-5 text-sm text-muted">
        <p>
          Found a vulnerability? Email{" "}
          <a href="mailto:security@agentnode.net" className="text-primary hover:underline">
            security@agentnode.net
          </a>
          . The code is open on{" "}
          <a
            href="https://github.com/agentnode-ai/agentnode"
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary hover:underline"
          >
            GitHub
          </a>
          .
        </p>
      </div>
    </main>
  );
}
