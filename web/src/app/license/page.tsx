import Link from "next/link";

export const metadata = {
  title: "License — AgentNode",
  description: "AgentNode dual licensing: BSL 1.1 for the backend, MIT for CLI, SDK, and packs.",
};

const sections = [
  {
    title: "Dual License Overview",
    content: `AgentNode uses a dual-license model to balance openness with sustainability.
The CLI, SDKs, adapters, and all starter packs are MIT-licensed — free to use, modify, and redistribute without restriction.
The backend (registry, resolution engine, trust layer) is licensed under the Business Source License 1.1 (BSL).`,
  },
  {
    title: "MIT License — CLI, SDK, Packs",
    badge: "MIT",
    content: `Applies to: cli/, sdk/, adapter-mcp/, starter-packs/, action/

You are free to use, copy, modify, merge, publish, distribute, sublicense, and sell copies of these components.
The only requirement is to include the copyright notice.`,
    link: {
      label: "View full MIT License",
      href: "https://github.com/agentnode-ai/agentnode/blob/main/LICENSE-MIT",
    },
  },
  {
    title: "Business Source License 1.1 — Backend",
    badge: "BSL 1.1",
    content: `Applies to: backend/

You may read, modify, and use the backend code for any purpose — including running a private internal registry for your organization.
The only restriction: you may not use it to operate a competing hosted registry service.

On March 16, 2030 the license automatically converts to Apache 2.0, making the code fully open source.`,
    link: {
      label: "View full BSL License",
      href: "https://github.com/agentnode-ai/agentnode/blob/main/LICENSE-BSL",
    },
  },
  {
    title: "What is permitted?",
    items: [
      "Using MIT-licensed components (CLI, SDK, packs) for any purpose, commercial or personal",
      "Running the backend privately within your own organization",
      "Building integrations, plugins, and tools that connect to AgentNode",
      "Studying, modifying, and learning from all source code",
      "Development, testing, and evaluation of the full platform",
    ],
  },
  {
    title: "What requires a commercial license?",
    items: [
      "Operating a hosted registry service based on the AgentNode backend that competes with agentnode.net",
    ],
    note: "For commercial licensing inquiries, contact hello@agentnode.net",
  },
];

export default function LicensePage() {
  return (
    <main className="mx-auto max-w-4xl px-6 py-16">
      <h1 className="mb-2 text-3xl font-bold tracking-tight">
        <span className="text-primary">License</span>
      </h1>
      <p className="mb-12 text-muted">
        Open core licensing — free tools, protected platform.
      </p>

      <div className="space-y-10">
        {sections.map((section) => (
          <section
            key={section.title}
            className="rounded-lg border border-border bg-card p-6"
          >
            <div className="mb-3 flex items-center gap-3">
              <h2 className="text-lg font-semibold">{section.title}</h2>
              {section.badge && (
                <span className="rounded bg-primary/10 px-2 py-0.5 font-mono text-xs text-primary">
                  {section.badge}
                </span>
              )}
            </div>

            {section.content && (
              <div className="space-y-2 text-sm text-muted">
                {section.content.split("\n").map((line, i) => (
                  <p key={i}>{line}</p>
                ))}
              </div>
            )}

            {section.items && (
              <ul className="mt-2 space-y-1 text-sm text-muted">
                {section.items.map((item, i) => (
                  <li key={i} className="flex items-start gap-2">
                    <span className="mt-1 text-primary">+</span>
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            )}

            {section.link && (
              <a
                href={section.link.href}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-4 inline-block text-sm text-primary underline hover:text-foreground"
              >
                {section.link.label} &rarr;
              </a>
            )}

            {section.note && (
              <p className="mt-4 text-xs text-muted/70">{section.note}</p>
            )}
          </section>
        ))}
      </div>

      <div className="mt-12 text-center text-sm text-muted">
        <Link href="/" className="text-primary underline hover:text-foreground">
          &larr; Back to home
        </Link>
      </div>
    </main>
  );
}
