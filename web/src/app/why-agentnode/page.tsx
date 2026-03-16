import Link from "next/link";

export const metadata = {
  title: "Why AgentNode — Stop Searching. Start Resolving.",
  description:
    "AgentNode is the capability registry for AI agents. Discover, verify, and install agent tools with trust-aware resolution, security scanning, and framework compatibility — instead of searching 500K PyPI packages.",
};

/* ------------------------------------------------------------------ */
/*  Data                                                               */
/* ------------------------------------------------------------------ */

const problems = [
  {
    source: "PyPI",
    pain: "500K+ packages. No AI-specific search, no permission model, no framework compatibility metadata, no trust verification. Every package has a different API surface. You have to read source code just to know if a package is safe.",
  },
  {
    source: "npm",
    pain: "Same fragmentation, different runtime. Thousands of abandoned packages, no way to filter by agent compatibility, no security policy enforcement. You are on your own.",
  },
  {
    source: "Building from Scratch",
    pain: "Weeks of development per capability. You own the maintenance burden, the security responsibility, and every edge case. PDF parsing alone can take a team a sprint to get right.",
  },
  {
    source: "Other Tool Marketplaces",
    pain: "Framework lock-in, proprietary formats, limited discovery. Most require you to adopt an entire platform just to get access to a handful of tools.",
  },
];

const differentiators = [
  {
    number: "01",
    title: "Capability-First Search",
    subtitle: "Describe what your agent needs, not what package to install.",
    description:
      'You don\'t search by package name. You describe a capability gap — "pdf extraction", "email sending", "web search" — and the resolution engine returns ranked, verified results. Scoring is weighted: capability match (40%), framework compatibility (20%), runtime fit (15%), trust level (15%), permissions safety (10%). Your agent gets the best tool for the job, every time.',
  },
  {
    number: "02",
    title: "Trust & Security Built In",
    subtitle: "Four trust levels. Zero guesswork.",
    description:
      "Every pack progresses through four trust levels: Unverified, Verified, Trusted, and Curated. Each level requires more evidence — from basic metadata validation up to manual review by the AgentNode team. Every published pack gets Bandit security scanning, Ed25519 signature verification, and typosquatting detection. You see the full permission manifest before install: does this pack need network access? File system writes? Code execution? You decide before it runs.",
  },
  {
    number: "03",
    title: "One Format, Every Framework",
    subtitle: "No framework lock-in. One interface everywhere.",
    description:
      "Every AgentNode pack exports a single run() function. It works with LangChain, CrewAI, AutoGPT, or vanilla Python. Write your agent in whatever framework you prefer — the packs adapt to you, not the other way around. The ANP (AgentNode Package) format is an open specification with a standardized manifest, so you always know what you are getting.",
  },
  {
    number: "04",
    title: "Programmatic Resolution",
    subtitle: "Your agent resolves its own capability gaps. No human needed.",
    description:
      'The SDK enables fully autonomous capability resolution. Your agent calls client.resolve(["pdf_extraction", "web_search"]) and gets back ranked results it can evaluate and install programmatically. This is the foundation for self-improving agents — they identify what they cannot do, find the right tool, verify it meets policy, and install it. All through code.',
  },
  {
    number: "05",
    title: "Policy Enforcement",
    subtitle: "Set the rules. The engine enforces them.",
    description:
      'Define policies like "only trusted publishers", "no network access", "no code execution", or "only packs compatible with LangChain". The resolution engine filters automatically — non-compliant packages never surface. This is critical for production deployments where agents must operate within strict security boundaries.',
  },
  {
    number: "06",
    title: "76+ Ready-to-Use Packs",
    subtitle: "Document processing, web, communication, data, DevOps, AI/ML, and more.",
    description:
      "The registry ships with a curated library covering the most common agent capability gaps: PDF extraction, web search, browser automation, email, Slack, Discord, CSV analysis, data visualization, database connectors, Docker management, Kubernetes, code execution, linting, test generation, OCR, speech-to-text, and dozens more. All tested. All verified. All following the same interface.",
  },
];

