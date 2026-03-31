/* ------------------------------------------------------------------ */
/*  Shared import/conversion utilities                                 */
/*  Used by /import (SEO landing page) and /publish (unified flow)     */
/* ------------------------------------------------------------------ */

export interface Platform {
  id: string;
  name: string;
  icon: string;
  example: string;
}

export interface ParsedTool {
  name: string;
  description: string;
  capability_id: string;
}

export interface ConversionResult {
  manifest: string;
  tools: ParsedTool[];
  detectedFramework: string;
  packageId: string;
  toolCount: number;
}

export const PLATFORMS: Platform[] = [
  {
    id: "langchain",
    name: "LangChain",
    icon: "\u{1F99C}",
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
    icon: "\u26A1",
    example: `from mcp.server.fastmcp import FastMCP
import pandas as pd

mcp = FastMCP("my-tools")

@mcp.tool()
def analyze_csv(file_path: str, operation: str = "describe") -> dict:
    """Analyze a CSV file \u2014 describe columns, show head, or compute stats."""
    df = pd.read_csv(file_path)
    if operation == "describe":
        return {"result": df.describe().to_string()}
    elif operation == "head":
        return {"result": df.head(10).to_string()}
    return {"columns": list(df.columns)}`,
  },
  {
    id: "openai",
    name: "OpenAI Functions",
    icon: "\u{1F916}",
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
    icon: "\u{1F680}",
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
/*  Parse result into structured data                                  */
/* ------------------------------------------------------------------ */

export function parseResult(manifest: string, platform: string): ConversionResult {
  const tools: ParsedTool[] = [];
  let packageId = "";

  const stripQuotes = (s: string) => s.replace(/^["']|["']$/g, "");

  const toolRegex = /- name:\s*(\S+)\n\s*capability_id:\s*(\S+)/g;
  let m;
  while ((m = toolRegex.exec(manifest)) !== null) {
    tools.push({ name: stripQuotes(m[1]), description: "", capability_id: stripQuotes(m[2]) });
  }

  const pkgMatch = manifest.match(/package_id:\s*(\S+)/);
  if (pkgMatch) packageId = stripQuotes(pkgMatch[1]);

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

export function convertClientSide(platform: string, content: string): string {
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
  const seenNames = new Set<string>();

  function addTool(name: string, description: string) {
    if (!seenNames.has(name)) {
      seenNames.add(name);
      tools.push({ name, description });
    }
  }

  if (platform === "openai") {
    try {
      const data = JSON.parse(content);
      const fns = Array.isArray(data) ? data : data.functions || data.tools || [data];
      for (const fn of fns) {
        const f = fn.function || fn;
        if (f.name) addTool(f.name, f.description || "");
      }
    } catch { /* skip */ }
  } else {
    const SKIP_PREFIXES = ["_run", "_arun", "run", "__init__", "setUp", "test_"];
    let m: RegExpExecArray | null;

    // 0. Detect FastMCP variable names (e.g., mcp = FastMCP("name"))
    const fastmcpVars = new Set<string>();
    const fastmcpVarRegex = /(\w+)\s*=\s*FastMCP\s*\(/g;
    while ((m = fastmcpVarRegex.exec(content)) !== null) {
      fastmcpVars.add(m[1]);
    }

    // 0b. Skip @mcp.resource() and @mcp.prompt() decorated functions
    const skipFuncs = new Set<string>();
    for (const v of fastmcpVars) {
      const skipRegex = new RegExp(`@${v}\\.(?:resource|prompt)\\([^)]*\\)\\s*\\n(?:async\\s+)?def\\s+(\\w+)`, "g");
      let sm;
      while ((sm = skipRegex.exec(content)) !== null) {
        skipFuncs.add(sm[1]);
      }
    }

    // 1. @tool decorator (most specific — CrewAI / MCP / FastMCP)
    // Match @tool(...), @server.tool(...), @mcp.tool(...), @<any_fastmcp_var>.tool(...)
    const varAlts = fastmcpVars.size > 0 ? [...fastmcpVars].join("|") : "";
    const prefixPattern = varAlts ? `(?:server|${varAlts})` : "server";
    const decoratorRegex = new RegExp(
      `@(?:${prefixPattern}\\.)?tool\\((?:["']([^"']+)["'])?\\)\\s*\\n(?:async\\s+)?def\\s+(\\w+)\\s*\\([^)]*\\)(?:\\s*->[^:]+)?:\\s*\\n\\s*"""([^"]*?)"""`,
      "g"
    );
    while ((m = decoratorRegex.exec(content)) !== null) {
      if (skipFuncs.has(m[2])) continue;
      const name = m[1] || m[2];
      addTool(name.toLowerCase().replace(/\s+/g, "_"), m[3].trim().split("\n")[0]);
    }

    // 2. class-based Tool (LangChain BaseTool)
    const classRegex = /class\s+(\w+)\s*\([^)]*Tool[^)]*\)[\s\S]*?name\s*[:=]\s*["']([^"']+)["'][\s\S]*?description\s*[:=]\s*["']([^"']+)["']/g;
    while ((m = classRegex.exec(content)) !== null) {
      addTool(m[2], m[3]);
    }

    // 3. Plain def with docstring (fallback — skip framework internals + MCP non-tools)
    const defRegex = /def\s+(\w+)\s*\([^)]*\)(?:\s*->[^:]+)?:\s*\n\s*"""([^"]*?)"""/g;
    while ((m = defRegex.exec(content)) !== null) {
      const match = m;
      if (!SKIP_PREFIXES.some((skip) => match[1].startsWith(skip)) && !skipFuncs.has(match[1])) {
        addTool(match[1], match[2].trim().split("\n")[0]);
      }
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

  // Derive categories from first tool's capability ID
  const firstCapId = guessCapId(tools[0].name, tools[0].description);
  const category = firstCapId.includes("_") ? firstCapId.split("_")[0] : firstCapId;
  const capIds = [...new Set(tools.map((t) => guessCapId(t.name, t.description)))];

  return `manifest_version: "${tools.length > 1 ? "0.2" : "0.1"}"
package_id: "${slug}"
package_type: toolpack
name: "${slug
    .split("-")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ")}"
version: "0.1.0"
summary: "${tools[0].description.slice(0, 120)}"
description: "${tools[0].description.slice(0, 500)}"
runtime: python
install_mode: package
hosting_type: agentnode_hosted
entrypoint: "${moduleName}.tool"
capabilities:
  tools:
${capTools}
  resources: []
  prompts: []
permissions:
  network: false
  filesystem: false
  code_execution: false
  data_access: false
  user_approval: true
compatibility:
  frameworks:
    - generic
  python: ">=3.10"
tags: [${capIds.slice(0, 3).map((c) => `"${c}"`).join(", ")}]
categories: ["${category}"]`;
}
