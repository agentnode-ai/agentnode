import Link from "next/link";

export const metadata = {
  title: "Publish AI Agent Skills — Get Your Tools Discovered by Agents",
  description:
    "Publish your AI tools as verified agent skills on AgentNode. One publish reaches LangChain, CrewAI, MCP, and OpenAI agents. 4-step verification, trust badges, and cross-framework discovery included.",
};

/* ------------------------------------------------------------------ */
/*  Data                                                               */
/* ------------------------------------------------------------------ */

const problemPoints = [
  {
    title: "Invisible to agents",
    detail:
      "Agents can't search by capability. Your tool sits among hundreds of thousands of packages — and agents never find it.",
  },
  {
    title: "No trust or permissions",
    detail:
      "Agents can't tell what your tool accesses before installing it. No permission model, no trust tiers, no security scanning.",
  },
  {
    title: "Fragmented ecosystem",
    detail:
      "Every framework uses different formats. You rewrite and maintain the same tool for LangChain, CrewAI, MCP, and OpenAI.",
  },
];

const publishSteps = [
  {
    step: 1,
    title: "Create your account",
    description:
      "Claim your namespace and start publishing.",
    code: null,
  },
  {
    step: 2,
    title: "Define your tool",
    description:
      "Describe what your tool does, what it needs, and how agents can use it.",
    code: `manifest_version: "0.2"
package_id: "github-integration-pack"
name: "GitHub Integration Pack"
entrypoint: "github_integration_pack.tool"

capabilities:
  tools:
    - name: "create_issue"
      capability_id: "github_integration"
      description: "Create a new GitHub issue"
      entrypoint: "github_integration_pack.tool:create_issue"
    - name: "list_repos"
      capability_id: "github_integration"
      description: "List user repositories"
      entrypoint: "github_integration_pack.tool:list_repos"

permissions:
  network: { level: "unrestricted" }
  filesystem: { level: "none" }

compatibility:
  frameworks: ["generic"]`,
  },
  {
    step: 3,
    title: "Import existing tools (optional)",
    description:
      "Already using LangChain, MCP or OpenAI? Import in one command.",
    code: `agentnode import tools.py --from langchain`,
  },
  {
    step: 4,
    title: "Validate",
    description: null,
    code: `$ agentnode validate .

Validating github-integration-pack...
  Manifest syntax       OK
  Capability IDs        OK
  Permissions           OK
  Entrypoint            OK

Package is valid and ready to publish.`,
  },
  {
    step: 5,
    title: "Publish",
    description: null,
    code: `$ agentnode publish .

Publishing github-integration-pack@1.0.0...
  Uploading package       done
  Security scan           passed
  Indexing capabilities   done

Published! Your tool is now discoverable and installable by agents.`,
  },
];

const publisherBenefits = [
  {
    title: "Targeted audience",
    detail:
      "Every person on AgentNode is actively building AI agents. No noise, no unrelated searches. Your tool is in front of people who need exactly what you built.",
  },
  {
    title: "Capability-based discovery",
    detail:
      "Agents find your tool by what it does, not by name. If your tool provides \"pdf_extraction\", any agent searching for that capability will find you.",
  },
  {
    title: "Trust badges",
    detail:
      "Packages progress through trust tiers: unverified, verified, trusted, and curated. A \"trusted\" badge signals quality before anyone reads your code.",
  },
  {
    title: "Cross-framework compatibility",
    detail:
      "Publish once, work everywhere. A single ANP package works across LangChain, CrewAI, and custom agents. No separate integrations.",
  },
  {
    title: "Security scanning",
    detail:
      "Every published package is scanned for vulnerabilities, signed with provenance metadata, and tracked for dependency issues.",
  },
  {
    title: "CI/CD ready",
    detail:
      "Publish from your pipeline with the agentnode/publish@v1 GitHub Action. Push a tag, and your package is validated, built, and published automatically.",
  },
  {
    title: "Install analytics",
    detail:
      "See how many agents install your package, which capabilities are most requested, and which frameworks your users prefer.",
    badge: "Coming Soon",
  },
  {
    title: "Revenue sharing",
    detail:
      "Monetize your tools. Set a price, and AgentNode handles billing, licensing, and distribution.",
    badge: "Coming Soon",
  },
];

