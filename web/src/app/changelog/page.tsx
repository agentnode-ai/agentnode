import type { Metadata } from "next";
import Link from "next/link";
import {
  RELEASES,
  LATEST,
  LAST_UPDATED,
  CHANGELOG_MD_URL,
  PYPI_PROJECT_URL,
  GITHUB_TAGS_URL,
  pypiUrl,
  tagUrl,
  anchorId,
  type ChangelogRelease,
} from "@/data/changelog";

function fmtDate(iso: string): string {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", {
    year: "numeric",
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  });
}

export const metadata: Metadata = {
  title: "Changelog — SDK Releases",
  description: `AgentNode SDK release notes: v${LATEST.version} (${fmtDate(
    LATEST.date!
  )}) — user-controlled host-trust policy, a full-coverage setup wizard, MCP network isolation, agent sandbox, and OS-keychain credential vault. Updated with every release.`,
  alternates: {
    canonical: "/changelog",
  },
  openGraph: {
    title: "Changelog — AgentNode SDK Releases",
    description: `Release notes for the AgentNode SDK. Latest: v${LATEST.version}.`,
    type: "website",
    url: "/changelog",
    siteName: "AgentNode",
  },
  twitter: {
    card: "summary",
    site: "@AgentNodenet",
  },
};

function ReleaseLinks({ release }: { release: ChangelogRelease }) {
  const pypi = pypiUrl(release);
  const tag = tagUrl(release);
  if (!pypi && !tag) return null;
  return (
    <span className="flex items-center gap-3 text-xs">
      {pypi && (
        <a
          href={pypi}
          target="_blank"
          rel="noopener noreferrer"
          className="text-primary hover:underline"
        >
          PyPI
        </a>
      )}
      {tag && (
        <a
          href={tag}
          target="_blank"
          rel="noopener noreferrer"
          className="text-primary hover:underline"
        >
          GitHub tag
        </a>
      )}
    </span>
  );
}

function ReleaseSection({ release, latest }: { release: ChangelogRelease; latest?: boolean }) {
  return (
    <section
      id={anchorId(release)}
      className={`scroll-mt-24 rounded-xl border p-6 ${
        latest ? "border-primary/40 bg-primary/5" : "border-border bg-card"
      }`}
    >
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-xl font-bold text-foreground">
          <a href={`#${anchorId(release)}`} className="hover:text-primary">
            {release.version}
          </a>{" "}
          <span className="font-semibold">— {release.title}</span>
        </h2>
        <div className="flex items-center gap-4">
          {release.date && (
            <time dateTime={release.date} className="text-sm text-muted">
              {fmtDate(release.date)}
            </time>
          )}
          <ReleaseLinks release={release} />
        </div>
      </div>

      {latest && (
        <span className="mb-3 inline-block rounded-full border border-primary/30 bg-primary/10 px-3 py-0.5 text-xs font-medium text-primary">
          Latest release
        </span>
      )}

      <p className="mb-3 text-sm leading-relaxed text-muted">{release.summary}</p>

      <ul className="space-y-1.5 text-sm text-muted">
        {release.highlights.map((h) => (
          <li key={h} className="flex gap-2">
            <span className="text-primary">·</span>
            <span>{h}</span>
          </li>
        ))}
      </ul>

      {release.breaking && (
        <p className="mt-3 rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-xs text-amber-400">
          <span className="font-semibold">Behavioral change:</span> {release.breaking}
        </p>
      )}

      {release.notes && <p className="mt-3 text-xs text-muted/70">{release.notes}</p>}
    </section>
  );
}

export default function ChangelogPage() {
  const [latest, ...rest] = RELEASES;

  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "SoftwareApplication",
    name: "AgentNode SDK",
    applicationCategory: "DeveloperApplication",
    operatingSystem: "Windows, macOS, Linux",
    softwareVersion: LATEST.version,
    downloadUrl: PYPI_PROJECT_URL,
    releaseNotes: "https://agentnode.net/changelog",
    dateModified: LAST_UPDATED,
    offers: { "@type": "Offer", price: "0", priceCurrency: "USD" },
    publisher: {
      "@type": "Organization",
      name: "AgentNode",
      url: "https://agentnode.net",
    },
  };

  const breadcrumbLd = {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: [
      { "@type": "ListItem", position: 1, name: "Home", item: "https://agentnode.net/" },
      { "@type": "ListItem", position: 2, name: "Changelog", item: "https://agentnode.net/changelog" },
    ],
  };

  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }} />
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(breadcrumbLd) }} />

      <div className="mx-auto max-w-3xl px-4 sm:px-6 py-16">
        <header className="mb-10">
          <h1 className="mb-4 text-4xl font-bold tracking-tight text-foreground sm:text-5xl">
            AgentNode Changelog
          </h1>
          <p className="mb-3 text-lg leading-relaxed text-muted">
            Release notes for the AgentNode SDK — the package manager and secure runtime for AI
            agent skills. The current release is{" "}
            <a href={`#${anchorId(LATEST)}`} className="font-semibold text-primary hover:underline">
              v{LATEST.version}
            </a>{" "}
            ({fmtDate(LATEST.date!)}). Upgrade with{" "}
            <code className="rounded bg-card px-1.5 py-0.5 text-sm text-primary">
              pip install --upgrade agentnode-sdk
            </code>
            .
          </p>
          <p className="text-sm text-muted/70">
            Last updated: <time dateTime={LAST_UPDATED}>{fmtDate(LAST_UPDATED)}</time> · Full
            engineering changelog:{" "}
            <a
              href={CHANGELOG_MD_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary hover:underline"
            >
              CHANGELOG.md on GitHub
            </a>
          </p>
        </header>

        <div className="space-y-6">
          <ReleaseSection release={latest} latest />
          {rest.map((release) => (
            <ReleaseSection key={release.version} release={release} />
          ))}
        </div>

        <footer className="mt-12 space-y-4 border-t border-border pt-8 text-sm text-muted">
          <p>
            Earlier releases (0.1.0 – 0.4.0, March 2026) are available on{" "}
            <a
              href={PYPI_PROJECT_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary hover:underline"
            >
              PyPI
            </a>{" "}
            and as{" "}
            <a
              href={GITHUB_TAGS_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary hover:underline"
            >
              git tags
            </a>
            .
          </p>
          <p>
            New to AgentNode? Start with the{" "}
            <Link href="/getting-started" className="text-primary hover:underline">
              Getting Started guide
            </Link>
            , browse the{" "}
            <Link href="/docs" className="text-primary hover:underline">
              documentation
            </Link>
            , or see which of the{" "}
            <Link href="/compatibility" className="text-primary hover:underline">
              182 supported LLM models
            </Link>{" "}
            fit your stack.
          </p>
        </footer>
      </div>
    </>
  );
}
