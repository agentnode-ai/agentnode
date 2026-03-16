"use client";

import { Suspense, useState, useEffect, useCallback } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import SearchInput from "@/components/SearchInput";
import PackageCard from "@/components/PackageCard";
import type { SearchHit, SearchResponse } from "@/lib/api";

const FILTER_OPTIONS = {
  package_type: ["toolpack", "agent", "upgrade"],
  framework: ["langchain", "crewai", "generic"],
  runtime: ["python"],
  trust_level: ["curated", "trusted", "verified", "unverified"],
};

interface Filters {
  package_type: string;
  capability_id: string;
  framework: string;
  runtime: string;
  trust_level: string;
}

function SearchContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const [query, setQuery] = useState(searchParams.get("q") ?? "");
  const [filters, setFilters] = useState<Filters>({
    package_type: searchParams.get("package_type") ?? "",
    capability_id: searchParams.get("capability_id") ?? "",
    framework: searchParams.get("framework") ?? "",
    runtime: searchParams.get("runtime") ?? "",
    trust_level: searchParams.get("trust_level") ?? "",
  });
  const [results, setResults] = useState<SearchHit[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);

  const performSearch = useCallback(
    async (q: string, f: Filters) => {
      setLoading(true);
      setSearched(true);

      const params = new URLSearchParams();
      if (q) params.set("q", q);
      if (f.package_type) params.set("package_type", f.package_type);
      if (f.capability_id) params.set("capability_id", f.capability_id);
      if (f.framework) params.set("framework", f.framework);
      if (f.runtime) params.set("runtime", f.runtime);
      if (f.trust_level) params.set("trust_level", f.trust_level);

      // Update URL
      const qs = params.toString();
      router.replace(`/search${qs ? `?${qs}` : ""}`, { scroll: false });

      try {
        const body: Record<string, unknown> = {};
        if (q) body.q = q;
        if (f.package_type) body.package_type = f.package_type;
        if (f.capability_id) body.capability_id = f.capability_id;
        if (f.framework) body.framework = f.framework;
        if (f.runtime) body.runtime = f.runtime;
        if (f.trust_level) body.trust_level = f.trust_level;

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
    [router]
  );

  // Search on mount — always load packages
  useEffect(() => {
    performSearch(searchParams.get("q") ?? "", filters);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSubmit = useCallback(() => {
    performSearch(query, filters);
  }, [query, filters, performSearch]);

  const handleFilterChange = useCallback(
    (key: keyof Filters, value: string) => {
      const newFilters = {
        ...filters,
        [key]: value === filters[key] ? "" : value,
      };
      setFilters(newFilters);
      performSearch(query, newFilters);
    },
    [filters, query, performSearch]
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
                  />
                ))}
              </div>
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
