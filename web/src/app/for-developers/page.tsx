import Link from "next/link";

export const metadata = {
  title: "For Developers — AgentNode",
  description:
    "Publish your AI agent tools where agents can find them. AgentNode is the specialized registry for AI capabilities — cross-framework, trust-verified, and built for discovery.",
};

/* ------------------------------------------------------------------ */
/*  Data                                                               */
/* ------------------------------------------------------------------ */

const problemPoints = [
  {
    title: "Lost in the noise",
    detail:
      "PyPI has 500,000+ packages. npm has 2 million. Your carefully-built AI tool sits next to a thousand abandoned forks and unrelated libraries. Agents have no way to find it.",
  },
  {
    title: "No standard format",
    detail:
      "LangChain tools look different from CrewAI tools, which look different from MCP tools, which look different from OpenAI function definitions. Every framework wants its own boilerplate.",
  },
  {
    title: "No permission model",
    detail:
      "Traditional registries have no concept of what a tool can access. An agent installing a PDF reader has no way to know if it also phones home, writes to disk, or executes code.",
  },
  {
    title: "No trust verification",
    detail:
      "Anyone can publish anything to PyPI. There are no trust tiers, no security scans, no provenance tracking. Agents can't tell safe packages from risky ones.",
  },
  {
    title: "No semantic discovery",
    detail:
      'An agent that needs "PDF extraction" has to guess package names. There is no standardized capability taxonomy that maps what an agent needs to which package provides it.',
  },
];

const publishSteps = [
  {
    step: 1,
    title: "Create your account",
    description:
      "Sign up on AgentNode, enable 2FA, and claim your publisher namespace. Your namespace appears in every package you publish.",
    code: null,
  },
  {
    step: 2,
    title: "Write your agentnode.yaml manifest",
    description:
      "The manifest declares what your tool does, what it needs access to, and which frameworks it supports. This is the single source of truth that makes your tool discoverable.",
    code: `manifest_version: "0.1"
package_id: "github-integration-pack"
package_type: "toolpack"
name: "GitHub Integration Pack"
publisher: "your-namespace"
version: "1.0.0"
summary: "Interact with GitHub repos, issues, and PRs."

runtime: "python"
entrypoint: "github_integration_pack.tool"
install_mode: "package"

capabilities:
  tools:
    - name: "manage_github"
      capability_id: "github_integration"
      description: "Create issues, review PRs, manage repos"
      input_schema:
        type: "object"
        properties:
          token: { type: "string" }
          operation: { type: "string" }
          repo: { type: "string" }
        required: ["token", "operation"]

permissions:
  network: { level: "unrestricted" }
  filesystem: { level: "none" }
  code_execution: { level: "none" }
  data_access: { level: "input_only" }

compatibility:
  frameworks: ["langchain", "crewai", "generic"]
  python: ">=3.10"

tags: ["github", "integration", "devtools"]`,
  },
  {
    step: 3,
    title: "Import existing tools (optional)",
    description:
      "Already have tools written for another framework? Import them directly. The CLI detects tool names, descriptions, schemas, and generates the manifest for you.",
    code: `# From MCP (Claude Code, Cursor)
agentnode import my_tools.py --from mcp

# From LangChain
agentnode import search_tool.py --from langchain

# From OpenAI function definitions
agentnode import functions.json --from openai

# From CrewAI
agentnode import crew_tools.py --from crewai`,
  },
  {
    step: 4,
    title: "Validate your package",
    description:
      "The validator checks your manifest syntax, verifies capability IDs exist in the taxonomy, ensures permissions are consistent, and confirms your entrypoint resolves.",
    code: `$ agentnode validate .

Validating github-integration-pack...
  Manifest syntax       OK
  Capability IDs        OK (1 tool, 0 resources)
  Permissions           OK (network: unrestricted)
  Entrypoint            OK (github_integration_pack.tool)
  Compatibility         OK (3 frameworks)

Package is valid and ready to publish.`,
  },
  {
    step: 5,
    title: "Publish",
    description:
      "One command. Your package is immediately available in the registry, searchable by capability, and installable by any agent developer.",
    code: `$ agentnode publish .

Publishing github-integration-pack@1.0.0...
  Uploading package       done
  Security scan           passed
  Signing package         done
  Indexing capabilities   done

Published! https://agentnode.net/packages/github-integration-pack`,
  },
];

