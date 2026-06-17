import Link from "next/link";
import { TOTAL_MODELS, S_TIER_COUNT, PROVIDER_COUNT } from "@/app/compatibility/data";

export const metadata = {
  // root layout title template appends "| AgentNode"
  title: "Frequently Asked Questions",
  description:
    "Answers about AgentNode: getting started, API keys and providers, Ollama, credentials, trust tiers, the sandbox, publishing, and AI-agent discovery.",
  alternates: {
    canonical: "/faq",
  },
  openGraph: {
    title: "Frequently Asked Questions | AgentNode",
    description:
      "Answers about AgentNode: getting started, providers and keys, trust and security, publishing, and AI-agent discovery.",
    type: "website",
    url: "https://agentnode.net/faq",
    siteName: "AgentNode",
  },
  twitter: {
    card: "summary_large_image",
    site: "@AgentNodenet",
  },
};

interface FaqLink {
  href: string;
  label: string;
}
interface FaqItem {
  q: string;
  a: string;
  links?: FaqLink[];
}
interface FaqCategory {
  title: string;
  items: FaqItem[];
}

const faqCategories: FaqCategory[] = [
  {
    title: "Basics",
    items: [
      {
        q: "What is AgentNode?",
        a: "AgentNode is a verified registry and runtime for AI agent capabilities. Agents discover, install, and run verified packages — tool packs, MCP servers, and agents — at runtime, governed by trust tiers, policy checks, and lockfiles.",
        links: [{ href: "/getting-started", label: "Get started" }],
      },
      {
        q: "Who is AgentNode for?",
        a: "Four audiences: people who want to give an agent new capabilities, developers who publish packages, teams who must govern what agents run, and AI agents themselves, which can read a machine-readable setup guide.",
      },
      {
        q: "Is AgentNode a framework?",
        a: "No. AgentNode is a registry plus a runtime that works with LangChain, CrewAI, MCP, and plain Python. You keep your framework; AgentNode supplies and governs the capabilities.",
      },
      {
        q: "How do I start?",
        a: "Install the SDK with pip install agentnode-sdk, run agentnode setup, then search, install, and run a package. No account is needed to search, install, or run.",
        links: [
          { href: "/getting-started", label: "Start hub" },
          { href: "/docs/quickstart", label: "Quick Start" },
        ],
      },
    ],
  },
  {
    title: "Capabilities and package types",
    items: [
      {
        q: "What can agents install?",
        a: "Tool packs, Skills, MCP servers, and full Agents — all in the portable ANP package format, discoverable by capability.",
        links: [{ href: "/search", label: "Browse the registry" }],
      },
      {
        q: "What is the difference between a Tool Pack, Skill, MCP server, and Agent?",
        a: "A tool pack provides callable tool functions; a skill provides prompt templates and assets; an MCP server is an external Model Context Protocol tool process; an agent orchestrates an LLM and tools toward a goal.",
        links: [{ href: "/docs/agents", label: "Agents" }],
      },
      {
        q: "How do I publish a package?",
        a: "Scaffold with agentnode init, validate it, run agentnode verify-local, then agentnode publish. Publishing requires an AgentNode account and an API key; browsing, installing, and running do not.",
        links: [
          { href: "/publish", label: "Publish" },
          { href: "/for-developers", label: "For developers" },
        ],
      },
    ],
  },
  {
    title: "Providers, keys, and local models",
    items: [
      {
        q: "Do I need an API key?",
        a: "No account or key is required to search, install, or run packages. You need a provider key only if you use a hosted LLM (such as OpenAI or Anthropic); an AgentNode API key is required only to publish.",
      },
      {
        q: "Can I use Ollama without a hosted API key?",
        a: "Yes. Ollama runs models locally with no hosted API key — once Ollama is installed, running, and a model is pulled. AgentNode never installs or starts it for you.",
        links: [{ href: "/docs/llm-providers", label: "LLM Providers" }],
      },
      {
        q: "Where are credentials stored?",
        a: "In your OS keychain, or a local file with owner-only (0600) permissions as a fallback. An environment variable always overrides the stored value. Keys are not uploaded to AgentNode, though a hosted provider receives your prompts and responses when you use it.",
        links: [{ href: "/docs/credentials", label: "Credentials" }],
      },
      {
        q: "Which providers and models work?",
        a: `Built-in: OpenAI, Anthropic, OpenRouter, DeepSeek, Mistral, Qwen, Gemini, plus local Ollama and custom OpenAI-compatible endpoints. The runtime is tested across ${TOTAL_MODELS} models from ${PROVIDER_COUNT} providers (${S_TIER_COUNT} pass all scenarios).`,
        links: [
          { href: "/docs/llm-providers", label: "LLM Providers" },
          { href: "/compatibility", label: "Compatibility" },
        ],
      },
    ],
  },
  {
    title: "Trust and security",
    items: [
      {
        q: "Are packages sandboxed?",
        a: "Untrusted community packages run in a hardened container or not at all (fail-closed), when a container runtime and the pinned image are available. Trusted and curated packages run host-side with policy checks and subprocess isolation — not OS-level sandboxing.",
        links: [
          { href: "/docs/sandbox", label: "Execution sandbox" },
          { href: "/security", label: "Security model" },
        ],
      },
      {
        q: "Are agents sandboxed?",
        a: "An agent's own code runs host-side, so trusted and curated agents run on the host. Community agents are refused by default — they run only if you opt into the agent sandbox, which is off by default.",
        links: [{ href: "/docs/agents", label: "Agents" }],
      },
      {
        q: "What do verified, trusted, and curated mean?",
        a: "They are trust levels: unverified (metadata validated only), verified (security-scanned, publisher confirmed), trusted (proven reliable over time), and curated (reviewed by AgentNode). Package versions are also scored into verification tiers.",
        links: [{ href: "/security", label: "Security model" }],
      },
      {
        q: "What happens if a package is untrusted?",
        a: "When runtime isolation is required, it is sandboxed-or-fail-closed: it runs in a container, or — if no sandbox is available — it is blocked, never silently run on the host.",
        links: [{ href: "/docs/sandbox", label: "Execution sandbox" }],
      },
      {
        q: "What does AgentNode Guard do?",
        a: "Guard is a pre-execution policy gateway. Every install and run is checked against trust level, permissions, and environment, and tool actions are classified allow / prompt / deny with rate limits. It is fail-closed.",
        links: [{ href: "/docs/cli", label: "Guard commands" }],
      },
      {
        q: "Does AgentNode have compliance certifications?",
        a: "AgentNode does not currently claim SOC 2, HIPAA, ISO, or similar compliance certifications. It is local-first, with trust tiers, publisher signatures, lockfile integrity, and sandbox-or-fail-closed isolation for untrusted code.",
        links: [{ href: "/security", label: "Security model" }],
      },
    ],
  },
  {
    title: "AI agents and discovery",
    items: [
      {
        q: "How do AI agents discover AgentNode?",
        a: "Through machine-readable files: /llms.txt and a deeper /llms-full.txt guide that explains setup, security boundaries, and the install commands for AI agents.",
        links: [{ href: "/llms-full.txt", label: "llms-full.txt" }],
      },
      {
        q: "Where should I read next?",
        a: "Start with Getting Started and the Quick Start, then the security model and the CLI reference.",
        links: [
          { href: "/getting-started", label: "Getting Started" },
          { href: "/docs/quickstart", label: "Quick Start" },
          { href: "/security", label: "Security" },
          { href: "/docs/cli", label: "CLI Reference" },
        ],
      },
    ],
  },
];

