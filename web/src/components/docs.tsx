// Shared building blocks for the /docs pages (extracted from the former
// docs monolith). Server-safe — no client hooks in this module.
import DocsNav from "@/components/DocsNav";

export const DOCS_UPDATED = "2026-06-12";
export const DOCS_APPLIES = "Applies to SDK 0.16+";

export interface DocsNavEntry {
  href: string;
  label: string;
}

export interface DocsNavGroup {
  title: string;
  entries: DocsNavEntry[];
}

export const DOCS_NAV: DocsNavGroup[] = [
  {
    title: "Start",
    entries: [
      { href: "/docs/quickstart", label: "Quick Start" },
      { href: "/docs/installation", label: "Installation" },
      { href: "/docs/discovery", label: "Search & Discovery" },
      { href: "/docs/packages", label: "Installing Packages" },
    ],
  },
  {
    title: "Runtime & Security",
    entries: [
      { href: "/docs/llm-runtime", label: "LLM Runtime" },
      { href: "/docs/llm-providers", label: "LLM Providers" },
      { href: "/docs/agents", label: "Agents" },
      { href: "/docs/sandbox", label: "Execution Sandbox" },
      { href: "/docs/security", label: "Security Model" },
      { href: "/docs/credentials", label: "Credentials & Connectors" },
      { href: "/docs/guard", label: "AgentNode Guard" },
      { href: "/docs/trust", label: "Trust & Security" },
      { href: "/docs/data-sovereignty", label: "Data Sovereignty" },
    ],
  },
  {
    title: "Publishing & Registry",
    entries: [
      { href: "/docs/publishing", label: "Publishing Guide" },
      { href: "/docs/verification", label: "Package Verification" },
      { href: "/docs/manifest", label: "ANP Manifest Reference" },
      { href: "/docs/github-action", label: "GitHub Action" },
      { href: "/docs/import", label: "Import Tools" },
    ],
  },
  {
    title: "Reference",
    entries: [
      { href: "/docs/cli", label: "CLI Reference" },
      { href: "/docs/python-sdk", label: "Python SDK" },
      { href: "/docs/rest-api", label: "REST API" },
      { href: "/docs/mcp", label: "MCP Integration" },
    ],
  },
];

/* ------------------------------------------------------------------ */
/*  Content building blocks                                            */
/* ------------------------------------------------------------------ */

export function CodeBlock({
  title,
  children,
  language,
}: {
  title: string;
  children: string;
  language?: string;
}) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-[#0d1117]">
      <div className="flex items-center gap-2 border-b border-border/50 px-4 py-2">
        <div className="h-3 w-3 rounded-full bg-red-500/60" />
        <div className="h-3 w-3 rounded-full bg-yellow-500/60" />
        <div className="h-3 w-3 rounded-full bg-green-500/60" />
        <span className="ml-2 font-mono text-xs text-muted">{title}</span>
        {language && (
          <span className="ml-auto font-mono text-xs text-muted/50">
            {language}
          </span>
        )}
      </div>
      <pre className="overflow-x-auto p-4 font-mono text-sm leading-relaxed text-gray-300">
        <code>{children}</code>
      </pre>
    </div>
  );
}

export function C({ children }: { children: React.ReactNode }) {
  return (
    <code className="rounded bg-background px-1.5 py-0.5 font-mono text-xs text-primary">
      {children}
    </code>
  );
}

export function SectionHeading({
  id,
  children,
}: {
  id: string;
  children: React.ReactNode;
}) {
  return (
    <h2
      id={id}
      className="mb-6 scroll-mt-24 border-b border-border pb-4 text-2xl font-bold text-foreground"
    >
      {children}
    </h2>
  );
}

export function SubHeading({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="mb-3 mt-8 text-lg font-semibold text-foreground">
      {children}
    </h3>
  );
}

export function DocTable({
  headers,
  rows,
}: {
  headers: string[];
  rows: string[][];
}) {
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border bg-card">
            {headers.map((h) => (
              <th
                key={h}
                className="px-4 py-3 text-left font-semibold text-foreground"
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr
              key={i}
              className={i < rows.length - 1 ? "border-b border-border/50" : ""}
            >
              {row.map((cell, j) => (
                <td
                  key={j}
                  className={`px-4 py-3 ${
                    j === 0 ? "font-mono text-primary text-xs" : "text-muted"
                  }`}
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Page frame + structured data                                       */
/* ------------------------------------------------------------------ */

export function DocsShell({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="mx-auto max-w-6xl px-4 sm:px-6 py-12">
      <div className="flex gap-10">
        <DocsNav groups={DOCS_NAV} />
        <main className="min-w-0 flex-1">
          <h1 className="mb-2 text-3xl font-bold text-foreground">{title}</h1>
          <p className="mb-8 text-xs text-muted/70">
            {DOCS_APPLIES} · Last updated: {DOCS_UPDATED}
          </p>
          <div className="space-y-16">{children}</div>
        </main>
      </div>
    </div>
  );
}

export function DocsJsonLd({
  title,
  description,
  path,
}: {
  title: string;
  description: string;
  path: string;
}) {
  const articleLd = {
    "@context": "https://schema.org",
    "@type": "TechArticle",
    headline: title,
    description,
    dateModified: DOCS_UPDATED,
    author: { "@type": "Organization", name: "AgentNode", url: "https://agentnode.net" },
    publisher: { "@type": "Organization", name: "AgentNode", url: "https://agentnode.net" },
    mainEntityOfPage: { "@type": "WebPage", "@id": `https://agentnode.net${path}` },
  };
  const breadcrumbLd = {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: [
      { "@type": "ListItem", position: 1, name: "Docs", item: "https://agentnode.net/docs" },
      { "@type": "ListItem", position: 2, name: title, item: `https://agentnode.net${path}` },
    ],
  };
  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(articleLd) }}
      />
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(breadcrumbLd) }}
      />
    </>
  );
}
