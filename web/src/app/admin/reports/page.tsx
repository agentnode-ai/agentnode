"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { fetchWithAuth } from "@/lib/api";

interface Report {
  id: string;
  package_id: string;
  package_slug: string | null;
  package_name: string | null;
  reporter_user_id: string;
  reporter_username: string | null;
  reason: string;
  description: string;
  status: string;
  resolution_note: string | null;
  created_at: string | null;
  resolved_at: string | null;
}

const STATUS_BADGES: Record<string, string> = {
  submitted: "bg-warning/20 text-warning",
  resolved: "bg-success/20 text-success",
  dismissed: "bg-muted/20 text-muted",
};

export default function AdminReportsPage() {
  const [reports, setReports] = useState<Report[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [resolveId, setResolveId] = useState("");
  const [resolveNote, setResolveNote] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [confirmDeleteId, setConfirmDeleteId] = useState("");

  useEffect(() => { loadReports(); }, [filter]);

  async function loadReports() {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (filter) params.set("status", filter);
      const res = await fetchWithAuth(`/admin/reports?${params}`);
      if (res.ok) { const d = await res.json(); setReports(d.reports); setTotal(d.total); }
    } catch { setError("Failed to load reports"); }
    finally { setLoading(false); }
  }

  async function resolveReport(reportId: string, status: "resolved" | "dismissed") {
    setError(""); setSuccess("");
    const res = await fetchWithAuth(`/admin/reports/${reportId}/resolve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status, resolution_note: resolveNote || null }),
    });
    if (res.ok) { setSuccess(`Report ${status}`); setResolveId(""); setResolveNote(""); await loadReports(); }
    else { const d = await res.json(); setError(d.error?.message || "Failed"); }
  }

  async function deleteReport(reportId: string) {
    setError(""); setSuccess("");
    const res = await fetchWithAuth(`/admin/reports/${reportId}`, {
      method: "DELETE",
    });
    if (res.ok) { setSuccess("Report deleted"); setConfirmDeleteId(""); await loadReports(); }
    else { const d = await res.json(); setError(d.error?.message || "Failed to delete report"); }
  }

  async function reopenReport(reportId: string) {
    setError(""); setSuccess("");
    const res = await fetchWithAuth(`/admin/reports/${reportId}/reopen`, {
      method: "POST",
    });
    if (res.ok) { setSuccess("Report reopened"); await loadReports(); }
    else { const d = await res.json(); setError(d.error?.message || "Failed to reopen report"); }
  }

  const filteredReports = reports.filter((r) => {
    if (!searchQuery) return true;
    const q = searchQuery.toLowerCase();
    return (
      (r.package_name && r.package_name.toLowerCase().includes(q)) ||
      (r.package_slug && r.package_slug.toLowerCase().includes(q)) ||
      (r.reporter_username && r.reporter_username.toLowerCase().includes(q))
    );
  });

  return (
    <div>
      <h1 className="mb-6 text-xl font-bold text-foreground">Reports ({total})</h1>

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

      {/* Search */}
      <div className="mb-4">
        <input
          type="text" placeholder="Search by package name or reporter username..."
          value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)}
          className="w-full rounded-md border border-border bg-card px-4 py-2 text-sm text-foreground placeholder:text-muted/50 focus:border-primary focus:outline-none"
        />
      </div>

      {/* Filter tabs */}
      <div className="mb-4 flex gap-1 border-b border-border">
        {[{ value: "", label: "All" }, { value: "submitted", label: "Open" }, { value: "resolved", label: "Resolved" }, { value: "dismissed", label: "Dismissed" }].map((f) => (
          <button
            key={f.value} onClick={() => setFilter(f.value)}
            className={`px-4 py-2 text-sm border-b-2 transition-colors ${filter === f.value ? "border-primary text-foreground" : "border-transparent text-muted hover:text-foreground"}`}
          >{f.label}</button>
        ))}
      </div>

      {loading ? (
        <div className="py-8 text-center text-muted">Loading...</div>
      ) : filteredReports.length === 0 ? (
        <div className="rounded-lg border border-border bg-card p-8 text-center text-sm text-muted">No reports found.</div>
      ) : (
        <div className="space-y-3">
          {filteredReports.map((r) => (
            <div key={r.id} className="rounded-lg border border-border bg-card p-4">
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={`rounded px-2 py-0.5 text-xs font-medium ${STATUS_BADGES[r.status] || "bg-muted/20 text-muted"}`}>
                      {r.status}
                    </span>
                    <span className="text-sm font-medium text-foreground">{r.reason}</span>
                    <span className="text-xs text-muted">{r.created_at ? new Date(r.created_at).toLocaleDateString() : ""}</span>
                  </div>
                  <p className="mt-1 text-sm text-muted">{r.description}</p>
                  <div className="mt-2 flex items-center gap-4 text-xs text-muted">
                    <span>
                      Package:{" "}
                      {r.package_slug ? (
                        <Link href={`/packages/${r.package_slug}`} className="text-primary hover:underline font-medium">
                          {r.package_slug}
                        </Link>
                      ) : (
                        <span className="font-mono">{r.package_id.slice(0, 8)}...</span>
                      )}
                    </span>
                    <span>
                      Reporter:{" "}
                      <span className="text-foreground font-medium">{r.reporter_username || r.reporter_user_id.slice(0, 8) + "..."}</span>
                    </span>
                  </div>
                  {r.resolution_note && (
                    <div className="mt-1 text-xs text-muted/70">Resolution: {r.resolution_note}</div>
                  )}
                  {r.resolved_at && (
                    <div className="mt-0.5 text-xs text-muted/50">Resolved: {new Date(r.resolved_at).toLocaleString()}</div>
                  )}
                </div>
                <div className="flex shrink-0 gap-2">
                  {r.status === "submitted" && (
                    <>
                      {resolveId === r.id ? (
                        <div className="flex gap-2">
                          <input
                            type="text" placeholder="Note (optional)"
                            value={resolveNote} onChange={(e) => setResolveNote(e.target.value)}
                            className="w-40 rounded border border-border bg-background px-2 py-1 text-xs focus:outline-none"
                          />
                          <button onClick={() => resolveReport(r.id, "resolved")} className="rounded bg-success/20 px-2 py-1 text-xs font-medium text-success hover:bg-success/30">Resolve</button>
                          <button onClick={() => resolveReport(r.id, "dismissed")} className="rounded bg-muted/20 px-2 py-1 text-xs font-medium text-muted hover:bg-muted/30">Dismiss</button>
                          <button onClick={() => setResolveId("")} className="text-xs text-muted hover:text-foreground">Cancel</button>
                        </div>
                      ) : (
                        <button onClick={() => setResolveId(r.id)} className="rounded bg-primary/20 px-3 py-1 text-xs font-medium text-primary hover:bg-primary/30">Review</button>
                      )}
                    </>
                  )}
                  {(r.status === "resolved" || r.status === "dismissed") && (
                    <button onClick={() => reopenReport(r.id)} className="rounded bg-warning/20 px-2 py-1 text-xs font-medium text-warning hover:bg-warning/30">Reopen</button>
                  )}
                  {confirmDeleteId === r.id ? (
                    <div className="flex gap-1">
                      <button onClick={() => deleteReport(r.id)} className="rounded bg-danger px-2 py-1 text-xs font-medium text-white hover:bg-danger/90">Confirm Delete</button>
                      <button onClick={() => setConfirmDeleteId("")} className="text-xs text-muted hover:text-foreground">Cancel</button>
                    </div>
                  ) : (
                    <button onClick={() => setConfirmDeleteId(r.id)} className="rounded bg-danger/20 px-2 py-1 text-xs font-medium text-danger hover:bg-danger/30">Delete</button>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
