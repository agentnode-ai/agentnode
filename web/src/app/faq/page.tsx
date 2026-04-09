import Link from "next/link";

export const metadata = {
  title: "FAQ — AgentNode Help & Frequently Asked Questions",
  description:
    "Find answers to common questions about AgentNode — accounts, publishing, reviews, SDK, billing, and security. Can't find your answer? Open a support ticket.",
};

const faqCategories = [
  {
    title: "Getting Started",
    items: [
      {
        q: "How do I create an AgentNode account?",
        a: 'Click "Sign up" in the top navigation, enter your email and a password, then verify your email address via the link we send you. That\'s it — you can start browsing and installing packages immediately.',
      },
      {
        q: "How do I get an API key?",
        a: "Go to your Dashboard and scroll to the API Keys section. Click \"Create API Key\", give it an optional label, and copy the key. You'll only see the full key once, so store it securely.",
      },
      {
        q: "How do I become a publisher?",
        a: 'In your Dashboard, click "Create Publisher Profile" and choose a slug (e.g. @my-org). Once created, you can publish packages under that publisher name.',
      },
      {
        q: "Do I need a publisher profile to install packages?",
        a: "No. Any registered user can search, resolve, and install packages. A publisher profile is only required if you want to publish your own packages.",
      },
    ],
  },
  {
    title: "Publishing",
    items: [
      {
        q: "How do I publish a package?",
        a: 'You can use the AI Builder to generate a package from a description, the Import tool to convert existing LangChain/MCP/CrewAI code, or the CLI with "agentnode publish". All methods produce a standard ANP package.',
      },
      {
        q: "What is the ANP manifest?",
        a: "The manifest (agentnode.yaml) describes your package: name, version, capabilities, permissions, entrypoints, and framework compatibility. It's human-readable YAML and is required for every package.",
      },
      {
        q: "How does versioning work?",
        a: "AgentNode uses semantic versioning (semver). Each publish creates a new version. You cannot overwrite an existing version — publish a new version number instead. Versions can be yanked but not deleted.",
      },
      {
        q: "What happens during verification?",
        a: "Every version goes through a 4-step pipeline on publish: Install (pip install), Import (module loads), Smoke Test (entrypoint executes), and Unit Tests (your tests pass). Results are shown as verification badges.",
      },
      {
        q: "My package was quarantined. What do I do?",
        a: "Quarantine means our security scanner flagged potential issues (embedded secrets, undeclared network access, etc.). An admin will review it. You'll receive an email with details. If cleared, the package becomes publicly available again.",
      },
    ],
  },
  {
    title: "Reviews",
    items: [
      {
        q: "What review tiers are available?",
        a: "Three tiers: Security Review (code security audit), Compatibility Review (framework compatibility testing), and Full Review (comprehensive security + compatibility + documentation review).",
      },
      {
        q: "How much do reviews cost?",
        a: "Pricing depends on the tier. Security reviews start at $49, Compatibility at $29, and Full Reviews at $99. Express processing (48h turnaround) is available for an additional fee.",
      },
      {
        q: "How long does a review take?",
        a: "Standard reviews are completed within 7 business days. Express reviews target 48-hour turnaround. You'll receive email notifications when your review is assigned and completed.",
      },
      {
        q: "What outcomes are possible?",
        a: 'Reviews can result in: Approved (badge granted), Changes Requested (fix issues and resubmit), or Rejected (does not meet standards). If rejected or if you\'re unsatisfied, refunds are available.',
      },
    ],
  },
  {
    title: "SDK & CLI",
    items: [
      {
        q: "How do I install the SDK?",
        a: 'Run "pip install agentnode-sdk". The SDK provides programmatic access to search, resolve, install, and use packages from Python. It supports async operations and API key authentication.',
      },
      {
        q: "How do I install the CLI?",
        a: 'Run "npm install -g agentnode-cli". The CLI lets you search, install, publish, and manage packages from the terminal.',
      },
      {
        q: "Which frameworks are supported?",
        a: "AgentNode packages work with LangChain, CrewAI, MCP (Model Context Protocol), and plain Python. The SDK auto-detects framework compatibility during resolution.",
      },
      {
        q: "Can I use AgentNode with my LLM provider?",
        a: "Yes. The AgentNodeRuntime supports OpenAI, Anthropic, and any OpenRouter-compatible provider. Check the compatibility page for the full list of tested models.",
      },
    ],
  },
  {
    title: "Billing",
    items: [
      {
        q: "How do payments work?",
        a: "Payments are processed through Stripe. When you request a review, you're redirected to a Stripe checkout page. We accept all major credit cards.",
      },
      {
        q: "Can I get a refund?",
        a: "Yes. If a review hasn't started yet, you can request a full refund. Partial refunds are available after review completion if you're unsatisfied with the service. Contact support for refund requests.",
      },
      {
        q: "Is there a free tier?",
        a: "Browsing, searching, installing, and publishing packages is completely free. Only paid code reviews have a cost. The platform itself has no subscription fees.",
      },
    ],
  },
  {
    title: "Security",
    items: [
      {
        q: "How do I report a security vulnerability?",
        a: "Email security@agentnode.net with details. Do not open a public issue. We follow responsible disclosure and will respond within 48 hours.",
      },
      {
        q: "What happens when a package is quarantined?",
        a: "Quarantined packages are hidden from search and installation. The publisher is notified and an admin reviews the flagged issues. Packages can be cleared (made public again) or permanently rejected.",
      },
      {
        q: "Does AgentNode support two-factor authentication?",
        a: "Yes. Enable 2FA in your Dashboard using any TOTP-compatible authenticator app (Google Authenticator, Authy, 1Password, etc.). Backup codes are provided during setup.",
      },
    ],
  },
];