const publisherBenefits = [
  {
    title: "Targeted audience",
    detail:
      "Every person browsing AgentNode is building or running AI agents. No hobbyist noise, no unrelated searches. Your tool is in front of people who need exactly what you built.",
  },
  {
    title: "Trust badges",
    detail:
      'Packages progress through trust tiers: unverified, verified, trusted, and curated. Each tier is earned through security review and community validation. A "trusted" badge signals quality before anyone reads your code.',
  },
  {
    title: "Cross-framework compatibility",
    detail:
      "Publish once, work everywhere. A single ANP pack can be used with LangChain, CrewAI, AutoGPT, or any Python agent framework. You don't need to maintain separate integrations.",
  },
  {
    title: "Capability-based discovery",
    detail:
      'Agents find your tool by what it does, not by name. If your tool provides "pdf_extraction", any agent searching for that capability will find you -- even if they have never heard of your package.',
  },
  {
    title: "Security scanning & signatures",
    detail:
      "Every published package is scanned for known vulnerabilities, signed with provenance metadata, and tracked for dependency issues. Your users get confidence. You get credibility.",
  },
  {
    title: "CI/CD with GitHub Actions",
    detail:
      "Automate publishing from your CI pipeline. Push a tag, and the agentnode/publish@v1 action validates, builds, and publishes your pack. No manual steps after initial setup.",
  },
  {
    title: "Install analytics",
    detail:
      "See how many agents install your pack, which capabilities are most requested, and which frameworks your users prefer. Coming soon with the analytics dashboard.",
    badge: "Coming Soon",
  },
  {
    title: "Revenue sharing for paid packs",
    detail:
      "Monetize your tools. Set a price, and AgentNode handles billing, licensing, and distribution. You build. We sell. Phase 3 of the platform roadmap.",
    badge: "Phase 3",
  },
];

const importPlatforms = [
  {
    name: "MCP",
    usedBy: "Claude Code, Cursor, Windsurf",
    example: `# Import an MCP @server.tool() function
agentnode import mcp_server.py --from mcp`,
  },
  {
    name: "LangChain",
    usedBy: "LangChain, LangGraph agents",
    example: `# Import a BaseTool subclass or @tool function
agentnode import search_tool.py --from langchain`,
  },
  {
    name: "OpenAI Functions",
    usedBy: "GPT Actions, Assistants API",
    example: `# Import a JSON function schema
agentnode import functions.json --from openai`,
  },
  {
    name: "CrewAI",
    usedBy: "CrewAI agents and crews",
    example: `# Import a CrewAI @tool decorated function
agentnode import tools.py --from crewai`,
  },
  {
    name: "ClawhHub",
    usedBy: "ClawhHub tool registry",
    example: `# Import a ClawhHub manifest
agentnode import manifest.json --from clawhub`,
  },
  {
    name: "Skills.sh",
    usedBy: "Skills.sh platform",
    example: `# Import a Skills.sh skill config
agentnode import skill.json --from skillssh`,
  },
];

