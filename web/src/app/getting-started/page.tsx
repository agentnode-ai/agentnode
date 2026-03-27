import type { Metadata } from "next";
import Link from "next/link";
import AiStackLogos from "@/components/AiStackLogos";

export const metadata: Metadata = {
  title: "Getting Started | AgentNode",
  description:
    "Install AgentNode, configure permissions, and let your agent learn new skills on its own. Works with ChatGPT, Claude, Gemini, LangChain, CrewAI, and more.",
};

const steps = [
  {
    number: "1",
    title: "Install the SDK",
    description: "One command. Python 3.10+ required. No account needed.",
    code: "pip install agentnode-sdk",
  },
  {
    number: "2",
    title: "Set up AgentNode",
    description:
      "Run the setup wizard to configure permissions and trust. Or skip it — sensible defaults work out of the box.",
    code: "agentnode setup",
  },
  {
    number: "3",
    title: "Find, install, and run",
    description:
      "Search the registry, install a skill, and run it — all from the CLI. Or use the Python SDK for programmatic access.",
    code: '# CLI\nagentnode search pdf\nagentnode install pdf-reader-pack\nagentnode run pdf-reader-pack --input \'{"file_path":"report.pdf"}\'\n\n# Python SDK\nfrom agentnode_sdk import AgentNodeClient\nclient = AgentNodeClient()\nclient.resolve_and_install(["pdf_extraction"])',
  },
];

const policies = [
  {
    name: "safe",
    tag: "default",
    description:
      "Auto-installs verified and trusted packages. Best for most setups — your agent learns new skills without manual approval, but only from reviewed sources.",
  },
  {
    name: "strict",
    description:
      "Only installs trusted or curated packages. Use this when security is critical and you want the tightest control over what your agent can install.",
  },
  {
    name: "off",
    description:
      "Detection only — the agent identifies missing skills but never installs anything automatically. You decide what to add.",
  },
];

const permissions = [
  {
    label: "Network",
    values: "none / read / write / full",
    description: "Controls whether a skill can make HTTP requests.",
  },
  {
    label: "Filesystem",
    values: "none / read / read_write",
    description: "Controls file access for the skill.",
  },
  {
    label: "Code execution",
    values: "none / local / remote",
    description: "Controls whether a skill can execute code.",
  },
  {
    label: "Data access",
    values: "none / metadata / read / read_write",
    description: "Controls what data the skill can see.",
  },
];

