"use client";

import { useState, useEffect } from "react";
import Link from "next/link";

interface Capability {
  id: string;
  display_name: string;
  description: string | null;
  category: string | null;
}

interface CapabilitiesResponse {
  capabilities: Capability[];
  total: number;
}

export default function CapabilitiesPage() {
  const [capabilities, setCapabilities] = useState<Capability[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function fetchCapabilities() {
      try {
        const res = await fetch("/api/v1/capabilities");
        if (!res.ok) {
          throw new Error(`Failed to fetch capabilities (${res.status})`);
        }
        const data: CapabilitiesResponse = await res.json();
        setCapabilities(data.capabilities);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Unknown error");
      } finally {
        setLoading(false);
      }
    }

    fetchCapabilities();
  }, []);

  // Group capabilities by category
  const grouped = capabilities.reduce<Record<string, Capability[]>>(
    (acc, cap) => {
      const category = cap.category ?? "other";
      if (!acc[category]) acc[category] = [];
      acc[category].push(cap);
      return acc;
    },
    {}
  );

  const sortedCategories = Object.keys(grouped).sort();

  return (
    <div className="mx-auto max-w-6xl px-6 py-12">
      {/* Page header */}
      <div className="mb-10">
        <h1 className="mb-3 text-3xl font-bold text-foreground">
          Capabilities
        </h1>
        <p className="text-sm text-muted">
          Browse all capabilities in the AgentNode taxonomy. Click a capability
          to find packages that provide it.
        </p>
      </div>

      {/* Loading state */}
      {loading && (
        <div className="flex items-center justify-center py-20">
          <div className="text-sm text-muted">Loading capabilities...</div>
        </div>
      )}

      {/* Error state */}
      {!loading && error && (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <p className="text-lg font-medium text-foreground">
            Failed to load capabilities
          </p>
          <p className="mt-2 text-sm text-muted">{error}</p>
        </div>
      )}

      {/* Empty state */}
      {!loading && !error && capabilities.length === 0 && (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <p className="text-lg font-medium text-foreground">
            No capabilities found
          </p>
          <p className="mt-2 text-sm text-muted">
            The capability taxonomy has not been seeded yet.
          </p>
        </div>
      )}

      {/* Capability categories */}
      {!loading && !error && sortedCategories.length > 0 && (
        <div className="space-y-10">
          {sortedCategories.map((category) => (
            <section key={category}>
              <div className="mb-4 flex items-center gap-3">
                <h2 className="text-lg font-semibold capitalize text-foreground">
                  {category.replace(/-/g, " ")}
                </h2>
                <span className="rounded-full bg-primary/10 px-2.5 py-0.5 text-xs font-medium text-primary">
                  {grouped[category].length}
                </span>
              </div>

              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {grouped[category].map((cap) => (
                  <Link
                    key={cap.id}
                    href={`/search?capability_id=${cap.id}`}
                    className="rounded-xl border border-border bg-card p-6 transition-all hover:border-primary/30"
                  >
                    <div className="mb-1 font-mono text-xs text-muted">
                      {cap.id}
                    </div>
                    <h3 className="text-lg font-semibold text-foreground">
                      {cap.display_name}
                    </h3>
                    {cap.description && (
                      <p className="mt-2 text-sm text-muted">
                        {cap.description}
                      </p>
                    )}
                  </Link>
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
