import Link from "next/link";

export default function Footer() {
  return (
    <footer className="border-t border-border bg-background">
      <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 px-6 py-8 sm:flex-row">
        <div className="flex items-center gap-2 text-sm text-muted">
          <span className="font-mono text-primary">&gt;_</span>
          <span>AgentNode</span>
          <span className="text-border">|</span>
          <span>Where agents find upgrades</span>
        </div>
        <div className="flex items-center gap-6 text-sm text-muted">
          <Link href="/search" className="transition-colors hover:text-foreground">
            Search
          </Link>
          <Link href="/docs" className="transition-colors hover:text-foreground">
            Docs
          </Link>
          <a
            href="https://github.com/agentnode-ai/agentnode"
            target="_blank"
            rel="noopener noreferrer"
            className="transition-colors hover:text-foreground"
          >
            GitHub
          </a>
        </div>
      </div>
    </footer>
  );
}
