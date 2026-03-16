/**
 * agentnode import — convert tools from other platforms to ANP format.
 *
 * Supported sources:
 *   --from mcp <file>         MCP server with @tool decorators
 *   --from langchain <file>   LangChain BaseTool subclass
 *   --from openai <file>      OpenAI function calling JSON
 *   --from crewai <file>      CrewAI @tool decorated functions
 *   --from clawhub <file>     ClawhHub tool manifest
 *   --from skillssh <file>    Skills.sh skill config
 */

import { Command } from "commander";
import chalk from "chalk";
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { join, basename, dirname } from "node:path";
import { stringify as toYAML } from "yaml";

// Capability ID guessing from tool names/descriptions
const CAPABILITY_KEYWORDS: Record<string, string> = {
  pdf: "pdf_extraction",
  search: "web_search",
  web: "web_search",
  scrape: "webpage_extraction",
  extract: "webpage_extraction",
  email: "email_sending",
  mail: "email_sending",
  slack: "slack_integration",
  discord: "discord_integration",
  telegram: "telegram_integration",
  whatsapp: "whatsapp_integration",
  github: "github_integration",
  gitlab: "gitlab_integration",
  docker: "docker_management",
  kubernetes: "kubernetes_management",
  k8s: "kubernetes_management",
  sql: "sql_generation",
  database: "database_access",
  csv: "csv_analysis",
  image: "image_analysis",
  photo: "image_analysis",
  ocr: "ocr_reading",
  translate: "text_translation",
  summarize: "document_summary",
  summary: "document_summary",
  code: "code_execution",
  lint: "code_linting",
  test: "test_generation",
  embed: "embedding_generation",
  vector: "embedding_generation",
  calendar: "calendar_management",
  schedule: "scheduling",
  notion: "notion_integration",
  json: "json_processing",
  file: "file_conversion",
  convert: "file_conversion",
  powerpoint: "powerpoint_generation",
  pptx: "powerpoint_generation",
  excel: "excel_processing",
  word: "word_document",
  audio: "audio_processing",
  speech: "speech_to_text",
  tts: "text_to_speech",
  video: "video_generation",
  gif: "gif_creation",
  screenshot: "screenshot_capture",
  browser: "browser_automation",
  crawl: "browser_automation",
  seo: "seo_optimization",
  news: "news_aggregation",
  arxiv: "arxiv_search",
  chart: "data_visualization",
  plot: "data_visualization",
  visuali: "data_visualization",
  aws: "aws_integration",
  azure: "azure_integration",
  cloud: "cloud_deployment",
  deploy: "cloud_deployment",
  security: "security_audit",
  secret: "secret_scanning",
  crm: "crm_integration",
  social: "social_media",
  api: "api_integration",
  regex: "regex_building",
  prompt: "prompt_engineering",
  redact: "document_redaction",
  citation: "citation_management",
  home: "home_automation",
  light: "smart_lighting",
};

function guessCapabilityId(name: string, description: string): string {
  const text = `${name} ${description}`.toLowerCase();
  for (const [keyword, capId] of Object.entries(CAPABILITY_KEYWORDS)) {
    if (text.includes(keyword)) return capId;
  }
  // Default: convert tool name to snake_case
  return name
    .replace(/([A-Z])/g, "_$1")
    .toLowerCase()
    .replace(/^_/, "")
    .replace(/[^a-z0-9_]/g, "_")
    .replace(/_+/g, "_");
}

function guessPermissions(
  name: string,
  description: string
): Record<string, any> {
  const text = `${name} ${description}`.toLowerCase();
  const perms: Record<string, any> = {
    network: { level: "none" },
    filesystem: { level: "none" },
    code_execution: { level: "none" },
    data_access: { level: "input_only" },
    user_approval: { required: "never" },
  };

  // Infer network needs
  if (
    text.match(
      /api|http|fetch|request|url|web|search|slack|discord|email|cloud|aws|azure|github/
    )
  ) {
    perms.network = { level: "restricted" };
  }

  // Infer filesystem needs
  if (text.match(/file|save|write|export|download|upload|pdf|csv|excel/)) {
    perms.filesystem = { level: "temp" };
  }

  // Infer code execution needs
  if (text.match(/exec|run|shell|command|subprocess|script/)) {
    perms.code_execution = { level: "limited_subprocess" };
    perms.user_approval = { required: "always" };
  }

  return perms;
}

interface ParsedTool {
  name: string;
  description: string;
  inputSchema?: Record<string, any>;
  outputSchema?: Record<string, any>;
}

// --- Parsers for each platform ---

