"use client";

import { useState } from "react";
import Link from "next/link";

const PLATFORMS = [
  {
    id: "mcp",
    name: "MCP Tools",
    description: "Model Context Protocol — @tool decorated Python functions",
    example: `@server.tool()
def search_web(query: str) -> str:
    """Search the web for information."""
    return results`,
  },
  {
    id: "langchain",
    name: "LangChain",
    description: "BaseTool subclasses or @tool decorated functions",
    example: `class SearchTool(BaseTool):
    name = "search"
    description = "Search the web"
    def _run(self, query: str):
        return results`,
  },
  {
    id: "openai",
    name: "OpenAI Functions",
    description: "Function calling JSON schema definitions",
    example: `[{
  "name": "search",
  "description": "Search the web",
  "parameters": {
    "type": "object",
    "properties": {
      "query": {"type": "string"}
    }
  }
}]`,
  },
  {
    id: "crewai",
    name: "CrewAI",
    description: "CrewAI @tool decorated functions or BaseTool classes",
    example: `@tool
def search_web(query: str) -> str:
    """Search the web for results."""
    return results`,
  },
  {
    id: "clawhub",
    name: "ClawhHub",
    description: "ClawhHub tool manifest (JSON or YAML)",
    example: `{
  "name": "web_search",
  "description": "Search the web",
  "tools": [{"name": "search", "description": "..."}]
}`,
  },
  {
    id: "skillssh",
    name: "Skills.sh",
    description: "Skills.sh skill configuration",
    example: `{
  "name": "web_search",
  "description": "Search the web",
  "inputs": {"query": "string"}
}`,
  },
];

