import type { Metadata } from "next";
import PackageCard from "@/components/PackageCard";
import type { SearchResponse } from "@/lib/api";
import { BACKEND_URL } from "@/lib/constants";

export const metadata: Metadata = {
  title: "Discover Agent Skills — Trending AI Agent Tools & Packs",
  description:
    "Explore trending and newly published agent skills on AgentNode. Browse verified AI tools, starter packs, and capabilities trusted by developers worldwide.",
  openGraph: {
    title: "Discover Agent Skills — Trending AI Agent Tools",
    description:
      "Explore trending agent skills and newly published AI tools on AgentNode. Verified, trusted, and ready to install.",
    type: "website",
    url: "https://agentnode.net/discover",
    siteName: "AgentNode",
  },
};

/* eslint-disable @typescript-eslint/no-explicit-any */

async function fetchTrending(): Promise<SearchResponse | null> {
  try {
    const baseUrl = BACKEND_URL;
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
    const baseUrl = BACKEND_URL;
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

async function fetchAgents(): Promise<SearchResponse | null> {
  try {
    const baseUrl = BACKEND_URL;
    const res = await fetch(`${baseUrl}/v1/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        q: "",
        package_type: "agent",
        sort_by: "published_at:desc",
        per_page: 12,
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
  const [trending, newest, agents] = await Promise.all([
    fetchTrending(),
    fetchNewest(),
    fetchAgents(),
  ]);

  const trendingHits = trending?.hits ?? [];
  const newestHits = newest?.hits ?? [];
  const agentHits = agents?.hits ?? [];

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
                install_count={pkg.install_count}
                verification_status={pkg.verification_status}
                verification_tier={pkg.verification_tier}
                verification_score={pkg.verification_score}
                package_type={pkg.package_type}
                publisher_name={pkg.publisher_name}
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

      {/* Featured Agents */}
      {agentHits.length > 0 && (
        <section className="mb-14">
          <div className="flex items-center justify-between mb-6">
            <div className="flex items-center gap-3">
              <h2 className="text-2xl font-bold text-foreground">
                Agents
              </h2>
              <span className="rounded-full bg-blue-500/10 border border-blue-500/20 px-2.5 py-0.5 text-xs font-medium text-blue-400">
                {agentHits.length} available
              </span>
            </div>
            <a
              href="/search?package_type=agent"
              className="text-sm text-primary hover:text-foreground transition-colors"
            >
              View all &rarr;
            </a>
          </div>
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {agentHits.map((pkg) => (
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
                publisher_name={pkg.publisher_name}
              />
            ))}
          </div>
        </section>
      )}

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
                install_count={pkg.install_count}
                verification_status={pkg.verification_status}
                verification_tier={pkg.verification_tier}
                verification_score={pkg.verification_score}
                package_type={pkg.package_type}
                publisher_name={pkg.publisher_name}
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