function parseMCP(content: string): ParsedTool[] {
  const tools: ParsedTool[] = [];
  // Match @mcp.tool() or @server.tool() decorated functions
  const toolRegex =
    /(?:@\w+\.tool\(\)|@tool(?:\([^)]*\))?)\s*(?:async\s+)?def\s+(\w+)\s*\(([^)]*)\)(?:\s*->\s*[^:]+)?:\s*\n\s*"""([^"]*(?:""[^"]*)*?)"""/g;
  let match;
  while ((match = toolRegex.exec(content)) !== null) {
    tools.push({
      name: match[1],
      description: match[3].trim().split("\n")[0],
    });
  }

  // Fallback: match def with Tool suffix or common patterns
  if (tools.length === 0) {
    const defRegex =
      /def\s+(\w+)\s*\(([^)]*)\)(?:\s*->\s*[^:]+)?:\s*\n\s*"""([^"]*(?:""[^"]*)*?)"""/g;
    while ((match = defRegex.exec(content)) !== null) {
      tools.push({
        name: match[1],
        description: match[3].trim().split("\n")[0],
      });
    }
  }
  return tools;
}

function parseLangChain(content: string): ParsedTool[] {
  const tools: ParsedTool[] = [];
  // Match class MyTool(BaseTool): with name and description
  const classRegex =
    /class\s+(\w+)\s*\((?:\w+\.)?BaseTool\):\s*\n([\s\S]*?)(?=\nclass\s|\n\S|\Z)/g;
  let match;
  while ((match = classRegex.exec(content)) !== null) {
    const className = match[1];
    const body = match[2];
    const nameMatch = body.match(/name\s*[:=]\s*["']([^"']+)["']/);
    const descMatch = body.match(/description\s*[:=]\s*["']([^"']+)["']/);
    tools.push({
      name: nameMatch ? nameMatch[1] : className,
      description: descMatch ? descMatch[1] : `${className} tool`,
    });
  }

  // Also match @tool decorator pattern (LangChain v2)
  const toolDecRegex =
    /@tool(?:\([^)]*\))?\s*\ndef\s+(\w+)\s*\([^)]*\)(?:\s*->\s*[^:]+)?:\s*\n\s*"""([^"]*(?:""[^"]*)*?)"""/g;
  while ((match = toolDecRegex.exec(content)) !== null) {
    tools.push({
      name: match[1],
      description: match[2].trim().split("\n")[0],
    });
  }
  return tools;
}

function parseOpenAI(content: string): ParsedTool[] {
  const tools: ParsedTool[] = [];
  try {
    const data = JSON.parse(content);
    const functions = Array.isArray(data) ? data : data.functions || data.tools || [data];
    for (const fn of functions) {
      const func = fn.function || fn;
      if (func.name) {
        tools.push({
          name: func.name,
          description: func.description || "",
          inputSchema: func.parameters,
        });
      }
    }
  } catch {
    // Try as JSONL or embedded in Python
    const jsonRegex = /\{[^{}]*"name"\s*:\s*"[^"]+"\s*,[^{}]*"description"[^{}]*\}/g;
    let match;
    while ((match = jsonRegex.exec(content)) !== null) {
      try {
        const fn = JSON.parse(match[0]);
        tools.push({
          name: fn.name,
          description: fn.description || "",
          inputSchema: fn.parameters,
        });
      } catch {
        // skip
      }
    }
  }
  return tools;
}

function parseCrewAI(content: string): ParsedTool[] {
  const tools: ParsedTool[] = [];
  // Match @tool decorator
  const toolRegex =
    /@tool(?:\([^)]*\))?\s*\ndef\s+(\w+)\s*\([^)]*\)(?:\s*->\s*[^:]+)?:\s*\n\s*"""([^"]*(?:""[^"]*)*?)"""/g;
  let match;
  while ((match = toolRegex.exec(content)) !== null) {
    tools.push({
      name: match[1],
      description: match[2].trim().split("\n")[0],
    });
  }

  // Match BaseTool subclass (CrewAI also supports this)
  const classRegex =
    /class\s+(\w+)\s*\(BaseTool\):\s*\n([\s\S]*?)(?=\nclass\s|\n\S|\Z)/g;
  while ((match = classRegex.exec(content)) !== null) {
    const className = match[1];
    const body = match[2];
    const nameMatch = body.match(/name\s*[:=]\s*["']([^"']+)["']/);
    const descMatch = body.match(/description\s*[:=]\s*["']([^"']+)["']/);
    tools.push({
      name: nameMatch ? nameMatch[1] : className,
      description: descMatch ? descMatch[1] : `${className} tool`,
    });
  }
  return tools;
}