const anpFeatures = [
  {
    title: "97 standardized capability IDs",
    detail:
      "Across 14 categories -- document processing, web & browsing, communication, data analysis, developer tools, and more. Your tool maps to a capability that agents already know how to request.",
  },
  {
    title: "Permission declarations",
    detail:
      "Every pack explicitly declares what it accesses: network (none/restricted/unrestricted), filesystem (none/temp/read/write), code execution (none/sandboxed/full), and data access levels. No surprises.",
  },
  {
    title: "Framework compatibility matrix",
    detail:
      'Declare which frameworks your pack supports -- langchain, crewai, generic, or all of them. The resolution engine only recommends your pack to agents using compatible frameworks.',
  },
  {
    title: "Trust & provenance metadata",
    detail:
      "Link to your source repo, commit hash, and build system. The trust layer verifies provenance and tracks your package through its entire lifecycle.",
  },
  {
    title: "Typed input/output schemas",
    detail:
      "Define JSON schemas for your tool inputs and outputs. Agents can validate calls before execution, and IDEs can provide autocomplete. No guessing at parameter types.",
  },
  {
    title: "Upgrade & recommendation hints",
    detail:
      'Declare which agent types benefit from your pack and what capability gaps it fills. The resolution engine uses these hints to recommend your tool to agents that need it.',
  },
];

