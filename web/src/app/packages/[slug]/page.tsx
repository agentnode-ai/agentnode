import { notFound } from "next/navigation";
import TrustBadge from "@/components/TrustBadge";
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

export default async function PackageDetailPage({ params }: PageProps) {
  const { slug } = await params;
  const pkg = await fetchPackage(slug);

  if (!pkg) {
    notFound();
  }

  const blocks = pkg.blocks ?? {};
  const publisher = pkg.publisher ?? {};
  const version = pkg.latest_version?.version_number ?? "unknown";
  const capabilities = blocks.capabilities ?? [];
  const recommendedFor = blocks.recommended_for ?? [];
  const install = blocks.install ?? {};
  const compat = blocks.compatibility ?? {};
  const perms = blocks.permissions;
  const trust = blocks.trust ?? {};

  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      {/* Header */}
      <header className="mb-10 border-b border-border pb-8">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="font-mono text-2xl font-bold text-foreground sm:text-3xl">
                {pkg.name}
              </h1>
              <TrustBadge level={publisher.trust_level ?? "unverified"} size="md" />
            </div>
            <p className="mt-1 text-sm text-muted">
              by{" "}
              <span className="text-foreground font-medium">
                {publisher.display_name ?? publisher.slug}
              </span>{" "}
              &middot; v{version} &middot; {pkg.package_type}
            </p>
            <p className="mt-3 max-w-2xl text-base leading-relaxed text-muted">
              {pkg.summary}
            </p>
          </div>
        </div>
      </header>

      <div className="grid gap-8 lg:grid-cols-3">
        {/* Main column */}
        <div className="space-y-8 lg:col-span-2">
          {/* Block 1: Capabilities */}
          <section className="rounded-xl border border-border bg-card p-6">
            <h2 className="mb-4 text-lg font-semibold text-foreground">
              Capabilities
            </h2>
            {pkg.description && (
              <p className="mb-4 text-sm text-muted">{pkg.description}</p>
            )}
            {capabilities.length > 0 && (
              <div className="space-y-3">
                {capabilities.map((cap: any) => (
                  <div
                    key={cap.capability_id}
                    className="flex items-start gap-3 rounded-lg border border-border bg-background p-3"
                  >
                    <span className="rounded-md border border-primary/20 bg-primary/5 px-2 py-0.5 text-xs font-mono text-primary">
                      {cap.capability_id}
                    </span>
                    <div>
                      <p className="text-sm font-medium text-foreground">
                        {cap.name}
                      </p>
                      {cap.description && (
                        <p className="text-xs text-muted mt-1">
                          {cap.description}
                        </p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* Block 2: Recommended For */}
          {recommendedFor.length > 0 && (
            <section className="rounded-xl border border-border bg-card p-6">
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

          {/* Block 3: Install */}
          <section className="rounded-xl border border-border bg-card p-6">
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
                    Import
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

          {/* Block 5: Permissions */}
          {perms && (
            <section className="rounded-xl border border-border bg-card p-6">
              <h2 className="mb-4 text-lg font-semibold text-foreground">
                Permissions
              </h2>
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
                    <span
                      className={`rounded-md px-2.5 py-0.5 text-xs font-mono ${
                        p.value === "none"
                          ? "bg-green-500/10 text-green-400"
                          : p.value === "unrestricted" || p.value === "shell" || p.value === "any"
                          ? "bg-red-500/10 text-red-400"
                          : "bg-yellow-500/10 text-yellow-400"
                      }`}
                    >
                      {p.value}
                    </span>
                  </div>
                ))}
              </div>
            </section>
          )}
        </div>

        {/* Sidebar */}
        <div className="space-y-6">
          {/* Block 4: Compatibility */}
          <section className="rounded-xl border border-border bg-card p-6">
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
                      className="rounded-md bg-background px-2.5 py-1 text-xs text-foreground"
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
                <span className="rounded-md bg-background px-2.5 py-1 text-xs text-foreground">
                  {pkg.package_type === "toolpack" ? "Python" : pkg.package_type}
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

          {/* Block 6: Performance */}
          <section className="rounded-xl border border-border bg-card p-6">
            <h2 className="mb-4 text-lg font-semibold text-foreground">
              Stats
            </h2>
            <div className="space-y-3">
              <div className="flex items-center justify-between text-sm">
                <span className="text-muted">Downloads</span>
                <span className="font-mono text-foreground">
                  {(pkg.download_count ?? 0).toLocaleString()}
                </span>
              </div>
              <div className="flex items-center justify-between text-sm">
                <span className="text-muted">Version</span>
                <span className="font-mono text-foreground">v{version}</span>
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

          {/* Block 7: Trust */}
          <section className="rounded-xl border border-border bg-card p-6">
            <h2 className="mb-4 text-lg font-semibold text-foreground">
              Trust
            </h2>
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted">Publisher Trust</span>
                <TrustBadge level={trust.publisher_trust_level ?? "unverified"} />
              </div>
              <div className="flex items-center justify-between text-sm">
                <span className="text-muted">Signature</span>
                <span className={trust.signature_present ? "text-green-400" : "text-zinc-500"}>
                  {trust.signature_present ? "Present" : "None"}
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
                <span className={trust.security_findings_count > 0 ? "text-red-400" : "text-green-400"}>
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
        </div>
      </div>
    </div>
  );
}
