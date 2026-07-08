import type { Metadata } from "next";
import Link from "next/link";
import PackageSearch from "@/components/PackageSearch";
import McpCodeBlock from "./McpCodeBlock";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "MCP Servers",
  description:
    "Discover and run MCP servers with AgentNode. Connect your AI agents to filesystems, databases, search APIs, GitHub, Slack, and more.",
  alternates: {
    canonical: "/mcp",
  },
  openGraph: {
    title: "MCP Servers | AgentNode",
    description:
      "Connect external tools to your AI agents. Filesystem, search, databases, GitHub, Slack - ready to install and run.",
    type: "website",
    url: "/mcp",
    siteName: "AgentNode",
  },
  twitter: {
    card: "summary_large_image",
    site: "@AgentNodenet",
  },
};



export default async function McpPage() {

  return (
    <div className="mx-auto max-w-6xl px-4 sm:px-6 py-12">
      {/* Hero */}
      <header className="mb-12 text-center">
        <h1 className="text-4xl font-bold text-foreground sm:text-5xl">
          MCP Servers
        </h1>
        <p className="mt-4 text-lg text-muted max-w-2xl mx-auto">
          Connect external tools to your AI agents.{" "}
          Filesystem. Search. Databases. GitHub. Slack.
        </p>

        {/* Trust badges */}
        <div className="mt-6 flex flex-wrap justify-center gap-3">
          <span className="rounded-full border border-green-500/20 bg-green-500/10 px-4 py-1.5 text-xs font-medium text-green-400">
            No account needed
          </span>
          <span className="rounded-full border border-blue-500/20 bg-blue-500/10 px-4 py-1.5 text-xs font-medium text-blue-400">
            No API key for first MCP
          </span>
          <span className="rounded-full border border-purple-500/20 bg-purple-500/10 px-4 py-1.5 text-xs font-medium text-purple-400">
            Runs locally
          </span>
        </div>

        <div className="mt-8 flex flex-wrap justify-center gap-4">
          <Link
            href="/search?runtime=mcp"
            className="rounded-lg bg-primary px-5 py-2.5 text-sm font-medium text-white hover:bg-primary/90 transition-colors"
          >
            Browse MCP Servers
          </Link>
          <a
            href="#getting-started"
            className="rounded-lg border border-border px-5 py-2.5 text-sm font-medium text-muted hover:text-foreground hover:border-primary/30 transition-colors"
          >
            Getting Started
          </a>
        </div>
      </header>

      {/* Browse MCP Servers — embedded search with locked runtime filter */}
      <section className="mb-16">
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-2xl font-bold text-foreground">
            Available MCP Servers
          </h2>
        </div>
        <PackageSearch
          fixed={{ runtime: "mcp" }}
          basePath="/mcp"
          heading={null}
          autoFocus={false}
        />
      </section>

      {/* What is MCP? - brief context */}
      <section className="mb-14 text-center">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-muted mb-3">
          What is an MCP Server?
        </h2>
        <p className="text-base text-muted max-w-2xl mx-auto leading-relaxed">
          An MCP server is a tool connector that lets AI agents interact with
          files, databases, APIs, developer tools, and other systems.
          AgentNode helps you discover, install, and run them.
        </p>
      </section>

      {/* Getting Started */}
      <section id="getting-started" className="mb-16">
        <h2 className="text-2xl font-bold text-foreground mb-8 text-center">
          Your First MCP in 3 Steps
        </h2>

        <div className="space-y-8 max-w-2xl mx-auto">
          {/* Step 1 */}
          <div>
            <div className="flex items-center gap-3 mb-3">
              <span className="flex h-8 w-8 items-center justify-center rounded-full bg-primary/10 text-sm font-bold text-primary">
                1
              </span>
              <h3 className="text-lg font-semibold text-foreground">
                Install AgentNode
              </h3>
            </div>
            <McpCodeBlock code="pip install agentnode-sdk" language="bash" />
            <p className="mt-2 text-xs text-muted">
              Python 3.10+ required. No account needed.
            </p>
          </div>

          {/* Step 2 */}
          <div>
            <div className="flex items-center gap-3 mb-3">
              <span className="flex h-8 w-8 items-center justify-center rounded-full bg-primary/10 text-sm font-bold text-primary">
                2
              </span>
              <h3 className="text-lg font-semibold text-foreground">
                Install an MCP Server
              </h3>
            </div>
            <McpCodeBlock code="agentnode install mcp-filesystem" language="bash" />
            <p className="mt-2 text-xs text-muted">
              Connects your agent to the local filesystem. No API key needed.
            </p>
          </div>

          {/* Step 3 */}
          <div>
            <div className="flex items-center gap-3 mb-3">
              <span className="flex h-8 w-8 items-center justify-center rounded-full bg-primary/10 text-sm font-bold text-primary">
                3
              </span>
              <h3 className="text-lg font-semibold text-foreground">
                Check & Run
              </h3>
            </div>
            <McpCodeBlock
              code={`agentnode mcp doctor mcp-filesystem\nagentnode run mcp-filesystem --input '{"path": "."}'`}
              language="bash"
            />
            <p className="mt-2 text-xs text-muted">
              Doctor confirms your setup works. Run executes the MCP server and returns results.
            </p>

            {/* Expected output */}
            <div className="mt-4 rounded-lg border border-green-500/20 bg-green-500/5 p-4">
              <p className="text-xs font-semibold text-green-400 mb-2">Expected output</p>
              <pre className="text-xs font-mono text-green-400/80 overflow-x-auto">
{`{"content": [{"text": "README.md\\nsrc/\\ntests/"}]}`}
              </pre>
              <p className="mt-2 text-xs text-green-400/60">
                That&apos;s it. Your first MCP server is running.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* Next: API-powered MCP */}
      <section className="mb-16 rounded-xl border border-border bg-card p-6 sm:p-8 max-w-2xl mx-auto">
        <h2 className="text-xl font-bold text-foreground mb-2">
          Next: Add an API-powered MCP
        </h2>
        <p className="text-sm text-muted mb-6">
          Now try an MCP that connects to an external service. This one searches the web via Brave Search.
        </p>

        <div className="space-y-4">
          <McpCodeBlock code="agentnode install mcp-brave-search" language="bash" />

          {/* ENV setup with OS tabs */}
          <div>
            <p className="text-xs font-semibold uppercase tracking-wider text-muted mb-2">
              Set your API key
            </p>
            <McpCodeBlock
              code='export BRAVE_API_KEY="your-key-here"'
              language="macOS / Linux"
              altCode='$env:BRAVE_API_KEY="your-key-here"'
              altLanguage="Windows (PowerShell)"
            />
            <p className="mt-2 text-xs text-muted">
              <a
                href="https://brave.com/search/api/"
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary hover:underline"
              >
                Get a free Brave API key
              </a>
            </p>
          </div>

          <McpCodeBlock
            code={`agentnode mcp doctor mcp-brave-search\nagentnode run mcp-brave-search --input '{"query": "latest AI news"}'`}
            language="bash"
          />
        </div>
      </section>


      {/* Publish your own MCP */}
      <section className="mb-16 rounded-xl border border-border bg-card p-6 sm:p-8 max-w-2xl mx-auto">
        <h2 className="text-xl font-bold text-foreground mb-2">
          Publish your own MCP server
        </h2>
        <p className="text-sm text-muted mb-4">
          Maintain an MCP server? List it in the AgentNode catalog. Verify the
          manifest locally, then submit it for review — and track the
          submission status from the CLI.
        </p>
        <McpCodeBlock
          code={`agentnode mcp verify .       # check your agentnode.yaml\nagentnode mcp submit .       # submit for catalog review\nagentnode mcp status <id>    # track review status`}
          language="bash"
        />
        <p className="mt-3 text-xs text-muted">
          Submissions are reviewed before they go live. Prefer the browser?{" "}
          <Link href="/mcp/submit" className="text-primary hover:underline">
            Submit via the web form
          </Link>{" "}
          — or see the{" "}
          <Link href="/docs/cli" className="text-primary hover:underline">
            CLI reference
          </Link>{" "}
          for the full publishing flow.
        </p>
      </section>

      {/* Footer context */}
      <footer className="text-center text-sm text-muted/60 py-8 border-t border-border">
        <p>
          MCP servers run locally on your machine.{" "}
          AgentNode helps you discover, install, and run them.
        </p>
      </footer>
    </div>
  );
}
