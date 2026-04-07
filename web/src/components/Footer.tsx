import Link from "next/link";

export default function Footer() {
  return (
    <footer className="border-t border-border bg-background">
      <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 px-6 py-8 sm:flex-row">
        <div className="flex items-center gap-2 text-sm text-muted">
          <span className="font-mono text-primary">&gt;_</span>
          <span>AgentNode</span>
          <span className="text-border">|</span>
          <span>Where agents find upgrades — powered by ANP</span>
        </div>
        <div className="flex flex-wrap items-center justify-center gap-x-6 gap-y-2 text-sm text-muted">
          <Link href="/search" className="transition-colors hover:text-foreground">
            Search
          </Link>
          <Link href="/capabilities" className="transition-colors hover:text-foreground">
            Capabilities
          </Link>
          <Link href="/compatibility" className="transition-colors hover:text-foreground">
            Models
          </Link>
          <Link href="/for-developers" className="transition-colors hover:text-foreground">
            For Developers
          </Link>
          <Link href="/why-agentnode" className="transition-colors hover:text-foreground">
            Why AgentNode
          </Link>
          <Link href="/blog" className="transition-colors hover:text-foreground">
            Blog
          </Link>
          <Link href="/docs" className="transition-colors hover:text-foreground">
            Docs
          </Link>
          <Link href="/license" className="transition-colors hover:text-foreground">
            License
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
      <div className="border-t border-border">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4 text-xs text-muted">
          <span>&copy; {new Date().getFullYear()} AgentNode. All rights reserved.</span>
          <div className="flex items-center gap-4">
            <span>
              Backend: <Link href="/license" className="underline hover:text-foreground">BSL 1.1</Link>
              {" · "}
              SDK &amp; Packs: <Link href="/license" className="underline hover:text-foreground">MIT</Link>
            </span>
            <div className="flex items-center gap-3">
              <a
                href="https://x.com/AgentNodenet"
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary transition-colors hover:text-foreground"
                aria-label="X (Twitter)"
              >
                <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z" />
                </svg>
              </a>
              <a
                href="https://www.reddit.com/r/AgentNode/"
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary transition-colors hover:text-foreground"
                aria-label="Reddit"
              >
                <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M12 0C5.373 0 0 5.373 0 12s5.373 12 12 12 12-5.373 12-12S18.627 0 12 0zm6.066 13.666c.005.145.005.29-.001.434C18.065 16.845 15.344 19 12 19s-6.066-2.155-6.066-4.9c-.005-.144-.005-.29.001-.434a1.205 1.205 0 01-.497-.98 1.222 1.222 0 012.218-.722c1.15-.778 2.752-1.267 4.56-1.308l.857-3.964a.264.264 0 01.312-.198l2.813.613a.86.86 0 011.69.18.86.86 0 01-.86.86.86.86 0 01-.852-.744l-2.508-.547-.756 3.494c1.788.05 3.37.538 4.506 1.308a1.222 1.222 0 012.218.722 1.205 1.205 0 01-.497.98zM9.08 14.1a1.08 1.08 0 002.16 0 1.08 1.08 0 00-2.16 0zm5.84 2.428c-.088.088-.462.372-1.44.54-.524.09-1.11.135-1.48.135s-.956-.045-1.48-.135c-.978-.168-1.352-.452-1.44-.54a.354.354 0 01.5-.5c.02.02.378.278 1.216.404.468.07.992.105 1.204.105s.736-.035 1.204-.105c.838-.126 1.196-.384 1.216-.404a.354.354 0 01.5.5zm-.36-1.348a1.08 1.08 0 000-2.16 1.08 1.08 0 000 2.16z" />
                </svg>
              </a>
            </div>
          </div>
        </div>
      </div>
    </footer>
  );
}
