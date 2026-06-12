import type { Metadata } from "next";
import Link from "next/link";
import {
  DocsShell,
  DocsJsonLd,
  SubHeading,
  CodeBlock,
  DocTable,
  C,
} from "@/components/docs";

const TITLE = "Import Tools";
const DESCRIPTION = "Convert existing tools into ANP packages: importers for MCP, LangChain, OpenAI Functions, CrewAI and more, with the agentnode import CLI.";
const PATH = "/docs/import";

export const metadata: Metadata = {
  title: "Import Tools — Docs | AgentNode",
  description: DESCRIPTION,
  alternates: { canonical: PATH },
  openGraph: {
    title: "Import Tools — Docs | AgentNode",
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
                ["ClawHub", "ClawHub manifest files", "agentnode import manifest.json --from clawhub"],
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
      </DocsShell>
    </>
  );
}
