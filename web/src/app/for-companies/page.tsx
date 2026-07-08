import type { Metadata } from "next";
import Link from "next/link";

// Top-level page: the root layout title template appends "| AgentNode" — no brand suffix here.
const DESCRIPTION =
  "Govern what your agents install and run. AgentNode gives teams a pre-execution control plane: trust tiers, the Guard policy gateway, lockfile integrity, sandbox-or-fail-closed, and a local audit trail — all local-first.";

export const metadata: Metadata = {
  title: "For Companies — Govern What Your Agents Run",
  description: DESCRIPTION,
  alternates: { canonical: "/for-companies" },
  openGraph: {
    title: "For Companies — Govern What Your Agents Run | AgentNode",
    description: DESCRIPTION,
    type: "website",
    url: "https://agentnode.net/for-companies",
    siteName: "AgentNode",
  },
  twitter: { card: "summary_large_image", site: "@AgentNodenet" },
};

const controls: { title: string; body: string }[] = [
  {
    title: "Minimum trust level",
    body: "Set a floor (e.g. trusted or curated). Packages below your bar are denied before they execute — not after.",
  },
  {
    title: "Guard policy gateway",
    body: "Every install and tool action is classified allow / prompt / deny, with rate limits and input inspection. Fail-closed by default.",
  },
  {
    title: "Sandbox or fail-closed",
    body: "Untrusted community code runs in a hardened container or not at all — never silently on the host. If no sandbox is available, it is blocked.",
  },
  {
    title: "Lockfile integrity",
    body: "Pin exactly what runs and verify it in CI with agentnode lock verify. Tampered entries are denied in strict mode.",
  },
  {
    title: "No silent approvals in CI",
    body: "In CI and other non-interactive contexts, any “prompt” decision escalates to a denial — automation never auto-approves.",
  },
  {
    title: "Local audit trail",
    body: "Every policy decision is recorded to a local audit log (~/.agentnode/audit.jsonl) that never leaves your machine.",
  },
];

const policyModes: { name: string; tag: string | null; body: string }[] = [
  { name: "off", tag: null, body: "Detection only — nothing installs automatically; a human decides." },
  { name: "safe", tag: "default", body: "Auto-installs only verified-or-higher packages." },
  { name: "strict", tag: null, body: "Auto-installs only trusted or curated packages — the tightest bar." },
];

const nextLinks: { href: string; label: string }[] = [
  { href: "/security", label: "Security model" },
  { href: "/docs/guard", label: "AgentNode Guard" },
  { href: "/docs/sandbox", label: "Execution sandbox" },
  { href: "/docs/data-sovereignty", label: "Data sovereignty" },
  { href: "/compatibility", label: "Model compatibility" },
  { href: "/for-developers", label: "For developers" },
];

