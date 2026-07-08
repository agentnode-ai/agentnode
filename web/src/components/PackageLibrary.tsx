/* ------------------------------------------------------------------ */
/*  Shared hero for the browse library pages (/toolpacks, /skills,     */
/*  /agents, /mcp). The package grid itself comes from PackageSearch   */
/*  (embedded search with locked filters) so every browse page offers  */
/*  the same search bar + filter sidebar experience.                   */
/* ------------------------------------------------------------------ */

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
