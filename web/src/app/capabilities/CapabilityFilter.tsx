"use client";

import { useState, useMemo } from "react";
import Link from "next/link";
import SearchInput from "@/components/SearchInput";

interface Capability {
  id: string;
  display_name: string;
  description: string | null;
  category: string | null;
  package_count?: number;
}

interface CategoryGroup {
  slug: string;
  name: string;
  capabilities: Capability[];
}

interface CapabilityFilterProps {
  capabilities: Capability[];
  groupedCategories: CategoryGroup[];
}

export default function CapabilityFilter({
  capabilities,
  groupedCategories,
}: CapabilityFilterProps) {
  const [searchQuery, setSearchQuery] = useState("");

  const filteredGroups = useMemo(() => {
    if (!searchQuery.trim()) return groupedCategories;

    const q = searchQuery.toLowerCase();
    return groupedCategories
      .map((group) => ({
        ...group,
        capabilities: group.capabilities.filter(
          (cap) =>
            cap.display_name.toLowerCase().includes(q) ||
            cap.id.toLowerCase().includes(q) ||
            (cap.category && cap.category.toLowerCase().includes(q))
        ),
      }))
      .filter((group) => group.capabilities.length > 0);
  }, [searchQuery, groupedCategories]);

  const totalVisible = filteredGroups.reduce(
    (sum, g) => sum + g.capabilities.length,
    0
  );

  return (
    <>
      {/* Search */}
      <div className="mb-6">
        <SearchInput
          value={searchQuery}
          onChange={setSearchQuery}
          placeholder="Filter capabilities..."
        />
        {searchQuery.trim() && (
          <p className="mt-2 text-xs text-muted">
            {totalVisible} of {capabilities.length} capabilities
          </p>
        )}
      </div>

      {/* Sticky category navigation */}
      <nav className="sticky top-16 z-10 -mx-6 mb-8 overflow-x-auto bg-background/95 px-6 py-3 backdrop-blur-sm">
        <div className="flex gap-2">
          {filteredGroups.map((group) => (
            <a
              key={group.slug}
              href={`#cat-${group.slug}`}
              className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:border-primary/30 hover:text-primary"
            >
              <span className="capitalize">{group.name}</span>
              <span className="text-muted">({group.capabilities.length})</span>
            </a>
          ))}
        </div>
      </nav>

      {/* No results */}
      {filteredGroups.length === 0 && (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <p className="text-lg font-medium text-foreground">
            No matching capabilities
          </p>
          <p className="mt-2 text-sm text-muted">
            Try a different search term.
          </p>
        </div>
      )}

      {/* Category grid */}
      <div className="space-y-10">
        {filteredGroups.map((group) => (
          <section
            key={group.slug}
            id={`cat-${group.slug}`}
            className="scroll-mt-28"
          >
            <div className="mb-4 flex items-center gap-3">
              <h2 className="text-lg font-semibold capitalize text-foreground">
                {group.name}
              </h2>
              <span className="rounded-full bg-primary/10 px-2.5 py-0.5 text-xs font-medium text-primary">
                {group.capabilities.length}
              </span>
            </div>

            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {group.capabilities.map((cap) => (
                <Link
                  key={cap.id}
                  href={`/search?capability_id=${cap.id}`}
                  className="flex flex-col rounded-xl border border-border bg-card p-6 transition-all hover:border-primary/30"
                >
                  <h3 className="text-lg font-semibold text-foreground">
                    {cap.display_name}
                  </h3>
                  <div className="mt-0.5 font-mono text-xs text-muted">
                    {cap.id}
                  </div>
                  {cap.description ? (
                    <p className="mt-2 line-clamp-2 text-sm text-muted">
                      {cap.description}
                    </p>
                  ) : (
                    <span className="mt-2 text-xs italic text-muted">
                      No description yet
                    </span>
                  )}
                  <div className="mt-auto pt-3">
                    {cap.package_count != null && cap.package_count > 0 ? (
                      <span className="inline-flex items-center rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
                        {cap.package_count}{" "}
                        {cap.package_count === 1 ? "package" : "packages"}
                      </span>
                    ) : (
                      <span className="text-xs text-muted">
                        No packages yet
                      </span>
                    )}
                  </div>
                </Link>
              ))}
            </div>
          </section>
        ))}
      </div>
    </>
  );
}