const comparisonFeatures = [
  "AI-specific search",
  "Trust verification",
  "Permission model",
  "Framework compatibility",
  "Standardized interface",
  "Programmatic resolution",
  "Security scanning",
  "Quality gate (tests)",
];

const comparisonData: Record<string, Record<string, string>> = {
  AgentNode: {
    "AI-specific search": "Capability-based resolution with weighted scoring",
    "Trust verification": "4-level trust: unverified to curated",
    "Permission model": "Full manifest: network, filesystem, code execution",
    "Framework compatibility": "LangChain, CrewAI, AutoGPT, generic Python",
    "Standardized interface": "Every pack exports run() — one interface",
    "Programmatic resolution": "SDK + CLI + MCP integration",
    "Security scanning": "Bandit, Ed25519 signatures, typosquatting detection",
    "Quality gate (tests)": "Required for trusted+ level",
  },
  PyPI: {
    "AI-specific search": "Keyword search only",
    "Trust verification": "None",
    "Permission model": "None",
    "Framework compatibility": "Not tracked",
    "Standardized interface": "Every package is different",
    "Programmatic resolution": "pip install — no resolution",
    "Security scanning": "Basic malware scan",
    "Quality gate (tests)": "Not required",
  },
  npm: {
    "AI-specific search": "Keyword search only",
    "Trust verification": "None",
    "Permission model": "None",
    "Framework compatibility": "Not tracked",
    "Standardized interface": "Every package is different",
    "Programmatic resolution": "npm install — no resolution",
    "Security scanning": "npm audit (CVE-based)",
    "Quality gate (tests)": "Not required",
  },
  ClawhHub: {
    "AI-specific search": "Category browsing",
    "Trust verification": "Manual review",
    "Permission model": "Limited",
    "Framework compatibility": "Platform-specific",
    "Standardized interface": "Platform-specific format",
    "Programmatic resolution": "No",
    "Security scanning": "Manual review",
    "Quality gate (tests)": "Varies",
  },
  "Skills.sh": {
    "AI-specific search": "Curated list",
    "Trust verification": "Manual curation",
    "Permission model": "None",
    "Framework compatibility": "Single framework",
    "Standardized interface": "Framework-specific",
    "Programmatic resolution": "No",
    "Security scanning": "Not documented",
    "Quality gate (tests)": "Not documented",
  },
};

const categories = [
  { name: "Document Processing", examples: "PDF, OCR, Word, PowerPoint, Excel, Markdown" },
  { name: "Web & Browsing", examples: "Search, extraction, browser automation, screenshots" },
  { name: "Communication", examples: "Email, Slack, Discord, Telegram, WhatsApp" },
  { name: "Data Analysis", examples: "CSV, visualization, database, JSON, SQL" },
  { name: "Developer Tools", examples: "Code execution, linting, test generation, regex, Git" },
  { name: "Cloud & DevOps", examples: "Docker, Kubernetes, AWS, Azure, CI/CD" },
  { name: "AI & ML", examples: "Embeddings, semantic search, image generation, speech" },
  { name: "Content", examples: "Copywriting, SEO, translation, summarization, humanizer" },
];

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function StatusDot({ level }: { level: "full" | "partial" | "none" }) {
  const color =
    level === "full"
      ? "bg-green-500"
      : level === "partial"
        ? "bg-yellow-500"
        : "bg-red-500/60";
  return <span className={`inline-block h-2 w-2 rounded-full ${color}`} />;
}

function getLevel(value: string): "full" | "partial" | "none" {
  const lower = value.toLowerCase();
  if (
    lower.includes("none") ||
    lower.includes("not required") ||
    lower.includes("not tracked") ||
    lower.includes("not documented") ||
    lower.includes("keyword search only") ||
    lower.includes("no resolution") ||
    lower.includes("every package is different")
  )
    return "none";
  if (
    lower.includes("limited") ||
    lower.includes("varies") ||
    lower.includes("manual") ||
    lower.includes("basic") ||
    lower.includes("category") ||
    lower.includes("curated list") ||
    lower.includes("platform-specific") ||
    lower.includes("single framework") ||
    lower.includes("framework-specific") ||
    lower.includes("cve-based")
  )
    return "partial";
  return "full";
}