export default function ForCompaniesPage() {
  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "WebPage",
    name: "AgentNode for Companies",
    description: DESCRIPTION,
    url: "https://agentnode.net/for-companies",
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
            For Companies &amp; Teams
          </span>
          <h1 className="mx-auto max-w-3xl text-4xl font-bold leading-tight tracking-tight text-foreground sm:text-5xl">
            Govern what your agents run — before they run it
          </h1>
          <p className="mx-auto mt-6 max-w-2xl text-lg leading-relaxed text-muted">
            When agents install and execute capabilities at runtime, &ldquo;what
            code ran, under what policy&rdquo; becomes a governance question.
            AgentNode answers it with a pre-execution control plane: trust tiers,
            the Guard policy gateway, lockfile integrity, sandbox-or-fail-closed,
            and a local audit trail — enforced locally, before anything runs.
            And because everything executes on your own infrastructure — with
            models you own, Ollama included — there is no per-call bill and no
            prompt or data egress to a third-party AI platform.
          </p>
          <div className="mt-10 flex flex-col items-center gap-4 sm:flex-row sm:justify-center">
            <Link
              href="/security"
              className="inline-flex h-12 items-center justify-center rounded-lg bg-primary px-8 text-sm font-medium text-white transition-colors hover:bg-primary/90"
            >
              See the security model
            </Link>
            <Link
              href="/docs/guard"
              className="inline-flex h-12 items-center justify-center rounded-lg border border-border px-8 text-sm font-medium text-foreground transition-colors hover:bg-card"
            >
              AgentNode Guard
            </Link>
          </div>
        </div>
      </section>

      {/* Problem */}
      <section className="border-b border-border bg-card/30">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-20">
          <h2 className="mb-4 text-center text-2xl font-bold text-foreground sm:text-3xl">
            Agents that install and run third-party code need a control plane
          </h2>
          <p className="mx-auto max-w-2xl text-center text-muted">
            AgentNode is local-first and governed before rollout: you decide
            which packages may install and run, the runtime enforces it
            fail-closed, and every decision is recorded — on your machines, not
            ours.
          </p>
        </div>
      </section>

      {/* Stay in control */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-20">
          <h2 className="mb-12 text-center text-2xl font-bold text-foreground sm:text-3xl">
            Stay in control
          </h2>
          <div className="mx-auto grid max-w-5xl gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {controls.map((c) => (
              <div key={c.title} className="rounded-xl border border-border bg-card p-6">
                <h3 className="mb-2 text-base font-semibold text-foreground">{c.title}</h3>
                <p className="text-sm leading-relaxed text-muted">{c.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Roll out on your terms */}
      <section className="border-b border-border bg-card/30">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-20">
          <h2 className="mb-4 text-center text-2xl font-bold text-foreground sm:text-3xl">
            Roll out on your terms
          </h2>
          <p className="mx-auto mb-10 max-w-2xl text-center text-muted">
            Choose how much agents may do on their own, and keep your data where
            it belongs.
          </p>
          <div className="mx-auto grid max-w-4xl gap-4 sm:grid-cols-3">
            {policyModes.map((p) => (
              <div key={p.name} className="rounded-xl border border-border bg-card p-5">
                <div className="mb-2 flex items-center gap-2">
                  <code className="font-mono text-sm font-semibold text-foreground">{p.name}</code>
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
          <p className="mx-auto mt-8 max-w-2xl text-center text-sm leading-relaxed text-muted">
            <span className="font-medium text-foreground/80">Local-first &amp; data sovereignty:</span>{" "}
            no telemetry, tool inputs/outputs and prompts never reach AgentNode,
            and the runtime works offline once packages are installed. See{" "}
            <Link href="/docs/data-sovereignty" className="text-primary hover:underline">
              data sovereignty
            </Link>
            .
          </p>
        </div>
      </section>

      {/* Honest limits */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-3xl px-4 sm:px-6 py-20">
          <h2 className="mb-4 text-2xl font-bold text-foreground sm:text-3xl">Honest limits</h2>
          <p className="mb-4 text-sm leading-relaxed text-muted">
            Trust is earned by being precise, not by overclaiming. So we state
            the boundaries plainly:
          </p>
          <ul className="mb-4 list-inside list-disc space-y-2 text-sm text-muted">
            <li>
              Trusted and curated packages run host-side with policy checks and
              subprocess isolation — <span className="text-foreground/80">not</span>{" "}
              OS-level filesystem or network sandboxing. Container isolation
              applies to untrusted/community code.
            </li>
            <li>
              AgentNode does <span className="text-foreground/80">not</span>{" "}
              currently offer SSO/SAML, RBAC, organization or seat management, a
              private or self-hosted registry, compliance certifications (SOC 2,
              HIPAA, ISO), or SLAs. Governance today is the local, fail-closed
              control plane described above.
            </li>
          </ul>
          <p className="text-sm text-muted">
            Full detail in the{" "}
            <Link href="/security#honest-limits" className="text-primary hover:underline">
              security model
            </Link>
            .
          </p>
        </div>
      </section>

      {/* Next / CTA */}
      <section>
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-24">
          <div className="flex flex-col items-center text-center">
            <h2 className="text-2xl font-bold text-foreground sm:text-3xl">
              Evaluate AgentNode for your team
            </h2>
            <p className="mt-4 max-w-xl text-muted">
              Review the security model and Guard, try the runtime, and reach out
              with security questions.
            </p>
            <div className="mt-10 flex flex-col items-center gap-4 sm:flex-row">
              <Link
                href="/security"
                className="inline-flex h-12 items-center justify-center rounded-lg bg-primary px-8 text-sm font-medium text-white transition-colors hover:bg-primary/90"
              >
                Security model
              </Link>
              <Link
                href="/getting-started"
                className="inline-flex h-12 items-center justify-center rounded-lg border border-border px-8 text-sm font-medium text-foreground transition-colors hover:bg-card"
              >
                Get started
              </Link>
            </div>
            <p className="mt-6 text-sm text-muted">
              Security questions?{" "}
              <a href="mailto:security@agentnode.net" className="text-primary hover:underline">
                security@agentnode.net
              </a>
            </p>
            <div className="mt-12 w-full border-t border-border pt-8">
              <p className="mb-3 text-xs font-medium uppercase tracking-wider text-muted">
                Related
              </p>
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
