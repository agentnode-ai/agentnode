import type { Metadata } from "next";
import PackageSearch from "@/components/PackageSearch";
import { LibraryHero } from "@/components/PackageLibrary";

export const metadata: Metadata = {
  title: "Tool Packs — Executable Tools for AI Agents",
  description:
    "Browse verified tool packs: executable Python tools AI agents install and run — permission-declared, trust-tiered, and sandboxed by trust level.",
  alternates: { canonical: "/toolpacks" },
};

export const revalidate = 300;

export default function ToolpacksPage() {
  return (
    <div className="flex flex-col">
      <LibraryHero
        eyebrow="Tool Packs"
        title={
          <>
            Executable <span className="text-primary">tools</span> for your agent
          </>
        }
        subtitle="Verified Python tools your agent installs and runs — PDF extraction, web search, data analysis, and more. Permission-declared and sandboxed by trust level."
      />

      <section className="border-b border-border">
        {/* runtime is pinned too: MCP servers are package_type=toolpack with
            runtime=mcp and have their own page — this library is python packs. */}
        <PackageSearch
          fixed={{ package_type: "toolpack", runtime: "python" }}
          basePath="/toolpacks"
          heading={null}
          autoFocus={false}
        />
      </section>

      {/* Explainer below the library */}
      <section>
        <div className="mx-auto max-w-4xl px-4 sm:px-6 py-14">
          <h2 className="mb-6 text-center text-2xl font-bold text-foreground">
            What is a tool pack?
          </h2>
          <div className="grid gap-6 sm:grid-cols-3">
            <div className="rounded-xl border border-border bg-card p-6">
              <h3 className="mb-2 font-semibold text-foreground">Real code, typed interface</h3>
              <p className="text-sm leading-relaxed text-muted">
                A tool pack ships executable Python with per-tool entrypoints
                and typed JSON-Schema inputs/outputs — your agent knows exactly
                what to pass and what to expect.
              </p>
            </div>
            <div className="rounded-xl border border-border bg-card p-6">
              <h3 className="mb-2 font-semibold text-foreground">Verified &amp; permission-declared</h3>
              <p className="text-sm leading-relaxed text-muted">
                Every pack passes the publish verification pipeline and declares
                its permissions (network, filesystem, code execution) up front.
                Community code runs in a container — or not at all.
              </p>
            </div>
            <div className="rounded-xl border border-border bg-card p-6">
              <h3 className="mb-2 font-semibold text-foreground">One install away</h3>
              <p className="text-sm leading-relaxed text-muted">
                <code className="rounded bg-background px-1.5 py-0.5 font-mono text-xs">agentnode install &lt;slug&gt;</code>{" "}
                and your agent uses it via{" "}
                <code className="rounded bg-background px-1.5 py-0.5 font-mono text-xs">run_tool()</code>{" "}
                — from LangChain, CrewAI, MCP, or plain Python.
              </p>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