/* ------------------------------------------------------------------ */
/*  Page                                                               */
/* ------------------------------------------------------------------ */

export default function WhyAgentNodePage() {
  return (
    <div className="flex flex-col">
      {/* ───────────────── Hero ───────────────── */}
      <section className="relative overflow-hidden border-b border-border">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-primary/10 via-transparent to-transparent" />

        <div className="relative mx-auto max-w-4xl px-6 pb-20 pt-24 sm:pt-32 text-center">
          <p className="mb-4 text-sm font-medium uppercase tracking-widest text-primary">
            Why AgentNode
          </p>
          <h1 className="text-4xl font-bold leading-tight tracking-tight text-foreground sm:text-5xl lg:text-6xl">
            Stop Searching.{" "}
            <span className="text-primary">Start Resolving.</span>
          </h1>
          <p className="mx-auto mt-6 max-w-2xl text-lg leading-relaxed text-muted">
            When your agent needs PDF extraction, web search, or email
            capabilities, you should not be browsing through 500,000 PyPI
            packages hoping to find something that works with your framework.
            AgentNode is the capability registry built specifically for AI agent
            developers.
          </p>
          <div className="mt-10 flex flex-col items-center gap-4 sm:flex-row sm:justify-center">
            <Link
              href="/search"
              className="inline-flex h-11 items-center justify-center rounded-lg bg-primary px-8 text-sm font-medium text-white transition-colors hover:bg-primary/90"
            >
              Browse Packages
            </Link>
            <Link
              href="/docs"
              className="inline-flex h-11 items-center justify-center rounded-lg border border-border px-8 text-sm font-medium text-foreground transition-colors hover:bg-card"
            >
              Read the Docs
            </Link>
          </div>
        </div>
      </section>

      {/* ───────────────── The Problem ───────────────── */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-5xl px-6 py-20">
          <h2 className="mb-2 text-center text-2xl font-bold text-foreground sm:text-3xl">
            The Problem with the Status Quo
          </h2>
          <p className="mb-12 text-center text-muted">
            AI agent developers deserve better than general-purpose package
            registries.
          </p>

          <div className="grid gap-6 sm:grid-cols-2">
            {problems.map((p) => (
              <div
                key={p.source}
                className="rounded-lg border border-border bg-card p-6"
              >
                <div className="mb-3 flex items-center gap-3">
                  <span className="inline-flex items-center rounded bg-red-500/10 px-2.5 py-0.5 font-mono text-xs font-medium text-red-400">
                    {p.source}
                  </span>
                </div>
                <p className="text-sm leading-relaxed text-muted">{p.pain}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ───────────────── How AgentNode Is Different ───────────────── */}
      <section className="border-b border-border bg-card/30">
        <div className="mx-auto max-w-5xl px-6 py-20">
          <h2 className="mb-2 text-center text-2xl font-bold text-foreground sm:text-3xl">
            How AgentNode Is Different
          </h2>
          <p className="mb-14 text-center text-muted">
            Six design decisions that make AgentNode the right choice for AI
            agent developers.
          </p>

          <div className="space-y-10">
            {differentiators.map((d) => (
              <div
                key={d.number}
                className="rounded-lg border border-border bg-card p-6 sm:p-8"
              >
                <div className="mb-1 flex items-center gap-4">
                  <span className="font-mono text-sm font-bold text-primary">
                    {d.number}
                  </span>
                  <h3 className="text-lg font-semibold text-foreground sm:text-xl">
                    {d.title}
                  </h3>
                </div>
                <p className="mb-3 text-sm font-medium text-primary/80">
                  {d.subtitle}
                </p>
                <p className="text-sm leading-relaxed text-muted">
                  {d.description}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ───────────────── Side-by-Side Comparison ───────────────── */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-6xl px-6 py-20">
          <h2 className="mb-2 text-center text-2xl font-bold text-foreground sm:text-3xl">
            Side-by-Side Comparison
          </h2>
          <p className="mb-12 text-center text-muted">
            How AgentNode stacks up against the alternatives.
          </p>

          {/* Desktop table */}
          <div className="hidden overflow-x-auto lg:block">
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="border-b border-border">
                  <th className="px-4 py-3 text-left font-medium text-muted">
                    Feature
                  </th>
                  {Object.keys(comparisonData).map((platform) => (
                    <th
                      key={platform}
                      className={`px-4 py-3 text-left font-semibold ${
                        platform === "AgentNode"
                          ? "text-primary"
                          : "text-foreground"
                      }`}
                    >
                      {platform}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {comparisonFeatures.map((feature, i) => (
                  <tr
                    key={feature}
                    className={
                      i < comparisonFeatures.length - 1
                        ? "border-b border-border/50"
                        : ""
                    }
                  >
                    <td className="px-4 py-3 font-medium text-foreground">
                      {feature}
                    </td>
                    {Object.keys(comparisonData).map((platform) => {
                      const value = comparisonData[platform][feature];
                      const level = getLevel(value);
                      return (
                        <td
                          key={platform}
                          className={`px-4 py-3 ${
                            platform === "AgentNode"
                              ? "bg-primary/5 font-medium text-foreground"
                              : "text-muted"
                          }`}
                        >
                          <span className="flex items-center gap-2">
                            <StatusDot level={level} />
                            {value}
                          </span>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Mobile cards */}
          <div className="space-y-6 lg:hidden">
            {comparisonFeatures.map((feature) => (
              <div
                key={feature}
                className="rounded-lg border border-border bg-card p-4"
              >
                <h3 className="mb-3 text-sm font-semibold text-foreground">
                  {feature}
                </h3>
                <div className="space-y-2">
                  {Object.keys(comparisonData).map((platform) => {
                    const value = comparisonData[platform][feature];
                    const level = getLevel(value);
                    return (
                      <div
                        key={platform}
                        className="flex items-start gap-2 text-xs"
                      >
                        <StatusDot level={level} />
                        <span
                          className={
                            platform === "AgentNode"
                              ? "font-semibold text-primary"
                              : "text-muted"
                          }
                        >
                          {platform}:
                        </span>
                        <span className="text-muted">{value}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ───────────────── How It Works (Code) ───────────────── */}
      <section className="border-b border-border bg-card/30">
        <div className="mx-auto max-w-4xl px-6 py-20">
          <h2 className="mb-2 text-center text-2xl font-bold text-foreground sm:text-3xl">
            The Developer Experience
          </h2>
          <p className="mb-12 text-center text-muted">
            From capability gap to working code in under a minute.
          </p>

          <div className="mx-auto max-w-2xl overflow-hidden rounded-lg border border-border bg-[#0d1117]">
            <div className="flex items-center gap-2 border-b border-border/50 px-4 py-2">
              <div className="h-3 w-3 rounded-full bg-red-500/60" />
              <div className="h-3 w-3 rounded-full bg-yellow-500/60" />
              <div className="h-3 w-3 rounded-full bg-green-500/60" />
              <span className="ml-2 font-mono text-xs text-muted">
                resolve.py
              </span>
            </div>
            <pre className="overflow-x-auto p-4 font-mono text-sm leading-relaxed text-gray-300">
              <code>{`pip install agentnode-sdk`}</code>
            </pre>
          </div>

          <div className="mx-auto mt-4 max-w-2xl overflow-hidden rounded-lg border border-border bg-[#0d1117]">
            <div className="flex items-center gap-2 border-b border-border/50 px-4 py-2">
              <div className="h-3 w-3 rounded-full bg-red-500/60" />
              <div className="h-3 w-3 rounded-full bg-yellow-500/60" />
              <div className="h-3 w-3 rounded-full bg-green-500/60" />
              <span className="ml-2 font-mono text-xs text-muted">
                agent.py
              </span>
            </div>
            <pre className="overflow-x-auto p-4 font-mono text-sm leading-relaxed text-gray-300">
              <code>{`from agentnode_sdk import AgentNodeClient

client = AgentNodeClient()

# Find the best PDF tool for your framework
result = client.resolve(
    ["pdf_extraction"],
    framework="langchain"
)

# Check permissions and trust before installing
policy = client.check_policy(result[0].slug)
print(f"Trust level: {policy.trust_level}")
print(f"Permissions: {policy.permissions}")

# If it passes your policy, install it
# $ agentnode install pdf-reader-pack

# Use it — every pack has the same interface
from pdf_reader_pack.tool import run
text = run("quarterly-report.pdf")
print(text["pages"])`}</code>
            </pre>
          </div>

          <div className="mt-8 grid gap-4 sm:grid-cols-3">
            <div className="rounded-lg border border-border bg-card p-4 text-center">
              <p className="text-2xl font-bold text-primary">1</p>
              <p className="mt-1 text-xs text-muted">
                Resolve — describe the capability your agent needs
              </p>
            </div>
            <div className="rounded-lg border border-border bg-card p-4 text-center">
              <p className="text-2xl font-bold text-primary">2</p>
              <p className="mt-1 text-xs text-muted">
                Verify — check trust level, permissions, and policy compliance
              </p>
            </div>
            <div className="rounded-lg border border-border bg-card p-4 text-center">
              <p className="text-2xl font-bold text-primary">3</p>
              <p className="mt-1 text-xs text-muted">
                Install and use — one command, one interface, every framework
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* ───────────────── Capability Categories ───────────────── */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-5xl px-6 py-20">
          <h2 className="mb-2 text-center text-2xl font-bold text-foreground sm:text-3xl">
            76+ Packs Across 8 Categories
          </h2>
          <p className="mb-12 text-center text-muted">
            Whatever your agent needs, there is probably a pack for it already.
          </p>

          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {categories.map((cat) => (
              <div
                key={cat.name}
                className="group rounded-lg border border-border bg-card p-4 transition-all hover:border-primary/30"
              >
                <h3 className="mb-1 text-sm font-semibold text-foreground">
                  {cat.name}
                </h3>
                <p className="text-xs leading-relaxed text-muted">
                  {cat.examples}
                </p>
              </div>
            ))}
          </div>

          <p className="mt-8 text-center text-sm text-muted">
            <Link
              href="/search"
              className="text-primary underline hover:text-foreground"
            >
              Browse the full registry
            </Link>{" "}
            or{" "}
            <Link
              href="/capabilities"
              className="text-primary underline hover:text-foreground"
            >
              explore capability taxonomy
            </Link>
          </p>
        </div>
      </section>

      {/* ───────────────── For Teams ───────────────── */}
      <section className="border-b border-border bg-card/30">
        <div className="mx-auto max-w-5xl px-6 py-20">
          <h2 className="mb-2 text-center text-2xl font-bold text-foreground sm:text-3xl">
            Built for Teams and Production
          </h2>
          <p className="mb-12 text-center text-muted">
            AgentNode works the way your team already works.
          </p>

          <div className="grid gap-6 sm:grid-cols-2">
            <div className="rounded-lg border border-border bg-card p-6">
              <div className="mb-3 flex items-center gap-3">
                <h3 className="text-lg font-semibold text-foreground">
                  MCP Integration
                </h3>
                <span className="rounded bg-primary/10 px-2 py-0.5 font-mono text-xs text-primary">
                  Available now
                </span>
              </div>
              <p className="text-sm leading-relaxed text-muted">
                Expose AgentNode packs as MCP tools for Claude Code, Cursor, and
                any MCP-compatible client. Your team searches, resolves, and
                installs capabilities without leaving their editor. The MCP
                adapter wraps the full platform API — search, resolve, explain,
                and capability browsing.
              </p>
            </div>

            <div className="rounded-lg border border-border bg-card p-6">
              <div className="mb-3 flex items-center gap-3">
                <h3 className="text-lg font-semibold text-foreground">
                  Policy Controls
                </h3>
                <span className="rounded bg-primary/10 px-2 py-0.5 font-mono text-xs text-primary">
                  Available now
                </span>
              </div>
              <p className="text-sm leading-relaxed text-muted">
                Enforce organization-wide policies on which packs can be
                installed. Restrict by trust level, permission scope, publisher,
                or framework. The resolution engine filters non-compliant
                packages automatically — your agents stay within bounds.
              </p>
            </div>

            <div className="rounded-lg border border-border bg-card p-6">
              <div className="mb-3 flex items-center gap-3">
                <h3 className="text-lg font-semibold text-foreground">
                  Python SDK
                </h3>
                <span className="rounded bg-primary/10 px-2 py-0.5 font-mono text-xs text-primary">
                  Available now
                </span>
              </div>
              <p className="text-sm leading-relaxed text-muted">
                Full programmatic access via{" "}
                <code className="rounded bg-background px-1.5 py-0.5 font-mono text-xs">
                  agentnode-sdk
                </code>
                . Search, resolve, check policies, and manage installations from
                Python. Supports async operations and API key authentication for
                CI/CD pipelines and automated workflows.
              </p>
            </div>

            <div className="rounded-lg border border-border bg-card p-6">
              <div className="mb-3 flex items-center gap-3">
                <h3 className="text-lg font-semibold text-foreground">
                  GitHub Action
                </h3>
                <span className="rounded bg-primary/10 px-2 py-0.5 font-mono text-xs text-primary">
                  Available now
                </span>
              </div>
              <p className="text-sm leading-relaxed text-muted">
                Automate pack publishing from your CI/CD pipeline with{" "}
                <code className="rounded bg-background px-1.5 py-0.5 font-mono text-xs">
                  agentnode/publish@v1
                </code>
                . Validates manifests, runs security scanning, and publishes on
                release. Supports dry-run mode for validation-only workflows.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* ───────────────── Trust Levels Visual ───────────────── */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-4xl px-6 py-20">
          <h2 className="mb-2 text-center text-2xl font-bold text-foreground sm:text-3xl">
            Trust You Can Verify
          </h2>
          <p className="mb-12 text-center text-muted">
            Every pack in the registry has a clear, auditable trust level.
          </p>

          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {[
              {
                level: "Unverified",
                color: "text-gray-400",
                border: "border-gray-500/30",
                desc: "Published but not yet reviewed. Use at your own risk.",
              },
              {
                level: "Verified",
                color: "text-blue-400",
                border: "border-blue-500/30",
                desc: "Metadata validated, publisher identity confirmed, basic checks pass.",
              },
              {
                level: "Trusted",
                color: "text-green-400",
                border: "border-green-500/30",
                desc: "Security scanned, tests pass, active maintenance, community usage.",
              },
              {
                level: "Curated",
                color: "text-primary",
                border: "border-primary/30",
                desc: "Manually reviewed by AgentNode team. Highest assurance level.",
              },
            ].map((t) => (
              <div
                key={t.level}
                className={`rounded-lg border ${t.border} bg-card p-4`}
              >
                <p className={`mb-2 font-mono text-sm font-bold ${t.color}`}>
                  {t.level}
                </p>
                <p className="text-xs leading-relaxed text-muted">{t.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ───────────────── Bottom CTA ───────────────── */}
      <section>
        <div className="mx-auto max-w-4xl px-6 py-24">
          <div className="flex flex-col items-center text-center">
            <h2 className="text-2xl font-bold text-foreground sm:text-3xl">
              Ready to stop searching and start{" "}
              <span className="text-primary">resolving</span>?
            </h2>
            <p className="mt-4 max-w-xl text-muted">
              Install the CLI, browse the registry, or read the docs. Your
              agents will thank you.
            </p>

            <div className="mt-10 flex flex-col items-center gap-4">
              <code className="rounded-lg border border-border bg-card px-6 py-3 font-mono text-sm text-foreground">
                npm install -g agentnode
              </code>

              <div className="flex flex-wrap items-center justify-center gap-4 text-sm">
                <Link
                  href="/search"
                  className="inline-flex h-10 items-center justify-center rounded-lg bg-primary px-6 font-medium text-white transition-colors hover:bg-primary/90"
                >
                  Browse Packages
                </Link>
                <Link
                  href="/docs"
                  className="inline-flex h-10 items-center justify-center rounded-lg border border-border px-6 font-medium text-foreground transition-colors hover:bg-card"
                >
                  Read the Docs
                </Link>
                <Link
                  href="/capabilities"
                  className="text-primary underline transition-colors hover:text-foreground"
                >
                  Explore Capabilities
                </Link>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
