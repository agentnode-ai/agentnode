import Link from "next/link";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import TrustBadge from "@/components/TrustBadge";
import VerificationBadgeShared from "@/components/VerificationBadge";
import { BACKEND_URL } from "@/lib/constants";
import CodeBlockWrapper from "./CodeBlockWrapper";
import QuickStartWrapper from "./QuickStartWrapper";
import ReadmeSection from "./ReadmeSection";
import VerificationMainPanel from "./VerificationMainPanel";
import AgentInfoPanel from "./AgentInfoPanel";
import FileBrowserWrapper from "./FileBrowserWrapper";
import VersionHistory from "./VersionHistory";
import VersionSelector from "./VersionSelector";
import OwnerActions from "./OwnerActions";

interface PageProps {
  params: Promise<{ slug: string }>;
  searchParams: Promise<{ v?: string }>;
}

/* eslint-disable @typescript-eslint/no-explicit-any */
async function fetchPackage(slug: string, version?: string): Promise<any | null> {
  try {
    const baseUrl = BACKEND_URL;
    const vParam = version ? `?v=${encodeURIComponent(version)}` : "";
    const res = await fetch(
      `${baseUrl}/v1/packages/${encodeURIComponent(slug)}${vParam}`,
      { next: { revalidate: 60 } }
    );
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

async function fetchInstallInfo(slug: string): Promise<any | null> {
  try {
    const res = await fetch(
      `${BACKEND_URL}/v1/packages/${encodeURIComponent(slug)}/install-info`,
      { next: { revalidate: 60 } }
    );
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

async function fetchVersions(slug: string): Promise<any[]> {
  try {
    const baseUrl = BACKEND_URL;
    const res = await fetch(
      `${baseUrl}/v1/packages/${encodeURIComponent(slug)}/versions`,
      { next: { revalidate: 120 } }
    );
    if (!res.ok) return [];
    const data = await res.json();
    return data.versions ?? [];
  } catch {
    return [];
  }
}

import { timeAgo } from "@/lib/time";

// Absolute origin for JSON-LD url/author.url (relative paths aren't valid there).
const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || "https://agentnode.net";

// Common SPDX identifiers we are confident enough about to emit as an SPDX URL.
// Anything else falls back to plain text (or is omitted) — never a pricing claim.
const SPDX_LICENSES = new Set([
  "MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "GPL-2.0", "GPL-3.0",
  "LGPL-2.1", "LGPL-3.0", "AGPL-3.0", "MPL-2.0", "ISC", "Unlicense",
  "BSL-1.0", "CC0-1.0", "CC-BY-4.0",
]);

// Single source of truth for the package type label, shared by <title> and the
// SoftwareApplication JSON-LD. Derived specials (MCP, character, connector) are
// checked BEFORE the generic package_type enum (agent | toolpack | upgrade) so
// they are not masked (e.g. a character modeled as package_type "agent" + tag).
function packageTypeLabel(pkg: any): string {
  const tags: string[] = pkg.tags ?? [];
  return pkg.blocks?.compatibility?.runtime === "mcp"
    ? "MCP Server"
    : tags.some((t: string) => t === "character" || t === "persona")
      ? "AI Character"
      : pkg.blocks?.connector
        ? "Connector"
        : pkg.package_type === "agent"
          ? "AI Agent"
          : pkg.package_type === "skill"
            ? "Agent Skill"
            : pkg.package_type === "toolpack"
              ? "Agent Tool Pack"
              : pkg.package_type === "upgrade"
                ? "Agent Upgrade"
                : "Agent Package";
}

// Build conservative, claim-clean SoftwareApplication JSON-LD. Only neutral,
// data-backed facts — deliberately NO aggregateRating / review / offers / price /
// isAccessibleForFree / downloadUrl / installUrl and NO trust, verification,
// sandbox or permission signals. operatingSystem IS set for cross-platform
// runtimes (python/mcp): together with applicationCategory it is the one honest
// attribute that satisfies Google's "two or more of offers / aggregateRating /
// applicationCategory / operatingSystem" rule without faking ratings or prices.
function buildPackageJsonLd(pkg: any, slug: string) {
  const description =
    pkg.summary ||
    `${pkg.name} — an agent package on AgentNode. Install it in compatible AI agent frameworks.`;

  const ld: Record<string, unknown> = {
    "@context": "https://schema.org",
    "@type": "SoftwareApplication",
    name: pkg.name,
    description,
    url: `${SITE_URL}/packages/${slug}`,
    applicationCategory: "DeveloperApplication",
    applicationSubCategory: packageTypeLabel(pkg),
  };

  const version = pkg.latest_version?.version_number;
  if (version) ld.softwareVersion = version;

  const publishedAt = pkg.latest_version?.published_at;
  if (publishedAt) ld.datePublished = publishedAt;

  // Neutral runtime requirement — no credential / provider / Docker / sandbox claim.
  const runtime = pkg.blocks?.compatibility?.runtime;
  if (runtime === "mcp") ld.softwareRequirements = "Model Context Protocol (MCP) host";
  else if (runtime === "python") ld.softwareRequirements = "Python";

  // Cross-platform runtimes only (python, mcp). Honest, neutral; second qualifying
  // attribute (with applicationCategory) for Google's "two or more" rule.
  if (runtime === "python" || runtime === "mcp") {
    ld.operatingSystem = "macOS, Windows, Linux";
  }

  // SPDX URL when recognized, otherwise plain text; never interpreted as price/free.
  const lic = typeof pkg.license_model === "string" ? pkg.license_model.trim() : "";
  if (lic) {
    ld.license = SPDX_LICENSES.has(lic)
      ? `https://spdx.org/licenses/${lic}.html`
      : lic;
  }

  const tags: string[] = pkg.tags ?? [];
  if (tags.length) ld.keywords = tags.join(", ");

  const publisher = pkg.publisher ?? {};
  const authorName = publisher.display_name || publisher.name;
  if (authorName) {
    const author: Record<string, unknown> = {
      "@type": "Organization",
      name: authorName,
    };
    if (publisher.slug) author.url = `${SITE_URL}/publishers/${publisher.slug}`;
    ld.author = author;
  }

  const sameAs = [pkg.source_url, pkg.homepage_url].filter(
    (u: unknown): u is string => typeof u === "string" && u.length > 0
  );
  if (sameAs.length) ld.sameAs = sameAs;

  return ld;
}

export async function generateMetadata({ params, searchParams }: PageProps): Promise<Metadata> {
  const { slug } = await params;
  const { v } = await searchParams;
  const pkg = await fetchPackage(slug, v);
  if (!pkg) return { title: "Package Not Found" };

  // Type-aware title — shares packageTypeLabel() with the JSON-LD so the
  // <title> and structured data never drift. No "AgentNode" in the string;
  // the root layout template appends "| AgentNode".
  const title = `${pkg.name} — ${packageTypeLabel(pkg)}`;
  const description = pkg.summary || `${pkg.name} — an agent package on AgentNode. Install it in compatible AI agent frameworks.`;

  // P1-SEO2: Set a canonical URL for each package detail page.
  // Without this, Google indexes the `?v=<version>` variant and the
  // slug-only URL as separate pages, splitting PageRank. Canonical
  // always points to the slug-only form; the version-scoped URL is
  // rendered as a visible "you are viewing vX.Y.Z" badge elsewhere.
  const canonicalPath = `/packages/${slug}`;

  return {
    title,
    description,
    alternates: {
      canonical: canonicalPath,
    },
    openGraph: {
      title: `${pkg.name} | AgentNode`,
      description,
      type: "website",
      url: canonicalPath,
      siteName: "AgentNode",
    },
  };
}

function PermissionLevel({ value }: { value: string }) {
  const color =
    value === "none"
      ? "bg-green-500/10 text-green-400"
      : value === "unrestricted" || value === "shell" || value === "any"
        ? "bg-red-500/10 text-red-400"
        : "bg-yellow-500/10 text-yellow-400";
  return (
    <span className={`rounded-md px-2.5 py-0.5 text-xs font-mono ${color}`}>
      {value}
    </span>
  );
}

function FrameworkBadge({ name, tested }: { name: string; tested?: boolean }) {
  return (
    <span
      className={`rounded-md px-2.5 py-1 text-xs font-medium border ${
        tested
          ? "bg-green-500/10 border-green-500/20 text-green-400"
          : "bg-card border-border text-muted"
      }`}
    >
      {name}
      {tested && (
        <span className="ml-1 text-[9px] text-green-500">tested</span>
      )}
    </span>
  );
}

function VerificationBadge({ verification }: { verification: any }) {
  if (!verification) return null;

  // Use tier-based display when tier is available
  if (verification.tier) {
    return (
      <VerificationBadgeShared
        tier={verification.tier}
        score={verification.score}
        smoke_reason={verification.smoke_reason}
        size="md"
      />
    );
  }

  // Legacy fallback: status-based display
  const status = verification.status;
  if (status === "verified" || status === "passed") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-green-500/10 border border-green-500/20 px-3 py-1 text-xs font-medium text-green-400">
        <span>&#10004;</span> Verified
      </span>
    );
  }
  if (status === "failed") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-red-500/10 border border-red-500/20 px-3 py-1 text-xs font-medium text-red-400">
        <span>&#10006;</span> Failed
      </span>
    );
  }
  if (status === "running" || status === "pending") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-yellow-500/10 border border-yellow-500/20 px-3 py-1 text-xs font-medium text-yellow-400">
        <span className="inline-block h-1.5 w-1.5 rounded-full bg-yellow-400 animate-pulse" />
        Verifying
      </span>
    );
  }
  return null;
}

