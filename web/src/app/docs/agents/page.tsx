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

const TITLE = "Agents";
const DESCRIPTION = "Agent packages on AgentNode: manifest tiers, goals and system prompts, tool access rules, and how agents are installed and run.";
const PATH = "/docs/agents";

export const metadata: Metadata = {
  title: "Agents — Docs | AgentNode",
  description: DESCRIPTION,
  alternates: { canonical: PATH },
  openGraph: {
    title: "Agents — Docs | AgentNode",
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
              AgentNode agents are self-describing AI agents packaged with a
              standardized manifest. Unlike traditional agents that hide behavior
              in code, every AgentNode agent declares its goal, behavior,
              permissions, tool access, and limits in a single{" "}
              <C>agentnode.yaml</C>.
            </p>

            <SubHeading>Agent tiers</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Each agent is classified by what it needs to run:
            </p>
            <DocTable
              headers={["Tier", "Description", "Example"]}
              rows={[
                ["llm_only", "Pure LLM reasoning — no tools, no API calls", "Blog writer, report generator"],
                ["llm_plus_tools", "LLM + AgentNode tool packs (search, extract, analyze)", "Deep research, code review"],
                ["llm_plus_credentials", "LLM + tools + external API credentials", "CRM enrichment, cloud cost analysis"],
              ]}
            />

            <SubHeading>Agent manifest fields</SubHeading>
            <p className="mb-3 text-sm text-muted">
              The <C>agent:</C> section in the manifest declares the agent
              configuration. All fields are visible on the package detail page.
            </p>
            <DocTable
              headers={["Field", "Required", "Description"]}
              rows={[
                ["entrypoint", "Yes", "Python module:function to execute the agent"],
                ["goal", "Yes", "What the agent does (shown in UI)"],
                ["system_prompt", "Recommended", "Agent behavior description (shown as 'Agent Behavior' in UI)"],
                ["tier", "No", "llm_only | llm_plus_tools | llm_plus_credentials"],
                ["tool_access.allowed_packages", "No", "List of tool packs the agent may use (empty = full registry)"],
                ["limits.max_iterations", "No", "Maximum reasoning iterations (1-100)"],
                ["limits.max_tool_calls", "No", "Maximum tool calls (1-500)"],
                ["limits.max_runtime_seconds", "No", "Maximum execution time (1-3600)"],
                ["isolation", "No", "process or thread (default: thread)"],
              ]}
            />

            <SubHeading>Installing and running an agent</SubHeading>
            <CodeBlock title="terminal">{`$ agentnode install deep-research-agent`}</CodeBlock>
            <CodeBlock title="run_agent.py" language="python">{`from agentnode_sdk import run_tool

result = run_tool("deep-research-agent",
    goal="Compare React vs Vue adoption in 2026")

print(result.result["report"])
print(result.result["sources"])`}</CodeBlock>

            <SubHeading>Agent behavior transparency</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Every agent shows its behavior description on the package page
              under &ldquo;Agent Behavior&rdquo;. This is a human-readable description
              of what the agent does — not necessarily the prompt sent to the LLM.
              It lets you evaluate an agent before installing it.
            </p>
            <p className="mb-3 text-sm text-muted">
              Combined with declared permissions, tool access, and verification
              results, you get full transparency into what an agent does, what
              it can access, and how it was tested.
            </p>

            <div className="mt-4 rounded-lg border border-primary/20 bg-primary/5 p-4">
              <p className="text-sm font-medium text-foreground">
                See the full list of available agents at{" "}
                <Link href="/agents" className="text-primary hover:underline">
                  agentnode.net/agents
                </Link>
                .
              </p>
            </div>
          </section>
      </DocsShell>
    </>
  );
}
