"use client";

import { useState, useEffect } from "react";
import Link from "next/link";

/* ------------------------------------------------------------------ */
/*  Sidebar navigation sections                                        */
/* ------------------------------------------------------------------ */

const sections = [
  { id: "quick-start", label: "Quick Start" },
  { id: "runtime-quickstart", label: "Runtime QuickStart" },
  { id: "installation", label: "Installation" },
  { id: "search-discovery", label: "Searching & Discovery" },
  { id: "resolution-engine", label: "Resolution Engine" },
  { id: "installing-packs", label: "Installing Packs" },
  { id: "publishing-guide", label: "Publishing Guide" },
  { id: "anp-manifest", label: "ANP Manifest Reference" },
  { id: "cli-reference", label: "CLI Reference" },
  { id: "llm-runtime", label: "LLM Runtime" },
  { id: "python-sdk", label: "Python SDK" },
  { id: "rest-api", label: "REST API" },
  { id: "mcp-integration", label: "MCP Integration" },
  { id: "github-action", label: "GitHub Action" },
  { id: "verification", label: "Package Verification" },
  { id: "trust-security", label: "Trust & Security" },
  { id: "import-tools", label: "Import Tools" },
];

/* ------------------------------------------------------------------ */
/*  Code block component                                               */
/* ------------------------------------------------------------------ */

