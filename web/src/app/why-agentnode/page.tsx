import Link from "next/link";
import { VerificationPipelineDiagram, TrustPyramidDiagram } from "@/components/diagrams";

export const metadata = {
  title: "Why AgentNode — The Verified Agent Skills Registry",
  description:
    "Why developers choose AgentNode over PyPI for AI agent tools. 4-step verification, trust-aware resolution, security scanning, and cross-framework agent skills — not 500K unverified packages.",
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
    title: "Verified on Publish",
    subtitle: "Every package is tested before it reaches your agent.",
    description:
      "Every pack goes through a 4-step verification pipeline on publish: Install (can it be pip-installed?), Import (does the module load?), Smoke Test (does the entry point execute?), and Unit Tests (do the author's tests pass?). If install or import fails, the pack is auto-quarantined and never reaches the registry. Smoke or test issues get transparent warning badges so you can make an informed decision. No other registry does this. PyPI lists packages that fail to install. npm has abandoned packages with zero warning. AgentNode guarantees: packages are tested on publish and scored for quality.",
  },
  {
    number: "03",
    title: "One Format, Every Framework",
    subtitle: "Per-tool entrypoints. Typed schemas. No framework lock-in.",
    description:
      "ANP v0.2 introduces per-tool entrypoints: a single pack can export multiple tools, each individually addressable via load_tool(). Every tool has typed JSON Schema input and output, so your agent knows exactly what to pass and what to expect. It works with LangChain, CrewAI, or vanilla Python. Write your agent in whatever framework you prefer — the packs adapt to you, not the other way around.",
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
    title: "Fully Open Source",
    subtitle: "MIT-licensed tools. Open format. No vendor lock-in.",
    description:
      "The CLI, SDK, and ANP pack format are MIT-licensed and developed in the open on GitHub. Every pack is inspectable — you see the manifest, permissions, and source before you install. The registry is community-driven: anyone can publish, the best tools earn trust through usage and review. You are never locked into AgentNode — packs are standard Python that works anywhere.",
  },
  {
    number: "07",
    title: "Build or Import — Zero Friction",
    subtitle: "Go from idea to published package in minutes, not days.",
    description:
      "Two paths to publishing, both frictionless. The AI Builder lets you describe what your agent should do in plain language — it generates a complete ANP package with manifest, code scaffold, and typed schemas, ready to publish. The Import tool takes existing LangChain, MCP, OpenAI, or CrewAI tool code and automatically converts it to ANP v0.2. No rewrite required. Paste your code, get a publish-ready package back.",
  },
];

const comparisonFeatures = [
  "AI-specific search",
  "Trust verification",
  "Automated verification",
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
    "Automated verification": "4-step: install, import, smoke test, unit tests",
    "Permission model": "Full manifest: network, filesystem, code execution",
    "Framework compatibility": "LangChain, CrewAI, generic Python",
    "Standardized interface": "Per-tool entrypoints via load_tool() — typed schemas",
    "Programmatic resolution": "SDK + CLI + MCP integration",
    "Security scanning": "Bandit, Ed25519 signatures, typosquatting detection",
    "Quality gate (tests)": "Automated on every publish",
  },
  PyPI: {
    "AI-specific search": "Keyword search only",
    "Trust verification": "None",
    "Automated verification": "None",
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
    "Automated verification": "None",
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
    "Automated verification": "Not documented",
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
    "Automated verification": "Not documented",
    "Permission model": "None",
    "Framework compatibility": "Single framework",
    "Standardized interface": "Framework-specific",
    "Programmatic resolution": "No",
    "Security scanning": "Not documented",
    "Quality gate (tests)": "Not documented",
  },
};

