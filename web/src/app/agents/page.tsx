import Link from "next/link";
import type { Metadata } from "next";
import PackageCard from "@/components/PackageCard";
import { BACKEND_URL } from "@/lib/constants";

export const metadata: Metadata = {
  title: "AI Agents — AgentNode",
  description:
    "Discover pre-built AI agents on AgentNode. Research, writing, code review, security scanning, and more — ready to install and run in any framework.",
  openGraph: {
    title: "AI Agents | AgentNode",
    description:
      "Pre-built AI agents you can install, configure, and run. From deep research to code review — each agent is verified, standardized, and framework-agnostic.",
    type: "website",
    url: "/agents",
    siteName: "AgentNode",
  },
};

interface SearchHit {
  slug: string;
  name: string;
  package_type: string;
  summary: string;
  publisher_name: string;
  trust_level: "curated" | "trusted" | "verified" | "unverified";
  latest_version: string | null;
  frameworks: string[];
  download_count: number;
  install_count: number;
  verification_status: string | null;
  verification_tier?: string | null;
  verification_score?: number | null;
  tags: string[];
  is_deprecated: boolean;
}

async function fetchAgents(): Promise<SearchHit[]> {
  try {
    const res = await fetch(`${BACKEND_URL}/v1/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        package_type: "agent",
        per_page: 50,
        sort_by: "download_count:desc",
      }),
      next: { revalidate: 300 },
    });
    if (!res.ok) return [];
    const data = await res.json();
    return data.hits ?? [];
  } catch {
    return [];
  }
}

const TIERS = [
  {
    name: "LLM Only",
    slug: "llm_only",
    description:
      "Pure reasoning agents. They use your LLM to think, write, and plan — no external tools or API calls needed.",
    examples: "Blog writer, newsletter, project planner, report generator",
    color: "purple",
  },
  {
    name: "LLM + Tools",
    slug: "llm_plus_tools",
    description:
      "Agents that combine LLM reasoning with AgentNode tool packs. They search the web, extract documents, analyze data, and more.",
    examples: "Deep research, code review, competitive intel, fact checker",
    color: "blue",
  },
  {
    name: "LLM + Credentials",
    slug: "llm_plus_credentials",
    description:
      "Agents that connect to external services using API keys or OAuth. They interact with your CRM, cloud provider, email, or databases.",
    examples: "CRM enrichment, cloud cost analysis, email triage, deployment",
    color: "amber",
  },
];

const HOW_IT_WORKS = [
  {
    step: "1",
    title: "Install",
    description: "One command. The agent and all its dependencies are downloaded, verified, and installed locally.",
    code: "agentnode install deep-research-agent",
  },
  {
    step: "2",
    title: "Configure",
    description: "Each agent declares what it needs: API keys, permissions, tool access. You see everything upfront — no hidden behavior.",
    code: 'result = run_tool("deep-research-agent",\n  goal="Compare React vs Vue in 2026")',
  },
  {
    step: "3",
    title: "Run",
    description: "Agents run locally on your machine with declared isolation. They orchestrate tool calls, handle errors, and return structured results.",
    code: 'print(result["report"])\nprint(result["sources"])',
  },
];

const ADVANTAGES = [
  {
    title: "Standardized Manifest",
    description:
      "Every agent declares its goal, behavior, permissions, tool access, and limits in a single agentnode.yaml. No guessing what an agent does or needs.",
  },
  {
    title: "Verified Before You Install",
    description:
      "Each agent is automatically installed, imported, and smoke-tested by AgentNode before listing. You see the verification score and tier upfront.",
  },
  {
    title: "Transparent Behavior",
    description:
      "The agent's behavior description, tool access, system prompt, and permission levels are all visible on the package page. Nothing is hidden.",
  },
  {
    title: "Any LLM Provider",
    description:
      "Agents work with any LLM provider — OpenAI, Anthropic, Gemini, or OpenRouter. The agent uses the same model that invokes it, auto-detected from your API key.",
  },
  {
    title: "Declared Permissions",
    description:
      "Network access, filesystem access, code execution — every permission level is declared in the manifest and shown before installation.",
  },
  {
    title: "Composable",
    description:
      "Agents use AgentNode tool packs as building blocks. A research agent combines web search, page extraction, and summarization — all from the registry.",
  },
];

export default async function AgentsPage() {
  const agents = await fetchAgents();

  return (
    <div className="mx-auto max-w-6xl px-4 sm:px-6 py-10">
      {/* Hero */}
      <header className="mb-16 text-center">
        <h1 className="text-4xl font-bold text-foreground sm:text-5xl">
          AI Agents
        </h1>
        <p className="mx-auto mt-4 max-w-2xl text-lg text-muted leading-relaxed">
          Pre-built agents you can install, inspect, and run.
          Every agent declares what it does, what it needs, and how it behaves
          — in a single, standardized manifest.
        </p>
        <div className="mt-6 flex items-center justify-center gap-3">
          <Link
            href="/search?package_type=agent"
            className="rounded-lg bg-primary px-5 py-2.5 text-sm font-medium text-white transition-colors hover:bg-primary/90"
          >
            Browse All Agents
          </Link>
          <Link
            href="/docs#anp-manifest"
            className="rounded-lg border border-border px-5 py-2.5 text-sm font-medium text-foreground transition-colors hover:border-primary/30"
          >
            Read the Spec
          </Link>
        </div>
      </header>

      {/* What is an AgentNode Agent? */}
      <section className="mb-16">
        <h2 className="mb-6 text-2xl font-bold text-foreground">
          What is an AgentNode Agent?
        </h2>
        <div className="grid gap-6 md:grid-cols-2">
          <div className="rounded-xl border border-border bg-card p-6">
            <p className="text-sm leading-relaxed text-muted">
              An AgentNode agent is a <strong className="text-foreground">self-describing AI agent</strong> packaged
              with a standardized manifest. Unlike traditional AI agents that hide their
              behavior in code, AgentNode agents declare everything upfront: their goal,
              behavior, tool access, permissions, and limits.
            </p>
            <p className="mt-3 text-sm leading-relaxed text-muted">
              This means you can <strong className="text-foreground">inspect before you install</strong>. You know exactly
              what the agent does, which tools it uses, what API keys it needs, and
              what permissions it requires — before running a single line of code.
            </p>
          </div>
          <div className="rounded-xl border border-border bg-card p-6">
            <p className="text-sm leading-relaxed text-muted">
              Agents are <strong className="text-foreground">composable</strong>. They use AgentNode tool packs as building
              blocks — a research agent might combine web search, page extraction,
              and document summarization, all installed from the registry.
            </p>
            <p className="mt-3 text-sm leading-relaxed text-muted">
              Each agent is <strong className="text-foreground">automatically verified</strong> by AgentNode before listing:
              installed, imported, and smoke-tested in a sandbox. You see the
              verification score, tier, and any issues upfront.
            </p>
          </div>
        </div>
      </section>

      {/* Agent Tiers */}
      <section className="mb-16">
        <h2 className="mb-6 text-2xl font-bold text-foreground">
          Agent Tiers
        </h2>
        <p className="mb-6 text-sm text-muted max-w-2xl">
          Agents are classified by what they need to run. This tells you
          at a glance how much trust and configuration is required.
        </p>
        <div className="grid gap-4 md:grid-cols-3">
          {TIERS.map((tier) => (
            <div
              key={tier.slug}
              className={`rounded-xl border p-6 ${
                tier.color === "purple"
                  ? "border-purple-500/20 bg-purple-500/5"
                  : tier.color === "blue"
                    ? "border-blue-500/20 bg-blue-500/5"
                    : "border-amber-500/20 bg-amber-500/5"
              }`}
            >
              <h3
                className={`text-lg font-semibold ${
                  tier.color === "purple"
                    ? "text-purple-400"
                    : tier.color === "blue"
                      ? "text-blue-400"
                      : "text-amber-400"
                }`}
              >
                {tier.name}
              </h3>
              <p className="mt-2 text-sm text-muted leading-relaxed">
                {tier.description}
              </p>
              <p className="mt-3 text-xs text-muted/70">
                <span className="font-medium text-muted">Examples:</span>{" "}
                {tier.examples}
              </p>
            </div>
          ))}
        </div>
      </section>

      {/* How it Works */}
      <section className="mb-16">
        <h2 className="mb-6 text-2xl font-bold text-foreground">
          How It Works
        </h2>
        <div className="grid gap-6 md:grid-cols-3">
          {HOW_IT_WORKS.map((item) => (
            <div key={item.step} className="rounded-xl border border-border bg-card p-6">
              <div className="mb-3 flex items-center gap-3">
                <span className="flex h-8 w-8 items-center justify-center rounded-full bg-primary/10 text-sm font-bold text-primary">
                  {item.step}
                </span>
                <h3 className="text-lg font-semibold text-foreground">
                  {item.title}
                </h3>
              </div>
              <p className="mb-4 text-sm text-muted leading-relaxed">
                {item.description}
              </p>
              <pre className="rounded-lg border border-border bg-background p-3 text-xs font-mono text-muted overflow-x-auto whitespace-pre-wrap">
                {item.code}
              </pre>
            </div>
          ))}
        </div>
      </section>

      {/* Advantages */}
      <section className="mb-16">
        <h2 className="mb-6 text-2xl font-bold text-foreground">
          Why AgentNode Agents?
        </h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {ADVANTAGES.map((adv) => (
            <div key={adv.title} className="rounded-xl border border-border bg-card p-5">
              <h3 className="mb-2 text-sm font-semibold text-foreground">
                {adv.title}
              </h3>
              <p className="text-sm text-muted leading-relaxed">
                {adv.description}
              </p>
            </div>
          ))}
        </div>
      </section>

      {/* Agent Listing */}
      {agents.length > 0 && (
        <section className="mb-16">
          <div className="mb-6 flex items-center justify-between">
            <h2 className="text-2xl font-bold text-foreground">
              Available Agents
            </h2>
            <Link
              href="/search?package_type=agent"
              className="text-sm text-primary hover:underline"
            >
              View all
            </Link>
          </div>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {agents.map((agent) => (
              <PackageCard
                key={agent.slug}
                slug={agent.slug}
                name={agent.name}
                summary={agent.summary}
                trust_level={agent.trust_level}
                frameworks={agent.frameworks}
                version={agent.latest_version ?? undefined}
                download_count={agent.download_count}
                install_count={agent.install_count}
                verification_status={agent.verification_status}
                verification_tier={agent.verification_tier}
                verification_score={agent.verification_score}
                package_type={agent.package_type}
                tags={agent.tags}
                publisher_name={agent.publisher_name}
                is_deprecated={agent.is_deprecated}
              />
            ))}
          </div>
        </section>
      )}

      {/* CTA */}
      <section className="rounded-xl border border-primary/20 bg-primary/5 p-8 text-center">
        <h2 className="text-2xl font-bold text-foreground">
          Build Your Own Agent
        </h2>
        <p className="mx-auto mt-3 max-w-lg text-sm text-muted leading-relaxed">
          Create an agent with a standardized manifest, publish it to the registry,
          and let others install and run it. The manifest format is open and documented.
        </p>
        <div className="mt-6 flex items-center justify-center gap-3">
          <Link
            href="/publish"
            className="rounded-lg bg-primary px-5 py-2.5 text-sm font-medium text-white transition-colors hover:bg-primary/90"
          >
            Publish an Agent
          </Link>
          <Link
            href="/docs#anp-manifest"
            className="rounded-lg border border-border px-5 py-2.5 text-sm font-medium text-foreground transition-colors hover:border-primary/30"
          >
            Manifest Reference
          </Link>
        </div>
      </section>
    </div>
  );
}
