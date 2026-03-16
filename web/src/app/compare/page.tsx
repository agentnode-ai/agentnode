"use client";

import { Suspense, useState, useEffect, useCallback } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import TrustBadge from "@/components/TrustBadge";

/* eslint-disable @typescript-eslint/no-explicit-any */

interface PackageData {
  slug: string;
  name: string;
  summary: string;
  package_type: string;
  download_count: number;
  publisher: {
    display_name?: string;
    slug: string;
    trust_level?: "curated" | "trusted" | "verified" | "unverified";
  };
  latest_version?: {
    version_number?: string;
  };
  blocks: {
    capabilities?: Array<{
      capability_id: string;
      name: string;
      description?: string;
    }>;
    install?: {
      install_mode?: string;
      cli_command?: string;
      entrypoint?: string;
    };
    compatibility?: {
      frameworks?: string[];
      python?: string;
    };
    permissions?: {
      network_level?: string;
      filesystem_level?: string;
      code_execution_level?: string;
    };
  };
}

function PermissionCell({ value }: { value?: string }) {
  if (!value) return <span className="text-zinc-500">--</span>;
  return (
    <span
      className={`inline-block rounded-md px-2.5 py-0.5 text-xs font-mono ${
        value === "none"
          ? "bg-green-500/10 text-green-400"
          : value === "unrestricted" || value === "shell" || value === "any"
            ? "bg-red-500/10 text-red-400"
            : "bg-yellow-500/10 text-yellow-400"
      }`}
    >
      {value}
    </span>
  );
}

function CompareContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const [packages, setPackages] = useState<PackageData[]>([]);
  const [loading, setLoading] = useState<Record<string, boolean>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [inputSlug, setInputSlug] = useState("");

  const updateUrl = useCallback(
    (slugs: string[]) => {
      const qs = slugs.length > 0 ? `?packages=${slugs.join(",")}` : "";
      router.replace(`/compare${qs}`, { scroll: false });
    },
    [router]
  );

  const fetchPackage = useCallback(async (slug: string): Promise<PackageData | null> => {
    try {
      const res = await fetch(`/api/v1/packages/${encodeURIComponent(slug)}`);
      if (!res.ok) return null;
      return res.json();
    } catch {
      return null;
    }
  }, []);

  const addPackage = useCallback(
    async (slug: string) => {
      const trimmed = slug.trim().toLowerCase();
      if (!trimmed) return;
      if (packages.some((p) => p.slug === trimmed)) return;

      setLoading((prev) => ({ ...prev, [trimmed]: true }));
      setErrors((prev) => {
        const next = { ...prev };
        delete next[trimmed];
        return next;
      });

      const data = await fetchPackage(trimmed);

      if (data) {
        setPackages((prev) => {
          const updated = [...prev, data];
          updateUrl(updated.map((p) => p.slug));
          return updated;
        });
      } else {
        setErrors((prev) => ({ ...prev, [trimmed]: `Package "${trimmed}" not found` }));
      }

      setLoading((prev) => {
        const next = { ...prev };
        delete next[trimmed];
        return next;
      });
    },
    [packages, fetchPackage, updateUrl]
  );

  const removePackage = useCallback(
    (slug: string) => {
      setPackages((prev) => {
        const updated = prev.filter((p) => p.slug !== slug);
        updateUrl(updated.map((p) => p.slug));
        return updated;
      });
    },
    [updateUrl]
  );

  // Load packages from URL on mount
  useEffect(() => {
    const raw = searchParams.get("packages");
    if (!raw) return;
    const slugs = raw
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);

    let cancelled = false;

    async function loadAll() {
      const loadingState: Record<string, boolean> = {};
      slugs.forEach((s) => (loadingState[s] = true));
      setLoading(loadingState);

      const results = await Promise.all(slugs.map((s) => fetchPackage(s)));
      if (cancelled) return;

      const loaded: PackageData[] = [];
      const errs: Record<string, string> = {};
      results.forEach((r, i) => {
        if (r) loaded.push(r);
        else errs[slugs[i]] = `Package "${slugs[i]}" not found`;
      });

      setPackages(loaded);
      setErrors(errs);
      setLoading({});
    }

    loadAll();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleAdd = () => {
    addPackage(inputSlug);
    setInputSlug("");
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      e.preventDefault();
      handleAdd();
    }
  };

  const isLoading = Object.values(loading).some(Boolean);

  const rows: {
    label: string;
    render: (pkg: PackageData) => React.ReactNode;
  }[] = [
    {
      label: "Name",
      render: (pkg) => (
        <a
          href={`/packages/${pkg.slug}`}
          className="font-medium text-primary hover:underline"
        >
          {pkg.name}
        </a>
      ),
    },
    {
      label: "Version",
      render: (pkg) => (
        <span className="font-mono text-foreground">
          {pkg.latest_version?.version_number ?? "--"}
        </span>
      ),
    },
    {
      label: "Publisher",
      render: (pkg) => (
        <span className="text-foreground">
          {pkg.publisher?.display_name ?? pkg.publisher?.slug ?? "--"}
        </span>
      ),
    },
    {
      label: "Trust Level",
      render: (pkg) => {
        const level = pkg.publisher?.trust_level ?? "unverified";
        return <TrustBadge level={level} />;
      },
    },
    {
      label: "Runtime",
      render: (pkg) => (
        <span className="text-foreground">
          {pkg.blocks?.compatibility?.frameworks?.length
            ? pkg.blocks.compatibility.frameworks.join(", ")
            : pkg.package_type === "toolpack"
              ? "Python"
              : pkg.package_type ?? "--"}
        </span>
      ),
    },
    {
      label: "Install Mode",
      render: (pkg) => (
        <span className="font-mono text-foreground">
          {pkg.blocks?.install?.install_mode ?? "--"}
        </span>
      ),
    },
    {
      label: "Network",
      render: (pkg) => (
        <PermissionCell value={pkg.blocks?.permissions?.network_level} />
      ),
    },
    {
      label: "Filesystem",
      render: (pkg) => (
        <PermissionCell value={pkg.blocks?.permissions?.filesystem_level} />
      ),
    },
    {
      label: "Code Execution",
      render: (pkg) => (
        <PermissionCell value={pkg.blocks?.permissions?.code_execution_level} />
      ),
    },
    {
      label: "Downloads",
      render: (pkg) => (
        <span className="font-mono text-foreground">
          {(pkg.download_count ?? 0).toLocaleString()}
        </span>
      ),
    },
    {
      label: "Capabilities",
      render: (pkg) => {
        const caps = pkg.blocks?.capabilities ?? [];
        if (caps.length === 0) return <span className="text-zinc-500">--</span>;
        return (
          <div className="flex flex-wrap gap-1.5">
            {caps.map((cap) => (
              <span
                key={cap.capability_id}
                className="rounded-md border border-primary/20 bg-primary/5 px-2 py-0.5 text-xs font-mono text-primary"
              >
                {cap.capability_id}
              </span>
            ))}
          </div>
        );
      },
    },
  ];

  return (
    <div className="mx-auto max-w-6xl px-6 py-12">
      {/* Header */}
      <div className="mb-8">
        <h1 className="mb-2 text-3xl font-bold text-foreground">
          Compare Packages
        </h1>
        <p className="text-sm text-muted">
          Add packages by slug to compare them side by side.
        </p>
      </div>

      {/* Add package input */}
      <div className="mb-8 flex items-center gap-3">
        <input
          type="text"
          value={inputSlug}
          onChange={(e) => setInputSlug(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Enter package slug..."
          className="rounded-md border border-border bg-card px-4 py-2 text-sm text-foreground placeholder:text-muted focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
        />
        <button
          onClick={handleAdd}
          disabled={!inputSlug.trim() || isLoading}
          className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Add
        </button>
      </div>

      {/* Error messages */}
      {Object.values(errors).length > 0 && (
        <div className="mb-6 space-y-2">
          {Object.entries(errors).map(([slug, msg]) => (
            <div
              key={slug}
              className="rounded-md border border-red-500/20 bg-red-500/10 px-4 py-2 text-sm text-red-400"
            >
              {msg}
            </div>
          ))}
        </div>
      )}

      {/* Loading state */}
      {isLoading && (
        <div className="mb-6 flex items-center gap-2 text-sm text-muted">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-muted border-t-primary" />
          Loading packages...
        </div>
      )}

      {/* Empty state */}
      {packages.length === 0 && !isLoading && (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <p className="text-lg font-medium text-foreground">
            No packages to compare
          </p>
          <p className="mt-2 text-sm text-muted">
            Add at least two packages using the input above to start comparing.
          </p>
        </div>
      )}

      {/* Comparison table */}
      {packages.length > 0 && (
        <div className="overflow-x-auto rounded-xl border border-border">
          <table className="w-full min-w-[600px] border-collapse">
            <thead>
              <tr>
                <th className="border-b border-border bg-card/50 p-4 text-left text-sm font-medium text-muted">
                  Attribute
                </th>
                {packages.map((pkg) => (
                  <th
                    key={pkg.slug}
                    className="border-b border-border bg-card/50 p-4 text-left text-sm font-medium text-muted"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono">{pkg.slug}</span>
                      <button
                        onClick={() => removePackage(pkg.slug)}
                        className="rounded p-1 text-zinc-500 hover:bg-red-500/10 hover:text-red-400 transition-colors"
                        title={`Remove ${pkg.slug}`}
                      >
                        <svg
                          xmlns="http://www.w3.org/2000/svg"
                          viewBox="0 0 20 20"
                          fill="currentColor"
                          className="h-4 w-4"
                        >
                          <path d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z" />
                        </svg>
                      </button>
                    </div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.label}>
                  <td className="border-b border-border p-4 text-sm font-medium text-muted whitespace-nowrap">
                    {row.label}
                  </td>
                  {packages.map((pkg) => (
                    <td
                      key={pkg.slug}
                      className="border-b border-border p-4 text-sm"
                    >
                      {row.render(pkg)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function CompareFallback() {
  return (
    <div className="mx-auto max-w-6xl px-6 py-12">
      <div className="mb-8">
        <h1 className="mb-2 text-3xl font-bold text-foreground">
          Compare Packages
        </h1>
        <div className="mt-4 h-10 w-64 animate-pulse rounded-md border border-border bg-card" />
      </div>
    </div>
  );
}

export default function ComparePage() {
  return (
    <Suspense fallback={<CompareFallback />}>
      <CompareContent />
    </Suspense>
  );
}
