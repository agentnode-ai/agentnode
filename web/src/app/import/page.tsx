"use client";

import { useState, useRef } from "react";
import Link from "next/link";

/* ------------------------------------------------------------------ */
/*  Platform definitions                                               */
/* ------------------------------------------------------------------ */

const PLATFORMS = [
  {
    id: "langchain",
    name: "LangChain",
    icon: "🦜",
    example: `from langchain.tools import BaseTool
from pydantic import BaseModel, Field

class SearchInput(BaseModel):
    query: str = Field(description="Search query")
    max_results: int = Field(default=5, description="Max results")

class WebSearchTool(BaseTool):
    name = "web_search"
    description = "Search the web and return structured results"
    args_schema = SearchInput

    def _run(self, query: str, max_results: int = 5) -> dict:
        """Execute web search."""
        results = perform_search(query, max_results)
        return {"results": results, "count": len(results)}`,
  },
  {
    id: "mcp",
    name: "MCP",
    icon: "⚡",
    example: `from mcp.server import Server
from mcp.types import Tool

server = Server("my-tools")

@server.tool()
async def analyze_csv(file_path: str, operation: str = "describe") -> str:
    """Analyze a CSV file — describe columns, show head, or compute stats."""
    import pandas as pd
    df = pd.read_csv(file_path)
    if operation == "describe":
        return df.describe().to_string()
    elif operation == "head":
        return df.head(10).to_string()
    return f"Columns: {list(df.columns)}"`,
  },
  {
    id: "openai",
    name: "OpenAI Functions",
    icon: "🤖",
    example: `[
  {
    "name": "create_github_issue",
    "description": "Create a new issue in a GitHub repository",
    "parameters": {
      "type": "object",
      "properties": {
        "repo": {"type": "string", "description": "Repository (owner/name)"},
        "title": {"type": "string", "description": "Issue title"},
        "body": {"type": "string", "description": "Issue body"}
      },
      "required": ["repo", "title"]
    }
  },
  {
    "name": "list_repos",
    "description": "List repositories for the authenticated user",
    "parameters": {
      "type": "object",
      "properties": {
        "sort": {"type": "string", "enum": ["created", "updated", "pushed"]}
      }
    }
  }
]`,
  },
  {
    id: "crewai",
    name: "CrewAI",
    icon: "🚀",
    example: `from crewai_tools import tool

@tool("Summarize Document")
def summarize_document(file_path: str, max_length: int = 500) -> str:
    """Read a document and return a concise summary."""
    with open(file_path) as f:
        content = f.read()
    return generate_summary(content, max_length)`,
  },
];

/* ------------------------------------------------------------------ */
/*  Parsed result type                                                 */
/* ------------------------------------------------------------------ */

interface ParsedTool {
  name: string;
  description: string;
  capability_id: string;
}

