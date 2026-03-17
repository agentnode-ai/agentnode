"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { fetchWithAuth } from "@/lib/api";

interface InstallItem {
  id: string;
  user_id: string | null;
  username: string | null;
  package_id: string;
  package_slug: string | null;
  package_name: string | null;
  status: string;
  source: string;
  event_type: string;
  installed_at: string | null;
}

const STATUS_COLORS: Record<string, string> = {
  installed: "bg-primary/20 text-primary",
  active: "bg-success/20 text-success",
  failed: "bg-danger/20 text-danger",
  uninstalled: "bg-muted/20 text-muted",
};

export default function AdminInstallationsPage() {
  const [installs, setInstalls] = useState<InstallItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [filterStatus, setFilterStatus] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => { loadInstalls(); }, [page, filterStatus]);

  async function loadInstalls() {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(page), per_page: "50" });
      if (filterStatus) params.set("status", filterStatus);
      const res = await fetchWithAuth(`/admin/installations?${params}`);
      if (res.ok) { const d = await res.json(); setInstalls(d.installations); setTotal(d.total); }
    } catch {}
    finally { setLoading(false); }
  }

  const totalPages = Math.ceil(total / 50);

  return (
    <div>
      <h1 className="mb-6 text-xl font-bold text-foreground">Installations ({total})</h1>

      <div className="mb-4">
        <select
          value={filterStatus} onChange={(e) => { setFilterStatus(e.target.value); setPage(1); }}
          className="rounded-md border border-border bg-card px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
        >
          <option value="">All statuses</option>
          <option value="installed">installed</option>
          <option value="active">active</option>
          <option value="failed">failed</option>
          <option value="uninstalled">uninstalled</option>
        </select>
      </div>

      <div className="rounded-lg border border-border bg-card overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs text-muted">
              <th className="px-4 py-2.5">Package</th>
              <th className="px-4 py-2.5">Status</th>
              <th className="px-4 py-2.5">Source</th>
              <th className="px-4 py-2.5">Event</th>
              <th className="px-4 py-2.5">User</th>
              <th className="px-4 py-2.5">Installed</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={6} className="px-4 py-8 text-center text-muted">Loading...</td></tr>
            ) : installs.length === 0 ? (
              <tr><td colSpan={6} className="px-4 py-8 text-center text-muted">No installations found.</td></tr>
            ) : (
              installs.map((i) => (
                <tr key={i.id} className="border-b border-border/50 last:border-0 hover:bg-card/80">
                  <td className="px-4 py-2.5">
                    {i.package_slug ? (
                      <Link href={`/packages/${i.package_slug}`} className="font-medium text-primary hover:underline text-xs">
                        {i.package_slug}
                      </Link>
                    ) : (
                      <span className="font-mono text-xs text-foreground">{i.package_id.slice(0, 8)}...</span>
                    )}
                  </td>
                  <td className="px-4 py-2.5">
                    <span className={`rounded px-2 py-0.5 text-xs font-medium ${STATUS_COLORS[i.status] || "text-muted"}`}>{i.status}</span>
                  </td>
                  <td className="px-4 py-2.5 text-muted text-xs">{i.source}</td>
                  <td className="px-4 py-2.5 text-muted text-xs">{i.event_type}</td>
                  <td className="px-4 py-2.5 text-xs">
                    {i.username ? (
                      <span className="text-foreground font-medium">{i.username}</span>
                    ) : (
                      <span className="text-muted">-</span>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-muted text-xs">{i.installed_at ? new Date(i.installed_at).toLocaleString() : "-"}</td>
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
    </div>
  );
}