export default function ImportPage() {
  const [platform, setPlatform] = useState("mcp");
  const [code, setCode] = useState("");
  const [result, setResult] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const selectedPlatform = PLATFORMS.find((p) => p.id === platform)!;

  const handleConvert = async () => {
    if (!code.trim()) {
      setError("Paste your tool code or manifest above.");
      return;
    }
    setLoading(true);
    setError("");
    setResult("");

    try {
      const res = await fetch("/api/v1/import/convert", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ platform, content: code }),
      });
      if (res.ok) {
        const data = await res.json();
        setResult(data.manifest_yaml || data.manifest || "Conversion failed");
      } else {
        const data = await res.json().catch(() => ({}));
        setError(data.error || data.detail || "Conversion failed");
      }
    } catch {
      // Client-side conversion fallback
      setResult(convertClientSide(platform, code));
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="mx-auto max-w-6xl px-6 py-16">
      <h1 className="mb-2 text-3xl font-bold tracking-tight">
        <span className="text-primary">Import Tools</span>
      </h1>
      <p className="mb-8 text-muted">
        Convert your existing AI tools to AgentNode&apos;s ANP format. Paste
        your code, get a ready-to-publish package.
      </p>

      {/* Platform selector */}
      <div className="mb-6">
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-muted">
          Source Platform
        </h2>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
          {PLATFORMS.map((p) => (
            <button
              key={p.id}
              onClick={() => {
                setPlatform(p.id);
                setResult("");
                setError("");
              }}
              className={`rounded-lg border px-3 py-2 text-left text-sm transition-colors ${
                platform === p.id
                  ? "border-primary/40 bg-primary/10 text-primary"
                  : "border-border text-muted hover:border-primary/20 hover:text-foreground"
              }`}
            >
              <div className="font-medium">{p.name}</div>
            </button>
          ))}
        </div>
        <p className="mt-2 text-xs text-muted">{selectedPlatform.description}</p>
      </div>

      {/* Input / Output */}
      <div className="grid gap-6 lg:grid-cols-2">
        {/* Code input */}
        <div>
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-muted">
              Your {selectedPlatform.name} Code
            </h2>
            <button
              onClick={() => setCode(selectedPlatform.example)}
              className="text-xs text-primary underline hover:text-foreground"
            >
              Load example
            </button>
          </div>
          <textarea
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder={`Paste your ${selectedPlatform.name} tool code here...`}
            className="h-80 w-full rounded-lg border border-border bg-card p-4 font-mono text-sm text-foreground placeholder:text-muted/50 focus:border-primary/40 focus:outline-none"
            spellCheck={false}
          />
        </div>

        {/* Output */}
        <div>
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wider text-muted">
            Generated agentnode.yaml
          </h2>
          <div className="relative h-80 overflow-auto rounded-lg border border-border bg-card p-4">
            {result ? (
              <pre className="font-mono text-sm text-foreground whitespace-pre-wrap">
                {result}
              </pre>
            ) : error ? (
              <p className="text-sm text-red-400">{error}</p>
            ) : (
              <p className="text-sm text-muted/50">
                Click &quot;Convert&quot; to generate your ANP manifest...
              </p>
            )}
            {result && (
              <button
                onClick={() => navigator.clipboard.writeText(result)}
                className="absolute right-3 top-3 rounded border border-border bg-background px-2 py-1 text-xs text-muted hover:text-foreground"
              >
                Copy
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Convert button */}
      <div className="mt-6 flex items-center gap-4">
        <button
          onClick={handleConvert}
          disabled={loading || !code.trim()}
          className="rounded-lg bg-primary px-6 py-2.5 text-sm font-medium text-white transition-colors hover:bg-primary/80 disabled:opacity-50"
        >
          {loading ? "Converting..." : "Convert to ANP"}
        </button>
        <span className="text-xs text-muted">
          Or use the CLI:{" "}
          <code className="rounded bg-card px-1.5 py-0.5 text-primary">
            agentnode import tool.py --from {platform}
          </code>
        </span>
      </div>

      {/* CTA after successful conversion */}
      {result && !result.startsWith("# No tools") && (
        <div className="mt-8 rounded-lg border border-primary/30 bg-gradient-to-r from-primary/5 to-primary/10 p-6">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h3 className="text-lg font-semibold text-foreground">
                Your tool is ready to publish
              </h3>
              <p className="mt-1 text-sm text-muted">
                Make it discoverable and installable across LangChain, CrewAI, MCP, and every agent framework.
              </p>
            </div>
            <Link
              href={`/publish?manifest=${encodeURIComponent(result)}`}
              className="shrink-0 rounded-md bg-primary px-6 py-3 text-center text-sm font-semibold text-white transition-colors hover:bg-primary/90"
            >
              Publish on AgentNode
            </Link>
          </div>
          <div className="mt-4 grid grid-cols-2 gap-3 text-xs text-muted sm:grid-cols-4">
            <div className="flex items-center gap-1.5">
              <span className="text-success">&#10003;</span> Auto-discovery
            </div>
            <div className="flex items-center gap-1.5">
              <span className="text-success">&#10003;</span> Version management
            </div>
            <div className="flex items-center gap-1.5">
              <span className="text-success">&#10003;</span> Download stats
            </div>
            <div className="flex items-center gap-1.5">
              <span className="text-success">&#10003;</span> Trust badges
            </div>
          </div>
        </div>
      )}

      {/* How it works */}
      <div className="mt-16 rounded-lg border border-border bg-card p-6">
        <h2 className="mb-4 text-lg font-semibold">How Import Works</h2>
        <div className="grid gap-6 sm:grid-cols-3">
          <div>
            <div className="mb-2 text-2xl font-bold text-primary">1</div>
            <h3 className="mb-1 font-medium">Paste or Upload</h3>
            <p className="text-sm text-muted">
              Paste your tool code from any supported platform. We detect tool
              names, descriptions, and schemas automatically.
            </p>
          </div>
          <div>
            <div className="mb-2 text-2xl font-bold text-primary">2</div>
            <h3 className="mb-1 font-medium">Review &amp; Edit</h3>
            <p className="text-sm text-muted">
              Review the generated manifest. Edit capability IDs, permissions,
              and metadata to match your tool exactly.
            </p>
          </div>
          <div>
            <div className="mb-2 text-2xl font-bold text-primary">3</div>
            <h3 className="mb-1 font-medium">Publish</h3>
            <p className="text-sm text-muted">
              Save the manifest as agentnode.yaml, implement your tool logic,
              and publish with{" "}
              <code className="text-primary">agentnode publish</code>.
            </p>
          </div>
        </div>
      </div>

      <div className="mt-8 text-center text-sm text-muted">
        <Link
          href="/docs"
          className="text-primary underline hover:text-foreground"
        >
          Read the publishing guide
        </Link>
        {" · "}
        <Link href="/" className="text-primary underline hover:text-foreground">
          Back to home
        </Link>
      </div>
    </main>
  );
}