export default async function PackageDetailPage({ params, searchParams }: PageProps) {
  const { slug } = await params;
  const { v } = await searchParams;
  const [pkg, versions, installInfo] = await Promise.all([
    fetchPackage(slug, v),
    fetchVersions(slug),
    fetchInstallInfo(slug),
  ]);

  if (!pkg) {
    notFound();
  }

  const blocks = pkg.blocks ?? {};
  const publisher = pkg.publisher ?? {};
  const latestVersion = pkg.latest_version;
  const version = latestVersion?.version_number ?? "unknown";
  const publishedAt = latestVersion?.published_at;
  const capabilities = blocks.capabilities ?? [];
  const prompts = blocks.prompts ?? [];
  const resources = blocks.resources ?? [];
  const connector = blocks.connector;
  const recommendedFor = blocks.recommended_for ?? [];
  const install = blocks.install ?? {};
  const compat = blocks.compatibility ?? {};
  const perms = blocks.permissions;
  const trust = blocks.trust ?? {};
  const verification = pkg.verification;

  // MCP detection
  const isMcp = compat.runtime === "mcp";
  const mcpServer = installInfo?.mcp_server ?? null;
  const mcpCommand = mcpServer?.command ?? [];
  const mcpEnvKeys: string[] = mcpServer?.env_keys ?? [];
  const mcpNpmPackage = mcpServer?.npm_package ?? null;
  const mcpSourceRepo = mcpServer?.source_repo ?? null;
  const mcpTransport = mcpServer?.transport ?? "stdio";

  // Derive UI category from package_type + tags
  const pkgTags: string[] = pkg.tags ?? [];
  const uiCategory = pkg.package_type === "agent"
    ? "agent"
    : pkgTags.some((t: string) => t === "character" || t === "persona")
      ? "character"
      : connector
        ? "connector"
        : null;

  const CATEGORY_BADGE: Record<string, { bg: string; border: string; text: string; label: string }> = {
    agent: { bg: "bg-blue-500/10", border: "border-blue-500/20", text: "text-blue-400", label: "Agent" },
    character: { bg: "bg-purple-500/10", border: "border-purple-500/20", text: "text-purple-400", label: "Character" },
    connector: { bg: "bg-orange-500/10", border: "border-orange-500/20", text: "text-orange-400", label: "Connector" },
  };

  const jsonLd = buildPackageJsonLd(pkg, slug);

  return (
    <div className="mx-auto max-w-6xl px-4 sm:px-6 py-12">
      {/* SoftwareApplication structured data (conservative, claim-clean) */}
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: JSON.stringify(jsonLd).replace(/</g, "\\u003c"),
        }}
      />
      {/* Breadcrumb */}
      <nav className="mb-6 text-sm text-muted">
        <Link href="/search" className="hover:text-foreground transition-colors">
          Packages
        </Link>
        <span className="mx-2">/</span>
        <span className="text-foreground">{pkg.slug}</span>
        {v && (
          <>
            <span className="mx-2">/</span>
            <span className="text-foreground">v{v}</span>
          </>
        )}
      </nav>

      {/* Quarantine / Under Review banner */}
      {pkg.quarantine_status === "quarantined" && (
        <div className="mb-6 rounded-lg border border-yellow-500/30 bg-yellow-500/10 px-4 py-3">
          <p className="text-sm font-medium text-yellow-400">Under Review</p>
          <p className="text-xs text-yellow-400/80 mt-1">
            This package is being reviewed before it becomes publicly available.
            This usually happens automatically after verification passes.
          </p>
        </div>
      )}

      {/* Deprecation warning */}
      {pkg.is_deprecated && (
        <div className="mb-6 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-400">
          This package has been deprecated and is no longer maintained.
        </div>
      )}

      {/* Header */}
      <header className="mb-10 border-b border-border pb-8">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <div className="flex items-center gap-3 flex-wrap">
              <h1 className="font-mono text-xl font-bold text-foreground sm:text-3xl break-words">
                {pkg.name}
              </h1>
              <TrustBadge level={publisher.trust_level ?? "unverified"} size="md" />
              {versions.length > 1 ? (
                <VersionSelector
                  slug={pkg.slug}
                  currentVersion={version}
                  versions={versions}
                />
              ) : (
                <span className="rounded-md bg-card px-2.5 py-1 text-xs font-mono text-muted border border-border">
                  v{version}
                </span>
              )}
              {pkg.license_model && (
                <span className="rounded-md bg-card px-2.5 py-1 text-xs font-mono text-muted border border-border">
                  {pkg.license_model}
                </span>
              )}
              <VerificationBadge verification={verification} />
              {uiCategory && CATEGORY_BADGE[uiCategory] && (
                <span className={`inline-flex items-center rounded-full ${CATEGORY_BADGE[uiCategory].bg} border ${CATEGORY_BADGE[uiCategory].border} px-3 py-1 text-xs font-medium ${CATEGORY_BADGE[uiCategory].text}`}>
                  {CATEGORY_BADGE[uiCategory].label}
                </span>
              )}
            </div>

            {/* Review badges (per-version) */}
            {(latestVersion?.security_reviewed_at || latestVersion?.compatibility_reviewed_at || latestVersion?.manually_reviewed_at) && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {latestVersion.security_reviewed_at && (
                  <span
                    className="inline-flex items-center gap-1 rounded-full bg-blue-500/10 border border-blue-500/20 px-2.5 py-0.5 text-[11px] font-medium text-blue-400"
                    title={`Security reviewed on ${new Date(latestVersion.security_reviewed_at).toLocaleDateString()}`}
                  >
                    Security Reviewed
                  </span>
                )}
                {latestVersion.compatibility_reviewed_at && (
                  <span
                    className="inline-flex items-center gap-1 rounded-full bg-purple-500/10 border border-purple-500/20 px-2.5 py-0.5 text-[11px] font-medium text-purple-400"
                    title={`Compatibility reviewed on ${new Date(latestVersion.compatibility_reviewed_at).toLocaleDateString()}`}
                  >
                    Compatibility Reviewed
                  </span>
                )}
                {latestVersion.manually_reviewed_at && (
                  <span
                    className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 border border-emerald-500/20 px-2.5 py-0.5 text-[11px] font-medium text-emerald-400"
                    title={`Manually reviewed on ${new Date(latestVersion.manually_reviewed_at).toLocaleDateString()}`}
                  >
                    Manually Reviewed
                  </span>
                )}
              </div>
            )}

            <p className="mt-2 text-sm text-muted">
              by{" "}
              <Link
                href={`/publishers/${publisher.slug}`}
                className="text-foreground font-medium hover:text-primary transition-colors"
              >
                {publisher.display_name ?? publisher.slug}
              </Link>
              {publishedAt && (
                <>
                  {" "}&middot; published {timeAgo(publishedAt)}
                </>
              )}
              {" "}&middot; {isMcp ? "MCP Server" : pkg.package_type}
            </p>
            <p className="mt-3 max-w-2xl text-base leading-relaxed text-muted">
              {pkg.summary}
            </p>
            {pkg.description && pkg.description !== pkg.summary && (
              <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted/80">
                {pkg.description}
              </p>
            )}
            {pkg.tags && pkg.tags.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-1.5">
                {(pkg.tags as string[]).map((tag: string) => (
                  <Link
                    key={tag}
                    href={`/search?q=${encodeURIComponent(tag)}`}
                    className="rounded-md bg-card px-2 py-0.5 text-xs text-muted border border-border hover:text-foreground hover:border-primary/30 transition-colors"
                  >
                    {tag}
                  </Link>
                ))}
              </div>
            )}

            {/* Compatibility badges */}
            {(compat.frameworks ?? []).length > 0 && (
              <div className="mt-3 flex flex-wrap gap-1.5">
                {(compat.frameworks as string[]).map((fw: string) => (
                  <FrameworkBadge key={fw} name={fw} />
                ))}
              </div>
            )}
          </div>
        </div>
      </header>

      <OwnerActions
        slug={slug}
        publisherSlug={publisher.slug}
        isDeprecated={!!pkg.is_deprecated}
        packageType={pkg.package_type}
        currentMetadata={{
          name: pkg.name,
          summary: pkg.summary,
          description: pkg.description ?? "",
          tags: pkg.tags ?? [],
        }}
        hasManualReview={!!(latestVersion?.security_reviewed_at || latestVersion?.compatibility_reviewed_at || latestVersion?.manually_reviewed_at)}
      />

      <div className="grid gap-8 lg:grid-cols-3">
        {/* Main column */}
        <div className="space-y-8 lg:col-span-2 min-w-0">
          {/* 1. Quick Start */}
          {isMcp ? (
            <section className="rounded-xl border border-border bg-card p-4 sm:p-6">
              <h2 className="mb-5 text-lg font-semibold text-foreground">
                Quick Start
              </h2>

              {/* Requirements */}
              <div className="mb-5 rounded-lg border border-yellow-500/20 bg-yellow-500/5 px-4 py-3">
                <p className="text-xs font-semibold uppercase tracking-wider text-yellow-400 mb-2">Requirements</p>
                <ul className="space-y-1 text-sm text-muted">
                  <li>&#x2022; Node.js and npx installed</li>
                  {mcpEnvKeys.length > 0 && (
                    <li>&#x2022; {mcpEnvKeys.length === 1 ? "1 API key" : `${mcpEnvKeys.length} environment variables`} required</li>
                  )}
                </ul>
              </div>

              {/* Step 1: Install */}
              <div className="mb-5">
                <p className="text-xs font-semibold uppercase tracking-wider text-muted mb-2">
                  1. Install
                </p>
                <CodeBlockWrapper
                  code={`agentnode install ${pkg.slug}`}
                  language="bash"
                />
              </div>

              {/* Step 2: Set Environment (if env_keys) */}
              {mcpEnvKeys.length > 0 && (
                <div className="mb-5">
                  <p className="text-xs font-semibold uppercase tracking-wider text-muted mb-2">
                    2. Set Environment
                  </p>
                  <CodeBlockWrapper
                    code={mcpEnvKeys.map(k => `export ${k}=your-key-here`).join("\n")}
                    language="bash"
                  />
                </div>
              )}

              {/* Step 3: Run */}
              <div className="mb-4">
                <p className="text-xs font-semibold uppercase tracking-wider text-muted mb-2">
                  {mcpEnvKeys.length > 0 ? "3" : "2"}. Run
                </p>
                <CodeBlockWrapper
                  code={`agentnode run ${pkg.slug} --input '{"query": "test"}'`}
                  language="bash"
                />
              </div>

              <p className="text-xs text-muted/70">
                When started, this MCP server runs locally on your machine via {mcpTransport} transport.
              </p>
            </section>
          ) : (
            <QuickStartWrapper
              slug={pkg.slug}
              entrypoint={install.entrypoint}
              examples={pkg.examples}
              envRequirements={pkg.env_requirements}
              readmeMd={pkg.readme_md}
              installResolution={install.install_resolution}
              installableVersion={install.installable_version}
              latestVersion={latestVersion?.version_number}
              sdkCode={install.sdk_code}
              postInstallCode={install.post_install_code}
            />
          )}

          <p className="text-xs text-muted -mt-4">
            {isMcp
              ? "This MCP server runs locally on your machine. AgentNode provides discovery metadata — the executable code is maintained by its npm publisher."
              : "Runs locally on your machine. No execution data is sent to AgentNode. Permissions are checked before execution."
            }
            {" "}<a href="/docs/security" className="text-primary/70 hover:text-primary hover:underline">Learn how this works</a>
            {isMcp && (
              <>
                {" | "}
                <a href="/mcp" className="text-primary/70 hover:text-primary hover:underline">New to MCP?</a>
              </>
            )}
          </p>

          {/* 2. Agent Info (only for agents) */}
          {pkg.agent_config && (
            <AgentInfoPanel agentConfig={pkg.agent_config} />
          )}

          {/* 3. Verification / External Source */}
          {isMcp ? (
            <section className="rounded-xl border border-border bg-card p-4 sm:p-6">
              <div className="flex items-center justify-between">
                <h2 className="text-lg font-semibold text-foreground">Registry Verification</h2>
                <span className="rounded-md bg-blue-500/10 border border-blue-500/20 px-2.5 py-1 text-xs font-medium text-blue-400">
                  External Source
                </span>
              </div>

              {/* Pipeline verification checklist */}
              <div className="mt-4 space-y-2">
                {[
                  { check: !!mcpNpmPackage, label: "Package resolved", detail: mcpNpmPackage ? `${mcpNpmPackage} on npm` : undefined },
                  { check: !!mcpSourceRepo, label: "Source verified", detail: mcpSourceRepo ? new URL(mcpSourceRepo).pathname.slice(1) : undefined },
                  { check: capabilities.length > 0, label: "Protocol verified", detail: capabilities.length > 0 ? `${capabilities.length} tools discovered` : undefined },
                  { check: !!mcpCommand.find((s: string) => s.includes("@")), label: "Version pinned", detail: mcpCommand.find((s: string) => s.includes("@")) },
                ].map(({ check, label, detail }) => (
                  <div key={label} className="flex items-start gap-2.5">
                    <span className={`mt-0.5 text-sm ${check ? "text-green-400" : "text-zinc-500"}`}>
                      {check ? "✓" : "○"}
                    </span>
                    <div>
                      <span className={`text-sm ${check ? "text-foreground" : "text-muted"}`}>
                        {label}
                      </span>
                      {detail && (
                        <span className="ml-1.5 text-xs text-muted">
                          &mdash; {detail}
                        </span>
                      )}
                    </div>
                  </div>
                ))}

                {/* Permission Profile */}
                {perms && (
                  <div className="mt-3 pt-3 border-t border-border/50">
                    <span className="text-xs font-semibold uppercase tracking-wider text-muted">Permission Profile</span>
                    <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1">
                      {[
                        { label: "Network", value: perms.network_level },
                        { label: "Filesystem", value: perms.filesystem_level },
                        { label: "Code Execution", value: perms.code_execution_level },
                      ].map(({ label: pLabel, value }) => (
                        <span key={pLabel} className="text-xs text-muted">
                          {pLabel}:{" "}
                          <span className={`font-mono ${value === "none" ? "text-green-400" : "text-amber-400"}`}>
                            {value ?? "none"}
                          </span>
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>

              <p className="mt-4 text-xs text-muted/70">
                No executable artifact is hosted in the AgentNode registry.
                AgentNode provides discovery, install metadata, and integrity protection for the start command.
                The server code itself is maintained by its npm publisher.
              </p>
            </section>
          ) : (
            <VerificationMainPanel slug={pkg.slug} verification={verification} publisherSlug={publisher.slug} />
          )}

          {/* 3. Use Cases */}
          {pkg.use_cases && pkg.use_cases.length > 0 && (
            <section className="rounded-xl border border-border bg-card p-4 sm:p-6">
              <h2 className="mb-4 text-lg font-semibold text-foreground">
                Use this when you need to...
              </h2>
              <ul className="space-y-2">
                {(pkg.use_cases as string[]).map((uc: string, i: number) => (
                  <li key={i} className="flex items-start gap-3 text-sm text-muted">
                    <span className="text-primary mt-0.5 shrink-0">&#8250;</span>
                    {uc}
                  </li>
                ))}
              </ul>
            </section>
          )}

          {/* 4. README */}
          {pkg.readme_md && (
            <ReadmeSection content={pkg.readme_md} />
          )}

          {/* 5. Version History */}
          {versions.length > 0 && (
            <section className="rounded-xl border border-border bg-card p-4 sm:p-6">
              <h2 className="mb-4 text-lg font-semibold text-foreground">
                Version History
              </h2>
              <VersionHistory
                versions={versions}
                currentVersion={version}
                slug={pkg.slug}
                installableVersion={install.installable_version}
              />
            </section>
          )}

          {/* 6. Capabilities */}
          <section className="rounded-xl border border-border bg-card p-4 sm:p-6">
            <h2 className="mb-4 text-lg font-semibold text-foreground">
              Capabilities
            </h2>
            {capabilities.length > 0 ? (
              <div className="space-y-3">
                {capabilities.map((cap: any) => (
                  <div
                    key={cap.capability_id}
                    className="rounded-lg border border-border bg-background p-4"
                  >
                    <div className="flex items-center gap-3 flex-wrap">
                      <Link
                        href={`/search?capability_id=${cap.capability_id}`}
                        className="rounded-md border border-primary/20 bg-primary/5 px-2.5 py-0.5 text-xs font-mono text-primary hover:bg-primary/10 transition-colors"
                      >
                        {cap.capability_id}
                      </Link>
                      <span className="text-sm font-medium text-foreground">
                        {cap.name}
                      </span>
                      {cap.capability_type && (
                        <span className="rounded bg-card px-2 py-0.5 text-xs text-muted border border-border">
                          {cap.capability_type}
                        </span>
                      )}
                    </div>
                    {cap.description && cap.description !== cap.name && (
                      <p className="text-sm text-muted mt-2">
                        {cap.description}
                      </p>
                    )}
                    {cap.input_schema && Object.keys(cap.input_schema).length > 0 && (
                      <div className="mt-3">
                        <p className="text-xs font-medium text-muted mb-1.5">Input Schema</p>
                        <pre className="rounded-md bg-card border border-border p-3 text-xs font-mono text-muted overflow-x-auto whitespace-pre-wrap max-h-48 overflow-y-auto">
                          {JSON.stringify(cap.input_schema, null, 2)}
                        </pre>
                      </div>
                    )}
                    {cap.output_schema && Object.keys(cap.output_schema).length > 0 && (
                      <div className="mt-3">
                        <p className="text-xs font-medium text-muted mb-1.5">Output Schema</p>
                        <pre className="rounded-md bg-card border border-border p-3 text-xs font-mono text-muted overflow-x-auto whitespace-pre-wrap max-h-48 overflow-y-auto">
                          {JSON.stringify(cap.output_schema, null, 2)}
                        </pre>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted">No capabilities declared.</p>
            )}
          </section>

          {/* 7. Prompts */}
          {prompts.length > 0 && (
            <section className="rounded-xl border border-border bg-card p-4 sm:p-6">
              <h2 className="mb-4 text-lg font-semibold text-foreground">
                Prompt Templates
              </h2>
              <div className="space-y-3">
                {prompts.map((prompt: any) => (
                  <div
                    key={prompt.name}
                    className="rounded-lg border border-border bg-background p-4"
                  >
                    <div className="flex items-center gap-3 flex-wrap">
                      <span className="text-sm font-medium text-foreground">
                        {prompt.name}
                      </span>
                      <span className="rounded bg-purple-500/10 px-2 py-0.5 text-xs text-purple-400 border border-purple-500/20">
                        prompt
                      </span>
                      {prompt.capability_id && (
                        <span className="rounded-md border border-primary/20 bg-primary/5 px-2.5 py-0.5 text-xs font-mono text-primary">
                          {prompt.capability_id}
                        </span>
                      )}
                    </div>
                    {prompt.description && (
                      <p className="text-sm text-muted mt-2">
                        {prompt.description}
                      </p>
                    )}
                    <pre className="mt-3 rounded-md bg-card border border-border p-3 text-xs font-mono text-muted overflow-x-auto whitespace-pre-wrap">
                      {prompt.template}
                    </pre>
                    {prompt.arguments && prompt.arguments.length > 0 && (
                      <div className="mt-3">
                        <p className="text-xs font-medium text-muted mb-1.5">Arguments:</p>
                        <div className="space-y-1">
                          {prompt.arguments.map((arg: any) => (
                            <div key={arg.name} className="flex items-center gap-2 text-xs">
                              <code className="font-mono text-foreground">{arg.name}</code>
                              {arg.required && (
                                <span className="text-red-400 text-[10px]">required</span>
                              )}
                              {arg.description && (
                                <span className="text-muted">&mdash; {arg.description}</span>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* 8. Resources */}
          {resources.length > 0 && (
            <section className="rounded-xl border border-border bg-card p-4 sm:p-6">
              <h2 className="mb-4 text-lg font-semibold text-foreground">
                Resources
              </h2>
              <div className="space-y-3">
                {resources.map((resource: any) => (
                  <div
                    key={resource.name}
                    className="rounded-lg border border-border bg-background p-4"
                  >
                    <div className="flex items-center gap-3 flex-wrap">
                      <span className="text-sm font-medium text-foreground">
                        {resource.name}
                      </span>
                      <span className="rounded bg-blue-500/10 px-2 py-0.5 text-xs text-blue-400 border border-blue-500/20">
                        resource
                      </span>
                      {resource.mime_type && (
                        <span className="rounded bg-card px-2 py-0.5 text-xs text-muted border border-border font-mono">
                          {resource.mime_type}
                        </span>
                      )}
                      {resource.capability_id && (
                        <span className="rounded-md border border-primary/20 bg-primary/5 px-2.5 py-0.5 text-xs font-mono text-primary">
                          {resource.capability_id}
                        </span>
                      )}
                    </div>
                    {resource.description && (
                      <p className="text-sm text-muted mt-2">
                        {resource.description}
                      </p>
                    )}
                    <div className="mt-2 flex items-center gap-2">
                      <code className="text-xs font-mono text-muted bg-card border border-border rounded px-2 py-1 break-all">
                        {resource.uri}
                      </code>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* 9. Connector */}
          {connector && (
            <section className="rounded-xl border border-border bg-card p-4 sm:p-6">
              <h2 className="mb-4 text-lg font-semibold text-foreground">
                Connector
              </h2>
              <div className="rounded-lg border border-border bg-background p-4 space-y-3">
                <div className="flex items-center gap-3 flex-wrap">
                  <span className="text-sm font-medium text-foreground">
                    {connector.provider}
                  </span>
                  <span className="rounded bg-orange-500/10 px-2 py-0.5 text-xs text-orange-400 border border-orange-500/20">
                    connector
                  </span>
                  {connector.auth_type && (
                    <span className="rounded bg-card px-2 py-0.5 text-xs text-muted border border-border font-mono">
                      {connector.auth_type}
                    </span>
                  )}
                  {connector.token_refresh && (
                    <span className="rounded bg-green-500/10 px-2 py-0.5 text-xs text-green-400 border border-green-500/20">
                      auto-refresh
                    </span>
                  )}
                </div>
                {connector.scopes && connector.scopes.length > 0 && (
                  <div>
                    <span className="text-xs text-muted">Scopes:</span>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {connector.scopes.map((scope: string) => (
                        <code
                          key={scope}
                          className="text-xs font-mono text-muted bg-card border border-border rounded px-1.5 py-0.5"
                        >
                          {scope}
                        </code>
                      ))}
                    </div>
                  </div>
                )}
                {connector.health_check_endpoint && (
                  <div className="text-xs text-muted">
                    Health check:{" "}
                    <code className="font-mono text-muted bg-card border border-border rounded px-1.5 py-0.5">
                      {connector.health_check_endpoint}
                    </code>
                  </div>
                )}
                {connector.rate_limit_rpm && (
                  <div className="text-xs text-muted">
                    Rate limit: {connector.rate_limit_rpm} req/min
                  </div>
                )}
              </div>
            </section>
          )}

          {/* 10. Permissions */}
          {perms && (
            <section className="rounded-xl border border-border bg-card p-4 sm:p-6">
              <h2 className="mb-4 text-lg font-semibold text-foreground">
                Permissions
              </h2>
              <p className="mb-4 text-xs text-muted">
                Declared by the publisher. Checked before execution by the policy gate.
              </p>
              <div className="space-y-2">
                {[
                  { label: "Network", value: perms.network_level },
                  { label: "Filesystem", value: perms.filesystem_level },
                  { label: "Code Execution", value: perms.code_execution_level },
                  { label: "Data Access", value: perms.data_access_level },
                  { label: "User Approval", value: perms.user_approval_level },
                ].map((p) => (
                  <div
                    key={p.label}
                    className="flex items-center justify-between rounded-lg border border-border bg-background px-4 py-2.5"
                  >
                    <span className="text-sm text-muted">{p.label}</span>
                    <PermissionLevel value={p.value} />
                  </div>
                ))}
              </div>
              <p className="mt-3 text-xs text-muted">
                Permissions are policy-checked before execution. For trusted and
                curated packages that run on the host, network and filesystem
                access are policy-checked but not OS-sandboxed. When runtime
                isolation is required for untrusted/community code, AgentNode uses
                sandbox-or-fail-closed if the required container runtime and
                pinned image are available.{" "}
                <a href="/docs/security" className="text-primary hover:underline">
                  Learn more
                </a>
              </p>
            </section>
          )}

          {/* Privacy */}
          <section className="rounded-xl border border-border bg-card p-4 sm:p-6">
            <h2 className="mb-2 text-lg font-semibold text-foreground">
              Privacy
            </h2>
            <p className="text-sm text-muted">
              All tool execution happens locally on your machine. AgentNode never receives:
            </p>
            <ul className="mt-2 space-y-1 text-sm text-muted">
              <li>&#x2022; Tool inputs or outputs</li>
              <li>&#x2022; Execution logs</li>
              <li>&#x2022; Data your agent processes</li>
            </ul>
            <p className="mt-2 text-sm text-muted">
              Only install events and search queries are sent to the registry.
            </p>
          </section>

          {/* Recommended For */}
          {recommendedFor.length > 0 && (
            <section className="rounded-xl border border-border bg-card p-4 sm:p-6">
              <h2 className="mb-4 text-lg font-semibold text-foreground">
                Recommended For
              </h2>
              <div className="space-y-2">
                {recommendedFor.map((rec: any, i: number) => (
                  <div
                    key={i}
                    className="flex items-center gap-3 rounded-lg border border-border bg-background p-3"
                  >
                    {rec.agent_type && (
                      <span className="rounded-md bg-primary/10 px-2.5 py-0.5 text-xs font-medium text-primary">
                        {rec.agent_type}
                      </span>
                    )}
                    {rec.missing_capability && (
                      <span className="text-sm text-muted">
                        missing <span className="font-mono text-foreground">{rec.missing_capability}</span>
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </section>
          )}
        </div>

        {/* Sidebar */}
        <div className="space-y-6 min-w-0">
          {/* Quick install */}
          <section className="rounded-xl border border-primary/20 bg-primary/5 p-3 sm:p-5">
            <CodeBlockWrapper
              code={install.cli_command ?? `agentnode install ${pkg.slug}`}
              language="bash"
            />
          </section>

          {/* Env Requirements (sidebar compact) */}
          {pkg.env_requirements && pkg.env_requirements.length > 0 && (
            <section className="rounded-xl border border-border bg-card p-4 sm:p-6">
              <h2 className="mb-3 text-sm font-semibold text-foreground">
                Environment Variables
              </h2>
              <div className="space-y-1.5">
                {(pkg.env_requirements as any[]).map((env: any) => (
                  <div key={env.name} className="flex items-center justify-between text-xs">
                    <code className="font-mono text-primary">{env.name}</code>
                    {env.required && (
                      <span className="text-red-400 text-[10px]">required</span>
                    )}
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* MCP External Source (replaces File Browser for MCPs) */}
          {isMcp && (mcpNpmPackage || mcpSourceRepo) && (
            <section className="rounded-xl border border-border bg-card p-4 sm:p-6">
              <h2 className="mb-3 text-sm font-semibold text-foreground">External Source</h2>
              <div className="space-y-2.5">
                {mcpNpmPackage && (
                  <div className="flex items-start gap-2">
                    <span className="text-xs text-muted shrink-0 mt-0.5">npm</span>
                    <a
                      href={`https://www.npmjs.com/package/${mcpNpmPackage}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-sm text-primary hover:underline font-mono break-all"
                    >
                      {mcpNpmPackage}
                    </a>
                  </div>
                )}
                {mcpSourceRepo && (
                  <div className="flex items-start gap-2">
                    <span className="text-xs text-muted shrink-0 mt-0.5">Repo</span>
                    <a
                      href={mcpSourceRepo}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-sm text-primary hover:underline break-all"
                    >
                      {mcpSourceRepo.replace("https://github.com/", "")}
                    </a>
                  </div>
                )}
              </div>
              <p className="mt-3 text-xs text-muted/60">
                Code is maintained externally, not hosted by AgentNode.
              </p>
            </section>
          )}

          {/* MCP Environment Variables (sidebar) */}
          {isMcp && mcpEnvKeys.length > 0 && (
            <section className="rounded-xl border border-yellow-500/20 bg-yellow-500/5 p-4 sm:p-6">
              <h2 className="mb-3 text-sm font-semibold text-foreground">Required Setup</h2>
              <div className="space-y-1.5">
                {mcpEnvKeys.map((key: string) => (
                  <div key={key} className="flex items-center justify-between">
                    <code className="font-mono text-xs text-yellow-400">{key}</code>
                    <span className="text-[10px] text-yellow-400/70">required</span>
                  </div>
                ))}
              </div>
              <p className="mt-2 text-xs text-muted/60">
                Set these environment variables before running this MCP server.
              </p>
            </section>
          )}

          {/* File Browser */}
          {pkg.file_list && pkg.file_list.length > 0 && (
            <section className="rounded-xl border border-border bg-card p-4 sm:p-6">
              <h2 className="mb-3 text-sm font-semibold text-foreground">
                Files ({pkg.file_list.length})
              </h2>
              <FileBrowserWrapper
                files={pkg.file_list}
                slug={pkg.slug}
                version={version}
              />
            </section>
          )}

          {/* License */}
          {pkg.license_model && (
            <section className="rounded-xl border border-border bg-card p-4 sm:p-6">
              <h2 className="mb-3 text-sm font-semibold text-foreground">License</h2>
              <a
                href={`https://spdx.org/licenses/${pkg.license_model}.html`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-sm text-primary hover:underline"
              >
                {pkg.license_model}
              </a>
            </section>
          )}

          {/* Stats */}
          <section className="rounded-xl border border-border bg-card p-4 sm:p-6">
            <h2 className="mb-4 text-sm font-semibold text-foreground">
              Stats
            </h2>
            <div className="space-y-3">
              <div className="flex items-center justify-between text-sm">
                <span className="text-muted">Downloads</span>
                <span className="font-mono font-medium text-foreground">
                  {(pkg.download_count ?? 0).toLocaleString()}
                </span>
              </div>
              <div className="flex items-center justify-between text-sm">
                <span className="text-muted">Installs</span>
                <span className="font-mono font-medium text-foreground">
                  {(pkg.install_count ?? 0).toLocaleString()}
                </span>
              </div>
              <div className="flex items-center justify-between text-sm">
                <span className="text-muted">Version</span>
                <span className="font-mono text-foreground">v{version}</span>
              </div>
              {publishedAt && (
                <div className="flex items-center justify-between text-sm">
                  <span className="text-muted">Published</span>
                  <span className="text-foreground text-xs">
                    {new Date(publishedAt).toLocaleDateString()}
                  </span>
                </div>
              )}
              <div className="flex items-center justify-between text-sm">
                <span className="text-muted">Channel</span>
                <span className="text-foreground">
                  {latestVersion?.channel ?? "stable"}
                </span>
              </div>
              <div className="flex items-center justify-between text-sm">
                <span className="text-muted">Type</span>
                <span className="text-foreground">{isMcp ? "MCP Server" : pkg.package_type}</span>
              </div>
              {install.entrypoint && (
                <div className="flex items-center justify-between text-sm">
                  <span className="text-muted">Entrypoint</span>
                  <span className="font-mono text-xs text-foreground">
                    {install.entrypoint}
                  </span>
                </div>
              )}
            </div>
          </section>

          {/* Compatibility */}
          <section className="rounded-xl border border-border bg-card p-4 sm:p-6">
            <h2 className="mb-4 text-sm font-semibold text-foreground">
              Compatibility
            </h2>
            <div className="space-y-4">
              <div>
                <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted">
                  Frameworks
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {(compat.frameworks ?? []).map((fw: string) => (
                    <FrameworkBadge key={fw} name={fw} />
                  ))}
                </div>
              </div>
              <div>
                <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted">
                  Runtime
                </p>
                <span className="rounded-md bg-background px-2.5 py-1 text-xs text-foreground border border-border">
                  {compat.runtime === "mcp" ? "MCP" : (compat.runtime ?? "Python")}
                </span>
              </div>
              {compat.python && (
                <div>
                  <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted">
                    Python Version
                  </p>
                  <span className="font-mono text-xs text-foreground">
                    {compat.python}
                  </span>
                </div>
              )}
              {(compat.dependencies ?? []).length > 0 && (
                <div>
                  <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted">
                    Dependencies
                  </p>
                  <ul className="space-y-1">
                    {compat.dependencies.map((dep: string) => (
                      <li key={dep} className="font-mono text-xs text-muted">
                        {dep}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </section>

          {/* Trust */}
          <section className="rounded-xl border border-border bg-card p-4 sm:p-6">
            <h2 className="mb-4 text-sm font-semibold text-foreground">
              Trust & Security
            </h2>
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted">Publisher</span>
                <TrustBadge level={trust.publisher_trust_level ?? "unverified"} />
              </div>
              <div className="flex items-center justify-between text-sm">
                <span className="text-muted">Signature</span>
                <span className={trust.signature_present ? "text-green-400" : "text-zinc-500"}>
                  {trust.signature_present ? "Verified" : "None"}
                </span>
              </div>
              <div className="flex items-center justify-between text-sm">
                <span className="text-muted">Provenance</span>
                <span className={trust.provenance_present ? "text-green-400" : "text-zinc-500"}>
                  {trust.provenance_present ? "Verified" : "None"}
                </span>
              </div>
              <div className="flex items-center justify-between text-sm">
                <span className="text-muted">Security Issues</span>
                <span className={trust.security_findings_count > 0 ? "text-red-400 font-medium" : "text-green-400"}>
                  {trust.security_findings_count ?? 0}
                </span>
              </div>
            </div>
          </section>

          {/* Links */}
          {(pkg.homepage_url || pkg.docs_url || pkg.source_url) && (
            <section className="rounded-xl border border-border bg-card p-4 sm:p-6">
              <h2 className="mb-3 text-sm font-semibold text-foreground">Links</h2>
              <div className="space-y-2">
                {pkg.homepage_url && /^https?:\/\//i.test(pkg.homepage_url) && (
                  <a href={pkg.homepage_url} target="_blank" rel="noopener noreferrer" className="block text-sm text-primary hover:underline truncate">
                    Homepage
                  </a>
                )}
                {pkg.docs_url && /^https?:\/\//i.test(pkg.docs_url) && (
                  <a href={pkg.docs_url} target="_blank" rel="noopener noreferrer" className="block text-sm text-primary hover:underline truncate">
                    Documentation
                  </a>
                )}
                {pkg.source_url && /^https?:\/\//i.test(pkg.source_url) && (
                  <a href={pkg.source_url} target="_blank" rel="noopener noreferrer" className="block text-sm text-primary hover:underline truncate">
                    Source Code
                  </a>
                )}
              </div>
            </section>
          )}

          {/* Publisher card */}
          <section className="rounded-xl border border-border bg-card p-4 sm:p-6">
            <h2 className="mb-4 text-sm font-semibold text-foreground">
              Publisher
            </h2>
            <Link
              href={`/publishers/${publisher.slug}`}
              className="group flex items-center gap-3"
            >
              <div className="flex h-10 w-10 items-center justify-center rounded-full bg-primary/10 text-sm font-bold text-primary">
                {(publisher.display_name ?? publisher.slug ?? "?").charAt(0).toUpperCase()}
              </div>
              <div>
                <p className="text-sm font-medium text-foreground group-hover:text-primary transition-colors">
                  {publisher.display_name ?? publisher.slug}
                </p>
                <p className="text-xs text-muted">@{publisher.slug}</p>
              </div>
            </Link>
          </section>

          {/* Report link */}
          <div className="text-center">
            <Link
              href={`https://github.com/agentnode-ai/agentnode/issues/new?title=Report:+${pkg.slug}`}
              className="text-xs text-muted hover:text-foreground transition-colors"
              target="_blank"
              rel="noopener noreferrer"
            >
              Report an issue with this package
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
