import Link from "next/link";

interface FooterLink {
  href: string;
  label: string;
  external?: boolean;
}
interface FooterColumn {
  title: string;
  links: FooterLink[];
}

const FOOTER_COLUMNS: FooterColumn[] = [
  {
    title: "Product",
    links: [
      { href: "/why-agentnode", label: "Why AgentNode" },
      { href: "/getting-started", label: "Getting Started" },
      { href: "/compatibility", label: "Model Compatibility" },
      { href: "/security", label: "Security" },
      { href: "/for-companies", label: "For Companies" },
      { href: "/for-agents", label: "For Agents" },
      { href: "/changelog", label: "Changelog" },
    ],
  },
  {
    title: "Browse",
    links: [
      { href: "/search", label: "Search" },
      { href: "/toolpacks", label: "Tool Packs" },
      { href: "/skills", label: "Skills" },
      { href: "/agents", label: "Agents" },
      { href: "/mcp", label: "MCP Servers" },
      { href: "/discover", label: "Trending" },
      { href: "/compare", label: "Compare" },
    ],
  },
  {
    title: "Publish",
    links: [
      { href: "/for-developers", label: "Why publish" },
      { href: "/publish", label: "Publish a package" },
      { href: "/builder", label: "Builder" },
      { href: "/import", label: "Import existing tools" },
      { href: "/docs/verification", label: "Verification & tiers" },
    ],
  },
  {
    title: "Docs & Resources",
    links: [
      { href: "/docs", label: "Docs" },
      { href: "/blog", label: "Blog" },
      { href: "/tutorials", label: "Tutorials" },
      { href: "/case-studies", label: "Case Studies" },
      { href: "/faq", label: "FAQ" },
    ],
  },
  {
    title: "Legal & Social",
    links: [
      { href: "/license", label: "License" },
      { href: "https://github.com/agentnode-ai/agentnode", label: "GitHub", external: true },
      { href: "https://x.com/AgentNodenet", label: "X (Twitter)", external: true },
      { href: "https://www.reddit.com/r/AgentNode/", label: "Reddit", external: true },
    ],
  },
];

export default function Footer() {
  return (
    <footer className="border-t border-border bg-background">
      <div className="mx-auto max-w-6xl px-6 py-12">
        <div className="grid grid-cols-2 gap-8 sm:grid-cols-3 lg:grid-cols-5">
          {FOOTER_COLUMNS.map((col) => (
            <div key={col.title}>
              <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-foreground">
                {col.title}
              </h3>
              <ul className="space-y-2">
                {col.links.map((link) => (
                  <li key={link.href}>
                    {link.external ? (
                      <a
                        href={link.href}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-sm text-muted transition-colors hover:text-foreground"
                      >
                        {link.label}
                      </a>
                    ) : (
                      <Link
                        href={link.href}
                        className="text-sm text-muted transition-colors hover:text-foreground"
                      >
                        {link.label}
                      </Link>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </div>

      <div className="border-t border-border">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-3 px-6 py-4 text-xs text-muted sm:flex-row">
          <div className="flex items-center gap-2">
            <span className="font-mono text-primary">&gt;_</span>
            <span>AgentNode</span>
            <span className="text-border">|</span>
            <span>Where agents find upgrades — powered by ANP</span>
          </div>
          <div className="flex flex-wrap items-center justify-center gap-x-4 gap-y-1">
            <span>&copy; {new Date().getFullYear()} AgentNode. All rights reserved.</span>
            <span>
              Backend: <Link href="/license" className="underline hover:text-foreground">BSL 1.1</Link>
              {" · "}
              SDK &amp; Packs: <Link href="/license" className="underline hover:text-foreground">MIT</Link>
            </span>
          </div>
        </div>
      </div>
    </footer>
  );
}
