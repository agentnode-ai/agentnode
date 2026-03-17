"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { fetchWithAuth } from "@/lib/api";

interface PackageItem {
  id: string;
  slug: string;
  name: string;
  package_type: string;
  publisher_slug: string | null;
  download_count: number;
  is_deprecated: boolean;
  created_at: string | null;
}

interface QuarantinedVersion {
  package_slug: string;
  version_number: string;
  quarantine_status: string;
  quarantined_at: string;
  quarantine_reason: string | null;
}

export default function AdminPackagesPage() {
  const [packages, setPackages] = useState<PackageItem[]>([]);
  const [quarantined, setQuarantined] = useState<QuarantinedVersion[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [tab, setTab] = useState<"all" | "quarantine">("all");
  const [confirmAction, setConfirmAction] = useState<{ slug: string; version: string; action: "clear" | "reject" } | null>(null);

  useEffect(() => { loadData(); }, [page, search]);

  async function loadData() {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(page), per_page: "50" });
      if (search) params.set("search", search);
      const [pkgRes, qRes] = await Promise.all([
        fetchWithAuth(`/admin/packages?${params}`),
        fetchWithAuth("/admin/quarantined"),
      ]);
      if (pkgRes.ok) { const d = await pkgRes.json(); setPackages(d.packages); setTotal(d.total); }
      if (qRes.ok) { setQuarantined(await qRes.json()); }
    } catch { setError("Failed to load data"); }
    finally { setLoading(false); }
  }

  async function executeQuarantineAction() {
    if (!confirmAction) return;
    setError(""); setSuccess("");
    const { slug, version, action } = confirmAction;
    const res = await fetchWithAuth(`/admin/packages/${slug}/versions/${version}/${action}`, { method: "POST" });
    if (res.ok) {
      setSuccess(action === "clear" ? `Quarantine cleared: ${slug}@${version}` : `Rejected: ${slug}@${version}`);
      await loadData();
    } else {
      const d = await res.json();
      setError(d.error?.message || "Failed");
    }
    setConfirmAction(null);
  }

  const totalPages = Math.ceil(total / 50);

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-bold text-foreground">Packages ({total})</h1>
        {quarantined.length > 0 && (
          <span className="rounded-full bg-danger/20 px-3 py-1 text-xs font-medium text-danger">
            {quarantined.length} quarantined
          </span>
        )}
      </div>

      {error && (
        <div className="mb-4 rounded-md border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">
          {error} <button onClick={() => setError("")} className="ml-2 underline">dismiss</button>
        </div>
      )}
      {success && (
        <div className="mb-4 rounded-md border border-success/30 bg-success/10 px-4 py-3 text-sm text-success">
          {success} <button onClick={() => setSuccess("")} className="ml-2 underline">dismiss</button>
        </div>
      )}

      {/* Confirmation dialog for quarantine actions */}
      {confirmAction && (
        <div className={`mb-4 rounded-lg border p-4 ${confirmAction.action === "reject" ? "border-danger/30 bg-danger/5" : "border-success/30 bg-success/5"}`}>
          <p className="text-sm text-foreground">
            {confirmAction.action === "clear"
              ? <>Clear quarantine for <span className="font-mono font-medium">{confirmAction.slug}@{confirmAction.version}</span>? This will make the version available again.</>
              : <>Reject <span className="font-mono font-medium">{confirmAction.slug}@{confirmAction.version}</span>? This version will be permanently rejected.</>
            }
          </p>
          <div className="mt-3 flex gap-2">
            <button
              onClick={executeQuarantineAction}
              className={`rounded px-4 py-1.5 text-sm font-medium text-white ${confirmAction.action === "clear" ? "bg-success hover:bg-success/90" : "bg-danger hover:bg-danger/90"}`}
            >
              Confirm {confirmAction.action === "clear" ? "Clear" : "Reject"}
            </button>
            <button onClick={() => setConfirmAction(null)} className="rounded border border-border px-4 py-1.5 text-sm text-muted hover:bg-card">Cancel</button>
          </div>
        </div>
      )}

      {/* Tabs */}
      <div className="mb-4 flex gap-1 border-b border-border">
        <button onClick={() => setTab("all")} className={`px-4 py-2 text-sm border-b-2 transition-colors ${tab === "all" ? "border-primary text-foreground" : "border-transparent text-muted hover:text-foreground"}`}>All Packages</button>
        <button onClick={() => setTab("quarantine")} className={`px-4 py-2 text-sm border-b-2 transition-colors ${tab === "quarantine" ? "border-primary text-foreground" : "border-transparent text-muted hover:text-foreground"}`}>
          Quarantine Queue {quarantined.length > 0 && `(${quarantined.length})`}
        </button>
      </div>

      {tab === "all" ? (
        <>
          <input
            type="text" placeholder="Search packages..."
            value={search} onChange={(e) => { setSearch(e.target.value); setPage(1); }}
            className="mb-4 w-full rounded-md border border-border bg-card px-4 py-2 text-sm text-foreground placeholder:text-muted/50 focus:border-primary focus:outline-none"
          />
          <div className="rounded-lg border border-border bg-card overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs text-muted">
                  <th className="px-4 py-2.5">Slug</th>
                  <th className="px-4 py-2.5">Name</th>
                  <th className="px-4 py-2.5">Type</th>
                  <th className="px-4 py-2.5">Publisher</th>
                  <th className="px-4 py-2.5 text-right">Downloads</th>
                  <th className="px-4 py-2.5">Created</th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr><td colSpan={6} className="px-4 py-8 text-center text-muted">Loading...</td></tr>
                ) : packages.length === 0 ? (
                  <tr><td colSpan={6} className="px-4 py-8 text-center text-muted">No packages found.</td></tr>
                ) : (
                  packages.map((p) => (
                    <tr key={p.id} className="border-b border-border/50 last:border-0 hover:bg-card/80">
                      <td className="px-4 py-2.5">
                        <Link href={`/packages/${p.slug}`} className="font-medium text-primary hover:underline">{p.slug}</Link>
                        {p.is_deprecated && <span className="ml-2 rounded bg-danger/20 px-1.5 py-0.5 text-xs text-danger">deprecated</span>}
                      </td>
                      <td className="px-4 py-2.5 text-foreground">{p.name}</td>
                      <td className="px-4 py-2.5">
                        <span className="rounded bg-primary/10 px-2 py-0.5 text-xs text-primary">{p.package_type}</span>
                      </td>
                      <td className="px-4 py-2.5">
                        {p.publisher_slug ? (
                          <Link href="/admin/publishers" className="text-primary text-xs hover:underline">@{p.publisher_slug}</Link>
                        ) : (
                          <span className="text-muted text-xs">-</span>
                        )}
                      </td>
                      <td className="px-4 py-2.5 text-right font-mono text-foreground">{p.download_count.toLocaleString()}</td>
                      <td className="px-4 py-2.5 text-muted text-xs">{p.created_at ? new Date(p.created_at).toLocaleDateString() : "-"}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
          {totalPages > 1 && (
            <div className="mt-4 flex items-center justify-between text-sm text-muted">
              <span>Page {page} of {totalPages}</span>
              <div className="flex gap-2">
                <button onClick={() => setPage(Math.max(1, page - 1))} disabled={page <= 1} className="rounded border border-border px-3 py-1 hover:bg-card disabled:opacity-50">Prev</button>
                <button onClick={() => setPage(Math.min(totalPages, page + 1))} disabled={page >= totalPages} className="rounded border border-border px-3 py-1 hover:bg-card disabled:opacity-50">Next</button>
              </div>
            </div>
          )}
        </>
      ) : (
        <div className="space-y-3">
          {quarantined.length === 0 ? (
            <div className="rounded-lg border border-border bg-card p-8 text-center text-sm text-muted">No quarantined versions.</div>
          ) : (
            quarantined.map((v) => (
              <div key={`${v.package_slug}-${v.version_number}`} className="flex items-center justify-between rounded-lg border border-border bg-card px-4 py-3">
                <div>
                  <Link href={`/packages/${v.package_slug}`} className="font-medium text-primary hover:underline">
                    {v.package_slug}@{v.version_number}
                  </Link>
                  {v.quarantine_reason && <span className="ml-2 text-sm text-muted">({v.quarantine_reason})</span>}
                  <div className="text-xs text-muted">{v.quarantined_at ? new Date(v.quarantined_at).toLocaleString() : ""}</div>
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={() => setConfirmAction({ slug: v.package_slug, version: v.version_number, action: "clear" })}
                    className="rounded bg-success/20 px-3 py-1 text-xs font-medium text-success hover:bg-success/30"
                  >Clear</button>
                  <button
                    onClick={() => setConfirmAction({ slug: v.package_slug, version: v.version_number, action: "reject" })}
                    className="rounded bg-danger/20 px-3 py-1 text-xs font-medium text-danger hover:bg-danger/30"
                  >Reject</button>
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