const capabilityCategories = [
  { name: "Document Processing", examples: "pdf_extraction, document_summary, ocr_reading" },
  { name: "Web & Browsing", examples: "web_search, webpage_extraction, browser_automation" },
  { name: "Communication", examples: "email_sending, slack_messaging, discord_messaging" },
  { name: "Data Analysis", examples: "csv_analysis, data_visualization, database_query" },
  { name: "Developer Tools", examples: "code_execution, code_linting, test_generation" },
  { name: "Content Generation", examples: "text_generation, image_generation, video_generation" },
  { name: "Cloud & DevOps", examples: "docker_management, kubernetes_management, ci_cd_pipeline" },
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

        <div className="relative mx-auto max-w-4xl px-6 pb-20 pt-24 sm:pt-32 text-center">
          <span className="mb-4 inline-block rounded-full bg-primary/10 px-4 py-1.5 text-xs font-semibold uppercase tracking-wider text-primary">
            For Tool Developers
          </span>
          <h1 className="mx-auto max-w-3xl text-4xl font-bold leading-tight tracking-tight text-foreground sm:text-5xl">
            Publish Your AI Tools Where Agents Can Find Them
          </h1>
          <p className="mx-auto mt-6 max-w-2xl text-lg leading-relaxed text-muted">
            You build great tools. But buried in PyPI&apos;s 500,000 packages, they&apos;re invisible
            to the AI agents that need them. AgentNode is the specialized registry where agents
            discover, trust, and install capabilities -- by what they do, not by name.
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
        <div className="mx-auto max-w-4xl px-6 py-20">
          <h2 className="mb-4 text-center text-2xl font-bold text-foreground sm:text-3xl">
            General-purpose registries weren&apos;t built for AI tools
          </h2>
          <p className="mx-auto mb-12 max-w-2xl text-center text-muted">
            PyPI and npm are excellent for libraries. But AI agent tools have
            requirements that general-purpose registries cannot meet.
          </p>
          <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
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
      {/*  HOW PUBLISHING WORKS                                        */}
      {/* ============================================================ */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-4xl px-6 py-20">
          <h2 className="mb-4 text-center text-2xl font-bold text-foreground sm:text-3xl">
            Publish in five steps
          </h2>
          <p className="mx-auto mb-14 max-w-2xl text-center text-muted">
            From zero to live in the registry. No approval queues, no
            gatekeepers. Validate locally, publish instantly.
          </p>

          <div className="space-y-12">
            {publishSteps.map((s) => (
              <div key={s.step} className="flex gap-6">
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
                <div className="flex-1 pb-4">
                  <h3 className="mb-1 text-lg font-semibold text-foreground">
                    {s.title}
                  </h3>
                  <p className="mb-4 text-sm leading-relaxed text-muted">
                    {s.description}
                  </p>
                  {s.code && (
                    <div className="overflow-hidden rounded-lg border border-border bg-[#0d1117]">
                      <div className="flex items-center gap-2 border-b border-border/50 px-4 py-2">
                        <div className="h-3 w-3 rounded-full bg-red-500/60" />
                        <div className="h-3 w-3 rounded-full bg-yellow-500/60" />
                        <div className="h-3 w-3 rounded-full bg-green-500/60" />
                        <span className="ml-2 font-mono text-xs text-muted">
                          {s.step === 2
                            ? "agentnode.yaml"
                            : s.step === 4
                            ? "terminal"
                            : s.step === 5
                            ? "terminal"
                            : "terminal"}
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
      <section className="border-b border-border bg-card/30">
        <div className="mx-auto max-w-4xl px-6 py-20">
          <h2 className="mb-4 text-center text-2xl font-bold text-foreground sm:text-3xl">
            What you get as a publisher
          </h2>
          <p className="mx-auto mb-12 max-w-2xl text-center text-muted">
            AgentNode is not just a file host. It is infrastructure built to
            make your tools successful.
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
      <section className="border-b border-border">
        <div className="mx-auto max-w-4xl px-6 py-20">
          <h2 className="mb-4 text-center text-2xl font-bold text-foreground sm:text-3xl">
            Import from anywhere
          </h2>
          <p className="mx-auto mb-12 max-w-2xl text-center text-muted">
            Already have tools on another platform? Don&apos;t rewrite them.
            Import them in one command. The CLI detects tool names, descriptions,
            and schemas automatically, then generates your ANP manifest.
          </p>

          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
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

          <div className="mt-8 text-center">
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
      {/*  THE ANP FORMAT                                              */}
      {/* ============================================================ */}
      <section className="border-b border-border bg-card/30">
        <div className="mx-auto max-w-4xl px-6 py-20">
          <h2 className="mb-4 text-center text-2xl font-bold text-foreground sm:text-3xl">
            The ANP format: built for agent tools
          </h2>
          <p className="mx-auto mb-12 max-w-2xl text-center text-muted">
            The AgentNode Package format is not just packaging. It is a contract
            between your tool and the agents that use it. Every field exists to
            make discovery, trust, and integration automatic.
          </p>

          <div className="grid gap-5 sm:grid-cols-2">
            {anpFeatures.map((f) => (
              <div
                key={f.title}
                className="rounded-lg border border-border bg-card p-5"
              >
                <h3 className="mb-2 text-sm font-semibold text-foreground">
                  {f.title}
                </h3>
                <p className="text-sm leading-relaxed text-muted">
                  {f.detail}
                </p>
              </div>
            ))}
          </div>

          {/* Capability categories preview */}
          <div className="mt-10 rounded-lg border border-border bg-card p-6">
            <h3 className="mb-4 text-base font-semibold text-foreground">
              Capability taxonomy preview
            </h3>
            <p className="mb-4 text-sm text-muted">
              97 capability IDs across 14 categories. Map your tools to what
              agents already know how to request.
            </p>
            <div className="grid gap-3 sm:grid-cols-2">
              {capabilityCategories.map((cat) => (
                <div key={cat.name} className="flex items-start gap-2 text-sm">
                  <span className="mt-0.5 text-primary">&bull;</span>
                  <div>
                    <span className="font-medium text-foreground">
                      {cat.name}:
                    </span>{" "}
                    <span className="text-muted">{cat.examples}</span>
                  </div>
                </div>
              ))}
            </div>
            <div className="mt-4">
              <Link
                href="/capabilities"
                className="text-sm text-primary underline transition-colors hover:text-foreground"
              >
                Browse all 97 capability IDs &rarr;
              </Link>
            </div>
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/*  GITHUB ACTION                                               */}
      {/* ============================================================ */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-4xl px-6 py-20">
          <h2 className="mb-4 text-center text-2xl font-bold text-foreground sm:text-3xl">
            Automate with GitHub Actions
          </h2>
          <p className="mx-auto mb-10 max-w-2xl text-center text-muted">
            Push a tag, publish a pack. No manual steps after initial setup.
          </p>
          <div className="mx-auto max-w-2xl overflow-hidden rounded-lg border border-border bg-[#0d1117]">
            <div className="flex items-center gap-2 border-b border-border/50 px-4 py-2">
              <div className="h-3 w-3 rounded-full bg-red-500/60" />
              <div className="h-3 w-3 rounded-full bg-yellow-500/60" />
              <div className="h-3 w-3 rounded-full bg-green-500/60" />
              <span className="ml-2 font-mono text-xs text-muted">
                .github/workflows/publish.yml
              </span>
            </div>
            <pre className="overflow-x-auto p-4 font-mono text-sm leading-relaxed text-gray-300">
              <code>{`name: Publish to AgentNode
on:
  release:
    types: [published]

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Publish pack
        uses: agentnode/publish@v1
        with:
          api-key: \${{ secrets.AGENTNODE_API_KEY }}
          # Optional: dry-run for validation only
          # dry-run: true`}</code>
            </pre>
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/*  COMPARISON                                                  */}
      {/* ============================================================ */}
      <section className="border-b border-border bg-card/30">
        <div className="mx-auto max-w-4xl px-6 py-20">
          <h2 className="mb-4 text-center text-2xl font-bold text-foreground sm:text-3xl">
            AgentNode vs. general-purpose registries
          </h2>
          <p className="mx-auto mb-10 max-w-2xl text-center text-muted">
            Not a replacement for PyPI. A layer built specifically for AI tool
            distribution.
          </p>

          <div className="overflow-x-auto">
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
                  <td className="py-3 pr-6 text-foreground/80">Semantic capability search</td>
                  <td className="py-3 pr-6">No</td>
                  <td className="py-3 text-foreground">97 standardized capability IDs</td>
                </tr>
                <tr className="border-b border-border/50">
                  <td className="py-3 pr-6 text-foreground/80">Permission declarations</td>
                  <td className="py-3 pr-6">No</td>
                  <td className="py-3 text-foreground">Network, filesystem, code execution, data access</td>
                </tr>
                <tr className="border-b border-border/50">
                  <td className="py-3 pr-6 text-foreground/80">Trust tiers</td>
                  <td className="py-3 pr-6">No</td>
                  <td className="py-3 text-foreground">Unverified / Verified / Trusted / Curated</td>
                </tr>
                <tr className="border-b border-border/50">
                  <td className="py-3 pr-6 text-foreground/80">Cross-framework support</td>
                  <td className="py-3 pr-6">No</td>
                  <td className="py-3 text-foreground">One pack works with LangChain, CrewAI, and more</td>
                </tr>
                <tr className="border-b border-border/50">
                  <td className="py-3 pr-6 text-foreground/80">Agent-native resolution</td>
                  <td className="py-3 pr-6">Keyword search only</td>
                  <td className="py-3 text-foreground">Capability gap resolution engine</td>
                </tr>
                <tr className="border-b border-border/50">
                  <td className="py-3 pr-6 text-foreground/80">Input/output schemas</td>
                  <td className="py-3 pr-6">No</td>
                  <td className="py-3 text-foreground">JSON Schema in the manifest</td>
                </tr>
                <tr>
                  <td className="py-3 pr-6 text-foreground/80">Security scanning</td>
                  <td className="py-3 pr-6">Limited (Malware checks)</td>
                  <td className="py-3 text-foreground">Scan + signature + provenance tracking</td>
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
        <div className="mx-auto max-w-4xl px-6 py-24">
          <div className="flex flex-col items-center text-center">
            <h2 className="text-2xl font-bold text-foreground sm:text-3xl">
              Start publishing today
            </h2>
            <p className="mt-4 max-w-xl text-muted">
              Create your account, import or write your first pack, and put your
              tools in front of every AI agent developer on the platform.
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
            <div className="mt-8 flex items-center gap-4 text-sm text-muted">
              <code className="rounded border border-border bg-card px-3 py-1.5 font-mono text-xs text-foreground">
                npm install -g agentnode
              </code>
              <span>then</span>
              <code className="rounded border border-border bg-card px-3 py-1.5 font-mono text-xs text-foreground">
                agentnode publish .
              </code>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
