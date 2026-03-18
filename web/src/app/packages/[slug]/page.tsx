import Link from "next/link";
import { notFound } from "next/navigation";
import TrustBadge from "@/components/TrustBadge";
import VerificationBadge from "@/components/VerificationBadge";
import VerificationPanel from "@/components/VerificationPanel";
import CodeBlockWrapper from "./CodeBlockWrapper";

interface PageProps {
  params: Promise<{ slug: string }>;
}

/* eslint-disable @typescript-eslint/no-explicit-any */
async function fetchPackage(slug: string): Promise<any | null> {
  try {
    const baseUrl = process.env.BACKEND_URL ?? "http://localhost:8001";
    const res = await fetch(
      `${baseUrl}/v1/packages/${encodeURIComponent(slug)}`,
      { next: { revalidate: 60 } }
    );
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

function timeAgo(dateStr: string): string {
  const now = new Date();
  const date = new Date(dateStr);
  const diffMs = now.getTime() - date.getTime();
  const days = Math.floor(diffMs / (1000 * 60 * 60 * 24));
  if (days === 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 30) return `${days} days ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months} month${months > 1 ? "s" : ""} ago`;
  const years = Math.floor(months / 12);
  return `${years} year${years > 1 ? "s" : ""} ago`;
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

export default async function PackageDetailPage({ params }: PageProps) {
  const { slug } = await params;
  const pkg = await fetchPackage(slug);

  if (!pkg) {
    notFound();
  }

  const blocks = pkg.blocks ?? {};
  const publisher = pkg.publisher ?? {};
  const latestVersion = pkg.latest_version;
  const version = latestVersion?.version_number ?? "unknown";
  const publishedAt = latestVersion?.published_at;
  const capabilities = blocks.capabilities ?? [];
  const recommendedFor = blocks.recommended_for ?? [];
  const install = blocks.install ?? {};
  const compat = blocks.compatibility ?? {};
  const perms = blocks.permissions;
  const trust = blocks.trust ?? {};
  const performance = blocks.performance ?? {};

  return (
    <div className="mx-auto max-w-6xl px-4 sm:px-6 py-10">
      {/* Breadcrumb */}
      <nav className="mb-6 text-sm text-muted">
        <Link href="/search" className="hover:text-foreground transition-colors">
          Packages
        </Link>
        <span className="mx-2">/</span>
        <span className="text-foreground">{pkg.slug}</span>
      </nav>

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
              <VerificationBadge status={trust.verification_status} />
              <span className="rounded-md bg-card px-2.5 py-1 text-xs font-mono text-muted border border-border">
                v{version}
              </span>
            </div>
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
          </div>
        </div>
      </header>

      <div className="grid gap-8 lg:grid-cols-3">
        {/* Main column */}
        <div className="space-y-8 lg:col-span-2 min-w-0">
          {/* Install */}
          <section className="rounded-xl border border-border bg-card p-4 sm:p-6">
            <h2 className="mb-4 text-lg font-semibold text-foreground">
              Install
            </h2>
            <div className="space-y-4">
              {install.cli_command && (
                <div>
                  <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted">
                    CLI
                  </p>
                  <CodeBlockWrapper
                    code={install.cli_command}
                    language="bash"
                  />
                </div>
              )}
              {install.post_install_code && (
                <div>
                  <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted">
                    Import in Python
                  </p>
                  <CodeBlockWrapper
                    code={install.post_install_code}
                    language="python"
                  />
                </div>
              )}
              {install.sdk_code && (
                <div>
                  <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted">
                    SDK
                  </p>
                  <CodeBlockWrapper
                    code={install.sdk_code}
                    language="python"
                  />
                </div>
              )}
            </div>
          </section>

          {/* Capabilities */}
          <section className="rounded-xl border border-border bg-card p-4 sm:p-6">
            <h2 className="mb-4 text-lg font-semibold text-foreground">
              Capabilities
            </h2>
            {capabilities.length > 0 && (
              <div className="space-y-3">
                {capabilities.map((cap: any) => (
                  <div
                    key={cap.capability_id}
                    className="rounded-lg border border-border bg-background p-4"
                  >
                    <div className="flex items-center gap-3 flex-wrap">
                      <Link
                        href={`/search?capability=${cap.capability_id}`}
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
            )}
            {capabilities.length === 0 && (
              <p className="text-sm text-muted">No capabilities declared.</p>
            )}
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

          {/* Permissions */}
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
                  { label: "Network", value: perms.network_level, icon: "globe" },
                  { label: "Filesystem", value: perms.filesystem_level, icon: "folder" },
                  { label: "Code Execution", value: perms.code_execution_level, icon: "terminal" },
                  { label: "Data Access", value: perms.data_access_level, icon: "database" },
                  { label: "User Approval", value: perms.user_approval_level, icon: "shield" },
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

          {/* Stats */}
          <section className="rounded-xl border border-border bg-card p-4 sm:p-6">
            <h2 className="mb-4 text-lg font-semibold text-foreground">
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
            <h2 className="mb-4 text-lg font-semibold text-foreground">
              Compatibility
            </h2>
            <div className="space-y-4">
              <div>
                <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted">
                  Frameworks
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {(compat.frameworks ?? []).map((fw: string) => (
                    <span
                      key={fw}
                      className="rounded-md bg-background px-2.5 py-1 text-xs text-foreground border border-border"
                    >
                      {fw}
                    </span>
                  ))}
                </div>
              </div>

              <div>
                <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted">
                  Runtime
                </p>
                <span className="rounded-md bg-background px-2.5 py-1 text-xs text-foreground border border-border">
                  Python
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

          {/* Verification */}
          <VerificationPanel slug={pkg.slug} />

          {/* Trust */}
          <section className="rounded-xl border border-border bg-card p-4 sm:p-6">
            <h2 className="mb-4 text-lg font-semibold text-foreground">
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
              {trust.last_updated && (
                <div className="flex items-center justify-between text-sm">
                  <span className="text-muted">Last Updated</span>
                  <span className="text-xs text-foreground">
                    {new Date(trust.last_updated).toLocaleDateString()}
                  </span>
                </div>
              )}
            </div>
          </section>

          {/* Publisher card */}
          <section className="rounded-xl border border-border bg-card p-4 sm:p-6">
            <h2 className="mb-4 text-lg font-semibold text-foreground">
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