const openSourcePoints = [
  {
    title: "MIT-licensed SDK & CLI",
    detail:
      "The tools you build with are fully open source under MIT. No vendor lock-in, no proprietary dependencies. Fork it, extend it, self-host it — your choice.",
  },
  {
    title: "Open ANP format",
    detail:
      "The AgentNode Package format is an open specification. Every manifest is human-readable YAML. You can inspect exactly what a pack does, what it accesses, and how it works — before you install it.",
  },
  {
    title: "Transparent by default",
    detail:
      "Every pack links to its source repository. Read the code, audit the dependencies, verify the build. No black boxes, no hidden network calls, no surprises in production.",
  },
  {
    title: "Community-driven registry",
    detail:
      "Anyone can publish packs. The AI Builder and Import tool make publishing even easier — describe a capability or paste existing code and get a publish-ready package. The registry grows because developers contribute what they build — not because a company decides what gets listed. The best tools rise through trust levels and community usage.",
  },
  {
    title: "No walled garden",
    detail:
      "Packs work outside AgentNode too. The code is standard Python with a standard interface. If you ever leave the platform, your tools still work. We earn your usage, not lock it in.",
  },
  {
    title: "Public roadmap & governance",
    detail:
      "Feature development happens in the open on GitHub. File issues, propose changes, contribute code. The platform belongs to the community that builds on it.",
  },
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
  return <span className={`inline-block h-2 w-2 shrink-0 rounded-full ${color}`} />;
}

