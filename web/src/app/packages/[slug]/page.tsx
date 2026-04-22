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

export async function generateMetadata({ params, searchParams }: PageProps): Promise<Metadata> {
  const { slug } = await params;
  const { v } = await searchParams;
  const pkg = await fetchPackage(slug, v);
  if (!pkg) return { title: "Package Not Found" };

  const title = `${pkg.name} — Agent Skill for AI Agents`;
  const description = pkg.summary || `${pkg.name} is a verified agent skill on AgentNode. Install it in any AI agent framework.`;

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
  const [pkg, versions] = await Promise.all([
    fetchPackage(slug, v),
    fetchVersions(slug),
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

  return (
    <div className="mx-auto max-w-6xl px-4 sm:px-6 py-10">
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
              {" "}&middot; {pkg.package_type}
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
          <QuickStartWrapper
            slug={pkg.slug}
            entrypoint={install.entrypoint}
            examples={pkg.examples}
            envRequirements={pkg.env_requirements}
            readmeMd={pkg.readme_md}
            installResolution={install.install_resolution}
            installableVersion={install.installable_version}
            latestVersion={latestVersion?.version_number}
          />

          {/* 2. Agent Info (only for agents) */}
          {pkg.agent_config && (
            <AgentInfoPanel agentConfig={pkg.agent_config} />
          )}

          {/* 3. Verification (prominent, main column) */}
          <VerificationMainPanel slug={pkg.slug} verification={verification} publisherSlug={publisher.slug} />

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
                This package declares the following access levels. Review before installing.
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
            </section>
          )}

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
                <span className="text-foreground">{pkg.package_type}</span>
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
                <span className="rounded-md bg-background px-2.5 py-1 text-xs text-foreground border border-border capitalize">
                  {compat.runtime ?? "python"}
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