export default function SupportPage() {
  const faqLd = {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    mainEntity: faqCategories.flatMap((cat) =>
      cat.items.map((item) => ({
        "@type": "Question",
        name: item.q,
        acceptedAnswer: {
          "@type": "Answer",
          text: item.a,
        },
      }))
    ),
  };

  return (
    <div className="flex flex-col">
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(faqLd) }}
      />
      {/* Hero */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-4xl px-4 sm:px-6 pb-16 pt-20 sm:pt-24 text-center">
          <p className="mb-4 text-sm font-medium uppercase tracking-widest text-primary">
            Support
          </p>
          <h1 className="text-3xl font-bold tracking-tight text-foreground sm:text-4xl">
            How can we help?
          </h1>
          <p className="mx-auto mt-4 max-w-2xl text-muted">
            Answers to common questions about getting started, providers and
            keys, trust and security, and publishing — with links to the full
            documentation.
          </p>
        </div>
      </section>

      {/* FAQ */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-4xl px-4 sm:px-6 py-16">
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
                        <p>{item.a}</p>
                        {item.links && (
                          <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5">
                            {item.links.map((l) =>
                              l.href.endsWith(".txt") ? (
                                <a
                                  key={l.href}
                                  href={l.href}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="text-primary transition-colors hover:text-foreground"
                                >
                                  {l.label} &rarr;
                                </a>
                              ) : (
                                <Link
                                  key={l.href}
                                  href={l.href}
                                  className="text-primary transition-colors hover:text-foreground"
                                >
                                  {l.label} &rarr;
                                </Link>
                              )
                            )}
                          </div>
                        )}
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
        <div className="mx-auto max-w-4xl px-4 sm:px-6 py-16 text-center">
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
