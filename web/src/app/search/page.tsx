"use client";

import { Suspense, useState, useEffect, useCallback, useRef } from "react";
import { useSearchParams } from "next/navigation";
import SearchInput from "@/components/SearchInput";
import PackageCard from "@/components/PackageCard";
import type { SearchHit, SearchResponse } from "@/lib/api";

const PER_PAGE = 20;

const FILTER_OPTIONS = {
  framework: ["langchain", "crewai", "generic"],
  trust_level: ["curated", "trusted", "verified", "unverified"],
  verification_tier: ["gold", "verified", "partial"],
};

const FILTER_LABELS: Record<string, string> = {
  framework: "Framework",
  trust_level: "Publisher Trust",
  verification_tier: "Code Verification",
};

const OPTION_LABELS: Record<string, string> = {
  generic: "Any framework",
};

const SORT_OPTIONS = [
  { value: "", label: "Relevance" },
  { value: "downloads", label: "Most downloads" },
  { value: "newest", label: "Newest first" },
  { value: "name", label: "Name A-Z" },
];

const SORT_VALUE_TO_API: Record<string, string> = {
  downloads: "download_count:desc",
  newest: "published_at:desc",
  name: "name:asc",
};

interface Filters {
  capability_id: string;
  framework: string;
  trust_level: string;
  verification_tier: string;
}

interface CapabilityItem {
  id: string;
  display_name: string;
  description: string;
  category: string;
}