function getLevel(value: string): "full" | "partial" | "none" {
  const lower = value.toLowerCase();
  if (
    lower === "no" ||
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

        <div className="relative mx-auto max-w-6xl px-6 pb-20 pt-24 sm:pt-32 text-center">
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
            AgentNode is the verified, buildable, importable capability registry
            built specifically for AI agent developers. Every package is
            automatically verified on publish — packages are tested and scored for quality.
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
        <div className="mx-auto max-w-6xl px-6 py-20">
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
        <div className="mx-auto max-w-6xl px-6 py-20">
          <h2 className="mb-2 text-center text-2xl font-bold text-foreground sm:text-3xl">
            How AgentNode Is Different
          </h2>
          <p className="mb-14 text-center text-muted">
            Seven design decisions that make AgentNode the right choice for AI
            agent developers.
          </p>

          <div className="space-y-10">
            {differentiators.map((d) => (
              <div key={d.number}>
                <div className="rounded-lg border border-border bg-card p-6 sm:p-8">
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
                  {d.number === "02" && <VerificationPipelineDiagram />}
                </div>
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
        <div className="mx-auto max-w-6xl px-6 py-20">
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
from agentnode_sdk.installer import load_tool

client = AgentNodeClient()

# Resolve and install
client.resolve_and_install(["pdf_extraction"])

# Load and use — typed input/output
extract = load_tool("pdf-reader-pack")
result = extract({"file_path": "quarterly-report.pdf"})
print(result["pages"])`}</code>
            </pre>
          </div>

          {/* LLM Runtime alternative */}
          <div className="mx-auto mt-6 max-w-2xl overflow-hidden rounded-lg border border-border bg-[#0d1117]">
            <div className="flex items-center gap-2 border-b border-border/50 px-4 py-2">
              <div className="h-3 w-3 rounded-full bg-red-500/60" />
              <div className="h-3 w-3 rounded-full bg-yellow-500/60" />
              <div className="h-3 w-3 rounded-full bg-green-500/60" />
              <span className="ml-2 font-mono text-xs text-muted">llm_agent.py</span>
              <span className="ml-auto font-mono text-xs text-muted/50">LLM Runtime</span>
            </div>
            <pre className="overflow-x-auto p-4 font-mono text-sm leading-relaxed text-gray-300">
              <code>{`from openai import OpenAI
from agentnode_sdk import AgentNodeRuntime

# Or let the LLM handle everything
result = AgentNodeRuntime().run(
    provider="openai",
    client=OpenAI(),
    model="gpt-4o",
    messages=[{"role": "user", "content": "Extract text from report.pdf"}],
)`}</code>
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

      {/* ───────────────── Open Source ───────────────── */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-6xl px-6 py-20">
          <h2 className="mb-2 text-center text-2xl font-bold text-foreground sm:text-3xl">
            Open Source. Open Format. Open Registry.
          </h2>
          <p className="mb-12 text-center text-muted">
            AgentNode is built in the open. The SDK, CLI, and pack format are MIT-licensed.
            The registry is community-driven. You are never locked in.
          </p>

          <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {openSourcePoints.map((point) => (
              <div
                key={point.title}
                className="group rounded-lg border border-border bg-card p-6 transition-all hover:border-primary/30"
              >
                <h3 className="mb-2 text-sm font-semibold text-foreground">
                  {point.title}
                </h3>
                <p className="text-sm leading-relaxed text-muted">
                  {point.detail}
                </p>
              </div>
            ))}
          </div>

          <div className="mt-10 flex flex-col items-center gap-3 sm:flex-row sm:justify-center">
            <a
              href="https://github.com/agentnode-ai/agentnode"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex h-10 items-center justify-center rounded-lg border border-border px-6 text-sm font-medium text-foreground transition-colors hover:bg-card"
            >
              View on GitHub <span className="ml-1.5 text-xs">&#8599;</span>
            </a>
            <Link
              href="/license"
              className="text-sm text-primary underline transition-colors hover:text-foreground"
            >
              Read the license details
            </Link>
          </div>
        </div>
      </section>

      {/* ───────────────── For Teams ───────────────── */}
      <section className="border-b border-border bg-card/30">
        <div className="mx-auto max-w-6xl px-6 py-20">
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
                  Python — for agents &amp; apps
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

            <div className="rounded-lg border border-border bg-card p-6">
              <div className="mb-3 flex items-center gap-3">
                <h3 className="text-lg font-semibold text-foreground">
                  AI Builder
                </h3>
                <span className="rounded bg-primary/10 px-2 py-0.5 font-mono text-xs text-primary">
                  Available now
                </span>
              </div>
              <p className="text-sm leading-relaxed text-muted">
                Describe a capability in plain language and get a
                production-ready ANP package — manifest, code scaffold, and
                typed schemas. No boilerplate.
              </p>
            </div>

            <div className="rounded-lg border border-border bg-card p-6">
              <div className="mb-3 flex items-center gap-3">
                <h3 className="text-lg font-semibold text-foreground">
                  Import from Any Framework
                </h3>
                <span className="rounded bg-primary/10 px-2 py-0.5 font-mono text-xs text-primary">
                  Available now
                </span>
              </div>
              <p className="text-sm leading-relaxed text-muted">
                Paste existing LangChain, MCP, OpenAI, or CrewAI tool code and
                get an ANP package back. Zero rewrite required.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* ───────────────── Trust Levels Visual ───────────────── */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-6xl px-6 py-20">
          <h2 className="mb-2 text-center text-2xl font-bold text-foreground sm:text-3xl">
            Trust You Can Verify
          </h2>
          <p className="mb-12 text-center text-muted">
            Every pack in the registry has a clear, auditable trust level.
          </p>

          <TrustPyramidDiagram />

          <div className="mt-8 rounded-lg border border-border bg-card/50 p-5 text-center">
            <p className="text-sm leading-relaxed text-muted">
              <span className="font-semibold text-foreground">
                Two independent quality signals:
              </span>{" "}
              Every pack has both a <span className="font-medium text-foreground">Trust Level</span> (who
              built it?) and a <span className="font-medium text-foreground">Verification Badge</span>{" "}
              (passed / failed / pending — does it actually work?). Trust tells you about the publisher.
              Verification tells you about the code.
            </p>
          </div>
        </div>
      </section>

      {/* ───────────────── Bottom CTA ───────────────── */}
      <section>
        <div className="mx-auto max-w-6xl px-6 py-24">
          <div className="flex flex-col items-center text-center">
            <h2 className="text-2xl font-bold text-foreground sm:text-3xl">
              Ready to stop searching and start{" "}
              <span className="text-primary">resolving</span>?
            </h2>
            <p className="mt-4 max-w-xl text-muted">
              Verified packages. Per-tool entrypoints. AI-powered builder.
              One-click import from LangChain, CrewAI, MCP, or OpenAI. Install your first pack or
              build your own — your agents will thank you.
            </p>

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

            <div className="mt-6 flex flex-wrap items-center justify-center gap-4 text-sm">
              <Link
                href="/search"
                className="inline-flex h-10 items-center justify-center rounded-lg bg-primary px-6 font-medium text-white transition-colors hover:bg-primary/90"
              >
                Browse Packages
              </Link>
              <Link
                href="/builder"
                className="inline-flex h-10 items-center justify-center rounded-lg border border-border px-6 font-medium text-foreground transition-colors hover:bg-card"
              >
                Build a Pack
              </Link>
              <Link
                href="/import"
                className="inline-flex h-10 items-center justify-center rounded-lg border border-border px-6 font-medium text-foreground transition-colors hover:bg-card"
              >
                Import Existing Tools
              </Link>
              <Link
                href="/docs"
                className="text-primary underline transition-colors hover:text-foreground"
              >
                Read the Docs
              </Link>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
