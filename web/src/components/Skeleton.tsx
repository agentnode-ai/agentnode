/**
 * P1-F9: Reusable loading skeletons for list/card views.
 *
 * Used on discover, search, and any other page that fetches a list of
 * packages. The goal is to stop the previous "blank → jump" flash and
 * give the browser a stable layout while the data is in-flight.
 *
 * Skeletons are marked with role="status" + aria-live="polite" so screen
 * readers announce loading state, and with aria-hidden on the decorative
 * shimmer bars so only the "Loading…" sr-only label is read.
 */

export function PackageCardSkeleton() {
  return (
    <div
      aria-hidden="true"
      className="animate-pulse rounded-lg border border-border bg-card p-5"
    >
      <div className="mb-3 h-4 w-2/3 rounded bg-border" />
      <div className="mb-2 h-3 w-full rounded bg-border/70" />
      <div className="mb-4 h-3 w-4/5 rounded bg-border/70" />
      <div className="flex gap-2">
        <div className="h-5 w-16 rounded-full bg-border/60" />
        <div className="h-5 w-20 rounded-full bg-border/60" />
      </div>
    </div>
  );
}

export function ListSkeleton({ count = 6 }: { count?: number }) {
  return (
    <div role="status" aria-live="polite" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      <span className="sr-only">Loading packages…</span>
      {Array.from({ length: count }).map((_, i) => (
        <PackageCardSkeleton key={i} />
      ))}
    </div>
  );
}
