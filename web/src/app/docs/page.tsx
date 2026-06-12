import type { Metadata } from "next";
import Link from "next/link";
import { CodeBlock, DOCS_NAV, DOCS_APPLIES, DOCS_UPDATED } from "@/components/docs";

const DESCRIPTION =
  "Developer documentation for AgentNode: install the SDK, discover and run verified agent skills, set up the sandbox and credentials, and publish your own packages.";

export const metadata: Metadata = {
  title: "Documentation — SDK, CLI & API Reference",
  description: DESCRIPTION,
  alternates: { canonical: "/docs" },
  openGraph: {
    title: "AgentNode Documentation — SDK, CLI & API Reference",
    description: DESCRIPTION,
    type: "website",
    url: "/docs",
    siteName: "AgentNode",
  },
  twitter: {
    card: "summary_large_image",
    site: "@AgentNodenet",
  },
};

// Short card blurbs for the hub grid (one line per topic).
const BLURBS: Record<string, string> = {
  "/docs/quickstart": "Zero to your first verified agent skill in minutes.",
  "/docs/installation": "Requirements, install options, and CLI verification.",
  "/docs/discovery": "How search and the resolution engine find the right pack.",
  "/docs/packages": "Install flow, hash verification, lockfile, updates.",
  "/docs/llm-runtime": "Connect OpenAI, Anthropic, and Gemini agents to the registry.",
  "/docs/llm-providers": "OpenRouter, DeepSeek, Mistral, Qwen, Gemini, local Ollama.",
  "/docs/agents": "Agent packages: manifests, tiers, tool access.",
  "/docs/sandbox": "Community code runs isolated — or not at all.",
  "/docs/security": "What is enforced, per trust tier — honestly documented.",
  "/docs/credentials": "Connector tokens, OAuth, and credential resolution.",
  "/docs/guard": "Pre-execution policy gateway: allow, prompt, deny, audit.",
  "/docs/trust": "Four trust levels, signatures, and the permission model.",
  "/docs/data-sovereignty": "What stays local and what the registry sees.",
  "/docs/publishing": "From project structure to a published, verified pack.",
  "/docs/verification": "The verification pipeline, scoring, and tiers.",
  "/docs/manifest": "Full ANP manifest field reference.",
  "/docs/github-action": "Publish packs from CI with the official action.",
  "/docs/import": "Convert MCP, LangChain, and OpenAI tools into packs.",
  "/docs/cli": "Every CLI command with flags and examples.",
  "/docs/python-sdk": "The Python SDK surface: search, resolve, install, run.",
  "/docs/rest-api": "REST endpoints with request/response examples.",
  "/docs/mcp": "Use AgentNode through the Model Context Protocol.",
};

export default function DocsHubPage() {
  return (
    <div className="mx-auto max-w-6xl px-4 sm:px-6 py-12">
      <div className="mb-10 max-w-3xl">
        <h1 className="mb-2 text-3xl font-bold text-foreground">Documentation</h1>
        <p className="mb-3 text-xs text-muted/70">
          {DOCS_APPLIES} · Last updated: {DOCS_UPDATED}
        </p>
        <p className="text-muted">
          AgentNode is the package manager and secure runtime for AI agent
          skills: agents discover, install, and run verified tools at runtime.
          This documentation covers the CLI, Python SDK, and REST API — from
          first install to publishing your own packages.
        </p>
      </div>

      {/* Quick start */}
      <div className="mb-12 max-w-3xl">
        <CodeBlock title="terminal">{`$ pip install agentnode-sdk
$ agentnode setup
$ agentnode install word-counter-pack`}</CodeBlock>
        <p className="mt-2 text-sm text-muted">
          New here? Start with the{" "}
          <Link href="/docs/quickstart" className="text-primary hover:underline">
            Quick Start
          </Link>
          .
        </p>
      </div>

      {/* Topic grid */}
      <div className="space-y-10">
        {DOCS_NAV.map((group) => (
          <section key={group.title}>
            <h2 className="mb-4 text-sm font-semibold uppercase tracking-wider text-muted">
              {group.title}
            </h2>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {group.entries.map((entry) => (
                <Link key={entry.href} href={entry.href} className="group block">
                  <div className="h-full rounded-xl border border-border bg-card p-4 transition-colors hover:border-primary/30">
                    <p className="mb-1 text-sm font-semibold text-foreground group-hover:text-primary">
                      {entry.label}
                    </p>
                    <p className="text-xs text-muted">{BLURBS[entry.href] ?? ""}</p>
                  </div>
                </Link>
              ))}
            </div>
          </section>
        ))}
      </div>

      <div className="mt-12 border-t border-border pt-6 text-sm text-muted">
        <p>
          Also see the{" "}
          <Link href="/changelog" className="text-primary hover:underline">
            changelog
          </Link>
          ,{" "}
          <a
            href="https://github.com/agentnode-ai/agentnode"
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary hover:underline"
          >
            GitHub repository
          </a>
          , and{" "}
          <a href="/llms.txt" className="text-primary hover:underline">
            llms.txt
          </a>{" "}
          for AI tooling.
        </p>
      </div>
    </div>
  );
}