const importPlatforms = [
  {
    name: "MCP",
    usedBy: "Claude Code, Cursor, Windsurf",
    example: `agentnode import mcp_server.py --from mcp`,
  },
  {
    name: "LangChain",
    usedBy: "LangChain, LangGraph agents",
    example: `agentnode import search_tool.py --from langchain`,
  },
  {
    name: "OpenAI Functions",
    usedBy: "GPT Actions, Assistants API",
    example: `agentnode import functions.json --from openai`,
  },
];

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function ForDevelopersPage() {
  return (
    <div className="flex flex-col">
      {/* ============================================================ */}
      {/*  HERO                                                        */}
      {/* ============================================================ */}
      <section className="relative overflow-hidden border-b border-border">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-primary/10 via-transparent to-transparent" />

        <div className="relative mx-auto max-w-6xl px-4 sm:px-6 pb-20 pt-24 sm:pt-32 text-center overflow-hidden">
          <span className="mb-4 inline-block rounded-full bg-primary/10 px-4 py-1.5 text-xs font-semibold uppercase tracking-wider text-primary">
            For Tool Developers
          </span>
          <h1 className="mx-auto max-w-3xl text-4xl font-bold leading-tight tracking-tight text-foreground sm:text-5xl">
            Get your AI tools discovered and installed by real agents
          </h1>
          <p className="mx-auto mt-6 max-w-2xl text-lg leading-relaxed text-muted">
            Your tools are buried in general-purpose registries — invisible to AI agents.
            AgentNode makes them discoverable, trusted, and installable.
          </p>
          <div className="mt-10 flex flex-col items-center gap-4 sm:flex-row sm:justify-center">
            <Link
              href="/auth/register"
              className="inline-flex h-12 items-center justify-center rounded-lg bg-primary px-8 text-sm font-medium text-white transition-colors hover:bg-primary/90"
            >
              Create Publisher Account
            </Link>
            <Link
              href="/import"
              className="inline-flex h-12 items-center justify-center rounded-lg border border-border px-8 text-sm font-medium text-foreground transition-colors hover:bg-card"
            >
              Import Existing Tools
            </Link>
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/*  THE PROBLEM                                                 */}
      {/* ============================================================ */}
      <section className="border-b border-border bg-card/30">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-20 overflow-hidden">
          <h2 className="mb-4 text-center text-2xl font-bold text-foreground sm:text-3xl">
            General-purpose registries weren&apos;t built for AI tools
          </h2>
          <p className="mx-auto mb-12 max-w-2xl text-center text-muted">
            PyPI and npm are great for libraries.
            But they weren&apos;t designed for how AI agents discover and use tools.
          </p>
          <div className="mx-auto grid max-w-4xl gap-5 sm:grid-cols-3">
            {problemPoints.map((point) => (
              <div
                key={point.title}
                className="rounded-lg border border-border bg-card p-5"
              >
                <div className="mb-2 text-sm font-semibold text-foreground">
                  {point.title}
                </div>
                <p className="text-sm leading-relaxed text-muted">
                  {point.detail}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/*  SOLUTION                                                    */}
      {/* ============================================================ */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-20 overflow-hidden">
          <h2 className="mb-4 text-center text-2xl font-bold text-foreground sm:text-3xl">
            Publish once. Get discovered everywhere.
          </h2>
          <p className="mx-auto max-w-2xl text-center text-lg leading-relaxed text-muted">
            AgentNode is a registry built specifically for AI agent tools.
            Agents find your tool by what it does — not by guessing package names.
            They verify it, trust it, and install it automatically.
          </p>
        </div>
      </section>

      {/* ============================================================ */}
      {/*  HOW PUBLISHING WORKS                                        */}
      {/* ============================================================ */}
      <section className="border-b border-border bg-card/30">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-20 overflow-hidden">
          <h2 className="mb-4 text-center text-2xl font-bold text-foreground sm:text-3xl">
            From code to installable capability in minutes
          </h2>
          <p className="mx-auto mb-14 max-w-2xl text-center text-muted">
            No approval queues, no gatekeepers. Validate locally, publish instantly.
          </p>

          <div className="mx-auto max-w-3xl space-y-12">
            {publishSteps.map((s) => (
              <div key={s.step} className="flex gap-4 sm:gap-6">
                {/* Step indicator */}
                <div className="flex flex-col items-center">
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary/10 text-lg font-bold text-primary">
                    {s.step}
                  </div>
                  {s.step < publishSteps.length && (
                    <div className="mt-2 w-px flex-1 bg-border" />
                  )}
                </div>

                {/* Content */}
                <div className="flex-1 min-w-0 pb-4">
                  <h3 className="mb-1 text-lg font-semibold text-foreground">
                    {s.title}
                  </h3>
                  {s.description && (
                    <p className="mb-4 text-sm leading-relaxed text-muted">
                      {s.description}
                    </p>
                  )}
                  {s.code && (
                    <div className="overflow-hidden rounded-lg border border-border bg-[#0d1117]">
                      <div className="flex items-center gap-2 border-b border-border/50 px-4 py-2">
                        <div className="h-3 w-3 rounded-full bg-red-500/60" />
                        <div className="h-3 w-3 rounded-full bg-yellow-500/60" />
                        <div className="h-3 w-3 rounded-full bg-green-500/60" />
                        <span className="ml-2 font-mono text-xs text-muted">
                          {s.step === 2 ? "agentnode.yaml" : "terminal"}
                        </span>
                      </div>
                      <pre className="overflow-x-auto p-4 font-mono text-sm leading-relaxed text-gray-300">
                        <code>{s.code}</code>
                      </pre>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/*  WHAT YOU GET                                                 */}
      {/* ============================================================ */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-20 overflow-hidden">
          <h2 className="mb-4 text-center text-2xl font-bold text-foreground sm:text-3xl">
            Everything your tool needs to succeed
          </h2>
          <p className="mx-auto mb-12 max-w-2xl text-center text-muted">
            AgentNode is not just a file host. It&apos;s infrastructure built to
            get your tools in front of the right audience.
          </p>

          <div className="grid gap-5 sm:grid-cols-2">
            {publisherBenefits.map((b) => (
              <div
                key={b.title}
                className="group rounded-xl border border-border bg-card p-6 transition-all hover:border-primary/30 hover:bg-card/80"
              >
                <div className="mb-2 flex items-center gap-3">
                  <h3 className="text-base font-semibold text-foreground">
                    {b.title}
                  </h3>
                  {b.badge && (
                    <span className="rounded bg-primary/10 px-2 py-0.5 font-mono text-xs text-primary">
                      {b.badge}
                    </span>
                  )}
                </div>
                <p className="text-sm leading-relaxed text-muted">
                  {b.detail}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/*  IMPORT FROM ANYWHERE                                        */}
      {/* ============================================================ */}
      <section className="border-b border-border bg-card/30">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-20 overflow-hidden">
          <h2 className="mb-4 text-center text-2xl font-bold text-foreground sm:text-3xl">
            Already built tools? Don&apos;t rewrite them. Import them.
          </h2>
          <p className="mx-auto mb-12 max-w-2xl text-center text-muted">
            Import your existing tools and make them installable instantly.
          </p>

          <div className="mx-auto grid max-w-4xl gap-4 sm:grid-cols-3">
            {importPlatforms.map((p) => (
              <div
                key={p.name}
                className="rounded-lg border border-border bg-card p-5"
              >
                <div className="mb-1 text-base font-semibold text-foreground">
                  {p.name}
                </div>
                <div className="mb-3 text-xs text-muted">{p.usedBy}</div>
                <div className="overflow-hidden rounded border border-border bg-[#0d1117]">
                  <pre className="overflow-x-auto p-3 font-mono text-xs leading-relaxed text-gray-300">
                    <code>{p.example}</code>
                  </pre>
                </div>
              </div>
            ))}
          </div>

          <p className="mt-6 text-center text-sm text-muted">
            Also supports CrewAI, ClawhHub, Skills.sh, and more.
          </p>

          <div className="mt-4 text-center">
            <Link
              href="/import"
              className="text-sm text-primary underline transition-colors hover:text-foreground"
            >
              Try the web-based import tool &rarr;
            </Link>
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/*  ANP FORMAT                                                  */}
      {/* ============================================================ */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-20 overflow-hidden">
          <h2 className="mb-4 text-center text-2xl font-bold text-foreground sm:text-3xl">
            The package format for agent tools
          </h2>
          <p className="mx-auto mb-12 max-w-2xl text-center text-muted">
            ANP (AgentNode Package) defines how tools become installable
            capabilities for AI agents.
          </p>

          <div className="mx-auto grid max-w-3xl gap-4 sm:grid-cols-2">
            <div className="rounded-lg border border-border bg-card p-5">
              <h3 className="mb-1 text-sm font-semibold text-foreground">Capability IDs</h3>
              <p className="text-sm leading-relaxed text-muted">
                Map your tool to capabilities agents already know how to request.
              </p>
            </div>
            <div className="rounded-lg border border-border bg-card p-5">
              <h3 className="mb-1 text-sm font-semibold text-foreground">Permissions</h3>
              <p className="text-sm leading-relaxed text-muted">
                Declare network, filesystem, code execution, and data access levels.
              </p>
            </div>
            <div className="rounded-lg border border-border bg-card p-5">
              <h3 className="mb-1 text-sm font-semibold text-foreground">Cross-framework</h3>
              <p className="text-sm leading-relaxed text-muted">
                Every ANP package works across all frameworks automatically — LangChain, CrewAI, MCP, and more.
              </p>
            </div>
            <div className="rounded-lg border border-border bg-card p-5">
              <h3 className="mb-1 text-sm font-semibold text-foreground">Typed schemas</h3>
              <p className="text-sm leading-relaxed text-muted">
                JSON schemas for inputs and outputs. Agents validate calls before execution.
              </p>
            </div>
          </div>

          <p className="mt-8 text-center text-sm text-muted">
            Every package follows the same contract — no adapters, no rewrites.
          </p>
        </div>
      </section>

      {/* ============================================================ */}
      {/*  HOW AGENTS USE YOUR TOOLS                                   */}
      {/* ============================================================ */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-20 overflow-hidden">
          <h2 className="mb-4 text-center text-2xl font-bold text-foreground sm:text-3xl">
            How agents use your tools at runtime
          </h2>
          <p className="mx-auto mb-10 max-w-2xl text-center text-muted">
            After you publish, any agent can discover and install your tool
            programmatically — no manual setup, no hardcoded dependencies.
          </p>

          <div className="mx-auto max-w-2xl overflow-hidden rounded-lg border border-border bg-[#0d1117]">
            <div className="flex items-center gap-2 border-b border-border/50 px-4 py-2">
              <div className="h-3 w-3 rounded-full bg-red-500/60" />
              <div className="h-3 w-3 rounded-full bg-yellow-500/60" />
              <div className="h-3 w-3 rounded-full bg-green-500/60" />
              <span className="ml-2 font-mono text-xs text-muted">
                agent.py
              </span>
              <span className="ml-auto font-mono text-xs text-muted/50">python</span>
            </div>
            <pre className="overflow-x-auto p-4 font-mono text-sm leading-relaxed text-gray-300">
              <code>{`from agentnode_sdk import AgentNodeClient
from agentnode_sdk.installer import load_tool

client = AgentNodeClient(api_key="ank_live_...")

# Agent needs PDF extraction → resolves your published tool
client.resolve_and_install(["pdf_extraction"])

# Loads and calls your tool — typed input/output
extract = load_tool("pdf-reader-pack")
result = extract({"file_path": "quarterly-report.pdf"})
print(result["text"])`}</code>
            </pre>
          </div>

          <div className="mt-8 mx-auto grid max-w-3xl gap-4 sm:grid-cols-4">
            <div className="rounded-lg border border-border bg-card p-4 text-center">
              <p className="text-2xl font-bold text-primary">1</p>
              <p className="mt-1 text-xs text-muted">Resolve — finds your tool by capability</p>
            </div>
            <div className="rounded-lg border border-border bg-card p-4 text-center">
              <p className="text-2xl font-bold text-primary">2</p>
              <p className="mt-1 text-xs text-muted">Verify — checks trust level and permissions</p>
            </div>
            <div className="rounded-lg border border-border bg-card p-4 text-center">
              <p className="text-2xl font-bold text-primary">3</p>
              <p className="mt-1 text-xs text-muted">Install — downloads, hash-checks, extracts</p>
            </div>
            <div className="rounded-lg border border-border bg-card p-4 text-center">
              <p className="text-2xl font-bold text-primary">4</p>
              <p className="mt-1 text-xs text-muted">Load — imports entrypoint, returns callable</p>
            </div>
          </div>

          <p className="mt-8 text-center text-sm text-muted">
            Higher verification scores rank your tool above competitors. See{" "}
            <Link href="/docs#runtime-quickstart" className="text-primary hover:underline">
              Runtime QuickStart
            </Link>{" "}
            for the full SDK reference.
          </p>
        </div>
      </section>

      {/* ============================================================ */}
      {/*  COMPARISON                                                  */}
      {/* ============================================================ */}
      <section className="border-b border-border bg-card/30">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-20 overflow-hidden">
          <h2 className="mb-10 text-center text-2xl font-bold text-foreground sm:text-3xl">
            Why not just use PyPI or npm?
          </h2>

          <div className="mx-auto max-w-3xl overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left">
                  <th className="py-3 pr-6 font-semibold text-foreground">Feature</th>
                  <th className="py-3 pr-6 font-semibold text-muted">PyPI / npm</th>
                  <th className="py-3 font-semibold text-primary">AgentNode</th>
                </tr>
              </thead>
              <tbody className="text-muted">
                <tr className="border-b border-border/50">
                  <td className="py-3 pr-6 text-foreground/80">Capability-based discovery</td>
                  <td className="py-3 pr-6">&#x2718;</td>
                  <td className="py-3 text-foreground">&#x2714;</td>
                </tr>
                <tr className="border-b border-border/50">
                  <td className="py-3 pr-6 text-foreground/80">Permission model</td>
                  <td className="py-3 pr-6">&#x2718;</td>
                  <td className="py-3 text-foreground">&#x2714;</td>
                </tr>
                <tr className="border-b border-border/50">
                  <td className="py-3 pr-6 text-foreground/80">Trust tiers</td>
                  <td className="py-3 pr-6">&#x2718;</td>
                  <td className="py-3 text-foreground">&#x2714;</td>
                </tr>
                <tr className="border-b border-border/50">
                  <td className="py-3 pr-6 text-foreground/80">Cross-framework support</td>
                  <td className="py-3 pr-6">&#x2718;</td>
                  <td className="py-3 text-foreground">&#x2714;</td>
                </tr>
                <tr>
                  <td className="py-3 pr-6 text-foreground/80">Agent-native installation</td>
                  <td className="py-3 pr-6">&#x2718;</td>
                  <td className="py-3 text-foreground">&#x2714;</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/*  BOTTOM CTA                                                  */}
      {/* ============================================================ */}
      <section>
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-24 overflow-hidden">
          <div className="flex flex-col items-center text-center">
            <h2 className="text-2xl font-bold text-foreground sm:text-3xl">
              Start publishing your first AI agent tool
            </h2>
            <p className="mt-4 max-w-xl text-muted">
              Create your account, publish your first package, and make your
              tools discoverable, trusted, and installable by AI agents.
            </p>
            <div className="mt-10 flex flex-col items-center gap-4 sm:flex-row">
              <Link
                href="/auth/register"
                className="inline-flex h-12 items-center justify-center rounded-lg bg-primary px-8 text-sm font-medium text-white transition-colors hover:bg-primary/90"
              >
                Create Publisher Account
              </Link>
              <Link
                href="/import"
                className="inline-flex h-12 items-center justify-center rounded-lg border border-border px-8 text-sm font-medium text-foreground transition-colors hover:bg-card"
              >
                Import Existing Tools
              </Link>
              <Link
                href="/docs"
                className="inline-flex h-12 items-center justify-center rounded-lg border border-border px-8 text-sm font-medium text-foreground transition-colors hover:bg-card"
              >
                Read the Docs
              </Link>
            </div>
            <div className="mt-10 mx-auto grid max-w-4xl gap-3 sm:grid-cols-3">
              <div className="rounded-lg border border-border bg-card px-5 py-3 text-center">
                <div className="mb-1 text-xs font-medium uppercase tracking-wider text-muted">Python &middot; for agents &amp; apps</div>
                <code className="whitespace-nowrap font-mono text-xs text-foreground">pip install agentnode-sdk</code>
              </div>
              <div className="rounded-lg border border-border bg-card px-5 py-3 text-center">
                <div className="mb-1 text-xs font-medium uppercase tracking-wider text-muted">Terminal &middot; install &amp; publish</div>
                <code className="whitespace-nowrap font-mono text-xs text-foreground">npm install -g agentnode-cli</code>
              </div>
              <div className="rounded-lg border border-border bg-card px-5 py-3 text-center">
                <div className="mb-1 text-xs font-medium uppercase tracking-wider text-muted">Frameworks &middot; LangChain, MCP</div>
                <code className="whitespace-nowrap font-mono text-xs text-foreground">pip install agentnode-langchain</code>
                <div className="mt-0.5">
                  <code className="whitespace-nowrap font-mono text-xs text-foreground">pip install agentnode-mcp</code>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