export default function GettingStartedPage() {
  return (
    <main className="min-h-screen">
      {/* Hero */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-4xl px-4 sm:px-6 py-20">
          <h1 className="mb-4 text-3xl font-bold text-foreground sm:text-4xl">
            Getting started
          </h1>
          <p className="max-w-2xl text-lg text-muted leading-relaxed">
            Install AgentNode, set your permissions, and your agent learns new
            skills on its own. No manual tool wiring, no dependency management.
          </p>
        </div>
      </section>

      {/* Steps */}
      <section className="border-b border-border bg-card/30">
        <div className="mx-auto max-w-4xl px-4 sm:px-6 py-20">
          <h2 className="mb-12 text-2xl font-bold text-foreground">
            Setup in 3 steps
          </h2>

          <div className="space-y-0">
            {steps.map((step, i) => (
              <div key={step.number} className="flex gap-6">
                {/* Timeline */}
                <div className="flex flex-col items-center">
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary/10 text-sm font-bold text-primary">
                    {step.number}
                  </div>
                  {i < steps.length - 1 && (
                    <div className="w-px flex-1 bg-border" />
                  )}
                </div>

                {/* Content */}
                <div className="pb-12">
                  <h3 className="mb-1 text-lg font-semibold text-foreground">
                    {step.title}
                  </h3>
                  <p className="mb-3 text-muted">{step.description}</p>
                  {step.code && (
                    <pre className="rounded-lg bg-[#0d1117] px-4 py-3 text-sm text-gray-300 overflow-x-auto">
                      <code>{step.code}</code>
                    </pre>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* How auto-skill works */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-4xl px-4 sm:px-6 py-20">
          <h2 className="mb-4 text-2xl font-bold text-foreground">
            How your agent learns skills
          </h2>
          <p className="mb-12 max-w-2xl text-muted">
            When your agent encounters something it can&apos;t do, AgentNode
            handles the rest automatically.
          </p>

          <div className="grid gap-px sm:grid-cols-3 rounded-xl border border-border overflow-hidden">
            {[
              {
                step: "Detect",
                text: "Agent fails at a task. AgentNode analyzes the error and identifies the missing capability.",
              },
              {
                step: "Resolve",
                text: "AgentNode searches its registry, scores matches by capability fit, trust level, and compatibility.",
              },
              {
                step: "Install & retry",
                text: "The best-matching skill is installed, verified, and the agent retries the task — all in one call.",
              },
            ].map((item) => (
              <div key={item.step} className="bg-card p-6">
                <h3 className="mb-2 text-sm font-bold uppercase tracking-wider text-primary">
                  {item.step}
                </h3>
                <p className="text-sm text-muted leading-relaxed">
                  {item.text}
                </p>
              </div>
            ))}
          </div>

          <pre className="mt-8 rounded-lg bg-[#0d1117] px-4 py-3 text-sm text-gray-300 overflow-x-auto">
            <code>
              {`# One call — detect gap, install skill, retry automatically
from agentnode_sdk import AgentNodeClient

client = AgentNodeClient()
result = client.smart_run(
    lambda: process_pdf("report.pdf"),
    auto_upgrade_policy="safe"
)
# result.upgraded == True, result.installed_slug == "pdf-reader-pack"`}
            </code>
          </pre>
        </div>
      </section>

      {/* Policies */}
      <section className="border-b border-border bg-card/30">
        <div className="mx-auto max-w-4xl px-4 sm:px-6 py-20">
          <h2 className="mb-4 text-2xl font-bold text-foreground">
            Auto-upgrade policies
          </h2>
          <p className="mb-12 max-w-2xl text-muted">
            You control how much autonomy your agent has. Set the policy once —
            it applies to all automatic installations.
          </p>

          <div className="grid gap-6 sm:grid-cols-3">
            {policies.map((policy) => (
              <div
                key={policy.name}
                className="rounded-xl border border-border bg-card p-6"
              >
                <div className="mb-3 flex items-center gap-3">
                  <code className="text-lg font-semibold text-foreground">
                    &quot;{policy.name}&quot;
                  </code>
                  {policy.tag && (
                    <span className="rounded-full bg-primary/10 px-2.5 py-0.5 text-xs font-medium text-primary">
                      {policy.tag}
                    </span>
                  )}
                </div>
                <p className="text-sm text-muted leading-relaxed">
                  {policy.description}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Permissions */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-4xl px-4 sm:px-6 py-20">
          <h2 className="mb-4 text-2xl font-bold text-foreground">
            Permissions
          </h2>
          <p className="mb-12 max-w-2xl text-muted">
            Every skill declares what it needs. AgentNode enforces these
            permissions at install time and at runtime. Skills that request more
            than their trust level allows are blocked.
          </p>

          <div className="space-y-4">
            {permissions.map((perm) => (
              <div
                key={perm.label}
                className="flex flex-col sm:flex-row sm:items-baseline gap-2 sm:gap-6 rounded-lg border border-border bg-card px-5 py-4"
              >
                <span className="text-sm font-semibold text-foreground shrink-0 sm:w-36">
                  {perm.label}
                </span>
                <code className="text-xs text-primary shrink-0">
                  {perm.values}
                </code>
                <span className="text-sm text-muted">{perm.description}</span>
              </div>
            ))}
          </div>

          <p className="mt-6 text-sm text-muted">
            Trusted and curated skills run directly in your process for speed.
            Verified and unverified skills run in an isolated subprocess with a
            restricted environment.
          </p>
        </div>
      </section>

      {/* Works with */}
      <section className="border-b border-border bg-card/30">
        <div className="mx-auto max-w-4xl px-4 sm:px-6 py-20">
          <h2 className="mb-4 text-2xl font-bold text-foreground">
            Works with your stack
          </h2>
          <p className="mb-12 max-w-2xl text-muted">
            AgentNode works with any AI system that supports Python or tool
            integration — ChatGPT, Claude, Gemini, LangChain, CrewAI, Ollama,
            and more.
          </p>
          <AiStackLogos />
        </div>
      </section>

      {/* CTA */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-4xl px-4 sm:px-6 py-20 text-center">
          <h2 className="mb-4 text-2xl font-bold text-foreground">
            Ready to go?
          </h2>
          <p className="mx-auto mb-8 max-w-lg text-muted">
            Install the SDK, set your policy, and let your agent figure out the
            rest.
          </p>
          <div className="flex flex-wrap items-center justify-center gap-4">
            <Link
              href="/search"
              className="rounded-lg bg-primary px-6 py-2.5 text-sm font-medium text-white transition-colors hover:bg-primary-hover"
            >
              Explore skills
            </Link>
            <Link
              href="/docs"
              className="rounded-lg border border-border px-6 py-2.5 text-sm font-medium text-foreground transition-colors hover:bg-card"
            >
              Read the docs
            </Link>
            <a
              href="https://github.com/agentnode-ai/agentnode"
              target="_blank"
              rel="noopener noreferrer"
              className="rounded-lg border border-border px-6 py-2.5 text-sm font-medium text-foreground transition-colors hover:bg-card"
            >
              GitHub
            </a>
          </div>
        </div>
      </section>
    </main>
  );
}
