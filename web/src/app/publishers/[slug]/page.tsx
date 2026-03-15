import Link from "next/link";
import TrustBadge from "@/components/TrustBadge";

const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8001";

async function getPublisher(slug: string) {
  const res = await fetch(`${BACKEND_URL}/v1/publishers/${slug}`, {
    next: { revalidate: 60 },
  });
  if (!res.ok) return null;
  return res.json();
}

async function getPublisherPackages(slug: string) {
  const res = await fetch(`${BACKEND_URL}/v1/search?q=&limit=50`, {
    next: { revalidate: 60 },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return (data.hits || []).filter((h: any) => h.publisher_slug === slug);
}

export default async function PublisherPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const publisher = await getPublisher(slug);

  if (!publisher) {
    return (
      <div className="mx-auto max-w-4xl px-6 py-24 text-center">
        <h1 className="text-2xl font-bold text-foreground">Publisher not found</h1>
        <p className="mt-2 text-muted">No publisher with slug &quot;{slug}&quot; exists.</p>
      </div>
    );
  }

  const packages = await getPublisherPackages(slug);

  return (
    <div className="mx-auto max-w-4xl px-6 py-12">
      <div className="mb-8">
        <div className="flex items-center gap-3 mb-2">
          <h1 className="text-2xl font-bold text-foreground">{publisher.display_name}</h1>
          <TrustBadge level={publisher.trust_level} />
        </div>
        <p className="text-muted text-sm">@{publisher.slug}</p>
        {publisher.bio && <p className="mt-3 text-foreground/80">{publisher.bio}</p>}

        <div className="mt-4 flex gap-4 text-sm text-muted">
          {publisher.website_url && (
            <a
              href={publisher.website_url}
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-primary"
            >
              Website &#8599;
            </a>
          )}
          {publisher.github_url && (
            <a
              href={publisher.github_url}
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-primary"
            >
              GitHub &#8599;
            </a>
          )}
        </div>
      </div>

      <h2 className="mb-4 text-lg font-semibold text-foreground">
        Packages ({packages.length})
      </h2>

      {packages.length === 0 ? (
        <p className="text-muted">No packages published yet.</p>
      ) : (
        <div className="space-y-3">
          {packages.map((pkg: any) => (
            <Link
              key={pkg.slug}
              href={`/packages/${pkg.slug}`}
              className="block rounded-lg border border-border bg-card p-4 transition-colors hover:border-primary/30"
            >
              <div className="flex items-center justify-between">
                <div>
                  <span className="font-medium text-foreground">{pkg.name || pkg.slug}</span>
                  <span className="ml-2 text-xs text-muted">{pkg.latest_version || ""}</span>
                </div>
                <span className="text-xs text-muted">{pkg.download_count} downloads</span>
              </div>
              <p className="mt-1 text-sm text-muted">{pkg.summary}</p>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