function parseClawhub(content: string): ParsedTool[] {
  const tools: ParsedTool[] = [];
  try {
    const data = JSON.parse(content);
    // ClawhHub format: { name, description, tools: [...] }
    const items = data.tools || data.skills || [data];
    for (const item of items) {
      tools.push({
        name: item.name || item.id || "unknown",
        description: item.description || item.summary || "",
        inputSchema: item.input_schema || item.parameters,
      });
    }
  } catch {
    // If not JSON, try YAML
    try {
      const { parse } = require("yaml");
      const data = parse(content);
      const items = data.tools || data.skills || [data];
      for (const item of items) {
        tools.push({
          name: item.name || item.id || "unknown",
          description: item.description || item.summary || "",
        });
      }
    } catch {
      // skip
    }
  }
  return tools;
}

function parseSkillssh(content: string): ParsedTool[] {
  const tools: ParsedTool[] = [];
  try {
    const data = JSON.parse(content);
    // Skills.sh format: { name, description, inputs, outputs }
    const items = Array.isArray(data) ? data : [data];
    for (const item of items) {
      tools.push({
        name: item.name || item.skill_name || "unknown",
        description: item.description || item.summary || "",
        inputSchema: item.inputs
          ? {
              type: "object",
              properties: Object.fromEntries(
                Object.entries(item.inputs).map(([k, v]: [string, any]) => [
                  k,
                  { type: typeof v === "string" ? v : "string", description: v?.description || "" },
                ])
              ),
            }
          : undefined,
      });
    }
  } catch {
    // skip
  }
  return tools;
}

const PARSERS: Record<string, (content: string) => ParsedTool[]> = {
  mcp: parseMCP,
  langchain: parseLangChain,
  openai: parseOpenAI,
  crewai: parseCrewAI,
  clawhub: parseClawhub,
  skillssh: parseSkillssh,
};

function generateSlug(name: string): string {
  return name
    .toLowerCase()
    .replace(/([A-Z])/g, "-$1")
    .replace(/[^a-z0-9-]/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "")
    .concat("-pack");
}

function generateManifest(
  tools: ParsedTool[],
  slug: string,
  publisher: string
): Record<string, any> {
  const primaryTool = tools[0];
  const allCapabilities = tools.map((t) => ({
    name: t.name,
    capability_id: guessCapabilityId(t.name, t.description),
    type: "tool" as const,
    description: t.description,
    ...(t.inputSchema ? { input_schema: t.inputSchema } : {}),
    ...(t.outputSchema ? { output_schema: t.outputSchema } : {}),
  }));

  const perms = guessPermissions(
    primaryTool.name,
    tools.map((t) => t.description).join(" ")
  );

  const entrypoint = slug.replace(/-/g, "_").replace(/_pack$/, "_pack.tool");

  return {
    manifest_version: "0.1",
    package_id: slug,
    package_type: "toolpack",
    name: slug
      .split("-")
      .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
      .join(" "),
    publisher,
    version: "1.0.0",
    summary: primaryTool.description.slice(0, 200),
    description: tools.length > 1
      ? `Provides ${tools.length} tools: ${tools.map((t) => t.name).join(", ")}.`
      : primaryTool.description,
    runtime: "python",
    install_mode: "package",
    hosting_type: "agentnode_hosted",
    entrypoint,
    capabilities: {
      tools: allCapabilities,
    },
    permissions: perms,
    compatibility: {
      frameworks: ["langchain", "crewai", "generic"],
    },
    tags: [
      ...new Set(
        tools.flatMap((t) =>
          guessCapabilityId(t.name, t.description)
            .split("_")
            .filter((w) => w.length > 2)
        )
      ),
    ].slice(0, 5),
  };
}

function generatePyprojectToml(slug: string): string {
  const pkgName = slug.replace(/-/g, "_");
  return `[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "${slug}"
version = "1.0.0"
description = "AgentNode pack — imported and converted to ANP format."
requires-python = ">=3.10"
license = "MIT"
dependencies = []

[tool.setuptools.packages.find]
where = ["src"]
`;
}

function generateToolPy(tools: ParsedTool[]): string {
  const funcs = tools
    .map(
      (t) => `
def ${t.name}(**kwargs):
    """${t.description}"""
    # TODO: Implement tool logic
    raise NotImplementedError("Implement ${t.name}")
`
    )
    .join("\n");

  return `"""Auto-generated tool stubs from import. Implement the logic below."""
${funcs}

def run(*args, **kwargs):
    """Default entrypoint — calls the primary tool."""
    return ${tools[0].name}(**kwargs)
`;
}

function generateTestPy(tools: ParsedTool[], slug: string): string {
  const pkgName = slug.replace(/-/g, "_");
  const tests = tools
    .map(
      (t) => `
def test_${t.name}_exists():
    """Verify ${t.name} tool is importable."""
    from ${pkgName}.tool import ${t.name}
    assert callable(${t.name})
`
    )
    .join("\n");

  return `"""Tests for ${slug} — auto-generated by agentnode import."""
${tests}

def test_run_entrypoint():
    """Verify run() entrypoint exists."""
    from ${pkgName}.tool import run
    assert callable(run)
`;
}