export default function SupportPage() {
  return (
    <div className="flex flex-col">
      {/* Hero */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-4xl px-6 pb-16 pt-20 sm:pt-24 text-center">
          <p className="mb-4 text-sm font-medium uppercase tracking-widest text-primary">
            Support
          </p>
          <h1 className="text-3xl font-bold tracking-tight text-foreground sm:text-4xl">
            How can we help?
          </h1>
          <p className="mx-auto mt-4 max-w-xl text-muted">
            Find answers to common questions below. If you need further
            assistance, registered users can open a support ticket.
          </p>
        </div>
      </section>

      {/* FAQ */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-4xl px-6 py-16">
          <div className="space-y-12">
            {faqCategories.map((cat) => (
              <div key={cat.title}>
                <h2 className="mb-4 text-lg font-semibold text-foreground">
                  {cat.title}
                </h2>
                <div className="space-y-2">
                  {cat.items.map((item) => (
                    <details
                      key={item.q}
                      className="group rounded-lg border border-border bg-card"
                    >
                      <summary className="cursor-pointer select-none px-5 py-4 text-sm font-medium text-foreground transition-colors hover:text-primary">
                        {item.q}
                      </summary>
                      <div className="border-t border-border px-5 py-4 text-sm leading-relaxed text-muted">
                        {item.a}
                      </div>
                    </details>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* CTA */}
      <section>
        <div className="mx-auto max-w-4xl px-6 py-16 text-center">
          <h2 className="text-xl font-bold text-foreground">
            Didn&apos;t find your answer?
          </h2>
          <p className="mt-2 text-sm text-muted">
            Registered users can open a support ticket and our team will get back
            to you.
          </p>
          <div className="mt-6 flex flex-col items-center gap-3 sm:flex-row sm:justify-center">
            <Link
              href="/dashboard/support"
              className="inline-flex h-10 items-center justify-center rounded-lg bg-primary px-6 text-sm font-medium text-white transition-colors hover:bg-primary/90"
            >
              Open a Support Ticket
            </Link>
            <Link
              href="/docs"
              className="inline-flex h-10 items-center justify-center rounded-lg border border-border px-6 text-sm font-medium text-foreground transition-colors hover:bg-card"
            >
              Read the Docs
            </Link>
          </div>
        </div>
      </section>
    </div>
  );
}
