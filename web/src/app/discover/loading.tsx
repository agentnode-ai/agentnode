import { ListSkeleton } from "@/components/Skeleton";

/**
 * P1-F9: Route-level loading UI for /discover.
 *
 * Next.js App Router renders this while the server component's data
 * fetches are in-flight, replacing the previous blank-then-jump flash
 * with a stable skeleton grid.
 */
export default function Loading() {
  return (
    <div className="mx-auto max-w-6xl px-6 py-12">
      <header className="mb-10">
        <h1 className="text-3xl font-bold text-foreground">Discover</h1>
        <p className="mt-2 text-base text-muted">
          Explore trending and recently published packages on AgentNode.
        </p>
      </header>
      <section className="mb-14">
        <h2 className="text-2xl font-bold text-foreground mb-6">Trending Packages</h2>
        <ListSkeleton count={6} />
      </section>
      <section>
        <h2 className="text-2xl font-bold text-foreground mb-6">Recently Published</h2>
        <ListSkeleton count={6} />
      </section>
    </div>
  );
}
