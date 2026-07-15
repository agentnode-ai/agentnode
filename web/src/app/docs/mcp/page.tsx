import type { Metadata } from "next";
import {
  DocsShell,
  DocsJsonLd,
  SubHeading,
  CodeBlock,
  DocTable,
  C,
} from "@/components/docs";

const TITLE = "MCP Integration";
const DESCRIPTION = "Use AgentNode through the Model Context Protocol: pack servers and the platform server, with Claude and Cursor configuration examples.";
const PATH = "/docs/mcp";

export const metadata: Metadata = {
  title: "MCP Integration — Docs | AgentNode",
  description: DESCRIPTION,
  alternates: { canonical: PATH },
  openGraph: {
    title: "MCP Integration — Docs | AgentNode",
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

            <div className="mb-4 rounded-lg border border-primary/30 bg-primary/5 px-4 py-3 text-sm text-muted">
              Building your <span className="font-medium text-foreground/80">own</span> MCP server and want
              to list it in the AgentNode registry? That is a separate flow — see the{" "}
              <a href="/docs/mcp-publishing" className="text-primary hover:underline">
                Publish &amp; verify an MCP server
              </a>{" "}
              guide.
            </div>

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
      </DocsShell>
    </>
  );
}
