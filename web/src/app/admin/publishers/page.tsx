"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { fetchWithAuth } from "@/lib/api";

interface PublisherItem {
  id: string;
  slug: string;
  display_name: string;
  trust_level: string;
  is_suspended: boolean;
  suspension_reason: string | null;
  packages_published_count: number;
  created_at: string | null;
}

const TRUST_LEVELS = ["unverified", "verified", "trusted", "curated"];

const TRUST_COLORS: Record<string, string> = {
  unverified: "bg-muted/20 text-muted",
  verified: "bg-primary/20 text-primary",
  trusted: "bg-success/20 text-success",
  curated: "bg-warning/20 text-warning",
};

export default function AdminPublishersPage() {
  const [publishers, setPublishers] = useState<PublisherItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [filterTrust, setFilterTrust] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [suspendSlug, setSuspendSlug] = useState("");
  const [suspendReason, setSuspendReason] = useState("");
  const [confirmUnsuspend, setConfirmUnsuspend] = useState("");
  const [deleteSlug, setDeleteSlug] = useState("");
  const [deleteConfirmInput, setDeleteConfirmInput] = useState("");

  useEffect(() => { loadPublishers(); }, [page, search, filterTrust]);

  async function loadPublishers() {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(page), per_page: "50" });
      if (search) params.set("search", search);
      if (filterTrust) params.set("trust_level", filterTrust);
      const res = await fetchWithAuth(`/admin/publishers?${params}`);
      if (res.ok) { const data = await res.json(); setPublishers(data.publishers); setTotal(data.total); }
    } catch { setError("Failed to load publishers"); }
    finally { setLoading(false); }
  }

  async function setTrustLevel(slug: string, level: string) {
    setError(""); setSuccess("");
    const res = await fetchWithAuth(`/admin/publishers/${slug}/trust`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ trust_level: level }),
    });
    if (res.ok) { setSuccess(`Trust level for '${slug}' set to '${level}'`); await loadPublishers(); }
    else { const d = await res.json(); setError(d.error?.message || "Failed"); }
  }

  async function suspend(slug: string, reason: string) {
    setError(""); setSuccess("");
    const res = await fetchWithAuth(`/admin/publishers/${slug}/suspend`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason }),
    });
    if (res.ok) { setSuccess(`Publisher '${slug}' suspended`); setSuspendSlug(""); setSuspendReason(""); await loadPublishers(); }
    else { const d = await res.json(); setError(d.error?.message || "Failed"); }
  }

  async function unsuspend(slug: string) {
    setError(""); setSuccess("");
    const res = await fetchWithAuth(`/admin/publishers/${slug}/unsuspend`, {
      method: "POST",
    });
    if (res.ok) { setSuccess(`Publisher '${slug}' unsuspended`); setConfirmUnsuspend(""); await loadPublishers(); }
    else { const d = await res.json(); setError(d.error?.message || "Failed"); }
  }

  async function deletePublisher(slug: string) {
    setError(""); setSuccess("");
    const res = await fetchWithAuth(`/admin/publishers/${slug}`, {
      method: "DELETE",
    });
    if (res.ok) { setSuccess(`Publisher '${slug}' deleted`); setDeleteSlug(""); setDeleteConfirmInput(""); await loadPublishers(); }
    else { const d = await res.json(); setError(d.error?.message || "Failed to delete publisher"); }
  }

  const totalPages = Math.ceil(total / 50);

  return (
    <div>
      <h1 className="mb-6 text-xl font-bold text-foreground">Publishers ({total})</h1>

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

      <div className="mb-4 flex gap-3">
        <input
          type="text" placeholder="Search by slug or name..."
          value={search} onChange={(e) => { setSearch(e.target.value); setPage(1); }}
          className="flex-1 rounded-md border border-border bg-card px-4 py-2 text-sm text-foreground placeholder:text-muted/50 focus:border-primary focus:outline-none"
        />
        <select
          value={filterTrust} onChange={(e) => { setFilterTrust(e.target.value); setPage(1); }}
          className="rounded-md border border-border bg-card px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
        >
          <option value="">All trust levels</option>
          {TRUST_LEVELS.map((l) => <option key={l} value={l}>{l}</option>)}
        </select>
      </div>

      <div className="rounded-lg border border-border bg-card overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs text-muted">
              <th className="px-4 py-2.5">Slug</th>
              <th className="px-4 py-2.5">Name</th>
              <th className="px-4 py-2.5">Trust</th>
              <th className="px-4 py-2.5 text-center">Status</th>
              <th className="px-4 py-2.5 text-right">Packages</th>
              <th className="px-4 py-2.5">Joined</th>
              <th className="px-4 py-2.5"></th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={7} className="px-4 py-8 text-center text-muted">Loading...</td></tr>
            ) : publishers.length === 0 ? (
              <tr><td colSpan={7} className="px-4 py-8 text-center text-muted">No publishers found.</td></tr>
            ) : (
              publishers.map((p) => (
                <tr key={p.id} className="border-b border-border/50 last:border-0 hover:bg-card/80">
                  <td className="px-4 py-2.5">
                    <Link href={`/publishers/${p.slug}`} className="font-medium text-primary hover:underline">@{p.slug}</Link>
                  </td>
                  <td className="px-4 py-2.5 text-foreground">{p.display_name}</td>
                  <td className="px-4 py-2.5">
                    <div className="flex items-center gap-2">
                      <span className={`rounded px-2 py-0.5 text-xs font-medium ${TRUST_COLORS[p.trust_level] || "text-muted"}`}>
                        {p.trust_level}
                      </span>
                      <select
                        value={p.trust_level}
                        onChange={(e) => setTrustLevel(p.slug, e.target.value)}
                        className="rounded border border-border bg-background px-1 py-0.5 text-xs text-foreground focus:outline-none"
                      >
                        {TRUST_LEVELS.map((l) => <option key={l} value={l}>{l}</option>)}
                      </select>
                    </div>
                  </td>
                  <td className="px-4 py-2.5 text-center">
                    {p.is_suspended ? (
                      <span className="rounded bg-danger/20 px-2 py-0.5 text-xs font-medium text-danger" title={p.suspension_reason || ""}>
                        Suspended
                      </span>
                    ) : (
                      <span className="rounded bg-success/20 px-2 py-0.5 text-xs font-medium text-success">Active</span>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-foreground">{p.packages_published_count}</td>
                  <td className="px-4 py-2.5 text-muted text-xs">{p.created_at ? new Date(p.created_at).toLocaleDateString() : "-"}</td>
                  <td className="px-4 py-2.5">
                    <div className="flex items-center gap-1">
                      {p.is_suspended ? (
                        confirmUnsuspend === p.slug ? (
                          <div className="flex gap-1">
                            <button onClick={() => unsuspend(p.slug)} className="rounded bg-primary px-2 py-1 text-xs font-medium text-white hover:bg-primary/90">Confirm</button>
                            <button onClick={() => setConfirmUnsuspend("")} className="text-xs text-muted hover:text-foreground">Cancel</button>
                          </div>
                        ) : (
                          <button onClick={() => setConfirmUnsuspend(p.slug)} className="rounded bg-primary/20 px-2 py-1 text-xs font-medium text-primary hover:bg-primary/30">Unsuspend</button>
                        )
                      ) : (
                        <button
                          onClick={() => setSuspendSlug(p.slug)}
                          className="rounded bg-danger/20 px-2 py-1 text-xs font-medium text-danger hover:bg-danger/30"
                        >Suspend</button>
                      )}
                      <button
                        onClick={() => { setDeleteSlug(p.slug); setDeleteConfirmInput(""); }}
                        className="rounded bg-danger px-2 py-1 text-xs font-medium text-white hover:bg-danger/90"
                      >Delete</button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Suspend dialog */}
      {suspendSlug && (
        <div className="mt-4 rounded-lg border border-danger/30 bg-card p-4">
          <h3 className="mb-2 text-sm font-semibold text-foreground">Suspend @{suspendSlug}</h3>
          <p className="mb-3 text-xs text-muted">This will prevent the publisher from uploading new packages. Enter a reason to confirm.</p>
          <div className="flex gap-2">
            <input
              type="text" placeholder="Reason for suspension..."
              value={suspendReason} onChange={(e) => setSuspendReason(e.target.value)}
              className="flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
            />
            <button
              onClick={() => suspend(suspendSlug, suspendReason)}
              disabled={!suspendReason}
              className="rounded bg-danger px-4 py-2 text-sm text-white hover:bg-danger/90 disabled:opacity-50"
            >Suspend</button>
            <button onClick={() => { setSuspendSlug(""); setSuspendReason(""); }} className="rounded border border-border px-4 py-2 text-sm text-muted hover:bg-card">Cancel</button>
          </div>
        </div>
      )}

      {/* Delete publisher dialog */}
      {deleteSlug && (
        <div className="mt-4 rounded-lg border border-danger/30 bg-card p-4">
          <h3 className="mb-2 text-sm font-semibold text-danger">Delete Publisher @{deleteSlug}</h3>
          <p className="mb-3 text-xs text-muted">
            This action is irreversible. Type <span className="font-mono font-bold text-foreground">{deleteSlug}</span> to confirm deletion.
          </p>
          <div className="flex gap-2">
            <input
              type="text" placeholder={`Type "${deleteSlug}" to confirm...`}
              value={deleteConfirmInput} onChange={(e) => setDeleteConfirmInput(e.target.value)}
              className="flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-danger focus:outline-none"
            />
            <button
              onClick={() => deletePublisher(deleteSlug)}
              disabled={deleteConfirmInput !== deleteSlug}
              className="rounded bg-danger px-4 py-2 text-sm text-white hover:bg-danger/90 disabled:opacity-50"
            >Delete Publisher</button>
            <button onClick={() => { setDeleteSlug(""); setDeleteConfirmInput(""); }} className="rounded border border-border px-4 py-2 text-sm text-muted hover:bg-card">Cancel</button>
          </div>
        </div>
      )}

      {totalPages > 1 && (
        <div className="mt-4 flex items-center justify-between text-sm text-muted">
          <span>Page {page} of {totalPages}</span>
          <div className="flex gap-2">
            <button onClick={() => setPage(Math.max(1, page - 1))} disabled={page <= 1} className="rounded border border-border px-3 py-1 hover:bg-card disabled:opacity-50">Prev</button>
            <button onClick={() => setPage(Math.min(totalPages, page + 1))} disabled={page >= totalPages} className="rounded border border-border px-3 py-1 hover:bg-card disabled:opacity-50">Next</button>
          </div>
        </div>
      )}
    </div>
  );
}