function SearchContent() {
  const searchParams = useSearchParams();
  const hasInitializedRef = useRef(false);

  const [query, setQuery] = useState(searchParams.get("q") ?? "");
  const [page, setPage] = useState(() => {
    const p = parseInt(searchParams.get("page") ?? "1", 10);
    return p >= 1 ? p : 1;
  });
  const [sortBy, setSortBy] = useState(searchParams.get("sort") ?? "");
  const [filters, setFilters] = useState<Filters>({
    capability_id: searchParams.get("capability_id") ?? "",
    framework: searchParams.get("framework") ?? "",
    trust_level: searchParams.get("trust_level") ?? "",
    verification_tier: searchParams.get("verification_tier") ?? "",
  });
  const [results, setResults] = useState<SearchHit[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filtersOpen, setFiltersOpen] = useState(false);

  // Capabilities
  const [capabilities, setCapabilities] = useState<CapabilityItem[]>([]);
  const [capSearch, setCapSearch] = useState("");
  const [capDropdownOpen, setCapDropdownOpen] = useState(false);
  const capRef = useRef<HTMLDivElement>(null);

  // Load capabilities once
  useEffect(() => {
    fetch("/api/v1/capabilities")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data?.capabilities) setCapabilities(data.capabilities);
      })
      .catch(() => {});
  }, []);

  // Close capability dropdown on outside click
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (capRef.current && !capRef.current.contains(e.target as Node)) {
        setCapDropdownOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

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
    async (q: string, f: Filters, p: number, sort: string) => {
      setLoading(true);
      setError(null);

      const params = new URLSearchParams();
      if (q) params.set("q", q);
      if (f.capability_id) params.set("capability_id", f.capability_id);
      if (f.framework) params.set("framework", f.framework);
      if (f.trust_level) params.set("trust_level", f.trust_level);
      if (f.verification_tier) params.set("verification_tier", f.verification_tier);
      if (sort) params.set("sort", sort);
      if (p > 1) params.set("page", String(p));

      // Update URL without triggering React re-render
      const qs = params.toString();
      window.history.replaceState(null, "", `/search${qs ? `?${qs}` : ""}`);

      try {
        const body: Record<string, unknown> = { per_page: PER_PAGE, page: p };
        if (q) body.q = q;
        if (f.capability_id) body.capability_id = f.capability_id;
        if (f.framework) body.framework = f.framework;
        if (f.trust_level) body.trust_level = f.trust_level;
        if (f.verification_tier) body.verification_tier = f.verification_tier;
        const apiSort = SORT_VALUE_TO_API[sort];
        if (apiSort) body.sort_by = apiSort;

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
          setError("Search failed — please try again");
        }
      } catch {
        setError("Network error — check your connection");
      } finally {
        setLoading(false);
      }
    },
    []
  );

  // Search on mount — always load packages
  useEffect(() => {
    performSearch(searchParams.get("q") ?? "", filters, page, sortBy);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Debounced search while typing (300ms) — skip initial mount
  useEffect(() => {
    if (!hasInitializedRef.current) {
      hasInitializedRef.current = true;
      return;
    }
    const timer = setTimeout(() => {
      setPage(1);
      performSearch(query, filters, 1, sortBy);
    }, 300);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  const handleSubmit = useCallback(() => {
    setPage(1);
    performSearch(query, filters, 1, sortBy);
  }, [query, filters, sortBy, performSearch]);

  const handleFilterChange = useCallback(
    (key: keyof Filters, value: string) => {
      const newFilters = {
        ...filters,
        [key]: value === filters[key] ? "" : value,
      };
      setFilters(newFilters);
      setPage(1);
      performSearch(query, newFilters, 1, sortBy);
    },
    [filters, query, sortBy, performSearch]
  );

  const handleSortChange = useCallback(
    (value: string) => {
      setSortBy(value);
      setPage(1);
      performSearch(query, filters, 1, value);
    },
    [query, filters, performSearch]
  );

  const activeFilterCount = Object.values(filters).filter(Boolean).length;

  const handleClearFilters = useCallback(() => {
    const cleared: Filters = {
      capability_id: "",
      framework: "",
      trust_level: "",
      verification_tier: "",
    };
    setFilters(cleared);
    setPage(1);
    performSearch(query, cleared, 1, sortBy);
  }, [query, sortBy, performSearch]);

  const totalPages = Math.ceil(total / PER_PAGE);

  const goToPage = useCallback(
    (p: number) => {
      if (p < 1 || p > totalPages || p === page) return;
      setPage(p);
      performSearch(query, filters, p, sortBy);
      window.scrollTo({ top: 0, behavior: "smooth" });
    },
    [page, totalPages, query, filters, sortBy, performSearch]
  );

  // Capability dropdown filtering
  const capQuery = capSearch.toLowerCase();
  const filteredCaps = capQuery
    ? capabilities.filter(
        (c) =>
          c.id.toLowerCase().includes(capQuery) ||
          c.display_name.toLowerCase().includes(capQuery) ||
          c.category.toLowerCase().includes(capQuery)
      )
    : capabilities;

  const groupedCaps: Record<string, CapabilityItem[]> = {};
  for (const c of filteredCaps.slice(0, 60)) {
    if (!groupedCaps[c.category]) groupedCaps[c.category] = [];
    groupedCaps[c.category].push(c);
  }

  const sidebarContent = (
    <div className="space-y-6 lg:sticky lg:top-24">
      {/* Clear filters */}
      {activeFilterCount > 0 && (
        <button
          onClick={handleClearFilters}
          className="w-full rounded-md border border-border px-3 py-2 text-xs text-muted transition-colors hover:text-foreground hover:border-primary/30"
        >
          Clear filters ({activeFilterCount})
        </button>
      )}

      {/* Capability dropdown */}
      <div ref={capRef}>
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted">
          Capability
        </h3>
        {filters.capability_id && (
          <button
            onClick={() => handleFilterChange("capability_id", filters.capability_id)}
            className="mb-2 flex items-center gap-2 rounded-md border border-primary/20 bg-primary/10 px-3 py-1.5 text-xs text-primary"
          >
            <span className="font-mono">{filters.capability_id}</span>
            <span className="text-muted hover:text-foreground">&#10005;</span>
          </button>
        )}
        <div className="relative">
          <input
            type="text"
            value={capDropdownOpen ? capSearch : ""}
            onChange={(e) => {
              setCapSearch(e.target.value);
              if (!capDropdownOpen) setCapDropdownOpen(true);
            }}
            onFocus={() => {
              setCapDropdownOpen(true);
              setCapSearch("");
            }}
            placeholder="Search capabilities..."
            aria-label="Filter by capability"
            className="w-full rounded-md border border-border bg-card px-3 py-1.5 text-xs text-foreground placeholder-muted focus:border-primary focus:outline-none"
          />
          {capDropdownOpen && (
            <div className="absolute z-50 mt-1 max-h-48 w-full overflow-auto rounded-md border border-border bg-card shadow-lg">
              {Object.keys(groupedCaps).length === 0 ? (
                <div className="px-3 py-2 text-xs text-muted">No capabilities found</div>
              ) : (
                Object.entries(groupedCaps).map(([category, items]) => (
                  <div key={category}>
                    <div className="sticky top-0 bg-card/95 backdrop-blur-sm px-3 py-1 text-[10px] font-semibold uppercase tracking-wider text-muted/60 border-b border-border/50">
                      {category}
                    </div>
                    {items.map((c) => (
                      <button
                        key={c.id}
                        type="button"
                        onClick={() => {
                          handleFilterChange("capability_id", c.id);
                          setCapDropdownOpen(false);
                          setCapSearch("");
                        }}
                        className={`block w-full px-3 py-1.5 text-left text-xs hover:bg-primary/10 ${
                          c.id === filters.capability_id ? "bg-primary/5 text-primary" : "text-foreground"
                        }`}
                      >
                        <span>{c.display_name}</span>
                        <span className="ml-1 font-mono text-[10px] text-muted/60">{c.id}</span>
                      </button>
                    ))}
                  </div>
                ))
              )}
            </div>
          )}
        </div>
      </div>

      {/* Filter sections */}
      {(
        Object.entries(FILTER_OPTIONS) as [keyof Filters, string[]][]
      ).map(([key, options]) => (
        <div key={key} role="group" aria-labelledby={`filter-${key}`}>
          <h3
            id={`filter-${key}`}
            className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted"
            title={
              key === "trust_level"
                ? "How much the publisher has been vetted"
                : key === "verification_tier"
                  ? "Automated code verification score"
                  : undefined
            }
          >
            {FILTER_LABELS[key] ?? key.replace(/_/g, " ")}
          </h3>
          <div className="flex flex-wrap gap-1.5 lg:flex-col">
            {options.map((option) => {
              const isActive = filters[key] === option;
              return (
                <button
                  key={option}
                  onClick={() => handleFilterChange(key, option)}
                  aria-pressed={isActive}
                  className={`rounded-md px-3 py-1.5 text-left text-xs transition-colors ${
                    isActive
                      ? "bg-primary/10 text-primary border border-primary/20"
                      : "text-muted border border-transparent hover:text-foreground hover:bg-card"
                  }`}
                >
                  {OPTION_LABELS[option] ?? option}
                </button>
              );
            })}
          </div>
        </div>
      ))}
    </div>
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
        {/* Filters sidebar — desktop */}
        <aside className="hidden w-full shrink-0 lg:block lg:w-56">
          {sidebarContent}
        </aside>

        {/* Results */}
        <div className="flex-1">
          {/* Mobile filter toggle */}
          <div className="mb-4 lg:hidden">
            <button
              onClick={() => setFiltersOpen(!filtersOpen)}
              className="rounded-lg border border-border px-4 py-2 text-sm text-muted transition-colors hover:text-foreground hover:border-primary/30"
            >
              Filters
              {activeFilterCount > 0 && (
                <span className="ml-1.5 inline-flex h-5 min-w-[1.25rem] items-center justify-center rounded-full bg-primary/10 px-1.5 text-xs text-primary">
                  {activeFilterCount}
                </span>
              )}
            </button>
            {filtersOpen && (
              <div className="mt-4">{sidebarContent}</div>
            )}
          </div>

          {error ? (
            <div className="flex flex-col items-center justify-center rounded-lg border border-red-500/20 bg-red-500/5 py-12 text-center">
              <p className="text-lg font-medium text-red-400">{error}</p>
              <button
                onClick={() => performSearch(query, filters, page, sortBy)}
                className="mt-4 rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2 text-sm text-red-400 transition-colors hover:bg-red-500/20"
              >
                Retry
              </button>
            </div>
          ) : (
            <>
              {loading && results.length === 0 && (
                <div className="flex items-center justify-center py-20">
                  <div className="text-sm text-muted">Searching...</div>
                </div>
              )}

              {!loading && results.length === 0 && (
                <div className="flex flex-col items-center justify-center py-20 text-center">
                  <p className="text-lg font-medium text-foreground">
                    No packages found
                  </p>
                  <p className="mt-2 text-sm text-muted">
                    Try a different search term or adjust your filters.
                  </p>
                </div>
              )}

              {results.length > 0 && (
                <div className={`transition-opacity ${loading ? "opacity-50 pointer-events-none" : ""}`}>
                  {/* Results header with count + sort */}
                  <div className="mb-4 flex items-center justify-between gap-4">
                    <div className="text-sm text-muted">
                      {total} package{total !== 1 ? "s" : ""} found
                      {totalPages > 1 && (
                        <span className="ml-1">
                          — page {page} of {totalPages}
                        </span>
                      )}
                    </div>
                    <select
                      value={sortBy}
                      onChange={(e) => handleSortChange(e.target.value)}
                      aria-label="Sort results"
                      className="shrink-0 rounded-md border border-border bg-card px-3 py-1.5 text-xs text-foreground focus:border-primary focus:outline-none"
                    >
                      {SORT_OPTIONS.map((opt) => (
                        <option key={opt.value} value={opt.value}>
                          {opt.label}
                        </option>
                      ))}
                    </select>
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
                            <span key={`dots-${i}`} className="px-2 text-sm text-muted" aria-hidden="true">...</span>
                          ) : (
                            <button
                              key={p}
                              onClick={() => goToPage(p)}
                              aria-label={`Page ${p}`}
                              aria-current={p === page ? "page" : undefined}
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
                </div>
              )}
            </>
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
