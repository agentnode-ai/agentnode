"use client";

import { Suspense, useState, useEffect, useCallback } from "react";
import { useSearchParams } from "next/navigation";
import SearchInput from "@/components/SearchInput";
import PackageCard from "@/components/PackageCard";
import type { SearchHit, SearchResponse } from "@/lib/api";

const PER_PAGE = 20;

const FILTER_OPTIONS = {
  package_type: ["toolpack", "upgrade"],
  framework: ["langchain", "crewai", "generic"],
  runtime: ["python"],
  trust_level: ["curated", "trusted", "verified", "unverified"],
  verification_tier: ["gold", "verified", "partial"],
};

interface Filters {
  package_type: string;
  capability_id: string;
  framework: string;
  runtime: string;
  trust_level: string;
  verification_tier: string;
}

function SearchContent() {
  const searchParams = useSearchParams();

  const [query, setQuery] = useState(searchParams.get("q") ?? "");
  const [page, setPage] = useState(() => {
    const p = parseInt(searchParams.get("page") ?? "1", 10);
    return p >= 1 ? p : 1;
  });
  const [filters, setFilters] = useState<Filters>({
    package_type: searchParams.get("package_type") ?? "",
    capability_id: searchParams.get("capability_id") ?? "",
    framework: searchParams.get("framework") ?? "",
    runtime: searchParams.get("runtime") ?? "",
    trust_level: searchParams.get("trust_level") ?? "",
    verification_tier: searchParams.get("verification_tier") ?? "",
  });
  const [results, setResults] = useState<SearchHit[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);

  // noindex for page 2+
  useEffect(() => {
    const existing = document.querySelector('meta[name="robots"]');
    if (page > 1) {
      if (existing) {
        existing.setAttribute("content", "noindex, follow");
      } else {
        const meta = document.createElement("meta");
        meta.name = "robots";
        meta.content = "noindex, follow";
        document.head.appendChild(meta);
      }
    } else {
      if (existing && existing.getAttribute("content") === "noindex, follow") {
        existing.remove();
      }
    }
  }, [page]);

  const performSearch = useCallback(
    async (q: string, f: Filters, p: number) => {
      setLoading(true);
      setSearched(true);

      const params = new URLSearchParams();
      if (q) params.set("q", q);
      if (f.package_type) params.set("package_type", f.package_type);
      if (f.capability_id) params.set("capability_id", f.capability_id);
      if (f.framework) params.set("framework", f.framework);
      if (f.runtime) params.set("runtime", f.runtime);
      if (f.trust_level) params.set("trust_level", f.trust_level);
      if (f.verification_tier) params.set("verification_tier", f.verification_tier);
      if (p > 1) params.set("page", String(p));

      // Update URL without triggering React re-render
      const qs = params.toString();
      window.history.replaceState(null, "", `/search${qs ? `?${qs}` : ""}`);

      try {
        const body: Record<string, unknown> = { per_page: PER_PAGE, page: p };
        if (q) body.q = q;
        if (f.package_type) body.package_type = f.package_type;
        if (f.capability_id) body.capability_id = f.capability_id;
        if (f.framework) body.framework = f.framework;
        if (f.runtime) body.runtime = f.runtime;
        if (f.trust_level) body.trust_level = f.trust_level;
        if (f.verification_tier) body.verification_tier = f.verification_tier;

        const res = await fetch("/api/v1/search", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (res.ok) {
          const data: SearchResponse = await res.json();
          setResults(data.hits);
          setTotal(data.total);
        } else {
          setResults([]);
          setTotal(0);
        }
      } catch {
        setResults([]);
        setTotal(0);
      } finally {
        setLoading(false);
      }
    },
    []
  );

  // Search on mount — always load packages
  useEffect(() => {
    performSearch(searchParams.get("q") ?? "", filters, page);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Debounced search while typing (300ms) — reset to page 1
  useEffect(() => {
    const timer = setTimeout(() => {
      setPage(1);
      performSearch(query, filters, 1);
    }, 300);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  const handleSubmit = useCallback(() => {
    setPage(1);
    performSearch(query, filters, 1);
  }, [query, filters, performSearch]);

  const handleFilterChange = useCallback(
    (key: keyof Filters, value: string) => {
      const newFilters = {
        ...filters,
        [key]: value === filters[key] ? "" : value,
      };
      setFilters(newFilters);
      setPage(1);
      performSearch(query, newFilters, 1);
    },
    [filters, query, performSearch]
  );

  const totalPages = Math.ceil(total / PER_PAGE);

  const goToPage = useCallback(
    (p: number) => {
      if (p < 1 || p > totalPages || p === page) return;
      setPage(p);
      performSearch(query, filters, p);
      window.scrollTo({ top: 0, behavior: "smooth" });
    },
    [page, totalPages, query, filters, performSearch]
  );

  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      {/* Search header */}
      <div className="mb-8">
        <h1 className="mb-4 text-3xl font-bold text-foreground">
          Search Packages
        </h1>
        <SearchInput
          value={query}
          onChange={setQuery}
          onSubmit={handleSubmit}
          placeholder="Search by name, capability, or keyword..."
          size="large"
          autoFocus
        />
      </div>

      <div className="flex flex-col gap-8 lg:flex-row">
        {/* Filters sidebar */}
        <aside className="w-full shrink-0 lg:w-56">
          <div className="sticky top-24 space-y-6">
            {/* Active capability filter badge */}
            {filters.capability_id && (
              <div>
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted">
                  Capability
                </h3>
                <button
                  onClick={() => handleFilterChange("capability_id", filters.capability_id)}
                  className="flex items-center gap-2 rounded-md border border-primary/20 bg-primary/10 px-3 py-1.5 text-xs text-primary"
                >
                  <span className="font-mono">{filters.capability_id}</span>
                  <span className="text-muted hover:text-foreground">✕</span>
                </button>
              </div>
            )}
            {(
              Object.entries(FILTER_OPTIONS) as [keyof Filters, string[]][]
            ).map(([key, options]) => (
              <div key={key}>
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted">
                  {key.replace("_", " ")}
                </h3>
                <div className="flex flex-wrap gap-1.5 lg:flex-col">
                  {options.map((option) => {
                    const isActive = filters[key] === option;
                    return (
                      <button
                        key={option}
                        onClick={() => handleFilterChange(key, option)}
                        className={`rounded-md px-3 py-1.5 text-left text-xs transition-colors ${
                          isActive
                            ? "bg-primary/10 text-primary border border-primary/20"
                            : "text-muted border border-transparent hover:text-foreground hover:bg-card"
                        }`}
                      >
                        {option}
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        </aside>

        {/* Results */}
        <div className="flex-1">
          {loading && (
            <div className="flex items-center justify-center py-20">
              <div className="text-sm text-muted">Searching...</div>
            </div>
          )}

          {!loading && searched && results.length === 0 && (
            <div className="flex flex-col items-center justify-center py-20 text-center">
              <p className="text-lg font-medium text-foreground">
                No packages found
              </p>
              <p className="mt-2 text-sm text-muted">
                Try a different search term or adjust your filters.
              </p>
            </div>
          )}

          {!loading && results.length > 0 && (
            <>
              <div className="mb-4 text-sm text-muted">
                {total} package{total !== 1 ? "s" : ""} found
                {totalPages > 1 && (
                  <span className="ml-1">
                    — page {page} of {totalPages}
                  </span>
                )}
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                {results.map((pkg) => (
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
                    verification_tier={pkg.verification_tier}
                    verification_score={pkg.verification_score}
                    package_type={pkg.package_type}
                    publisher_name={pkg.publisher_name}
                  />
                ))}
              </div>

              {/* Pagination */}
              {totalPages > 1 && (
                <nav className="mt-8 flex items-center justify-center gap-1" aria-label="Pagination">
                  <button
                    onClick={() => goToPage(page - 1)}
                    disabled={page <= 1}
                    className="rounded-lg border border-border px-3 py-2 text-sm text-muted transition-colors hover:text-foreground hover:border-primary/30 disabled:opacity-30 disabled:cursor-not-allowed"
                  >
                    Previous
                  </button>

                  {(() => {
                    const pages: (number | "...")[] = [];
                    if (totalPages <= 7) {
                      for (let i = 1; i <= totalPages; i++) pages.push(i);
                    } else {
                      pages.push(1);
                      if (page > 3) pages.push("...");
                      for (let i = Math.max(2, page - 1); i <= Math.min(totalPages - 1, page + 1); i++) {
                        pages.push(i);
                      }
                      if (page < totalPages - 2) pages.push("...");
                      pages.push(totalPages);
                    }
                    return pages.map((p, i) =>
                      p === "..." ? (
                        <span key={`dots-${i}`} className="px-2 text-sm text-muted">...</span>
                      ) : (
                        <button
                          key={p}
                          onClick={() => goToPage(p)}
                          className={`min-w-[2.5rem] rounded-lg border px-3 py-2 text-sm font-medium transition-colors ${
                            p === page
                              ? "border-primary bg-primary/10 text-primary"
                              : "border-border text-muted hover:text-foreground hover:border-primary/30"
                          }`}
                        >
                          {p}
                        </button>
                      )
                    );
                  })()}

                  <button
                    onClick={() => goToPage(page + 1)}
                    disabled={page >= totalPages}
                    className="rounded-lg border border-border px-3 py-2 text-sm text-muted transition-colors hover:text-foreground hover:border-primary/30 disabled:opacity-30 disabled:cursor-not-allowed"
                  >
                    Next
                  </button>
                </nav>
              )}
            </>
          )}

          {!loading && !searched && (
            <div className="flex flex-col items-center justify-center py-20 text-center">
              <p className="text-lg font-medium text-foreground">
                Search the AgentNode registry
              </p>
              <p className="mt-2 text-sm text-muted">
                Find packages by name, capability, framework, or keyword.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function SearchFallback() {
  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      <div className="mb-8">
        <h1 className="mb-4 text-3xl font-bold text-foreground">
          Search Packages
        </h1>
        <div className="h-14 w-full animate-pulse rounded-xl border border-border bg-card" />
      </div>
    </div>
  );
}

export default function SearchPage() {
  return (
    <Suspense fallback={<SearchFallback />}>
      <SearchContent />
    </Suspense>
  );
}