interface ConversionResult {
  manifest: string;
  tools: ParsedTool[];
  detectedFramework: string;
  packageId: string;
  toolCount: number;
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function ImportPage() {
  const [platform, setPlatform] = useState("langchain");
  const [code, setCode] = useState("");
  const [result, setResult] = useState<ConversionResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const selectedPlatform = PLATFORMS.find((p) => p.id === platform)!;

  const handleConvert = async () => {
    if (!code.trim()) {
      setError("Paste your tool code above.");
      return;
    }
    setLoading(true);
    setError("");
    setResult(null);

    try {
      const res = await fetch("/api/v1/import/convert", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ platform, content: code }),
      });
      if (res.ok) {
        const data = await res.json();
        const manifest = data.manifest_yaml || data.manifest || "";
        setResult(parseResult(manifest, platform));
      } else {
        const data = await res.json().catch(() => ({}));
        setError(data.error || data.detail || "Conversion failed");
      }
    } catch {
      const manifest = convertClientSide(platform, code);
      if (manifest.startsWith("# No tools")) {
        setError("No tools detected. Check your input format and try again.");
      } else {
        setResult(parseResult(manifest, platform));
      }
    } finally {
      setLoading(false);
    }
  };

  const handleTryExample = () => {
    setCode(selectedPlatform.example);
    setResult(null);
    setError("");
    textareaRef.current?.focus();
  };

  const handleCopy = () => {
    if (result) {
      navigator.clipboard.writeText(result.manifest);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const shareText = result
    ? `I just converted my ${selectedPlatform.name} tool into a portable ANP package on @agentnode_ai — any AI agent can now discover and install it.\n\nhttps://agentnode.net/import`
    : "";

  return (
    <div className="flex flex-col">
      {/* ============================================================ */}
      {/*  HERO — The main CTA                                         */}
      {/* ============================================================ */}
      <section className="relative overflow-hidden border-b border-border">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-primary/10 via-transparent to-transparent" />
        <div className="relative mx-auto max-w-4xl px-4 sm:px-6 pt-16 pb-6 text-center">
          <h1 className="text-4xl font-bold leading-tight tracking-tight text-foreground sm:text-5xl">
            Turn any AI tool into an
            <br />
            <span className="text-primary">AgentNode package</span>
          </h1>
          <p className="mx-auto mt-5 max-w-2xl text-lg text-muted">
            Paste code from LangChain, MCP, OpenAI, or CrewAI.
            Get a publishable ANP package in seconds.
          </p>
        </div>
      </section>

      {/* ============================================================ */}
      {/*  CONVERTER — The core experience                             */}
      {/* ============================================================ */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-5xl px-4 sm:px-6 py-10">
          {/* Platform tabs */}
          <div className="mb-6 flex flex-wrap gap-2">
            {PLATFORMS.map((p) => (
              <button
                key={p.id}
                onClick={() => {
                  setPlatform(p.id);
                  setResult(null);
                  setError("");
                }}
                className={`flex items-center gap-2 rounded-full border px-4 py-2 text-sm font-medium transition-all ${
                  platform === p.id
                    ? "border-primary bg-primary/10 text-primary"
                    : "border-border text-muted hover:border-primary/30 hover:text-foreground"
                }`}
              >
                <span>{p.icon}</span>
                {p.name}
              </button>
            ))}
          </div>

          {/* Input area */}
          <div className="relative">
            <textarea
              ref={textareaRef}
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder={`Paste your ${selectedPlatform.name} tool code here...\n\nOr click "Try example" to see it in action →`}
              className="h-64 w-full rounded-xl border border-border bg-card p-5 font-mono text-sm text-foreground placeholder:text-muted/40 focus:border-primary/50 focus:outline-none focus:ring-1 focus:ring-primary/20 transition-all"
              spellCheck={false}
            />
            <button
              onClick={handleTryExample}
              className="absolute right-3 top-3 rounded-lg border border-primary/30 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary transition-colors hover:bg-primary/20"
            >
              Try example
            </button>
          </div>

          {/* Convert button */}
          <div className="mt-4 flex items-center gap-4">
            <button
              onClick={handleConvert}
              disabled={loading || !code.trim()}
              className="rounded-xl bg-primary px-8 py-3 text-sm font-semibold text-white transition-all hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {loading ? (
                <span className="flex items-center gap-2">
                  <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                  Converting...
                </span>
              ) : (
                "Convert to ANP"
              )}
            </button>
            <span className="text-xs text-muted">
              Free — no account required
            </span>
          </div>

          {/* Error */}
          {error && (
            <div className="mt-4 rounded-lg border border-red-500/30 bg-red-500/5 px-4 py-3 text-sm text-red-400">
              {error}
            </div>
          )}
        </div>
      </section>

      {/* ============================================================ */}
      {/*  RESULT — The aha moment                                     */}
      {/* ============================================================ */}
      {result && (
        <section className="border-b border-border bg-card/30">
          <div className="mx-auto max-w-5xl px-4 sm:px-6 py-10">
            {/* Success header */}
            <div className="mb-6 flex items-start gap-4">
              <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-green-500/10">
                <svg className="h-6 w-6 text-green-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                </svg>
              </div>
              <div>
                <h2 className="text-xl font-bold text-foreground">
                  Your tool is ANP-compatible
                </h2>
                <p className="mt-1 text-sm text-muted">
                  Converted from {selectedPlatform.name} — ready to publish on AgentNode.
                </p>
              </div>
            </div>

            {/* Detection cards */}
            <div className="mb-6 grid gap-3 sm:grid-cols-4">
              <div className="rounded-lg border border-border bg-card px-4 py-3">
                <div className="text-xs font-medium uppercase tracking-wider text-muted">Source</div>
                <div className="mt-1 text-sm font-semibold text-foreground">{selectedPlatform.name}</div>
              </div>
              <div className="rounded-lg border border-border bg-card px-4 py-3">
                <div className="text-xs font-medium uppercase tracking-wider text-muted">Tools detected</div>
                <div className="mt-1 text-sm font-semibold text-foreground">{result.toolCount}</div>
              </div>
              <div className="rounded-lg border border-border bg-card px-4 py-3">
                <div className="text-xs font-medium uppercase tracking-wider text-muted">Package ID</div>
                <div className="mt-1 text-sm font-semibold text-primary">{result.packageId}</div>
              </div>
              <div className="rounded-lg border border-border bg-card px-4 py-3">
                <div className="text-xs font-medium uppercase tracking-wider text-muted">Format</div>
                <div className="mt-1 text-sm font-semibold text-green-400">ANP v0.2</div>
              </div>
            </div>

            {/* Detected tools */}
            {result.tools.length > 0 && (
              <div className="mb-6">
                <h3 className="mb-2 text-sm font-semibold uppercase tracking-wider text-muted">Detected Tools</h3>
                <div className="flex flex-wrap gap-2">
                  {result.tools.map((t) => (
                    <div key={t.name} className="rounded-full border border-border bg-card px-3 py-1 text-xs">
                      <span className="font-medium text-foreground">{t.name}</span>
                      <span className="ml-1.5 text-muted">{t.capability_id}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Manifest preview */}
            <div className="relative overflow-hidden rounded-xl border border-border bg-[#0d1117]">
              <div className="flex items-center justify-between border-b border-border/50 px-4 py-2">
                <div className="flex items-center gap-2">
                  <div className="h-3 w-3 rounded-full bg-red-500/60" />
                  <div className="h-3 w-3 rounded-full bg-yellow-500/60" />
                  <div className="h-3 w-3 rounded-full bg-green-500/60" />
                  <span className="ml-2 font-mono text-xs text-muted">agentnode.yaml</span>
                </div>
                <button
                  onClick={handleCopy}
                  className="rounded border border-border px-2 py-1 text-xs text-muted transition-colors hover:text-foreground"
                >
                  {copied ? "Copied!" : "Copy"}
                </button>
              </div>
              <pre className="max-h-72 overflow-auto p-4 font-mono text-xs leading-relaxed text-gray-300">
                {result.manifest}
              </pre>
            </div>

            {/* CTAs */}
            <div className="mt-8 rounded-xl border border-primary/30 bg-gradient-to-r from-primary/5 to-primary/10 p-6">
              <div className="flex flex-col gap-5 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <h3 className="text-lg font-bold text-foreground">
                    Publish this package on AgentNode
                  </h3>
                  <p className="mt-1 text-sm text-muted">
                    Make it discoverable and installable by any AI agent — across LangChain, CrewAI, MCP, and more.
                  </p>
                </div>
                <Link
                  href={`/publish?manifest=${encodeURIComponent(result.manifest)}`}
                  className="shrink-0 rounded-xl bg-primary px-8 py-3 text-center text-sm font-bold text-white transition-colors hover:bg-primary/90"
                >
                  Publish on AgentNode
                </Link>
              </div>

              <div className="mt-5 flex flex-wrap gap-4">
                <button
                  onClick={handleCopy}
                  className="rounded-lg border border-border px-4 py-2 text-sm text-muted transition-colors hover:text-foreground"
                >
                  {copied ? "Copied!" : "Copy manifest"}
                </button>
                <a
                  href={`https://twitter.com/intent/tweet?text=${encodeURIComponent(shareText)}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="rounded-lg border border-border px-4 py-2 text-sm text-muted transition-colors hover:text-foreground"
                >
                  Share on X
                </a>
                <button
                  onClick={() => {
                    setResult(null);
                    setCode("");
                    setError("");
                  }}
                  className="rounded-lg border border-border px-4 py-2 text-sm text-muted transition-colors hover:text-foreground"
                >
                  Convert another tool
                </button>
              </div>
            </div>
          </div>
        </section>
      )}

      {/* ============================================================ */}
      {/*  BEFORE / AFTER                                               */}
      {/* ============================================================ */}
      {!result && (
        <>
          <section className="border-b border-border bg-card/30">
            <div className="mx-auto max-w-5xl px-4 sm:px-6 py-14">
              <h2 className="mb-8 text-center text-2xl font-bold text-foreground">
                From framework-locked to universally installable
              </h2>
              <div className="grid gap-6 sm:grid-cols-2">
                <div className="rounded-xl border border-border bg-card p-6">
                  <div className="mb-3 text-sm font-semibold uppercase tracking-wider text-red-400">
                    Before
                  </div>
                  <ul className="space-y-3 text-sm">
                    {[
                      "Tool works in one framework only",
                      "No way for agents to discover it",
                      "No permission model or trust badges",
                      "Manual sharing via GitHub or PyPI",
                    ].map((t) => (
                      <li key={t} className="flex items-center gap-2 text-muted">
                        <span className="text-red-400">✕</span> {t}
                      </li>
                    ))}
                  </ul>
                </div>
                <div className="rounded-xl border border-primary/30 bg-primary/5 p-6">
                  <div className="mb-3 text-sm font-semibold uppercase tracking-wider text-primary">
                    After AgentNode Import
                  </div>
                  <ul className="space-y-3 text-sm">
                    {[
                      "Portable ANP package — works everywhere",
                      "Discoverable by any AI agent automatically",
                      "Permission declarations + trust verification",
                      "Installable with one command or API call",
                    ].map((t) => (
                      <li key={t} className="flex items-center gap-2 text-foreground">
                        <span className="text-green-400">✓</span> {t}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </div>
          </section>

          {/* ============================================================ */}
          {/*  HOW IT WORKS                                                */}
          {/* ============================================================ */}
          <section className="border-b border-border">
            <div className="mx-auto max-w-5xl px-4 sm:px-6 py-14">
              <h2 className="mb-10 text-center text-2xl font-bold text-foreground">
                Three steps to a published package
              </h2>
              <div className="grid gap-8 sm:grid-cols-3">
                <div className="text-center">
                  <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-primary/10 text-xl font-bold text-primary">
                    1
                  </div>
                  <h3 className="mb-2 text-base font-semibold text-foreground">Paste your code</h3>
                  <p className="text-sm text-muted">
                    Paste a LangChain tool, MCP server function, OpenAI schema, or CrewAI tool. We detect everything automatically.
                  </p>
                </div>
                <div className="text-center">
                  <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-primary/10 text-xl font-bold text-primary">
                    2
                  </div>
                  <h3 className="mb-2 text-base font-semibold text-foreground">Get your ANP manifest</h3>
                  <p className="text-sm text-muted">
                    Instant conversion to the ANP v0.2 format with detected tools, capabilities, and permission declarations.
                  </p>
                </div>
                <div className="text-center">
                  <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-primary/10 text-xl font-bold text-primary">
                    3
                  </div>
                  <h3 className="mb-2 text-base font-semibold text-foreground">Publish</h3>
                  <p className="text-sm text-muted">
                    One click to publish. Your tool becomes discoverable and installable by any AI agent on any framework.
                  </p>
                </div>
              </div>
            </div>
          </section>

          {/* ============================================================ */}
          {/*  SOCIAL PROOF                                                */}
          {/* ============================================================ */}
          <section className="border-b border-border bg-card/30">
            <div className="mx-auto max-w-5xl px-4 sm:px-6 py-14">
              <div className="grid gap-6 sm:grid-cols-3">
                <div className="rounded-xl border border-border bg-card p-6 text-center">
                  <div className="text-3xl font-bold text-foreground">76+</div>
                  <div className="mt-1 text-sm text-muted">Published packages</div>
                </div>
                <div className="rounded-xl border border-border bg-card p-6 text-center">
                  <div className="text-3xl font-bold text-foreground">97</div>
                  <div className="mt-1 text-sm text-muted">Capability IDs</div>
                </div>
                <div className="rounded-xl border border-border bg-card p-6 text-center">
                  <div className="text-3xl font-bold text-foreground">All</div>
                  <div className="mt-1 text-sm text-muted">Frameworks supported</div>
                </div>
              </div>
              <div className="mt-8 flex flex-wrap items-center justify-center gap-4 text-sm text-muted">
                <span>Works with:</span>
                {PLATFORMS.map((p) => (
                  <span key={p.id} className="rounded-full border border-border bg-card px-3 py-1 text-xs font-medium text-foreground">
                    {p.icon} {p.name}
                  </span>
                ))}
              </div>
            </div>
          </section>

          {/* ============================================================ */}
          {/*  BOTTOM CTA                                                  */}
          {/* ============================================================ */}
          <section>
            <div className="mx-auto max-w-4xl px-4 sm:px-6 py-20 text-center">
              <h2 className="text-2xl font-bold text-foreground sm:text-3xl">
                Your tool deserves to be discovered
              </h2>
              <p className="mx-auto mt-4 max-w-xl text-muted">
                AI agents are already searching for capabilities on AgentNode.
                Import your tool and make it available to every agent, on every framework.
              </p>
              <button
                onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
                className="mt-8 rounded-xl bg-primary px-8 py-3.5 text-sm font-bold text-white transition-colors hover:bg-primary/90"
              >
                Import your tool now
              </button>
              <p className="mt-4 text-sm text-muted">
                Free to convert — no account needed until you publish.
              </p>
            </div>
          </section>
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Parse result into structured data                                  */
/* ------------------------------------------------------------------ */

function parseResult(manifest: string, platform: string): ConversionResult {
  const tools: ParsedTool[] = [];
  let packageId = "";

  // Extract tools
  const toolRegex = /- name:\s*(\S+)\n\s*capability_id:\s*(\S+)/g;
  let m;
  while ((m = toolRegex.exec(manifest)) !== null) {
    tools.push({ name: m[1], description: "", capability_id: m[2] });
  }

  // Extract package_id
  const pkgMatch = manifest.match(/package_id:\s*(\S+)/);
  if (pkgMatch) packageId = pkgMatch[1];

  return {
    manifest,
    tools,
    detectedFramework: platform,
    packageId,
    toolCount: tools.length,
  };
}

/* ------------------------------------------------------------------ */
/*  Client-side conversion (fallback when API not available)           */
/* ------------------------------------------------------------------ */

function convertClientSide(platform: string, content: string): string {
  const KEYWORDS: Record<string, string> = {
    pdf: "pdf_extraction", search: "web_search", web: "web_search",
    email: "email_sending", slack: "slack_integration", sql: "sql_generation",
    database: "database_access", csv: "csv_analysis", image: "image_analysis",
    translate: "text_translation", summarize: "document_summary",
    code: "code_execution", file: "file_conversion", api: "api_integration",
    github: "github_integration", docker: "docker_management",
    browser: "browser_automation", screenshot: "screenshot_capture",
    analyze: "data_analysis", extract: "pdf_extraction",
  };

  function guessCapId(name: string, desc: string): string {
    const text = `${name} ${desc}`.toLowerCase();
    for (const [kw, cap] of Object.entries(KEYWORDS)) {
      if (text.includes(kw)) return cap;
    }
    return name.replace(/([A-Z])/g, "_$1").toLowerCase().replace(/^_/, "").replace(/[^a-z0-9_]/g, "_");
  }

  const tools: { name: string; description: string }[] = [];

  if (platform === "openai") {
    try {
      const data = JSON.parse(content);
      const fns = Array.isArray(data) ? data : data.functions || data.tools || [data];
      for (const fn of fns) {
        const f = fn.function || fn;
        if (f.name) tools.push({ name: f.name, description: f.description || "" });
      }
    } catch { /* skip */ }
  } else {
    // Python-based: extract def/class patterns
    const defRegex = /def\s+(\w+)\s*\([^)]*\)(?:\s*->[^:]+)?:\s*\n\s*"""([^"]*?)"""/g;
    let m: RegExpExecArray | null;
    while ((m = defRegex.exec(content)) !== null) {
      const match = m;
      if (!["run", "__init__", "setUp", "test_"].some((skip) => match[1].startsWith(skip))) {
        tools.push({ name: match[1], description: match[2].trim().split("\n")[0] });
      }
    }
    const classRegex = /class\s+(\w+)\s*\([^)]*Tool[^)]*\)[\s\S]*?name\s*[:=]\s*["']([^"']+)["'][\s\S]*?description\s*[:=]\s*["']([^"']+)["']/g;
    while ((m = classRegex.exec(content)) !== null) {
      tools.push({ name: m[2], description: m[3] });
    }
    // @tool decorator
    const decoratorRegex = /@(?:server\.)?tool\((?:["']([^"']+)["'])?\)\s*\n(?:async\s+)?def\s+(\w+)\s*\([^)]*\)(?:\s*->[^:]+)?:\s*\n\s*"""([^"]*?)"""/g;
    while ((m = decoratorRegex.exec(content)) !== null) {
      const name = m[1] || m[2];
      tools.push({ name: name.toLowerCase().replace(/\s+/g, "_"), description: m[3].trim().split("\n")[0] });
    }
  }

  if (tools.length === 0) return "# No tools detected. Check your input format.";

  const slug = tools[0].name.toLowerCase().replace(/[^a-z0-9]/g, "-").replace(/-+/g, "-") + "-pack";
  const moduleName = slug.replace(/-/g, "_");

  const capTools = tools
    .map(
      (t) =>
        `    - name: "${t.name}"\n      capability_id: "${guessCapId(t.name, t.description)}"\n      description: "${t.description}"\n      entrypoint: "${moduleName}.tool:${t.name}"`
    )
    .join("\n");

  return `manifest_version: "0.2"
package_id: "${slug}"
package_type: toolpack
name: "${slug
    .split("-")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ")}"
publisher: "your-publisher-name"
version: "1.0.0"
summary: "${tools[0].description.slice(0, 200)}"
runtime: python
entrypoint: "${moduleName}.tool"
capabilities:
  tools:
${capTools}
compatibility:
  frameworks:
    - generic
tags: [${tools.map((t) => `"${t.name}"`).join(", ")}]`;
}
