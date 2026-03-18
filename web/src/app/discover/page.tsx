import PackageCard from "@/components/PackageCard";
import type { SearchResponse } from "@/lib/api";

/* eslint-disable @typescript-eslint/no-explicit-any */

async function fetchTrending(): Promise<SearchResponse | null> {
  try {
    const baseUrl = process.env.BACKEND_URL ?? "http://localhost:8001";
    const res = await fetch(`${baseUrl}/v1/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        q: "",
        sort_by: "download_count:desc",
        per_page: 20,
        page: 1,
      }),
      next: { revalidate: 60 },
    });
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

async function fetchNewest(): Promise<SearchResponse | null> {
  try {
    const baseUrl = process.env.BACKEND_URL ?? "http://localhost:8001";
    const res = await fetch(`${baseUrl}/v1/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        q: "",
        sort_by: "published_at:desc",
        per_page: 10,
        page: 1,
      }),
      next: { revalidate: 60 },
    });
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

export default async function DiscoverPage() {
  const [trending, newest] = await Promise.all([
    fetchTrending(),
    fetchNewest(),
  ]);

  const trendingHits = trending?.hits ?? [];
  const newestHits = newest?.hits ?? [];

  return (
    <div className="mx-auto max-w-6xl px-6 py-12">
      {/* Page header */}
      <header className="mb-10">
        <h1 className="text-3xl font-bold text-foreground">Discover</h1>
        <p className="mt-2 text-base text-muted">
          Explore trending and recently published packages on AgentNode.
        </p>
      </header>

      {/* Trending Packages */}
      <section className="mb-14">
        <h2 className="text-2xl font-bold text-foreground mb-6">
          Trending Packages
        </h2>
        {trendingHits.length > 0 ? (
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {trendingHits.map((pkg) => (
              <PackageCard
                key={pkg.slug}
                slug={pkg.slug}
                name={pkg.name}
                summary={pkg.summary}
                trust_level={pkg.trust_level}
                frameworks={pkg.frameworks}
                version={pkg.latest_version ?? undefined}
                download_count={pkg.download_count}
                verification_status={pkg.verification_status}
              />
            ))}
          </div>
        ) : (
          <div className="flex items-center justify-center rounded-xl border border-border bg-card py-16">
            <p className="text-sm text-muted">
              No trending packages available right now.
            </p>
          </div>
        )}
      </section>

      {/* Recently Published */}
      <section>
        <h2 className="text-2xl font-bold text-foreground mb-6">
          Recently Published
        </h2>
        {newestHits.length > 0 ? (
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {newestHits.map((pkg) => (
              <PackageCard
                key={pkg.slug}
                slug={pkg.slug}
                name={pkg.name}
                summary={pkg.summary}
                trust_level={pkg.trust_level}
                frameworks={pkg.frameworks}
                version={pkg.latest_version ?? undefined}
                download_count={pkg.download_count}
                verification_status={pkg.verification_status}
              />
            ))}
          </div>
        ) : (
          <div className="flex items-center justify-center rounded-xl border border-border bg-card py-16">
            <p className="text-sm text-muted">
              No recent packages available right now.
            </p>
          </div>
        )}
      </section>
    </div>
  );
}