/**
 * Client-side conversion fallback when API is not available.
 * Mirrors the CLI logic for basic tool detection.
 */
function convertClientSide(platform: string, content: string): string {
  const KEYWORDS: Record<string, string> = {
    pdf: "pdf_extraction", search: "web_search", web: "web_search",
    email: "email_sending", slack: "slack_integration", sql: "sql_generation",
    database: "database_access", csv: "csv_analysis", image: "image_analysis",
    translate: "text_translation", summarize: "document_summary",
    code: "code_execution", file: "file_conversion", api: "api_integration",
    github: "github_integration", docker: "docker_management",
    browser: "browser_automation", screenshot: "screenshot_capture",
  };

  function guessCapId(name: string, desc: string): string {
    const text = `${name} ${desc}`.toLowerCase();
    for (const [kw, cap] of Object.entries(KEYWORDS)) {
      if (text.includes(kw)) return cap;
    }
    return name.replace(/([A-Z])/g, "_$1").toLowerCase().replace(/^_/, "").replace(/[^a-z0-9_]/g, "_");
  }

  // Simple regex-based tool extraction
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
  } else if (platform === "clawhub" || platform === "skillssh") {
    try {
      const data = JSON.parse(content);
      const items = data.tools || data.skills || [data];
      for (const item of items) {
        tools.push({ name: item.name || "unknown", description: item.description || "" });
      }
    } catch { /* skip */ }
  } else {
    // Python-based: extract def/class patterns
    const defRegex = /def\s+(\w+)\s*\([^)]*\)(?:\s*->[^:]+)?:\s*\n\s*"""([^"]*?)"""/g;
    let m;
    while ((m = defRegex.exec(content)) !== null) {
      tools.push({ name: m[1], description: m[2].trim().split("\n")[0] });
    }
    // Class-based
    const classRegex = /class\s+(\w+)\s*\([^)]*Tool[^)]*\)[\s\S]*?name\s*[:=]\s*["']([^"']+)["'][\s\S]*?description\s*[:=]\s*["']([^"']+)["']/g;
    while ((m = classRegex.exec(content)) !== null) {
      tools.push({ name: m[2], description: m[3] });
    }
  }

  if (tools.length === 0) return "# No tools detected. Check your input format.";

  const slug = tools[0].name.toLowerCase().replace(/[^a-z0-9]/g, "-").replace(/-+/g, "-") + "-pack";
  const capTools = tools.map(t => `    - name: ${t.name}\n      capability_id: ${guessCapId(t.name, t.description)}\n      type: tool\n      description: "${t.description}"`).join("\n");

  return `manifest_version: "0.1"
package_id: ${slug}
package_type: toolpack
name: ${slug.split("-").map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(" ")}
publisher: your-publisher-name
version: 1.0.0
summary: "${tools[0].description.slice(0, 200)}"
description: "Provides ${tools.length} tool(s): ${tools.map(t => t.name).join(", ")}."
runtime: python
install_mode: package
hosting_type: agentnode_hosted
entrypoint: ${slug.replace(/-/g, "_")}.tool
capabilities:
  tools:
${capTools}
permissions:
  network:
    level: none
  filesystem:
    level: none
  code_execution:
    level: none
  data_access:
    level: input_only
  user_approval:
    required: never
compatibility:
  frameworks:
    - langchain
    - crewai
    - generic
tags: []`;
}
