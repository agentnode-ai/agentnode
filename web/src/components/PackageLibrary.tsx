import Link from "next/link";
import PackageCard from "./PackageCard";
import { BACKEND_URL } from "@/lib/constants";

/* ------------------------------------------------------------------ */
/*  Shared library section for the browse pages (/toolpacks, /skills,  */
/*  /agents, /mcp). One consistent, search-style layout: a short       */
/*  header, the package grid first, explanatory text below (per page). */
/* ------------------------------------------------------------------ */

export interface LibraryHit {
  slug: string;
  name: string;
  package_type: string;
  runtime?: string | null;
  summary: string;
  publisher_name: string;
  trust_level: "curated" | "trusted" | "verified" | "unverified";
  latest_version: string | null;
  frameworks: string[];
  download_count: number;
  install_count: number;
  verification_status: string | null;
  verification_tier?: string | null;
  verification_score?: number | null;
  tags: string[];
  is_deprecated: boolean;
  network_level?: string | null;
  filesystem_level?: string | null;
  code_execution_level?: string | null;
  has_connector?: boolean | null;
}

export async function fetchLibrary(body: {
  package_type?: string;
  runtime?: string;
  per_page?: number;
}): Promise<LibraryHit[]> {
  try {
    const res = await fetch(`${BACKEND_URL}/v1/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        per_page: 50,
        sort_by: "download_count:desc",
        ...body,
      }),
      next: { revalidate: 300 },
    });
    if (!res.ok) return [];
    const data = await res.json();
    return data.hits ?? [];
  } catch {
    return [];
  }
}

export function LibraryGrid({
  hits,
  searchHref,
  emptyLabel,
}: {
  hits: LibraryHit[];
  searchHref: string;
  emptyLabel: string;
}) {
  return (
    <>
      {hits.length > 0 ? (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {hits.map((pkg) => (
              <PackageCard
                key={pkg.slug}
                slug={pkg.slug}
                name={pkg.name}
                summary={pkg.summary}
                trust_level={pkg.trust_level}
                frameworks={pkg.frameworks}
                version={pkg.latest_version ?? undefined}
                download_count={pkg.download_count}
                install_count={pkg.install_count}
                verification_status={pkg.verification_status}
                verification_tier={pkg.verification_tier}
                verification_score={pkg.verification_score}
                package_type={pkg.package_type}
                runtime={pkg.runtime}
                tags={pkg.tags}
                publisher_name={pkg.publisher_name}
                is_deprecated={pkg.is_deprecated}
                network_level={pkg.network_level}
                filesystem_level={pkg.filesystem_level}
                code_execution_level={pkg.code_execution_level}
                has_connector={pkg.has_connector}
              />
            ))}
          </div>
          <div className="mt-8 text-center">
            <Link
              href={searchHref}
              className="inline-flex h-11 items-center justify-center rounded-lg border border-border px-6 text-sm font-medium text-foreground transition-colors hover:bg-card"
            >
              Search &amp; filter all →
            </Link>
          </div>
        </>
      ) : (
        <p className="py-12 text-center text-sm text-muted">{emptyLabel}</p>
      )}
    </>
  );
}

export function LibraryHero({
  eyebrow,
  title,
  subtitle,
}: {
  eyebrow: string;
  title: React.ReactNode;
  subtitle: string;
}) {
  return (
    <section className="relative overflow-hidden border-b border-border">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-primary/10 via-transparent to-transparent" />
      <div className="relative mx-auto max-w-6xl px-4 sm:px-6 pt-14 pb-8 text-center">
        <span className="mb-3 inline-block rounded-full border border-primary/30 bg-primary/5 px-4 py-1 text-xs font-medium text-primary">
          {eyebrow}
        </span>
        <h1 className="text-3xl font-bold leading-tight tracking-tight text-foreground sm:text-4xl">
          {title}
        </h1>
        <p className="mx-auto mt-4 max-w-2xl text-muted">{subtitle}</p>
      </div>
    </section>
  );
}