function CodeBlock({
  title,
  children,
  language,
}: {
  title: string;
  children: string;
  language?: string;
}) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-[#0d1117]">
      <div className="flex items-center gap-2 border-b border-border/50 px-4 py-2">
        <div className="h-3 w-3 rounded-full bg-red-500/60" />
        <div className="h-3 w-3 rounded-full bg-yellow-500/60" />
        <div className="h-3 w-3 rounded-full bg-green-500/60" />
        <span className="ml-2 font-mono text-xs text-muted">{title}</span>
        {language && (
          <span className="ml-auto font-mono text-xs text-muted/50">
            {language}
          </span>
        )}
      </div>
      <pre className="overflow-x-auto p-4 font-mono text-sm leading-relaxed text-gray-300">
        <code>{children}</code>
      </pre>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Inline code helper                                                 */
/* ------------------------------------------------------------------ */

function C({ children }: { children: React.ReactNode }) {
  return (
    <code className="rounded bg-background px-1.5 py-0.5 font-mono text-xs text-primary">
      {children}
    </code>
  );
}

/* ------------------------------------------------------------------ */
/*  Section heading                                                    */
/* ------------------------------------------------------------------ */

function SectionHeading({
  id,
  children,
}: {
  id: string;
  children: React.ReactNode;
}) {
  return (
    <h2
      id={id}
      className="mb-6 scroll-mt-24 border-b border-border pb-4 text-2xl font-bold text-foreground"
    >
      {children}
    </h2>
  );
}

function SubHeading({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="mb-3 mt-8 text-lg font-semibold text-foreground">
      {children}
    </h3>
  );
}

/* ------------------------------------------------------------------ */
/*  Table component for structured data                                */
/* ------------------------------------------------------------------ */

function DocTable({
  headers,
  rows,
}: {
  headers: string[];
  rows: string[][];
}) {
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border bg-card">
            {headers.map((h) => (
              <th
                key={h}
                className="px-4 py-3 text-left font-semibold text-foreground"
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr
              key={i}
              className={i < rows.length - 1 ? "border-b border-border/50" : ""}
            >
              {row.map((cell, j) => (
                <td
                  key={j}
                  className={`px-4 py-3 ${
                    j === 0 ? "font-mono text-primary text-xs" : "text-muted"
                  }`}
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Page component                                                     */
/* ------------------------------------------------------------------ */

export default function DocsPage() {
  const [activeSection, setActiveSection] = useState("quick-start");

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setActiveSection(entry.target.id);
          }
        }
      },
      { rootMargin: "-80px 0px -70% 0px", threshold: 0 }
    );

    for (const section of sections) {
      const el = document.getElementById(section.id);
      if (el) observer.observe(el);
    }

    return () => observer.disconnect();
  }, []);

  return (
    <div className="mx-auto max-w-6xl px-6 py-12">
      {/* Page header */}
      <div className="mb-10">
        <h1 className="mb-2 text-3xl font-bold text-foreground">
          Documentation
        </h1>
        <p className="text-muted">
          The complete reference for discovering, installing, publishing, and
          integrating AI agent capabilities with AgentNode.
        </p>
      </div>

      {/* Layout: sidebar + content */}
      <div className="flex gap-10">
        {/* ── Sidebar ── */}
        <nav className="hidden w-56 shrink-0 lg:block">
          <div className="sticky top-24">
            <p className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted">
              On this page
            </p>
            <ul className="space-y-1">
              {sections.map((s) => (
                <li key={s.id}>
                  <a
                    href={`#${s.id}`}
                    className={`block rounded px-3 py-1.5 text-sm transition-colors ${
                      activeSection === s.id
                        ? "bg-primary/10 font-medium text-primary"
                        : "text-muted hover:text-foreground"
                    }`}
                  >
                    {s.label}
                  </a>
                </li>
              ))}
            </ul>
            <div className="mt-6 border-t border-border pt-4">
              <Link
                href="/search"
                className="block text-xs text-muted transition-colors hover:text-primary"
              >
                Browse packages
              </Link>
              <Link
                href="/capabilities"
                className="mt-2 block text-xs text-muted transition-colors hover:text-primary"
              >
                Capability taxonomy
              </Link>
              <a
                href="https://github.com/agentnode-ai/agentnode"
                target="_blank"
                rel="noopener noreferrer"
                className="mt-2 block text-xs text-muted transition-colors hover:text-primary"
              >
                GitHub repository
              </a>
            </div>
          </div>
        </nav>

        {/* ── Main content ── */}
        <main className="min-w-0 flex-1 space-y-16">
          {/* ============================================================ */}
          {/*  QUICK START                                                  */}
          {/* ============================================================ */}
          <section>
            <SectionHeading id="quick-start">Quick Start</SectionHeading>
            <p className="mb-4 text-sm leading-relaxed text-muted">
              Go from zero to using your first AI agent capability in under five
              minutes. This walkthrough installs the CLI, searches the registry,
              installs a pack, and uses it in Python code.
            </p>

            <SubHeading>1. Install the CLI</SubHeading>
            <CodeBlock title="terminal">{`$ npm install -g agentnode-cli

added 1 package in 2.1s`}</CodeBlock>

            <SubHeading>2. Authenticate</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Log in to connect the CLI to your AgentNode account. If you do not
              have an account yet, register at{" "}
              <Link href="/auth/register" className="text-primary hover:underline">
                agentnode.net/auth/register
              </Link>
              .
            </p>
            <CodeBlock title="terminal">{`$ agentnode login
? Email: developer@example.com
? Password: ********
? 2FA Code: 123456

Authenticated as developer@example.com
API key stored in ~/.agentnode/credentials`}</CodeBlock>

            <SubHeading>3. Search for a capability</SubHeading>
            <CodeBlock title="terminal">{`$ agentnode search "pdf extraction"

Results for "pdf extraction":

  pdf-reader-pack          v1.2.0  trusted   Extract text, tables, and metadata from PDFs
  pdf-extractor-pack       v1.0.0  verified  High-fidelity PDF text extraction
  ocr-reader-pack          v1.1.0  trusted   OCR-based document reading including PDFs

3 results found`}</CodeBlock>

            <SubHeading>4. Install a pack</SubHeading>
            <CodeBlock title="terminal">{`$ agentnode install pdf-reader-pack

Installing pdf-reader-pack@1.2.0...
  Downloading package       done
  Verifying hash (SHA-256)  done
  Installing dependencies   done
  Writing lockfile           done

Installed pdf-reader-pack@1.2.0`}</CodeBlock>

            <SubHeading>5. Run it in your code</SubHeading>
            <CodeBlock title="agent.py" language="python">{`from agentnode_sdk import run_tool

# v0.3: Run with trust-aware isolation (auto = safe default)
result = run_tool("pdf-reader-pack", file_path="quarterly-report.pdf")
print(result.result["text"])
print(result.mode_used)  # "direct" for trusted, "subprocess" for others

# Multi-tool packs: specify the tool name
result = run_tool("csv-analyzer-pack", tool_name="describe", file_path="data.csv")`}</CodeBlock>

            <div className="mt-6 rounded-lg border border-primary/20 bg-primary/5 p-4">
              <p className="text-sm font-medium text-foreground">
                That is it. You searched the registry, installed a
                trust-verified pack, and used it with a single function call.
                Read on for the full reference.
              </p>
            </div>
          </section>

          {/* ============================================================ */}
          {/*  RUNTIME QUICKSTART                                            */}
          {/* ============================================================ */}
          <section>
            <SectionHeading id="runtime-quickstart">
              Runtime QuickStart
            </SectionHeading>
            <p className="mb-4 text-sm leading-relaxed text-muted">
              Build agents that discover and install capabilities at runtime —
              no hardcoded dependencies. Five lines from{" "}
              <C>pip install</C> to a working tool.
            </p>

            <SubHeading>Install the SDK</SubHeading>
            <CodeBlock title="terminal">{`$ pip install agentnode-sdk`}</CodeBlock>

            <SubHeading>The 5-line agent pattern</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Describe what your agent needs. AgentNode resolves it to the
              best-scored, trust-verified package — downloads, verifies, and
              installs it locally. Then run it with automatic isolation.
            </p>
            <CodeBlock title="agent.py" language="python">{`from agentnode_sdk import AgentNodeClient, run_tool

client = AgentNodeClient(api_key="ank_live_...")

# Resolve capability → install best match (trust-verified)
client.resolve_and_install(["pdf_extraction"])

# Run with trust-aware isolation (auto = safe default)
result = run_tool("pdf-reader-pack", file_path="report.pdf")
print(result.result["text"])`}</CodeBlock>

            <div className="mt-4 rounded-lg border border-primary/20 bg-primary/5 p-4">
              <p className="text-sm font-medium text-foreground">
                That is the complete runtime flow.{" "}
                <C>resolve_and_install()</C> handles resolution, trust
                verification, download, hash check, extraction, dependency
                install, and lockfile update.{" "}
                <C>run_tool()</C> then executes with automatic isolation —
                trusted tools run direct, others in a subprocess.
              </p>
            </div>

            <SubHeading>The smart_run() pattern (v0.4.0)</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Even simpler: wrap your logic and let AgentNode detect, install,
              and retry automatically when a capability is missing.
            </p>
            <CodeBlock title="smart_agent.py" language="python">{`from agentnode_sdk import AgentNodeClient

client = AgentNodeClient(api_key="ank_live_...")

# If process_pdf fails because pdfplumber is missing,
# AgentNode detects the gap, installs a PDF skill, and retries
result = client.smart_run(
    lambda: process_pdf("report.pdf"),
    auto_upgrade_policy="safe",  # only verified+ skills
)

print(result.success)        # True
print(result.upgraded)       # True (skill was installed)
print(result.installed_slug) # "pdf-reader-pack"`}</CodeBlock>

            <SubHeading>Step-by-step for more control</SubHeading>
            <p className="mb-3 text-sm text-muted">
              When you need to inspect candidates, check policies, or control
              trust requirements before installing:
            </p>
            <CodeBlock title="agent_detailed.py" language="python">{`from agentnode_sdk import AgentNodeClient, run_tool

client = AgentNodeClient(api_key="ank_live_...")

# 1. Resolve: find the best package for a capability
result = client.resolve(capabilities=["pdf_extraction"])
best = result.results[0]
print(f"Best match: {best.slug} v{best.version}")
print(f"  Trust: {best.trust_level}  Score: {best.score}")

# 2. Pre-flight check (optional): verify trust + permissions
check = client.can_install(best.slug, require_trusted=True)
if not check.allowed:
    print(f"Blocked: {check.reason}")
    exit(1)

# 3. Install locally (download → verify hash → extract → pip install → lockfile)
installed = client.install(best.slug)
print(installed.message)  # "Installed pdf-reader-pack@1.2.0"

# 4. Run with isolation (auto-mode routes by trust level)
data = run_tool(best.slug, file_path="report.pdf")
print(data.result["text"])
print(f"Ran in {data.mode_used} mode ({data.duration_ms}ms)")`}</CodeBlock>

            <SubHeading>What happens under the hood</SubHeading>
            <DocTable
              headers={["Step", "What it does"]}
              rows={[
                ["detect_gap()", "Analyzes error to identify missing capability — 3 layers: ImportError (high), keywords (medium), context (low)"],
                ["resolve()", "Scores packages by capability match (40%), framework fit (20%), runtime compatibility (15%), trust level (15%), permissions (10%)"],
                ["can_install()", "Pre-flight check — verifies trust level, permissions, deprecation status without downloading anything"],
                ["install()", "Downloads artifact, verifies SHA-256 hash, extracts to ~/.agentnode/packages/, runs pip install for dependencies, writes agentnode.lock with trust metadata"],
                ["run_tool()", "Reads trust level from lockfile → routes to direct (in-process) or subprocess (isolated) execution → returns RunToolResult with output, timing, and mode used"],
                ["smart_run()", "Full loop: run → detect gap → resolve → install → retry once. Returns SmartRunResult with complete transparency"],
              ]}
            />

            <SubHeading>Lockfile</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Every install writes to <C>agentnode.lock</C> in your project
              root. This pins exact versions and hashes for reproducible builds
              across environments.
            </p>
            <CodeBlock title="agentnode.lock" language="json">{`{
  "pdf-reader-pack": {
    "version": "1.2.0",
    "hash": "sha256:a1b2c3d4...",
    "entrypoint": "pdf_reader.extract:run",
    "tools": ["extract_pdf", "extract_tables"],
    "installed_at": "2026-03-24T10:30:00Z"
  }
}`}</CodeBlock>
          </section>

          {/* ============================================================ */}
          {/*  LLM RUNTIME                                                  */}
          {/* ============================================================ */}
          <section>
            <SectionHeading id="llm-runtime">LLM Runtime</SectionHeading>
            <p className="mb-4 text-sm leading-relaxed text-muted">
              <C>AgentNodeRuntime</C> connects any OpenAI, Anthropic, or Gemini agent
              to AgentNode with zero configuration. It registers 5 meta-tools,
              injects a system prompt, and runs the tool loop automatically.
              The LLM discovers, installs, and runs capabilities on its own.
            </p>

            <SubHeading>Quick start</SubHeading>
            <CodeBlock title="terminal">{`$ pip install agentnode-sdk`}</CodeBlock>

            <SubHeading>OpenAI</SubHeading>
            <CodeBlock title="openai_agent.py" language="python">{`from openai import OpenAI
from agentnode_sdk import AgentNodeRuntime

runtime = AgentNodeRuntime()
client = OpenAI()

result = runtime.run(
    provider="openai",
    client=client,
    model="gpt-4o",
    messages=[{"role": "user", "content": "Count the words in 'Hello world'"}],
)
print(result.content)`}</CodeBlock>

            <SubHeading>Anthropic</SubHeading>
            <CodeBlock title="anthropic_agent.py" language="python">{`from anthropic import Anthropic
from agentnode_sdk import AgentNodeRuntime

runtime = AgentNodeRuntime()
client = Anthropic()

result = runtime.run(
    provider="anthropic",
    client=client,
    model="claude-sonnet-4-6",
    messages=[{"role": "user", "content": "Search for PDF tools on AgentNode"}],
)`}</CodeBlock>

            <SubHeading>Gemini</SubHeading>
            <CodeBlock title="gemini_agent.py" language="python">{`from google import genai
from agentnode_sdk import AgentNodeRuntime

runtime = AgentNodeRuntime()
client = genai.Client()

result = runtime.run(
    provider="gemini",
    client=client,
    model="gemini-2.5-flash",
    messages=[{"role": "user", "content": "What AgentNode tools are available?"}],
)`}</CodeBlock>

            <SubHeading>OpenRouter / any OpenAI-compatible provider</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Use Mistral, DeepSeek, Qwen, Llama, and more via OpenRouter or any
              OpenAI-compatible endpoint:
            </p>
            <CodeBlock title="openrouter_agent.py" language="python">{`from openai import OpenAI
from agentnode_sdk import AgentNodeRuntime

runtime = AgentNodeRuntime()
client = OpenAI(
    api_key="sk-or-...",
    base_url="https://openrouter.ai/api/v1",
)

result = runtime.run(
    provider="openai",
    client=client,
    model="mistralai/mistral-large",
    messages=[{"role": "user", "content": "Find and install a PDF reader tool"}],
)`}</CodeBlock>

            <SubHeading>Manual tool calling</SubHeading>
            <p className="mb-3 text-sm text-muted">
              For any provider that supports tool calling, get tool definitions
              and dispatch calls manually with <C>handle()</C>:
            </p>
            <CodeBlock title="manual.py" language="python">{`runtime = AgentNodeRuntime()

# Get tool definitions in your provider's format
tools = runtime.as_openai_tools()    # OpenAI function-calling format
tools = runtime.as_anthropic_tools() # Anthropic format
tools = runtime.as_gemini_tools()    # Gemini format
tools = runtime.as_generic_tools()   # Generic / baseline format

# When the LLM makes a tool call, dispatch it:
result = runtime.handle("agentnode_search", {"query": "pdf extraction"})
# → {"success": true, "result": {"total": 5, "results": [...]}}`}</CodeBlock>

            <SubHeading>Constructor</SubHeading>
            <CodeBlock title="init.py" language="python">{`AgentNodeRuntime(
    client=None,                     # Optional AgentNodeClient
    api_key=None,                    # Optional API key
    minimum_trust_level="verified",  # "verified" | "trusted" | "curated"
)`}</CodeBlock>

            <SubHeading>5 meta-tools</SubHeading>
            <p className="mb-3 text-sm text-muted">
              These tools are automatically registered when you create a Runtime.
              The LLM calls them as needed during the tool loop.
            </p>
            <DocTable
              headers={["Tool", "Description"]}
              rows={[
                ["agentnode_capabilities", "List installed packages (local, no API call)"],
                ["agentnode_search", "Search the registry (max 5 results)"],
                ["agentnode_install", "Install a package by slug"],
                ["agentnode_run", "Execute an installed tool"],
                ["agentnode_acquire", "Search + install in one step"],
              ]}
            />

            <SubHeading>API reference</SubHeading>
            <DocTable
              headers={["Method", "Description"]}
              rows={[
                ["tool_specs()", "Internal typed tool definitions (list[ToolSpec])"],
                ["as_openai_tools()", "Tools in OpenAI function-calling format"],
                ["as_anthropic_tools()", "Tools in Anthropic format"],
                ["as_gemini_tools()", "Tools in Google Gemini format"],
                ["as_generic_tools()", "Tools in generic/baseline format"],
                ["system_prompt()", "AgentNode system prompt block (append to yours)"],
                ["tool_bundle()", "Combined {\"tools\": [...], \"system_prompt\": \"...\"}"],
                ["handle(name, args)", "Dispatch a tool call. Returns dict. Never throws."],
                ["run(provider, client, ...)", "Auto-loop with tool dispatch. Never throws."],
              ]}
            />

            <SubHeading>run() parameters</SubHeading>
            <DocTable
              headers={["Parameter", "Type", "Default", "Description"]}
              rows={[
                ["provider", "str", "—", "\"openai\", \"anthropic\", or \"gemini\""],
                ["client", "Any", "—", "Provider SDK client instance"],
                ["messages", "list[dict]", "—", "Conversation messages"],
                ["model", "str", "\"\"", "Model name (e.g. \"gpt-4o\")"],
                ["max_tool_rounds", "int", "8", "Max tool call rounds before stopping"],
                ["inject_system_prompt", "bool", "True", "Append AgentNode prompt to system message"],
              ]}
            />

            <SubHeading>Trust levels</SubHeading>
            <p className="mb-3 text-sm text-muted">
              <C>minimum_trust_level</C> controls which packages can be
              installed and run through the Runtime. Higher levels are stricter:
            </p>
            <DocTable
              headers={["Level", "Accepts"]}
              rows={[
                ["\"verified\"", "verified, trusted, curated"],
                ["\"trusted\"", "trusted, curated"],
                ["\"curated\"", "curated only"],
              ]}
            />

            <SubHeading>Three surfaces</SubHeading>
            <div className="mb-4 overflow-hidden rounded-lg border border-border">
              <table className="w-full text-sm">
                <tbody>
                  <tr className="border-b border-border">
                    <td className="px-4 py-3 font-mono text-xs text-primary">CLI</td>
                    <td className="px-4 py-3 text-muted">For humans &mdash; search, install, publish</td>
                  </tr>
                  <tr className="border-b border-border">
                    <td className="px-4 py-3 font-mono text-xs text-primary">SDK / Client</td>
                    <td className="px-4 py-3 text-muted">For programmatic access &mdash; search, resolve, install, run</td>
                  </tr>
                  <tr>
                    <td className="px-4 py-3 font-mono text-xs text-primary">Runtime</td>
                    <td className="px-4 py-3 text-muted">For LLM agents &mdash; tool registration, dispatch, auto-loop</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          {/* ============================================================ */}
          {/*  INSTALLATION                                                 */}
          {/* ============================================================ */}
          <section>
            <SectionHeading id="installation">Installation</SectionHeading>

            <SubHeading>System requirements</SubHeading>
            <DocTable
              headers={["Requirement", "Details"]}
              rows={[
                ["Node.js", "v18 or later (for the CLI)"],
                ["Python", "v3.10 or later (for running packs and using the SDK)"],
                ["npm", "v9 or later"],
                ["OS", "macOS, Linux, Windows (WSL recommended on Windows)"],
              ]}
            />

            <SubHeading>Choose your install</SubHeading>
            <DocTable
              headers={["Use case", "Package", "Install"]}
              rows={[
                ["Build agents & apps in Python", "agentnode-sdk", "pip install agentnode-sdk"],
                ["Install & publish from terminal", "agentnode-cli", "npm install -g agentnode-cli"],
                ["Use with LangChain / LangGraph", "agentnode-langchain", "pip install agentnode-langchain"],
                ["Use with MCP (Claude, Cursor)", "agentnode-mcp", "pip install agentnode-mcp"],
              ]}
            />

            <SubHeading>Install the CLI</SubHeading>
            <p className="mb-3 text-sm text-muted">
              The AgentNode CLI is distributed as a global npm package. It
              provides all commands for searching, installing, publishing, and
              managing packs.
            </p>
            <CodeBlock title="terminal">{`$ npm install -g agentnode-cli`}</CodeBlock>

            <p className="mt-4 mb-3 text-sm text-muted">
              Verify the installation:
            </p>
            <CodeBlock title="terminal">{`$ agentnode --version
agentnode/0.3.0

$ agentnode --help
Usage: agentnode <command> [options]

Commands:
  login             Authenticate with the registry
  search            Search for packages
  resolve           Resolve capabilities to packages
  install           Install a package
  update            Update a package to the latest version
  rollback          Roll back to a specific version
  info              Show package details
  explain           Explain capabilities, permissions, and use cases
  audit             Show trust and security information
  doctor            Analyze setup and suggest improvements
  list              Show installed packages
  publish           Publish a package
  validate          Validate a manifest
  report            Generate a security report
  recommend         Get recommendations for missing capabilities
  resolve-upgrade   Find upgrade packages for capability gaps
  policy-check      Check policy constraints
  api-keys          Manage API keys
  import            Import tools from other frameworks`}</CodeBlock>

            <SubHeading>Authentication</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Most read operations (search, info, explain) work without
              authentication. Publishing, installing, and resolution require an
              authenticated session. Credentials are stored locally in{" "}
              <C>~/.agentnode/credentials</C>.
            </p>
            <CodeBlock title="terminal">{`$ agentnode login
? Email: developer@example.com
? Password: ********
? 2FA Code: 123456

Authenticated as developer@example.com
API key stored in ~/.agentnode/credentials`}</CodeBlock>

            <p className="mt-4 text-sm text-muted">
              For CI/CD and automated workflows, use an API key instead of
              interactive login. Set the <C>AGENTNODE_API_KEY</C> environment
              variable:
            </p>
            <CodeBlock title="terminal">{`$ export AGENTNODE_API_KEY=ank_live_abc123def456`}</CodeBlock>
          </section>

          {/* ============================================================ */}
          {/*  SEARCHING & DISCOVERY                                        */}
          {/* ============================================================ */}
          <section>
            <SectionHeading id="search-discovery">
              Searching & Discovery
            </SectionHeading>
            <p className="mb-4 text-sm leading-relaxed text-muted">
              AgentNode search is designed for AI agent developers. Instead of
              keyword matching against package names, search queries are matched
              against capability descriptions, tool declarations, tags, and
              metadata. Results are ranked by relevance, trust level, and
              framework compatibility.
            </p>

            <SubHeading>Basic search</SubHeading>
            <CodeBlock title="terminal">{`$ agentnode search "web scraping"

Results for "web scraping":

  webpage-extractor-pack   v1.0.0  trusted   Extract clean text and metadata from any webpage
  browser-automation-pack  v1.1.0  verified  Automate browser interactions for data extraction
  web-search-pack          v1.0.0  trusted   Search the web and retrieve structured results

3 results found`}</CodeBlock>

            <SubHeading>Filtering results</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Narrow results by framework, trust level, runtime, or capability
              ID.
            </p>
            <CodeBlock title="terminal">{`# Only show packs compatible with LangChain
$ agentnode search "pdf" --framework langchain

# Only show trusted or curated packs
$ agentnode search "email" --trust trusted

# Filter by runtime
$ agentnode search "data analysis" --runtime python

# Filter by specific capability ID
$ agentnode search --capability pdf_extraction

# Combine filters
$ agentnode search "document processing" --framework crewai --trust verified`}</CodeBlock>

            <SubHeading>Search flags</SubHeading>
            <DocTable
              headers={["Flag", "Type", "Description"]}
              rows={[
                ["--framework", "string", "Filter by framework compatibility: langchain, crewai, generic"],
                ["--trust", "string", "Minimum trust level: unverified, verified, trusted, curated"],
                ["--runtime", "string", "Filter by runtime: python"],
                ["--capability", "string", "Filter by exact capability ID from the taxonomy"],
                ["--limit", "number", "Maximum number of results to return (default: 20)"],
                ["--json", "boolean", "Output results as JSON for programmatic consumption"],
                ["--publisher", "string", "Filter by publisher namespace"],
              ]}
            />

            <SubHeading>Understanding results</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Each result shows the package slug, current version, trust level,
              and a summary. Use <C>agentnode info</C> or{" "}
              <C>agentnode explain</C> for detailed information about a specific
              pack before installing.
            </p>
            <CodeBlock title="terminal">{`$ agentnode explain pdf-reader-pack

pdf-reader-pack@1.2.0
  Publisher:    agentnode-official
  Trust:        trusted
  Runtime:      python >=3.10
  Frameworks:   langchain, crewai, generic

  Capabilities:
    - pdf_extraction: Extract text, tables, and metadata from PDF documents

  Permissions:
    Network:        none
    Filesystem:     read (reads input PDF files)
    Code Execution: none
    Data Access:    input_only

  Use Cases:
    - Extract text from PDF reports for summarization
    - Parse tables from financial PDFs
    - Read metadata and page counts from document archives

  Install: agentnode install pdf-reader-pack`}</CodeBlock>
          </section>

          {/* ============================================================ */}
          {/*  RESOLUTION ENGINE                                            */}
          {/* ============================================================ */}
          <section>
            <SectionHeading id="resolution-engine">
              Resolution Engine
            </SectionHeading>
            <p className="mb-4 text-sm leading-relaxed text-muted">
              Resolution is different from search. While search finds packages
              matching a text query, resolution takes a list of capability IDs
              your agent needs and returns the optimal packages to fill those
              gaps. The resolution engine scores candidates across multiple
              dimensions and respects policy constraints.
            </p>

            <SubHeading>How resolution scoring works</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Each candidate package receives a composite score from 0 to 1
              based on five weighted factors:
            </p>
            <DocTable
              headers={["Factor", "Weight", "Description"]}
              rows={[
                ["Capability match", "40%", "How well the pack's declared capabilities match your requested capability IDs"],
                ["Framework compatibility", "20%", "Whether the pack supports your agent's framework (LangChain, CrewAI, etc.)"],
                ["Runtime fit", "15%", "Whether the pack's runtime and version constraints match your environment"],
                ["Trust level", "15%", "Higher trust levels (curated > trusted > verified > unverified) score higher"],
                ["Permissions safety", "10%", "Packs requesting fewer permissions score higher (principle of least privilege)"],
              ]}
            />

            <SubHeading>CLI resolution</SubHeading>
            <CodeBlock title="terminal">{`$ agentnode resolve pdf_extraction web_search --framework langchain

Resolving 2 capabilities for langchain...

  pdf_extraction:
    1. pdf-reader-pack       v1.2.0  score: 0.94  trusted
    2. pdf-extractor-pack    v1.0.0  score: 0.81  verified

  web_search:
    1. web-search-pack       v1.0.0  score: 0.92  trusted
    2. browser-automation-pack v1.1.0 score: 0.73  verified

Recommended: agentnode install pdf-reader-pack web-search-pack`}</CodeBlock>

            <SubHeading>SDK resolution</SubHeading>
            <CodeBlock title="resolve.py" language="python">{`from agentnode_sdk import AgentNodeClient

client = AgentNodeClient(api_key="ank_live_abc123def456")

# Resolve multiple capability gaps at once
result = client.resolve(
    capabilities=["pdf_extraction", "web_search", "email_sending"],
    framework="langchain",
    limit=5,
)

for match in result.results:
    print(f"{match.matched_capabilities}: {match.slug} (score: {match.score})")
    print(f"  Trust: {match.trust_level}")
    print()`}</CodeBlock>

            <SubHeading>Policy constraints</SubHeading>
            <p className="mb-3 text-sm text-muted">
              The resolution engine accepts policy constraints that
              automatically filter out non-compliant packages. This is critical
              for production deployments where agents must operate within strict
              security boundaries.
            </p>
            <CodeBlock title="terminal">{`# Only resolve packages that are trusted or curated
$ agentnode resolve pdf_extraction --trust trusted

# Only resolve packages with no network access
$ agentnode resolve pdf_extraction --policy-no-network

# Check if a specific package meets your policy
$ agentnode policy-check pdf-reader-pack --trust trusted --no-code-execution
Policy check for pdf-reader-pack@1.2.0:
  Trust level:     trusted     PASS
  Network:         none        PASS
  Filesystem:      read        PASS
  Code execution:  none        PASS
  Data access:     input_only  PASS

Package passes all policy constraints.`}</CodeBlock>
          </section>

          {/* ============================================================ */}
          {/*  INSTALLING PACKS                                             */}
          {/* ============================================================ */}
          <section>
            <SectionHeading id="installing-packs">
              Installing Packs
            </SectionHeading>

            <SubHeading>Basic installation</SubHeading>
            <CodeBlock title="terminal">{`$ agentnode install pdf-reader-pack

Installing pdf-reader-pack@1.2.0...
  Downloading package       done
  Verifying hash (SHA-256)  done
  Installing dependencies   done
  Writing lockfile           done

Installed pdf-reader-pack@1.2.0`}</CodeBlock>

            <SubHeading>Install a specific version</SubHeading>
            <CodeBlock title="terminal">{`$ agentnode install pdf-reader-pack@1.1.0`}</CodeBlock>

            <SubHeading>Install multiple packs</SubHeading>
            <CodeBlock title="terminal">{`$ agentnode install pdf-reader-pack web-search-pack email-drafter-pack`}</CodeBlock>

            <SubHeading>What happens during installation</SubHeading>
            <p className="mb-3 text-sm text-muted">
              When you run <C>agentnode install</C>, the following steps execute
              in order:
            </p>
            <ol className="mb-4 list-inside list-decimal space-y-2 text-sm text-muted">
              <li>
                <span className="text-foreground/80 font-medium">
                  Version resolution
                </span>{" "}
                -- the registry resolves the latest compatible version (or the
                pinned version you specified).
              </li>
              <li>
                <span className="text-foreground/80 font-medium">
                  Download
                </span>{" "}
                -- the package archive is downloaded from the registry.
              </li>
              <li>
                <span className="text-foreground/80 font-medium">
                  Hash verification
                </span>{" "}
                -- the downloaded archive is verified against the SHA-256 hash
                stored in the registry. If the hash does not match, the install
                is aborted.
              </li>
              <li>
                <span className="text-foreground/80 font-medium">
                  Dependency installation
                </span>{" "}
                -- Python dependencies declared in the pack are installed via
                pip.
              </li>
              <li>
                <span className="text-foreground/80 font-medium">
                  Lockfile update
                </span>{" "}
                -- the pack version and hash are recorded in{" "}
                <C>agentnode.lock</C> for reproducible installations.
              </li>
            </ol>

            <SubHeading>The lockfile</SubHeading>
            <p className="mb-3 text-sm text-muted">
              The <C>agentnode.lock</C> file records exactly which versions and
              hashes are installed. Commit this file to version control for
              reproducible builds across environments.
            </p>
            <CodeBlock title="agentnode.lock" language="yaml">{`# Auto-generated by agentnode. Do not edit manually.
lockfile_version: 2

packages:
  csv-analyzer-pack:
    version: "1.1.0"
    hash: "sha256:a1b2c3d4e5f6..."
    installed_at: "2025-01-15T10:30:00Z"
    entrypoint: "csv_analyzer_pack.tool"
    tools:
      - name: "describe"
        entrypoint: "csv_analyzer_pack.tool:describe"
        capability_id: "csv_analysis"
      - name: "filter"
        entrypoint: "csv_analyzer_pack.tool:filter_rows"
        capability_id: "data_cleaning"

  pdf-reader-pack:
    version: "1.2.0"
    hash: "sha256:f6e5d4c3b2a1..."
    installed_at: "2025-01-15T10:31:00Z"
    entrypoint: "pdf_reader_pack.tool"
    tools: []`}</CodeBlock>

            <SubHeading>Using packs in code</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Every pack is loaded through the SDK&apos;s <C>load_tool()</C> function.
              v0.2 packs support multiple tools with individual entrypoints.
              v0.1 packs work the same way with a single default entrypoint.
            </p>
            <CodeBlock title="agent.py" language="python">{`from agentnode_sdk.installer import load_tool

# v0.2 multi-tool pack — load specific tools by name
describe = load_tool("csv-analyzer-pack", tool_name="describe")
filter_rows = load_tool("csv-analyzer-pack", tool_name="filter")

result = describe({"file_path": "data.csv"})
filtered = filter_rows({"file_path": "data.csv", "column": "status", "value": "active"})

# v0.1 single-tool packs — no tool_name needed
extract = load_tool("pdf-reader-pack")
pdf_result = extract({"file_path": "report.pdf"})

search = load_tool("web-search-pack")
search_result = search({"query": "AgentNode", "max_results": 5})`}</CodeBlock>

            <SubHeading>Updating and rolling back</SubHeading>
            <CodeBlock title="terminal">{`# Update to latest version
$ agentnode update pdf-reader-pack
Updating pdf-reader-pack 1.2.0 -> 1.3.0... done

# Roll back to a specific version
$ agentnode rollback pdf-reader-pack@1.2.0
Rolling back pdf-reader-pack 1.3.0 -> 1.2.0... done

# List all installed packs
$ agentnode list
Installed packages:
  pdf-reader-pack    v1.2.0  trusted
  web-search-pack    v1.0.0  trusted`}</CodeBlock>
          </section>

          {/* ============================================================ */}
          {/*  PUBLISHING GUIDE                                             */}
          {/* ============================================================ */}
          <section>
            <SectionHeading id="publishing-guide">
              Publishing Guide
            </SectionHeading>
            <p className="mb-4 text-sm leading-relaxed text-muted">
              Publishing a pack to AgentNode makes your AI tool discoverable,
              installable, and verifiable by any agent developer. This guide
              walks through the full process from account creation to published
              pack.
            </p>

            <SubHeading>Step 1: Create your publisher account</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Sign up at{" "}
              <Link
                href="/auth/register"
                className="text-primary hover:underline"
              >
                agentnode.net/auth/register
              </Link>{" "}
              and enable two-factor authentication. Your publisher namespace
              (e.g., <C>your-org</C>) appears in every package you publish and
              cannot be changed later.
            </p>

            <SubHeading>Step 2: Structure your project</SubHeading>
            <p className="mb-3 text-sm text-muted">
              A minimal pack has three files: the manifest, a pyproject.toml for
              Python packaging, and the tool module with your tool functions.
            </p>
            <CodeBlock title="project structure">{`my-pack/
  agentnode.yaml          # ANP manifest (required)
  pyproject.toml          # Python package config (required)
  src/
    my_pack/
      __init__.py
      tool.py             # Tool functions (required)`}</CodeBlock>

            <SubHeading>Step 3: Write your agentnode.yaml manifest</SubHeading>
            <p className="mb-3 text-sm text-muted">
              The manifest is the source of truth for what your pack does, what
              it needs, and how it integrates. See the{" "}
              <a href="#anp-manifest" className="text-primary hover:underline">
                ANP Manifest Reference
              </a>{" "}
              below for every field.
            </p>
            <CodeBlock title="agentnode.yaml" language="yaml">{`manifest_version: "0.2"
package_id: "github-integration-pack"
package_type: "toolpack"
name: "GitHub Integration Pack"
publisher: "your-namespace"
version: "1.0.0"
summary: "Interact with GitHub repos, issues, and PRs."
description: "A comprehensive toolkit for GitHub automation including issue creation, PR review, repository management, and webhook handling."

runtime: "python"
entrypoint: "github_integration_pack.tool"
install_mode: "package"
hosting_type: "agentnode_hosted"

capabilities:
  tools:
    - name: "create_issue"
      capability_id: "github_integration"
      description: "Create a new GitHub issue"
      entrypoint: "github_integration_pack.tool:create_issue"
      input_schema:
        type: "object"
        properties:
          token:
            type: "string"
            description: "GitHub personal access token"
          repo:
            type: "string"
            description: "Repository in owner/repo format"
          title:
            type: "string"
          body:
            type: "string"
        required: ["token", "repo", "title"]
    - name: "list_repos"
      capability_id: "github_integration"
      description: "List repositories for authenticated user"
      entrypoint: "github_integration_pack.tool:list_repos"
      input_schema:
        type: "object"
        properties:
          token:
            type: "string"
        required: ["token"]

permissions:
  network:
    level: "unrestricted"
    justification: "Requires access to GitHub API"
  filesystem:
    level: "none"
  code_execution:
    level: "none"
  data_access:
    level: "input_only"

compatibility:
  frameworks: ["generic"]
  python: ">=3.10"

tags: ["github", "integration", "devtools", "automation"]`}</CodeBlock>

            <SubHeading>Step 4: Implement your tool functions</SubHeading>
            <CodeBlock title="src/github_integration_pack/tool.py" language="python">{`from agentnode_sdk.exceptions import AgentNodeToolError

def create_issue(inputs: dict) -> dict:
    """Create a new GitHub issue."""
    token = inputs["token"]
    repo = inputs["repo"]
    title = inputs["title"]
    body = inputs.get("body", "")

    # Your implementation here
    response = _github_api(token, f"/repos/{repo}/issues", {
        "title": title, "body": body
    })
    return {"issue_number": response["number"], "url": response["html_url"]}

def list_repos(inputs: dict) -> dict:
    """List repositories for authenticated user."""
    token = inputs["token"]
    repos = _github_api(token, "/user/repos")
    return {"repos": [{"name": r["name"], "url": r["html_url"]} for r in repos]}

# Optional: backward-compatible run() wrapper for v0.1 callers
# Not required for v0.2 — per-tool entrypoints (tool:create_issue, tool:list_repos) are used instead
def run(inputs: dict) -> dict:
    operation = inputs.get("operation", "list_repos")
    dispatch = {"create_issue": create_issue, "list_repos": list_repos}
    handler = dispatch.get(operation)
    if not handler:
        raise AgentNodeToolError(f"Unknown operation: {operation}", tool_name=operation)
    return handler(inputs)`}</CodeBlock>

            <SubHeading>Step 5: Validate</SubHeading>
            <CodeBlock title="terminal">{`$ agentnode validate .

Validating github-integration-pack...
  Manifest syntax       OK
  Capability IDs        OK (1 tool, 0 resources)
  Permissions           OK (network: unrestricted)
  Entrypoint            OK (github_integration_pack.tool)
  Compatibility         OK (3 frameworks)

Package is valid and ready to publish.`}</CodeBlock>

            <SubHeading>Step 6: Publish</SubHeading>
            <CodeBlock title="terminal">{`$ agentnode publish .

Publishing github-integration-pack@1.0.0...
  Uploading package       done
  Security scan           passed (no issues found)
  Signing package         done (Ed25519)
  Indexing capabilities   done

Published! https://agentnode.net/packages/github-integration-pack`}</CodeBlock>

            <div className="mt-6 rounded-lg border border-primary/20 bg-primary/5 p-4">
              <p className="text-sm text-muted">
                <span className="font-medium text-foreground">
                  Tip:
                </span>{" "}
                Use <C>agentnode publish --dry-run .</C> to test the full
                publishing pipeline without actually publishing. This runs
                validation, security scanning, and packaging but does not upload
                to the registry.
              </p>
            </div>
          </section>

          {/* ============================================================ */}
          {/*  ANP MANIFEST REFERENCE                                       */}
          {/* ============================================================ */}
          <section>
            <SectionHeading id="anp-manifest">
              ANP Manifest Reference
            </SectionHeading>
            <p className="mb-4 text-sm leading-relaxed text-muted">
              The <C>agentnode.yaml</C> manifest is the heart of every pack. It
              declares identity, capabilities, permissions, and compatibility
              in a single human-readable file.
            </p>

            <SubHeading>Identity fields</SubHeading>
            <DocTable
              headers={["Field", "Type", "Required", "Description"]}
              rows={[
                ["manifest_version", "string", "Yes", "ANP format version. \"0.1\" or \"0.2\". Use \"0.2\" for multi-tool packs with per-tool entrypoints."],
                ["package_id", "string", "Yes", "Unique identifier for the package. Must be lowercase, hyphenated. Example: \"pdf-reader-pack\""],
                ["package_type", "string", "Yes", "Package type. Currently \"toolpack\" is the primary type."],
                ["name", "string", "Yes", "Human-readable display name. Example: \"PDF Reader Pack\""],
                ["publisher", "string", "Yes", "Publisher namespace from your account. Example: \"agentnode-official\""],
                ["version", "string", "Yes", "Semantic version. Must follow semver: \"1.0.0\", \"2.1.3\""],
                ["summary", "string", "Yes", "One-line description (under 120 characters). Used in search results."],
                ["description", "string", "No", "Longer description with full details. Supports plain text."],
              ]}
            />

            <SubHeading>Runtime fields</SubHeading>
            <DocTable
              headers={["Field", "Type", "Required", "Description"]}
              rows={[
                ["runtime", "string", "Yes", "Execution runtime. Currently \"python\"."],
                ["entrypoint", "string", "Yes", "Package-level Python module path. Example: \"pdf_reader_pack.tool\". In v0.2, individual tools can have their own entrypoints."],
                ["install_mode", "string", "Yes", "How the pack is installed. Values: \"package\" (pip install), \"standalone\" (script)."],
                ["hosting_type", "string", "No", "Where the pack is hosted. Values: \"agentnode_hosted\" (default), \"self_hosted\", \"remote\"."],
              ]}
            />

            <SubHeading>Capabilities</SubHeading>
            <p className="mb-3 text-sm text-muted">
              The <C>capabilities</C> section declares what your pack can do.
              Each tool in the <C>tools</C> array maps to a function the pack
              exposes.
            </p>
            <DocTable
              headers={["Field", "Type", "Required", "Description"]}
              rows={[
                ["capabilities.tools", "array", "Yes", "Array of tool declarations."],
                ["tools[].name", "string", "Yes", "Tool name used in code. Example: \"extract_pdf\""],
                ["tools[].capability_id", "string", "Yes", "Standardized capability ID from the taxonomy. Example: \"pdf_extraction\""],
                ["tools[].entrypoint", "string", "v0.2", "Per-tool entrypoint in module.path:function format. Required for multi-tool v0.2 packs. Example: \"csv_analyzer_pack.tool:describe\""],
                ["tools[].description", "string", "Yes", "What this tool does, in plain language."],
                ["tools[].input_schema", "object", "No", "JSON Schema describing the tool's input parameters."],
                ["tools[].output_schema", "object", "No", "JSON Schema describing the tool's return value."],
              ]}
            />

            <SubHeading>Permissions</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Every pack must explicitly declare what system resources it
              accesses. This is not optional -- it is enforced at publish time
              and surfaced to users before installation.
            </p>
            <DocTable
              headers={["Field", "Values", "Description"]}
              rows={[
                ["permissions.network.level", "none | restricted | unrestricted", "Network access. \"none\" = no outbound calls. \"restricted\" = specific domains only. \"unrestricted\" = any network access."],
                ["permissions.network.justification", "string", "Why this permission level is needed (recommended for restricted/unrestricted)."],
                ["permissions.filesystem.level", "none | temp | read | write", "File system access. \"none\" = no FS access. \"temp\" = temp directory only. \"read\" = read files. \"write\" = read and write."],
                ["permissions.code_execution.level", "none | sandboxed | full", "Code execution. \"none\" = no code exec. \"sandboxed\" = restricted sandbox. \"full\" = unrestricted execution."],
                ["permissions.data_access.level", "input_only | output_only | bidirectional", "Data flow direction. \"input_only\" = reads input, does not send data out. \"bidirectional\" = sends and receives."],
              ]}
            />

            <SubHeading>Compatibility</SubHeading>
            <DocTable
              headers={["Field", "Type", "Required", "Description"]}
              rows={[
                ["compatibility.frameworks", "array", "No", "Auto-defaults to [\"generic\"]. ANP packages work across all frameworks automatically."],
                ["compatibility.python", "string", "No", "Python version constraint. Example: \">=3.10\""],
              ]}
            />

            <SubHeading>Tags</SubHeading>
            <p className="mb-3 text-sm text-muted">
              An array of lowercase string tags for search and categorization.
              Example: <C>{`["pdf", "extraction", "documents"]`}</C>. Tags
              are free-form but should describe the domain and use case.
            </p>
          </section>

          {/* ============================================================ */}
          {/*  CLI REFERENCE                                                */}
          {/* ============================================================ */}
          <section>
            <SectionHeading id="cli-reference">CLI Reference</SectionHeading>
            <p className="mb-6 text-sm text-muted">
              Complete reference for all 18 commands in the AgentNode CLI.
            </p>

            {/* login */}
            <div className="mb-8 rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode login
              </h4>
              <p className="mb-3 text-sm text-muted">
                Authenticate with the AgentNode registry. Stores credentials in{" "}
                <C>~/.agentnode/credentials</C>.
              </p>
              <CodeBlock title="terminal">{`$ agentnode login
? Email: developer@example.com
? Password: ********
? 2FA Code: 123456
Authenticated as developer@example.com`}</CodeBlock>
            </div>

            {/* search */}
            <div className="mb-8 rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode search &lt;query&gt;
              </h4>
              <p className="mb-3 text-sm text-muted">
                Search the registry for packages matching a text query.
              </p>
              <DocTable
                headers={["Flag", "Description"]}
                rows={[
                  ["--framework <name>", "Filter by framework (langchain, crewai, generic)"],
                  ["--trust <level>", "Minimum trust level (unverified, verified, trusted, curated)"],
                  ["--runtime <name>", "Filter by runtime (python)"],
                  ["--capability <id>", "Filter by capability ID"],
                  ["--publisher <slug>", "Filter by publisher namespace"],
                  ["--limit <n>", "Maximum results (default: 20)"],
                  ["--json", "Output as JSON"],
                ]}
              />
              <div className="mt-3">
                <CodeBlock title="terminal">{`$ agentnode search "email automation" --framework langchain --trust verified`}</CodeBlock>
              </div>
            </div>

            {/* resolve */}
            <div className="mb-8 rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode resolve &lt;capability_id...&gt;
              </h4>
              <p className="mb-3 text-sm text-muted">
                Resolve one or more capability IDs to ranked package
                recommendations using the scoring engine.
              </p>
              <DocTable
                headers={["Flag", "Description"]}
                rows={[
                  ["--framework <name>", "Preferred framework for scoring"],
                  ["--trust <level>", "Minimum trust level"],
                  ["--json", "Output as JSON"],
                ]}
              />
              <div className="mt-3">
                <CodeBlock title="terminal">{`$ agentnode resolve pdf_extraction email_sending --framework crewai`}</CodeBlock>
              </div>
            </div>

            {/* install */}
            <div className="mb-8 rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode install &lt;slug&gt; [slug...]
              </h4>
              <p className="mb-3 text-sm text-muted">
                Install one or more packs. Supports version pinning with{" "}
                <C>slug@version</C>.
              </p>
              <CodeBlock title="terminal">{`$ agentnode install pdf-reader-pack
$ agentnode install pdf-reader-pack@1.1.0
$ agentnode install pdf-reader-pack web-search-pack`}</CodeBlock>
            </div>

            {/* update */}
            <div className="mb-8 rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode update &lt;slug&gt;
              </h4>
              <p className="mb-3 text-sm text-muted">
                Update an installed pack to its latest version.
              </p>
              <CodeBlock title="terminal">{`$ agentnode update pdf-reader-pack
Updating pdf-reader-pack 1.2.0 -> 1.3.0... done`}</CodeBlock>
            </div>

            {/* rollback */}
            <div className="mb-8 rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode rollback &lt;slug&gt;@&lt;version&gt;
              </h4>
              <p className="mb-3 text-sm text-muted">
                Roll back an installed pack to a specific previous version.
              </p>
              <CodeBlock title="terminal">{`$ agentnode rollback pdf-reader-pack@1.2.0
Rolling back pdf-reader-pack 1.3.0 -> 1.2.0... done`}</CodeBlock>
            </div>

            {/* info */}
            <div className="mb-8 rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode info &lt;slug&gt;
              </h4>
              <p className="mb-3 text-sm text-muted">
                Display detailed metadata about a package: version history,
                publisher, trust level, permissions, capabilities, and
                compatibility.
              </p>
              <CodeBlock title="terminal">{`$ agentnode info pdf-reader-pack`}</CodeBlock>
            </div>

            {/* explain */}
            <div className="mb-8 rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode explain &lt;slug&gt;
              </h4>
              <p className="mb-3 text-sm text-muted">
                Explain a package in plain language: what it does, what
                permissions it requires, which frameworks it supports, and
                typical use cases. Designed for deciding whether to install.
              </p>
              <CodeBlock title="terminal">{`$ agentnode explain pdf-reader-pack`}</CodeBlock>
            </div>

            {/* audit */}
            <div className="mb-8 rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode audit &lt;slug&gt;
              </h4>
              <p className="mb-3 text-sm text-muted">
                Show trust and security details for a package: trust level
                progression, security scan results, signature verification
                status, and known issues.
              </p>
              <CodeBlock title="terminal">{`$ agentnode audit pdf-reader-pack
Trust:     trusted (since 2025-01-10)
Signature: valid (Ed25519)
Scan:      passed (Bandit, 0 findings)
Publisher: agentnode-official (verified)`}</CodeBlock>
            </div>

            {/* doctor */}
            <div className="mb-8 rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode doctor
              </h4>
              <p className="mb-3 text-sm text-muted">
                Analyze your local setup, check for outdated packs, missing
                dependencies, configuration issues, and suggest improvements.
              </p>
              <CodeBlock title="terminal">{`$ agentnode doctor
Checking environment...
  Node.js:    v20.10.0    OK
  Python:     3.11.5      OK
  CLI:        1.0.0       OK
  Auth:       logged in   OK
  Lockfile:   found       OK

  1 outdated pack: pdf-reader-pack (1.2.0 -> 1.3.0)
  Run: agentnode update pdf-reader-pack`}</CodeBlock>
            </div>

            {/* list */}
            <div className="mb-8 rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode list
              </h4>
              <p className="mb-3 text-sm text-muted">
                Show all locally installed packs with versions and trust levels.
              </p>
              <CodeBlock title="terminal">{`$ agentnode list
Installed packages:
  pdf-reader-pack    v1.2.0  trusted
  web-search-pack    v1.0.0  trusted
  email-drafter-pack v1.0.0  verified`}</CodeBlock>
            </div>

            {/* publish */}
            <div className="mb-8 rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode publish &lt;directory&gt;
              </h4>
              <p className="mb-3 text-sm text-muted">
                Publish a pack to the registry. Runs validation, security
                scanning, signing, and indexing.
              </p>
              <DocTable
                headers={["Flag", "Description"]}
                rows={[
                  ["--dry-run", "Run the full pipeline without uploading"],
                ]}
              />
              <div className="mt-3">
                <CodeBlock title="terminal">{`$ agentnode publish .
$ agentnode publish ./my-pack --dry-run`}</CodeBlock>
              </div>
            </div>

            {/* validate */}
            <div className="mb-8 rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode validate &lt;directory&gt;
              </h4>
              <p className="mb-3 text-sm text-muted">
                Validate a manifest without publishing. Checks syntax, capability
                IDs, permissions consistency, entrypoint resolution, and
                framework compatibility.
              </p>
              <CodeBlock title="terminal">{`$ agentnode validate .`}</CodeBlock>
            </div>

            {/* report */}
            <div className="mb-8 rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode report &lt;slug&gt;
              </h4>
              <p className="mb-3 text-sm text-muted">
                Generate a full security report for a package, including trust
                history, scan results, dependency analysis, and permission
                audit.
              </p>
              <CodeBlock title="terminal">{`$ agentnode report pdf-reader-pack`}</CodeBlock>
            </div>

            {/* recommend */}
            <div className="mb-8 rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode recommend
              </h4>
              <p className="mb-3 text-sm text-muted">
                Analyze your installed packs and suggest additional capabilities
                that complement your current setup.
              </p>
              <CodeBlock title="terminal">{`$ agentnode recommend
Based on your installed packs, you might also need:
  document_summary    -> document-summarizer-pack   trusted
  data_visualization  -> data-visualizer-pack       verified`}</CodeBlock>
            </div>

            {/* resolve-upgrade */}
            <div className="mb-8 rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode resolve-upgrade
              </h4>
              <p className="mb-3 text-sm text-muted">
                Find higher-scored or more trusted alternatives for your
                currently installed packs.
              </p>
              <CodeBlock title="terminal">{`$ agentnode resolve-upgrade
Checking for upgrades...
  pdf-extractor-pack -> pdf-reader-pack (higher trust, better score)`}</CodeBlock>
            </div>

            {/* policy-check */}
            <div className="mb-8 rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode policy-check &lt;slug&gt;
              </h4>
              <p className="mb-3 text-sm text-muted">
                Check whether a package meets specified policy constraints.
              </p>
              <DocTable
                headers={["Flag", "Description"]}
                rows={[
                  ["--trust <level>", "Required minimum trust level"],
                  ["--no-network", "Require no network access"],
                  ["--no-code-execution", "Require no code execution"],
                  ["--no-filesystem-write", "Require no filesystem write access"],
                ]}
              />
              <div className="mt-3">
                <CodeBlock title="terminal">{`$ agentnode policy-check pdf-reader-pack --trust trusted --no-network`}</CodeBlock>
              </div>
            </div>

            {/* import */}
            <div className="rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode import &lt;file&gt; --from &lt;platform&gt;
              </h4>
              <p className="mb-3 text-sm text-muted">
                Import existing tools from other frameworks and generate an ANP
                manifest automatically. See{" "}
                <a
                  href="#import-tools"
                  className="text-primary hover:underline"
                >
                  Import Tools
                </a>{" "}
                for full details.
              </p>
              <DocTable
                headers={["Flag", "Description"]}
                rows={[
                  ["--from <platform>", "Source platform: mcp, langchain, openai, crewai, clawhub, skillssh"],
                  ["--output <dir>", "Output directory for generated manifest (default: current directory)"],
                ]}
              />
              <div className="mt-3">
                <CodeBlock title="terminal">{`$ agentnode import my_tools.py --from langchain`}</CodeBlock>
              </div>
            </div>
          </section>

          {/* ============================================================ */}
          {/*  PYTHON SDK                                                   */}
          {/* ============================================================ */}
          <section>
            <SectionHeading id="python-sdk">Python SDK</SectionHeading>
            <p className="mb-4 text-sm leading-relaxed text-muted">
              The Python SDK provides programmatic access to the AgentNode
              registry for search, resolution, trust checking, installation,
              tool loading, and capability gap detection. Use it to build
              agents that detect missing capabilities and safely acquire
              verified skills on demand.
            </p>

            <SubHeading>Installation</SubHeading>
            <CodeBlock title="terminal">{`$ pip install agentnode-sdk`}</CodeBlock>

            <SubHeading>Initialization</SubHeading>
            <CodeBlock title="app.py" language="python">{`from agentnode_sdk import AgentNodeClient

# API key authentication (recommended)
client = AgentNodeClient(api_key="ank_live_abc123def456")

# Or use a bearer token
client = AgentNodeClient(token="your_bearer_token")

# Custom base URL (for self-hosted registries)
client = AgentNodeClient(
    api_key="ank_live_abc123def456",
    base_url="https://api.your-registry.com/v1"
)`}</CodeBlock>

            <SubHeading>Search</SubHeading>
            <CodeBlock title="search.py" language="python">{`result = client.search(
    query="pdf extraction",
    framework="langchain",
    per_page=10
)

print(f"Found {result.total} packages")
for hit in result.hits:
    print(f"  {hit.slug}  {hit.trust_level}  {hit.summary}")`}</CodeBlock>

            <SubHeading>Resolve</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Resolve finds the best package for a set of capability IDs,
              scoring each candidate on capability match, framework fit,
              runtime compatibility, trust level, and permissions.
            </p>
            <CodeBlock title="resolve.py" language="python">{`result = client.resolve(
    capabilities=["pdf_extraction", "web_search"],
    framework="langchain"
)

for match in result.results:
    print(f"{match.slug} v{match.version}")
    print(f"  Score: {match.score}  Trust: {match.trust_level}")
    print(f"  Breakdown: cap={match.breakdown.capability} "
          f"fw={match.breakdown.framework} "
          f"trust={match.breakdown.trust}")`}</CodeBlock>

            <SubHeading>Pre-flight check</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Check whether a package can be installed under given trust and
              permission constraints — without downloading anything.
            </p>
            <CodeBlock title="check.py" language="python">{`check = client.can_install(
    "pdf-reader-pack",
    require_trusted=True,
    denied_permissions=["network", "code_execution"]
)

if check.allowed:
    print(f"OK — trust: {check.trust_level}")
else:
    print(f"Blocked: {check.reason}")`}</CodeBlock>

            <SubHeading>Install</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Downloads the artifact, verifies the SHA-256 hash, extracts to{" "}
              <C>~/.agentnode/packages/</C>, installs pip dependencies, and
              writes <C>agentnode.lock</C>.
            </p>
            <CodeBlock title="install.py" language="python">{`result = client.install("pdf-reader-pack", require_trusted=True)

print(result.message)       # "Installed pdf-reader-pack@1.2.0"
print(result.installed)     # True
print(result.hash_verified) # True`}</CodeBlock>

            <SubHeading>Load and run tools</SubHeading>
            <CodeBlock title="run.py" language="python">{`# Load a tool from an installed package
extract = client.load_tool("pdf-reader-pack")
result = extract({"file_path": "report.pdf"})

# Multi-tool packs: load a specific tool by name
describe = client.load_tool("csv-analyzer-pack", tool_name="describe")
summary = describe({"file_path": "data.csv"})`}</CodeBlock>

            <SubHeading>One-call autonomous install</SubHeading>
            <p className="mb-3 text-sm text-muted">
              For agents that need to self-upgrade: describe what you need and
              let AgentNode handle the rest.
            </p>
            <CodeBlock title="autonomous.py" language="python">{`# Resolve + trust check + install in one call
result = client.resolve_and_install(
    capabilities=["pdf_extraction"],
    require_trusted=True  # only install trusted/curated packages
)

if result.installed:
    tool = client.load_tool(result.slug)
    data = tool({"file_path": "report.pdf"})
else:
    print(f"Could not install: {result.message}")`}</CodeBlock>

            <SubHeading>Capability gap detection (v0.4.0)</SubHeading>
            <p className="mb-3 text-sm text-muted">
              AgentNode can analyze runtime errors to detect missing capabilities
              — without any LLM. Three detection layers with confidence levels:
            </p>
            <ul className="mb-4 list-disc pl-5 text-sm text-muted space-y-1">
              <li><strong>High</strong> — <C>ImportError</C> for a known module (e.g. pdfplumber, pandas, selenium)</li>
              <li><strong>Medium</strong> — Error message contains technical keywords (e.g. &quot;chromedriver&quot;, &quot;csv parser&quot;)</li>
              <li><strong>Low</strong> — Context hints like file extensions or URLs</li>
            </ul>
            <CodeBlock title="detect.py" language="python">{`from agentnode_sdk import detect_gap

gap = detect_gap(ImportError("No module named 'pdfplumber'"))
print(gap.capability)   # "pdf_extraction"
print(gap.confidence)   # "high"
print(gap.source)       # "import_error"

# Context helps when the error itself isn't specific
gap = detect_gap(RuntimeError("failed"), context={"file": "report.pdf"})
print(gap.capability)   # "pdf_extraction"
print(gap.confidence)   # "low"`}</CodeBlock>

            <SubHeading>detect_and_install() (v0.4.0)</SubHeading>
            <p className="mb-3 text-sm text-muted">
              The product-level API for self-upgrading agents. Detects the gap,
              resolves the best match, and installs it — all in one call.
            </p>
            <CodeBlock title="detect_install.py" language="python">{`try:
    result = my_agent_logic()
except Exception as exc:
    upgrade = client.detect_and_install(
        exc,
        auto_upgrade_policy="safe",  # only verified+ skills
        on_detect=lambda cap, conf, err: print(f"Detected: {cap} ({conf})"),
        on_install=lambda slug: print(f"Installed: {slug}"),
    )

    if upgrade.installed:
        result = my_agent_logic()  # retry manually
    else:
        print(f"Detection: {upgrade.capability} ({upgrade.confidence})")
        print(f"Error: {upgrade.error}")`}</CodeBlock>

            <SubHeading>smart_run() (v0.4.0)</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Convenience wrapper: wrap your logic and let AgentNode handle
              detection, installation, and exactly one retry automatically.
            </p>
            <CodeBlock title="smart.py" language="python">{`result = client.smart_run(
    lambda: process_pdf("report.pdf"),
    auto_upgrade_policy="safe",
)

if result.success:
    print(result.result)          # your function's return value
    print(result.upgraded)        # True if a skill was installed
    print(result.installed_slug)  # e.g. "pdf-reader-pack"
    print(result.duration_ms)     # total time including retry
else:
    print(result.error)
    print(result.original_error)  # the first error, always available`}</CodeBlock>

            <SubHeading>Auto-upgrade policies (v0.4.0)</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Named policies control what gets auto-installed. When set, the policy
              overrides individual parameters like <C>require_verified</C>.
            </p>
            <div className="mb-4 overflow-hidden rounded-lg border border-border">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-card">
                    <th className="px-4 py-2 text-left font-medium text-foreground">Policy</th>
                    <th className="px-4 py-2 text-left font-medium text-foreground">Behavior</th>
                  </tr>
                </thead>
                <tbody>
                  <tr className="border-b border-border">
                    <td className="px-4 py-2 font-mono text-xs text-foreground">&quot;off&quot;</td>
                    <td className="px-4 py-2 text-muted">Detect only, never install</td>
                  </tr>
                  <tr className="border-b border-border">
                    <td className="px-4 py-2 font-mono text-xs text-primary">&quot;safe&quot;</td>
                    <td className="px-4 py-2 text-muted">Auto-install verified+ skills (recommended)</td>
                  </tr>
                  <tr>
                    <td className="px-4 py-2 font-mono text-xs text-foreground">&quot;strict&quot;</td>
                    <td className="px-4 py-2 text-muted">Auto-install trusted+ skills only</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p className="mb-3 text-sm text-muted">
              Low-confidence detections are blocked from auto-install by default.
              Use <C>allow_low_confidence=True</C> to override.
            </p>

            <SubHeading>Package metadata</SubHeading>
            <CodeBlock title="metadata.py" language="python">{`# Package details
pkg = client.get_package("pdf-reader-pack")
print(f"{pkg.name} v{pkg.latest_version}")
print(f"Downloads: {pkg.download_count}")
print(f"Deprecated: {pkg.is_deprecated}")

# Install metadata (capabilities, permissions, artifact info)
meta = client.get_install_metadata("pdf-reader-pack")
print(f"Runtime: {meta.runtime}")
print(f"Entrypoint: {meta.entrypoint}")
for cap in meta.capabilities:
    print(f"  {cap.name} ({cap.capability_id})")
if meta.permissions:
    print(f"  Network: {meta.permissions.network_level}")
    print(f"  Filesystem: {meta.permissions.filesystem_level}")`}</CodeBlock>
          </section>

          {/* ============================================================ */}
          {/*  REST API                                                     */}
          {/* ============================================================ */}
          <section>
            <SectionHeading id="rest-api">REST API</SectionHeading>
            <p className="mb-4 text-sm leading-relaxed text-muted">
              The AgentNode REST API provides direct HTTP access to all registry
              functionality. Base URL:{" "}
              <C>https://api.agentnode.net/v1</C>
            </p>

            <SubHeading>Authentication</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Include your API key in the <C>X-API-Key</C> header.
              Read-only endpoints (search, info) may work without
              authentication but are rate-limited.
            </p>
            <CodeBlock title="terminal" language="bash">{`curl -H "X-API-Key: ank_live_abc123def456" \\
  https://api.agentnode.net/v1/packages/pdf-reader-pack`}</CodeBlock>

            <SubHeading>Search packages</SubHeading>
            <CodeBlock title="terminal" language="bash">{`# POST /v1/search
curl -X POST "https://api.agentnode.net/v1/search" \\
  -H "Content-Type: application/json" \\
  -d '{"q": "pdf extraction", "framework": "langchain", "trust": "verified"}'

# Response:
{
  "results": [
    {
      "slug": "pdf-reader-pack",
      "name": "PDF Reader Pack",
      "version": "1.2.0",
      "summary": "Extract text, tables, and metadata from PDF documents",
      "trust_level": "trusted",
      "publisher": "agentnode-official",
      "frameworks": ["generic"],
      "capabilities": ["pdf_extraction"]
    }
  ],
  "total": 1
}`}</CodeBlock>

            <SubHeading>Get package details</SubHeading>
            <CodeBlock title="terminal" language="bash">{`# GET /v1/packages/:slug
curl "https://api.agentnode.net/v1/packages/pdf-reader-pack"

# Response:
{
  "slug": "pdf-reader-pack",
  "name": "PDF Reader Pack",
  "version": "1.2.0",
  "publisher": "agentnode-official",
  "trust_level": "trusted",
  "summary": "Extract text, tables, and metadata from PDF documents",
  "runtime": "python",
  "capabilities": [
    {
      "name": "extract_pdf",
      "capability_id": "pdf_extraction",
      "description": "Extract text, tables, and metadata from PDF documents"
    }
  ],
  "permissions": {
    "network": "none",
    "filesystem": "read",
    "code_execution": "none",
    "data_access": "input_only"
  },
  "compatibility": {
    "frameworks": ["generic"],
    "python": ">=3.10"
  }
}`}</CodeBlock>

            <SubHeading>Resolve capabilities</SubHeading>
            <CodeBlock title="terminal" language="bash">{`# POST /v1/resolve
curl -X POST "https://api.agentnode.net/v1/resolve" \\
  -H "X-API-Key: ank_live_abc123def456" \\
  -H "Content-Type: application/json" \\
  -d '{
    "capabilities": ["pdf_extraction", "web_search"],
    "framework": "langchain",
    "policy": {
      "min_trust": "verified"
    }
  }'

# Response:
{
  "results": [
    {
      "slug": "pdf-reader-pack",
      "version": "1.2.0",
      "score": 0.94,
      "trust_level": "trusted",
      "matched_capabilities": ["pdf_extraction"]
    },
    {
      "slug": "web-search-pack",
      "version": "1.0.0",
      "score": 0.92,
      "trust_level": "trusted",
      "matched_capabilities": ["web_search"]
    }
  ]
}`}</CodeBlock>

            <SubHeading>Check policy</SubHeading>
            <CodeBlock title="terminal" language="bash">{`# POST /v1/check-policy
curl -X POST "https://api.agentnode.net/v1/check-policy" \\
  -H "X-API-Key: ank_live_abc123def456" \\
  -H "Content-Type: application/json" \\
  -d '{
    "package_slug": "pdf-reader-pack",
    "policy": {
      "min_trust": "trusted",
      "max_permissions": {
        "network": "none",
        "code_execution": "none"
      }
    }
  }'

# Response:
{
  "passes": true,
  "checks": [
    { "name": "trust_level", "passed": true, "actual": "trusted", "required": "trusted" },
    { "name": "network", "passed": true, "actual": "none", "max": "none" },
    { "name": "code_execution", "passed": true, "actual": "none", "max": "none" }
  ]
}`}</CodeBlock>

            <SubHeading>Publish a package</SubHeading>
            <CodeBlock title="terminal" language="bash">{`# POST /v1/packages/publish
curl -X POST "https://api.agentnode.net/v1/packages/publish" \\
  -H "X-API-Key: ank_live_abc123def456" \\
  -H "Content-Type: multipart/form-data" \\
  -F "artifact=@./dist/my-pack-1.0.0.tar.gz" \\
  -F "manifest=@./agentnode.yaml"

# Response:
{
  "slug": "my-pack",
  "version": "1.0.0",
  "url": "https://agentnode.net/packages/my-pack",
  "trust_level": "unverified"
}`}</CodeBlock>

            <SubHeading>List capabilities</SubHeading>
            <CodeBlock title="terminal" language="bash">{`# GET /v1/capabilities
curl "https://api.agentnode.net/v1/capabilities"

# Response:
{
  "capabilities": [
    { "id": "pdf_extraction", "category": "Document Processing", "description": "Extract text and data from PDF files" },
    { "id": "web_search", "category": "Web & Browsing", "description": "Search the web and return structured results" },
    { "id": "email_sending", "category": "Communication", "description": "Compose and send emails" }
  ]
}`}</CodeBlock>

            <SubHeading>Additional endpoints</SubHeading>
            <DocTable
              headers={["Method", "Endpoint", "Description"]}
              rows={[
                ["POST", "/v1/auth/register", "Create a new account"],
                ["POST", "/v1/auth/login", "Authenticate and receive a session token"],
                ["POST", "/v1/auth/2fa/setup", "Initialize two-factor authentication"],
                ["POST", "/v1/auth/2fa/verify", "Verify a 2FA code"],
                ["GET", "/v1/packages/:slug/trust", "Get trust level details and history"],
                ["POST", "/v1/packages/:slug/reviews", "Submit a review for a package"],
                ["GET", "/v1/packages/:slug/reviews", "List reviews for a package"],
                ["POST", "/v1/packages/:slug/report", "Report a package for security or policy violations"],
                ["GET", "/v1/packages/:slug/install-info", "Get install metadata (hash, URL, dependencies)"],
                ["POST", "/v1/packages/:slug/install", "Record an installation event"],
                ["POST", "/v1/recommend", "Get pack recommendations based on installed capabilities"],
                ["POST", "/v1/packages/validate", "Validate a manifest without publishing"],
              ]}
            />
          </section>

          {/* ============================================================ */}
          {/*  MCP INTEGRATION                                              */}
          {/* ============================================================ */}
          <section>
            <SectionHeading id="mcp-integration">
              MCP Integration
            </SectionHeading>
            <p className="mb-4 text-sm leading-relaxed text-muted">
              The{" "}
              <a
                href="https://modelcontextprotocol.io"
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary hover:underline"
              >
                Model Context Protocol (MCP)
              </a>{" "}
              is an open standard for connecting AI models to external tools and
              data sources. The AgentNode MCP adapter lets you search, resolve,
              and browse the AgentNode registry directly from MCP-compatible
              editors like Claude Code and Cursor.
            </p>

            <SubHeading>Installation</SubHeading>
            <CodeBlock title="terminal">{`$ pip install agentnode-mcp`}</CodeBlock>

            <SubHeading>Two modes of operation</SubHeading>
            <p className="mb-3 text-sm text-muted">
              The MCP adapter runs in two modes:
            </p>
            <ul className="mb-4 list-inside list-disc space-y-2 text-sm text-muted">
              <li>
                <span className="font-medium text-foreground/80">
                  Pack server
                </span>{" "}
                -- exposes a single installed pack as MCP tools. Use when you
                want to give an editor access to one specific pack.
              </li>
              <li>
                <span className="font-medium text-foreground/80">
                  Platform server
                </span>{" "}
                -- exposes the full AgentNode platform API as MCP tools. Use
                when you want to search, resolve, and browse the registry from
                your editor.
              </li>
            </ul>

            <SubHeading>Pack server</SubHeading>
            <CodeBlock title="terminal">{`# Expose a single pack as MCP tools
$ agentnode-mcp --pack pdf-reader-pack`}</CodeBlock>

            <SubHeading>Platform server</SubHeading>
            <CodeBlock title="terminal">{`# Expose the full AgentNode platform API
$ agentnode-mcp-platform --api-url https://api.agentnode.net`}</CodeBlock>

            <SubHeading>Available MCP tools (platform server)</SubHeading>
            <DocTable
              headers={["Tool", "Description"]}
              rows={[
                ["agentnode_search", "Search the registry for packages by query, framework, and trust level"],
                ["agentnode_resolve", "Resolve capability IDs to ranked package recommendations"],
                ["agentnode_explain", "Get a detailed explanation of a package's capabilities and permissions"],
                ["agentnode_capabilities", "List all available capability IDs in the taxonomy"],
              ]}
            />

            <SubHeading>Claude Code configuration</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Add the following to your Claude Code MCP configuration file
              (typically <C>claude_desktop_config.json</C> or your project{" "}
              <C>.mcp.json</C>):
            </p>
            <CodeBlock title="claude_desktop_config.json" language="json">{`{
  "mcpServers": {
    "agentnode": {
      "command": "agentnode-mcp-platform",
      "args": ["--api-url", "https://api.agentnode.net"],
      "env": {
        "AGENTNODE_API_KEY": "ank_live_abc123def456"
      }
    }
  }
}`}</CodeBlock>

            <SubHeading>Cursor configuration</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Add the same server block to your Cursor MCP settings. The exact
              file location depends on your OS:
            </p>
            <CodeBlock title="cursor mcp config" language="json">{`{
  "mcpServers": {
    "agentnode": {
      "command": "agentnode-mcp-platform",
      "args": ["--api-url", "https://api.agentnode.net"],
      "env": {
        "AGENTNODE_API_KEY": "ank_live_abc123def456"
      }
    }
  }
}`}</CodeBlock>

            <SubHeading>Using a specific pack in your editor</SubHeading>
            <p className="mb-3 text-sm text-muted">
              To expose a specific installed pack as an MCP tool (so your editor
              can use it directly):
            </p>
            <CodeBlock title="claude_desktop_config.json" language="json">{`{
  "mcpServers": {
    "pdf-reader": {
      "command": "agentnode-mcp",
      "args": ["--pack", "pdf-reader-pack"]
    }
  }
}`}</CodeBlock>
          </section>

          {/* ============================================================ */}
          {/*  GITHUB ACTION                                                */}
          {/* ============================================================ */}
          <section>
            <SectionHeading id="github-action">GitHub Action</SectionHeading>
            <p className="mb-4 text-sm leading-relaxed text-muted">
              The <C>agentnode/publish@v1</C> GitHub Action automates pack
              publishing from your CI/CD pipeline. Push a tag or create a
              release, and the action validates, scans, signs, and publishes
              your pack automatically.
            </p>

            <SubHeading>Basic workflow</SubHeading>
            <CodeBlock title=".github/workflows/publish.yml" language="yaml">{`name: Publish to AgentNode
on:
  release:
    types: [published]

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Publish pack
        uses: agentnode/publish@v1
        with:
          api-key: \${{ secrets.AGENTNODE_API_KEY }}`}</CodeBlock>

            <SubHeading>Action inputs</SubHeading>
            <DocTable
              headers={["Input", "Required", "Default", "Description"]}
              rows={[
                ["api-key", "Yes", "--", "Your AgentNode API key. Store as a repository secret."],
                ["dry-run", "No", "false", "Run validation and scanning without publishing. Set to \"true\" for PR checks."],
                ["directory", "No", ".", "Path to the pack directory containing agentnode.yaml."],
              ]}
            />

            <SubHeading>Dry-run on pull requests</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Use dry-run mode to validate manifests on every pull request
              without publishing:
            </p>
            <CodeBlock title=".github/workflows/validate.yml" language="yaml">{`name: Validate AgentNode Pack
on:
  pull_request:
    paths:
      - "agentnode.yaml"
      - "src/**"

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Validate pack
        uses: agentnode/publish@v1
        with:
          api-key: \${{ secrets.AGENTNODE_API_KEY }}
          dry-run: true`}</CodeBlock>

            <SubHeading>Example output</SubHeading>
            <CodeBlock title="github actions log">{`Run agentnode/publish@v1
  Validating agentnode.yaml...
    Manifest syntax       OK
    Capability IDs        OK (1 tool, 0 resources)
    Permissions           OK (network: unrestricted)
    Entrypoint            OK (github_integration_pack.tool)
    Compatibility         OK (3 frameworks)

  Running security scan...
    Bandit scan           passed (0 issues)
    Dependency audit      passed

  Publishing github-integration-pack@1.0.0...
    Uploading package     done
    Signing package       done (Ed25519)
    Indexing capabilities done

  Published: https://agentnode.net/packages/github-integration-pack`}</CodeBlock>

            <SubHeading>Triggering on tags</SubHeading>
            <p className="mb-3 text-sm text-muted">
              If you prefer tag-based releases instead of GitHub Releases:
            </p>
            <CodeBlock title=".github/workflows/publish-on-tag.yml" language="yaml">{`name: Publish to AgentNode
on:
  push:
    tags:
      - "v*"

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Publish pack
        uses: agentnode/publish@v1
        with:
          api-key: \${{ secrets.AGENTNODE_API_KEY }}`}</CodeBlock>
          </section>

          {/* ============================================================ */}
          {/*  PACKAGE VERIFICATION                                         */}
          {/* ============================================================ */}
          <section>
            <SectionHeading id="verification">
              Package Verification
            </SectionHeading>
            <p className="mb-4 text-sm leading-relaxed text-muted">
              AgentNode verifies every package on publish in a real sandbox
              and computes a verification score from 0&ndash;100. The score is
              based on evidence &mdash; not self-reported badges. Scores are
              visible on every package page and factor into search ranking.
            </p>

            <SubHeading>Verification steps</SubHeading>
            <p className="mb-4 text-sm leading-relaxed text-muted">
              Each package goes through four checks, each contributing points
              to the overall score:
            </p>
            <div className="mb-6 space-y-3">
              <div className="rounded-lg border border-border bg-card p-4">
                <div className="flex items-center justify-between mb-1">
                  <p className="font-mono text-sm font-bold text-green-400">
                    1. Install
                  </p>
                  <span className="text-xs text-muted font-mono">15 pts</span>
                </div>
                <p className="text-sm text-muted">
                  The package is installed in a clean virtual environment. If
                  installation fails (missing dependencies, build errors), the
                  package is automatically quarantined.
                </p>
              </div>
              <div className="rounded-lg border border-border bg-card p-4">
                <div className="flex items-center justify-between mb-1">
                  <p className="font-mono text-sm font-bold text-green-400">
                    2. Import
                  </p>
                  <span className="text-xs text-muted font-mono">15 pts</span>
                </div>
                <p className="text-sm text-muted">
                  All declared tool entrypoints are imported and checked for
                  existence and callability. If any entrypoint is missing or
                  not callable, the package is quarantined.
                </p>
              </div>
              <div className="rounded-lg border border-border bg-card p-4">
                <div className="flex items-center justify-between mb-1">
                  <p className="font-mono text-sm font-bold text-yellow-400">
                    3. Smoke test
                  </p>
                  <span className="text-xs text-muted font-mono">25 pts</span>
                </div>
                <p className="text-sm text-muted">
                  Tools are called with test inputs generated from their JSON
                  schema. Results are classified as{" "}
                  <span className="text-green-400">passed</span> (25 pts),{" "}
                  <span className="text-yellow-400">inconclusive</span> (0&ndash;12
                  pts depending on reason), or{" "}
                  <span className="text-red-400">failed</span> (0 pts). Smoke
                  failures do not block the package but reduce the score.
                </p>
              </div>
              <div className="rounded-lg border border-border bg-card p-4">
                <div className="flex items-center justify-between mb-1">
                  <p className="font-mono text-sm font-bold text-yellow-400">
                    4. Tests
                  </p>
                  <span className="text-xs text-muted font-mono">15 pts</span>
                </div>
                <p className="text-sm text-muted">
                  If the package includes a test suite, it is executed with
                  pytest. Real tests that pass earn 15 pts. Auto-generated tests
                  earn 8 pts. No tests: 3 pts. Integration tests marked with{" "}
                  <C>@pytest.mark.integration</C> are skipped.
                </p>
              </div>
            </div>

            <SubHeading>Quality checks (multi-run)</SubHeading>
            <p className="mb-4 text-sm leading-relaxed text-muted">
              After a successful smoke test, the same input is run multiple
              times to measure stability:
            </p>
            <div className="mb-6 overflow-hidden rounded-lg border border-border">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-card">
                    <th className="px-4 py-2 text-left font-medium text-foreground">Check</th>
                    <th className="px-4 py-2 text-left font-medium text-foreground">Points</th>
                    <th className="px-4 py-2 text-left font-medium text-foreground">What it measures</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  <tr><td className="px-4 py-2 font-mono text-foreground">Reliability</td><td className="px-4 py-2 text-muted">0&ndash;10</td><td className="px-4 py-2 text-muted">Proportion of runs that succeed (e.g. 3/3 = 10 pts)</td></tr>
                  <tr><td className="px-4 py-2 font-mono text-foreground">Determinism</td><td className="px-4 py-2 text-muted">0&ndash;5</td><td className="px-4 py-2 text-muted">Output consistency across runs (same hash = 5 pts)</td></tr>
                  <tr><td className="px-4 py-2 font-mono text-foreground">Contract</td><td className="px-4 py-2 text-muted">0&ndash;10</td><td className="px-4 py-2 text-muted">Return value is serializable, non-null, structurally valid</td></tr>
                  <tr><td className="px-4 py-2 font-mono text-foreground">Warnings</td><td className="px-4 py-2 text-muted">&minus;2 each</td><td className="px-4 py-2 text-muted">Deprecation warnings, unsafe patterns (max &minus;10)</td></tr>
                </tbody>
              </table>
            </div>

            <SubHeading>Verification tiers</SubHeading>
            <p className="mb-4 text-sm leading-relaxed text-muted">
              The total score maps to a tier displayed on the package page and
              in search results:
            </p>
            <div className="mb-6 grid grid-cols-2 sm:grid-cols-4 gap-3">
              <div className="rounded-lg border border-yellow-500/20 bg-yellow-500/5 p-4 text-center">
                <p className="text-sm font-bold text-yellow-400">Gold</p>
                <p className="text-xs text-muted">90&ndash;100</p>
              </div>
              <div className="rounded-lg border border-green-500/20 bg-green-500/5 p-4 text-center">
                <p className="text-sm font-bold text-green-400">Verified</p>
                <p className="text-xs text-muted">70&ndash;89</p>
              </div>
              <div className="rounded-lg border border-yellow-500/20 bg-yellow-500/5 p-4 text-center">
                <p className="text-sm font-bold text-yellow-400">Partial</p>
                <p className="text-xs text-muted">50&ndash;69</p>
              </div>
              <div className="rounded-lg border border-zinc-500/20 bg-zinc-500/5 p-4 text-center">
                <p className="text-sm font-bold text-zinc-500">Unverified</p>
                <p className="text-xs text-muted">&lt;50</p>
              </div>
            </div>

            <SubHeading>Inconclusive reasons &amp; partial credit</SubHeading>
            <p className="mb-4 text-sm leading-relaxed text-muted">
              Not every tool can be fully tested in a sandbox. A tool that
              needs an API key is not broken &mdash; it just cannot be smoke-tested
              without credentials. We classify <em>why</em> a smoke test is
              inconclusive and score accordingly:
            </p>
            <div className="mb-6 overflow-hidden rounded-lg border border-border">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-card">
                    <th className="px-4 py-2 text-left font-medium text-foreground">Reason</th>
                    <th className="px-4 py-2 text-left font-medium text-foreground">Smoke pts</th>
                    <th className="px-4 py-2 text-left font-medium text-foreground">Meaning</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  <tr><td className="px-4 py-2 font-mono text-yellow-400">needs_credentials</td><td className="px-4 py-2 text-muted">12/25</td><td className="px-4 py-2 text-muted">Requires API keys not available in sandbox</td></tr>
                  <tr><td className="px-4 py-2 font-mono text-yellow-400">missing_system_dependency</td><td className="px-4 py-2 text-muted">12/25</td><td className="px-4 py-2 text-muted">Requires Chromium, FFmpeg, etc.</td></tr>
                  <tr><td className="px-4 py-2 font-mono text-yellow-400">needs_binary_input</td><td className="px-4 py-2 text-muted">12/25</td><td className="px-4 py-2 text-muted">Requires real PDF, image, or audio files</td></tr>
                  <tr><td className="px-4 py-2 font-mono text-yellow-400">external_network_blocked</td><td className="px-4 py-2 text-muted">12/25</td><td className="px-4 py-2 text-muted">Needs network access blocked in sandbox</td></tr>
                  <tr><td className="px-4 py-2 font-mono text-zinc-400">not_implemented</td><td className="px-4 py-2 text-muted">0/25</td><td className="px-4 py-2 text-muted">Stub package, raises NotImplementedError</td></tr>
                  <tr><td className="px-4 py-2 font-mono text-zinc-400">unknown_smoke_condition</td><td className="px-4 py-2 text-muted">8/25</td><td className="px-4 py-2 text-muted">Ambiguous error &mdash; may be broken or just missing data</td></tr>
                </tbody>
              </table>
            </div>

            <SubHeading>How to improve your score</SubHeading>
            <p className="mb-4 text-sm leading-relaxed text-muted">
              As a publisher, there are several things you can do to maximize
              your verification score:
            </p>
            <div className="mb-6 space-y-3">
              <div className="rounded-lg border border-border bg-card p-4">
                <p className="mb-1 text-sm font-semibold text-foreground">
                  Include real tests
                </p>
                <p className="text-sm text-muted">
                  A passing test suite earns 15 pts (vs. 3 pts with no tests).
                  Use <C>pytest</C> and place tests in a{" "}
                  <C>tests/</C> directory. Mark integration tests that need
                  external services with <C>@pytest.mark.integration</C>.
                </p>
              </div>
              <div className="rounded-lg border border-border bg-card p-4">
                <p className="mb-1 text-sm font-semibold text-foreground">
                  Define a complete input schema
                </p>
                <p className="text-sm text-muted">
                  Declare <C>input_schema</C> with <C>properties</C>,{" "}
                  <C>required</C>, and <C>type</C> for every field. Use{" "}
                  <C>enum</C> for constrained fields and <C>default</C> values
                  where appropriate. The more schema detail, the better our
                  test input generation.
                </p>
              </div>
              <div className="rounded-lg border border-border bg-card p-4">
                <p className="mb-1 text-sm font-semibold text-foreground">
                  Add schema examples
                </p>
                <p className="text-sm text-muted">
                  Include an <C>examples</C> key in your input schema with a
                  working example input. This is the highest-confidence test
                  input and is tried first.
                </p>
              </div>
              <div className="rounded-lg border border-border bg-card p-4">
                <p className="mb-1 text-sm font-semibold text-foreground">
                  Return serializable values
                </p>
                <p className="text-sm text-muted">
                  Tools should return JSON-serializable values (dicts, lists,
                  strings). Returning <C>None</C> or non-serializable objects
                  reduces the contract score.
                </p>
              </div>
            </div>

            <SubHeading>Quarantine behavior</SubHeading>
            <p className="mb-4 text-sm leading-relaxed text-muted">
              Packages are automatically quarantined if installation or import
              fails. Quarantined packages are hidden from search results and
              cannot be installed. Other issues (smoke test, unit tests) reduce
              the score but do not block usage.
            </p>

            <SubHeading>Continuous re-verification</SubHeading>
            <p className="mb-4 text-sm leading-relaxed text-muted">
              Scores are not static. Every package is automatically
              re-verified on every publish. If a dependency update breaks
              something, the score drops and users see it before their agent
              does. Admins can also trigger targeted re-verification at any
              time.
            </p>

            <SubHeading>What verification guarantees</SubHeading>
            <div className="mb-6 grid gap-4 sm:grid-cols-2">
              <div className="rounded-lg border border-green-500/20 bg-green-500/5 p-4">
                <p className="mb-2 text-sm font-semibold text-green-400">Guaranteed</p>
                <ul className="space-y-1 text-sm text-muted">
                  <li>Can be installed in a clean environment</li>
                  <li>All declared entrypoints exist and are callable</li>
                  <li>Package structure is valid</li>
                  <li>Score reflects real sandbox execution</li>
                </ul>
              </div>
              <div className="rounded-lg border border-border bg-card p-4">
                <p className="mb-2 text-sm font-semibold text-muted">Not guaranteed</p>
                <ul className="space-y-1 text-sm text-muted">
                  <li>Correct behavior with real-world data</li>
                  <li>Availability of external services</li>
                  <li>Full test coverage</li>
                  <li>Tools requiring credentials/system deps work correctly</li>
                </ul>
              </div>
            </div>
            <p className="mb-4 text-sm leading-relaxed text-muted">
              Verification is evidence-based, not absolute. It filters out
              broken packages and scores what it can prove. &ldquo;Partially
              Verified&rdquo; means &ldquo;not fully testable in a
              sandbox&rdquo; &mdash; not &ldquo;broken.&rdquo;
            </p>
          </section>

          {/* ============================================================ */}
          {/*  TRUST & SECURITY                                             */}
          {/* ============================================================ */}
          <section>
            <SectionHeading id="trust-security">
              Trust & Security
            </SectionHeading>
            <p className="mb-4 text-sm leading-relaxed text-muted">
              Trust is not binary. AgentNode provides a layered trust model
              where every pack has a clear, auditable trust level that
              progresses over time through verification, community usage, and
              manual review.
            </p>

            <SubHeading>The four trust levels</SubHeading>
            <div className="mb-6 grid gap-4 sm:grid-cols-2">
              <div className="rounded-lg border border-gray-500/30 bg-card p-4">
                <p className="mb-2 font-mono text-sm font-bold text-gray-400">
                  Unverified
                </p>
                <p className="text-sm leading-relaxed text-muted">
                  Newly published pack. Metadata has been validated and the
                  manifest is syntactically correct, but no further review has
                  been performed. Use with caution in production.
                </p>
              </div>
              <div className="rounded-lg border border-blue-500/30 bg-card p-4">
                <p className="mb-2 font-mono text-sm font-bold text-blue-400">
                  Verified
                </p>
                <p className="text-sm leading-relaxed text-muted">
                  Publisher identity has been confirmed. The pack passes
                  automated security scans (Bandit), and its declared
                  permissions are consistent with actual behavior. Publisher has
                  2FA enabled.
                </p>
              </div>
              <div className="rounded-lg border border-green-500/30 bg-card p-4">
                <p className="mb-2 font-mono text-sm font-bold text-green-400">
                  Trusted
                </p>
                <p className="text-sm leading-relaxed text-muted">
                  Security scanned with zero findings, tests pass, active
                  maintenance history, meaningful community usage, and no
                  reported issues. The pack has demonstrated reliability over
                  time.
                </p>
              </div>
              <div className="rounded-lg border border-primary/30 bg-card p-4">
                <p className="mb-2 font-mono text-sm font-bold text-primary">
                  Curated
                </p>
                <p className="text-sm leading-relaxed text-muted">
                  Manually reviewed by the AgentNode team. Code has been
                  audited, permissions verified against actual behavior, and the
                  pack meets the highest quality bar. This is the highest
                  assurance level in the registry.
                </p>
              </div>
            </div>

            <SubHeading>How to progress through trust levels</SubHeading>
            <DocTable
              headers={["From", "To", "Requirements"]}
              rows={[
                ["Unverified", "Verified", "Confirm publisher identity, enable 2FA, pass Bandit security scan, permissions match declared behavior"],
                ["Verified", "Trusted", "Zero security findings, tests pass, active maintenance, community usage, no unresolved reports"],
                ["Trusted", "Curated", "Manual review by AgentNode team, code audit, permissions verification, documentation review"],
              ]}
            />

            <SubHeading>Security scanning</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Every pack published to the registry undergoes automated security
              scanning:
            </p>
            <ul className="mb-4 list-inside list-disc space-y-2 text-sm text-muted">
              <li>
                <span className="font-medium text-foreground/80">
                  Bandit analysis
                </span>{" "}
                -- static analysis for common Python security issues (hardcoded
                passwords, SQL injection, insecure deserialization, etc.)
              </li>
              <li>
                <span className="font-medium text-foreground/80">
                  Ed25519 signatures
                </span>{" "}
                -- every published pack is signed with the publisher&apos;s key.
                Install-time verification ensures the pack has not been tampered
                with after publication.
              </li>
              <li>
                <span className="font-medium text-foreground/80">
                  Typosquatting detection
                </span>{" "}
                -- the registry detects package names that are suspiciously
                similar to popular packs (e.g., <C>pdf-reeder-pack</C> vs.{" "}
                <C>pdf-reader-pack</C>) and flags them for manual review.
              </li>
              <li>
                <span className="font-medium text-foreground/80">
                  Hash verification
                </span>{" "}
                -- SHA-256 hashes are computed at publish time and verified at
                install time. If the hash does not match, the installation is
                aborted.
              </li>
            </ul>

            <SubHeading>The permission model</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Every pack must explicitly declare its permissions across four
              dimensions. Agents and users see the full permission manifest
              before installation, and the resolution engine can filter by
              permission constraints.
            </p>
            <DocTable
              headers={["Dimension", "Levels", "Description"]}
              rows={[
                ["Network", "none, restricted, unrestricted", "What network access the pack requires. \"none\" means no outbound calls. \"restricted\" means specific domains only. \"unrestricted\" means any network access."],
                ["Filesystem", "none, temp, read, write", "What file system access the pack requires. \"temp\" means temporary directory only. \"read\" means it reads files. \"write\" means it reads and writes."],
                ["Code Execution", "none, sandboxed, full", "Whether the pack executes arbitrary code. \"sandboxed\" means restricted execution environment. \"full\" means unrestricted."],
                ["Data Access", "input_only, output_only, bidirectional", "The direction of data flow. \"input_only\" means the pack reads input but does not send data externally. \"bidirectional\" means it both receives and sends data."],
              ]}
            />

            <SubHeading>Auditing a package</SubHeading>
            <CodeBlock title="terminal">{`$ agentnode audit pdf-reader-pack

Trust:     trusted (since 2025-01-10)
Signature: valid (Ed25519)
Scan:      passed (Bandit, 0 findings)
Publisher: agentnode-official (verified, 2FA enabled)

Permissions:
  Network:         none
  Filesystem:      read
  Code Execution:  none
  Data Access:     input_only

History:
  2025-01-01  published (unverified)
  2025-01-05  verified (identity confirmed, scan passed)
  2025-01-10  trusted (community usage, zero findings)`}</CodeBlock>
          </section>

          {/* ============================================================ */}
          {/*  IMPORT TOOLS                                                 */}
          {/* ============================================================ */}
          <section>
            <SectionHeading id="import-tools">Import Tools</SectionHeading>
            <p className="mb-4 text-sm leading-relaxed text-muted">
              Already have tools written for another framework? The import
              command detects tool names, descriptions, and input schemas from
              your existing code and generates an ANP manifest automatically.
              No rewriting required.
            </p>

            <SubHeading>Supported platforms</SubHeading>
            <DocTable
              headers={["Platform", "What it detects", "Command"]}
              rows={[
                ["MCP", "@server.tool() decorated functions, tool descriptions, input schemas", "agentnode import server.py --from mcp"],
                ["LangChain", "BaseTool subclasses, @tool decorated functions, schemas", "agentnode import tools.py --from langchain"],
                ["OpenAI Functions", "Function definitions in JSON format", "agentnode import functions.json --from openai"],
                ["CrewAI", "@tool decorated functions, tool descriptions", "agentnode import tools.py --from crewai"],
                ["ClawhHub", "ClawhHub manifest files", "agentnode import manifest.json --from clawhub"],
                ["Skills.sh", "Skills.sh skill configs", "agentnode import skill.json --from skillssh"],
              ]}
            />

            <SubHeading>Import from MCP</SubHeading>
            <CodeBlock title="terminal">{`$ agentnode import mcp_server.py --from mcp

Detected 3 tools in mcp_server.py:
  search_web      -> capability: web_search
  extract_page    -> capability: webpage_extraction
  send_email      -> capability: email_sending

Generated agentnode.yaml with 3 tools.
Review and edit the manifest, then publish with: agentnode publish .`}</CodeBlock>

            <SubHeading>Import from LangChain</SubHeading>
            <CodeBlock title="terminal">{`$ agentnode import search_tool.py --from langchain

Detected 1 tool in search_tool.py:
  SearchTool (BaseTool subclass)
    name: "web_search"
    description: "Search the web for information"
    -> capability: web_search

Generated agentnode.yaml with 1 tool.`}</CodeBlock>

            <SubHeading>Import from OpenAI Functions</SubHeading>
            <CodeBlock title="terminal">{`$ agentnode import functions.json --from openai

Detected 2 functions in functions.json:
  get_weather     -> capability: weather_lookup
  search_docs     -> capability: document_search

Generated agentnode.yaml with 2 tools.`}</CodeBlock>

            <SubHeading>Import from CrewAI</SubHeading>
            <CodeBlock title="terminal">{`$ agentnode import crew_tools.py --from crewai

Detected 2 tools in crew_tools.py:
  @tool search_internet  -> capability: web_search
  @tool analyze_data     -> capability: data_analysis

Generated agentnode.yaml with 2 tools.`}</CodeBlock>

            <SubHeading>Web import tool</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Prefer a visual interface? Use the{" "}
              <Link href="/import" className="text-primary hover:underline">
                web-based import tool
              </Link>{" "}
              to paste your code or upload a file and generate a manifest in your
              browser.
            </p>

            <SubHeading>After importing</SubHeading>
            <p className="mb-3 text-sm text-muted">
              The import command generates an <C>agentnode.yaml</C> manifest
              with auto-detected values. You should review the generated
              manifest and:
            </p>
            <ol className="mb-4 list-inside list-decimal space-y-2 text-sm text-muted">
              <li>Verify the detected capability IDs are correct</li>
              <li>Set the appropriate permission levels (the importer defaults to conservative values)</li>
              <li>Add your publisher namespace</li>
              <li>
                Verify the per-tool entrypoints are correct (e.g. <C>tool:create_issue</C> for multi-tool packs)
              </li>
              <li>
                Run <C>agentnode validate .</C> to confirm everything is correct
              </li>
              <li>
                Publish with <C>agentnode publish .</C>
              </li>
            </ol>
          </section>

          {/* ============================================================ */}
          {/*  FOOTER                                                       */}
          {/* ============================================================ */}
          <div className="mt-16 border-t border-border pt-8">
            <div className="flex flex-col items-start gap-4 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-sm text-muted">
                Something missing or incorrect?{" "}
                <a
                  href="https://github.com/agentnode-ai/agentnode/issues"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-primary hover:underline"
                >
                  Open an issue on GitHub
                </a>
                .
              </p>
              <div className="flex gap-4 text-sm">
                <Link
                  href="/search"
                  className="text-muted transition-colors hover:text-foreground"
                >
                  Browse packages
                </Link>
                <Link
                  href="/capabilities"
                  className="text-muted transition-colors hover:text-foreground"
                >
                  Capabilities
                </Link>
                <Link
                  href="/for-developers"
                  className="text-muted transition-colors hover:text-foreground"
                >
                  For developers
                </Link>
              </div>
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
