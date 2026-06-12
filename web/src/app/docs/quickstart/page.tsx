import type { Metadata } from "next";
import Link from "next/link";
import {
  DocsShell,
  DocsJsonLd,
  SectionHeading,
  SubHeading,
  CodeBlock,
  DocTable,
  C,
} from "@/components/docs";

const TITLE = "Quick Start";
const DESCRIPTION = "Install the AgentNode SDK, search the registry, and run your first verified agent skill in minutes — CLI and Python examples included.";
const PATH = "/docs/quickstart";

export const metadata: Metadata = {
  title: "Quick Start — Docs | AgentNode",
  description: DESCRIPTION,
  alternates: { canonical: PATH },
  openGraph: {
    title: "Quick Start — Docs | AgentNode",
    description: DESCRIPTION,
    type: "website",
    url: PATH,
    siteName: "AgentNode",
  },
};

export default function Page() {
  return (
    <>
      <DocsJsonLd title={TITLE} description={DESCRIPTION} path={PATH} />
      <DocsShell title={TITLE}>
          <section>
            <p className="mb-4 text-sm leading-relaxed text-muted">
              Go from zero to using your first AI agent capability in under five
              minutes. This walkthrough installs the CLI, searches the registry,
              installs a pack, and uses it in Python code.
            </p>

            <SubHeading>1. Install the SDK</SubHeading>
            <CodeBlock title="terminal">{`$ pip install agentnode-sdk`}</CodeBlock>

            <SubHeading>2. Set up</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Run the setup wizard, or skip it — sensible defaults work out of
              the box. No account needed for searching, installing, or running
              packages. For publishing, register at{" "}
              <Link href="/auth/register" className="text-primary hover:underline">
                agentnode.net/auth/register
              </Link>{" "}
              and create an API key in your dashboard.
            </p>
            <CodeBlock title="terminal">{`$ agentnode setup`}</CodeBlock>

            <SubHeading>3. Search for a capability</SubHeading>
            <CodeBlock title="terminal">{`$ agentnode search "pdf extraction"

Results for "pdf extraction":

  pdf-reader-pack          v1.0.0  trusted   Extract text, tables, and metadata from PDFs
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

# Run with automatic subprocess isolation (all trust levels)
result = run_tool("pdf-reader-pack", file_path="quarterly-report.pdf")
print(result.result["text"])
print(result.mode_used)  # "subprocess" (default) or "direct" (explicit opt-in)

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
                <C>run_tool()</C> then executes with automatic subprocess
                isolation for all trust levels.
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
                ["run_tool()", "Always runs in subprocess isolation by default (mode='auto'). Pass mode='direct' to opt into in-process execution. Returns RunToolResult with output, timing, and mode used"],
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
      </DocsShell>
    </>
  );
}
