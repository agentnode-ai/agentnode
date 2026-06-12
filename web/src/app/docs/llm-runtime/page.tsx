import type { Metadata } from "next";
import {
  DocsShell,
  DocsJsonLd,
  SubHeading,
  CodeBlock,
  DocTable,
  C,
} from "@/components/docs";

const TITLE = "LLM Runtime";
const DESCRIPTION = "AgentNodeRuntime connects OpenAI, Anthropic, and Gemini agents to the registry: five meta-tools, automatic tool loop, runtime capability discovery.";
const PATH = "/docs/llm-runtime";

export const metadata: Metadata = {
  title: "LLM Runtime — Docs | AgentNode",
  description: DESCRIPTION,
  alternates: { canonical: PATH },
  openGraph: {
    title: "LLM Runtime — Docs | AgentNode",
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
      </DocsShell>
    </>
  );
}
