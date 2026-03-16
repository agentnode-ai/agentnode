import Link from "next/link";

const docs = [
  {
    title: "Getting Started",
    description: "Install the CLI, search for packages, and integrate your first capability.",
    sections: [
      "Install the CLI: npm install -g agentnode",
      "Login: agentnode login",
      "Search: agentnode search pdf",
      "Install: agentnode install pdf-reader-pack",
      "Use: from pdf_reader_pack.tool import run",
    ],
  },
  {
    title: "Publishing Packages",
    description: "Create an ANP package and publish it to the registry.",
    sections: [
      "Create an agentnode.yaml manifest",
      "Build your package with pyproject.toml",
      "Enable 2FA on your account",
      "Validate: agentnode validate .",
      "Publish: agentnode publish .",
    ],
  },
  {
    title: "ANP Format",
    description: "The AgentNode Package format specification.",
    sections: [
      "identity: package_id, version, name, description",
      "runtime: runtime, entrypoint, install_mode",
      "capabilities: tool/resource/prompt declarations",
      "permissions: network, filesystem, code_execution",
      "compatibility: frameworks, runtime_version",
    ],
  },
  {
    title: "CLI Reference",
    description: "All 17 CLI commands and their options.",
    commands: [
      { cmd: "agentnode login", desc: "Authenticate with the registry" },
      { cmd: "agentnode search <query>", desc: "Search for packages" },
      { cmd: "agentnode install <slug>", desc: "Install a package" },
      { cmd: "agentnode update <slug>", desc: "Update to latest version" },
      { cmd: "agentnode rollback <slug>@<ver>", desc: "Roll back to specific version" },
      { cmd: "agentnode info <slug>", desc: "Show package details" },
      { cmd: "agentnode explain <slug>", desc: "Explain capabilities, permissions & use cases" },
      { cmd: "agentnode audit <slug>", desc: "Show trust & security info" },
      { cmd: "agentnode doctor", desc: "Analyze setup and suggest improvements" },
      { cmd: "agentnode list", desc: "Show installed packages" },
      { cmd: "agentnode publish <dir>", desc: "Publish a package" },
      { cmd: "agentnode validate <dir>", desc: "Validate a manifest" },
      { cmd: "agentnode report <slug>", desc: "Generate security report" },
      { cmd: "agentnode recommend", desc: "Get recommendations for missing capabilities" },
      { cmd: "agentnode resolve-upgrade", desc: "Find upgrade packages" },
      { cmd: "agentnode policy-check", desc: "Check policy constraints" },
      { cmd: "agentnode resolve", desc: "Resolve capabilities to packages" },
    ],
  },
  {
    title: "API Reference",
    description: "REST API endpoints for programmatic access.",
    sections: [
      "Auth: POST /v1/auth/register, /login, /2fa/setup, /2fa/verify",
      "Packages: GET /v1/packages/{slug}, POST /publish, /validate",
      "Install: GET /{slug}/install, POST /{slug}/install, POST /{slug}/download",
      "Resolution: POST /v1/resolve, /check-policy, /recommend",
      "Trust: GET /v1/packages/{slug}/trust",
      "Reviews: POST/GET /v1/packages/{slug}/reviews",
      "Reports: POST /v1/packages/{slug}/report",
    ],
  },
  {
    title: "SDK Reference",
    description: "Python SDK for search, resolution, and policy checks.",
    sections: [
      "pip install agentnode-sdk",
      "from agentnode_sdk import AgentNodeClient",
      "client = AgentNodeClient(api_key='ank_...')",
      "client.search('pdf')",
      "client.resolve(['pdf_extraction'])",
      "client.check_policy('pdf-reader-pack')",
    ],
  },
  {
    title: "MCP Integration",
    description: "Expose AgentNode packs as MCP tools for Claude Code, Cursor, and other MCP clients.",
    sections: [
      "pip install agentnode-mcp",
      "Pack server: agentnode-mcp --pack pdf-reader-pack",
      "Platform server: agentnode-mcp-platform --api-url https://api.agentnode.net",
      "Tools: agentnode_search, agentnode_resolve, agentnode_explain, agentnode_capabilities",
      'Config: Add to claude_desktop_config.json under "mcpServers"',
    ],
  },
  {
    title: "GitHub Action",
    description: "Automate pack publishing from CI/CD with agentnode/publish@v1.",
    sections: [
      "Add agentnode.yaml to your repository root",
      "Set AGENTNODE_API_KEY as a repository secret",
      "uses: agentnode/publish@v1 with api-key: ${{ secrets.AGENTNODE_API_KEY }}",
      "Trigger on release or workflow_dispatch",
      "Supports dry-run mode for validation only",
    ],
  },
  {
    title: "Capability Taxonomy",
    description: "97 standardized capability IDs across 14 categories for semantic resolution.",
    sections: [
      "Document Processing: pdf_extraction, document_summary, ocr_reading, ...",
      "Web & Browsing: web_search, webpage_extraction, browser_automation, ...",
      "Communication: email_sending, slack_messaging, discord_messaging, ...",
      "Data Analysis: csv_analysis, data_visualization, database_query, ...",
      "Developer Tools: code_execution, code_linting, test_generation, ...",
      "API: GET /v1/capabilities — list all capability IDs",
    ],
  },
];

export default function DocsPage() {
  return (
    <div className="mx-auto max-w-4xl px-6 py-12">
      <h1 className="mb-2 text-3xl font-bold text-foreground">Documentation</h1>
      <p className="mb-10 text-muted">
        Everything you need to discover, install, and publish AI agent capabilities.
      </p>

      <div className="space-y-8">
        {docs.map((doc) => (
          <section
            key={doc.title}
            className="rounded-lg border border-border bg-card p-6"
          >
            <h2 className="mb-2 text-xl font-semibold text-foreground">{doc.title}</h2>
            <p className="mb-4 text-sm text-muted">{doc.description}</p>

            {doc.sections && (
              <ul className="space-y-1.5">
                {doc.sections.map((s, i) => (
                  <li key={i} className="text-sm text-foreground/80">
                    <span className="mr-2 text-muted">&#8226;</span>
                    <code className="rounded bg-background px-1.5 py-0.5 text-xs font-mono">
                      {s}
                    </code>
                  </li>
                ))}
              </ul>
            )}

            {doc.commands && (
              <div className="space-y-1">
                {doc.commands.map((c) => (
                  <div key={c.cmd} className="flex items-start gap-3 text-sm">
                    <code className="shrink-0 rounded bg-background px-2 py-0.5 text-xs font-mono text-primary">
                      {c.cmd}
                    </code>
                    <span className="text-muted">{c.desc}</span>
                  </div>
                ))}
              </div>
            )}
          </section>
        ))}
      </div>
    </div>
  );
}