export const importCommand = new Command("import")
  .description(
    "Import tools from other platforms and convert to ANP format.\n\n" +
      "Supported platforms: mcp, langchain, openai, crewai, clawhub, skillssh"
  )
  .argument("<file>", "Source file or directory to import from")
  .requiredOption(
    "--from <platform>",
    "Source platform (mcp, langchain, openai, crewai, clawhub, skillssh)"
  )
  .option("-o, --output <dir>", "Output directory", ".")
  .option("--publisher <name>", "Publisher name", "my-publisher")
  .option("--slug <slug>", "Package slug (auto-generated if not provided)")
  .option("--dry-run", "Show what would be generated without writing files")
  .option("--json", "Output manifest as JSON instead of YAML")
  .action(
    async (
      file: string,
      opts: {
        from: string;
        output: string;
        publisher: string;
        slug?: string;
        dryRun?: boolean;
        json?: boolean;
      }
    ) => {
      const platform = opts.from.toLowerCase();
      const parser = PARSERS[platform];

      if (!parser) {
        console.error(
          chalk.red(
            `✗ Unknown platform: ${platform}\n` +
              `  Supported: ${Object.keys(PARSERS).join(", ")}`
          )
        );
        process.exit(1);
      }

      // Read source file
      let content: string;
      try {
        content = readFileSync(file, "utf-8");
      } catch {
        console.error(chalk.red(`✗ Cannot read file: ${file}`));
        process.exit(1);
      }

      console.log(
        chalk.blue(`Importing from ${platform}: ${file}`)
      );

      // Parse tools
      const tools = parser(content);
      if (tools.length === 0) {
        console.error(
          chalk.red(
            `✗ No tools found in ${file}.\n` +
              `  Make sure the file contains ${platform}-format tool definitions.`
          )
        );
        process.exit(1);
      }

      console.log(chalk.green(`  Found ${tools.length} tool(s):`));
      for (const t of tools) {
        console.log(chalk.dim(`    - ${t.name}: ${t.description.slice(0, 80)}`));
      }

      // Generate slug
      const slug =
        opts.slug || generateSlug(tools[0].name);

      // Generate manifest
      const manifest = generateManifest(tools, slug, opts.publisher);

      if (opts.dryRun) {
        console.log(chalk.yellow("\n--- Generated agentnode.yaml (dry run) ---\n"));
        console.log(toYAML(manifest));
        return;
      }

      // Create output directory structure
      const outDir = join(opts.output, slug);
      const srcDir = join(outDir, "src", slug.replace(/-/g, "_"));
      const testsDir = join(outDir, "tests");

      mkdirSync(srcDir, { recursive: true });
      mkdirSync(testsDir, { recursive: true });

      // Write files
      const manifestContent = opts.json
        ? JSON.stringify(manifest, null, 2)
        : toYAML(manifest);
      const manifestFile = opts.json ? "agentnode.json" : "agentnode.yaml";

      writeFileSync(join(outDir, manifestFile), manifestContent);
      writeFileSync(join(outDir, "pyproject.toml"), generatePyprojectToml(slug));
      writeFileSync(join(srcDir, "__init__.py"), "");
      writeFileSync(join(srcDir, "tool.py"), generateToolPy(tools));
      writeFileSync(join(testsDir, "__init__.py"), "");
      writeFileSync(join(testsDir, "test_tool.py"), generateTestPy(tools, slug));

      console.log(chalk.green(`\n✓ Package generated at ${outDir}/`));
      console.log(chalk.dim(`  ${outDir}/`));
      console.log(chalk.dim(`  ├── ${manifestFile}`));
      console.log(chalk.dim(`  ├── pyproject.toml`));
      console.log(chalk.dim(`  ├── src/${slug.replace(/-/g, "_")}/`));
      console.log(chalk.dim(`  │   ├── __init__.py`));
      console.log(chalk.dim(`  │   └── tool.py        ← implement your logic here`));
      console.log(chalk.dim(`  └── tests/`));
      console.log(chalk.dim(`      └── test_tool.py`));
      console.log(
        chalk.blue(`\nNext steps:`)
      );
      console.log(
        chalk.dim(
          `  1. Implement tool logic in src/${slug.replace(/-/g, "_")}/tool.py`
        )
      );
      console.log(chalk.dim(`  2. Run: agentnode validate ${outDir}`));
      console.log(chalk.dim(`  3. Run: agentnode publish ${outDir}`));
    }
  );
