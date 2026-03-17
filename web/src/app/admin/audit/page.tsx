"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { fetchWithAuth } from "@/lib/api";

interface AuditLog {
  id: string;
  admin_username: string;
  admin_email: string;
  action: string;
  target_type: string;
  target_id: string;
  metadata: Record<string, unknown> | null;
  ip_address: string | null;
  created_at: string | null;
}

const ACTION_COLORS: Record<string, string> = {
  quarantine_version: "bg-danger/20 text-danger",
  clear_quarantine: "bg-success/20 text-success",
  reject_version: "bg-danger/20 text-danger",
  set_trust_level: "bg-primary/20 text-primary",
  suspend_publisher: "bg-danger/20 text-danger",
  unsuspend_publisher: "bg-success/20 text-success",
  promote_admin: "bg-warning/20 text-warning",
  demote_admin: "bg-warning/20 text-warning",
  resolve_report: "bg-primary/20 text-primary",
};

const ACTION_LABELS: Record<string, string> = {
  quarantine_version: "Quarantined Version",
  clear_quarantine: "Cleared Quarantine",
  reject_version: "Rejected Version",
  set_trust_level: "Changed Trust Level",
  suspend_publisher: "Suspended Publisher",
  unsuspend_publisher: "Unsuspended Publisher",
  promote_admin: "Promoted to Admin",
  demote_admin: "Demoted from Admin",
  resolve_report: "Resolved Report",
};

function targetLink(type: string, id: string) {
  switch (type) {
    case "package": return `/admin/packages`;
    case "publisher": return `/admin/publishers`;
    case "user": return `/admin/users`;
    case "report": return `/admin/reports`;
    default: return null;
  }
}

export default function AdminAuditPage() {
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [filterAction, setFilterAction] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => { loadLogs(); }, [page, filterAction]);

  async function loadLogs() {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(page), per_page: "50" });
      if (filterAction) params.set("action", filterAction);
      const res = await fetchWithAuth(`/admin/audit?${params}`);
      if (res.ok) {
        const d = await res.json();
        setLogs(d.logs);
        setTotal(d.total);
      }
    } catch {}
    finally { setLoading(false); }
  }

  const totalPages = Math.ceil(total / 50);

  const allActions = [
    "quarantine_version", "clear_quarantine", "reject_version",
    "set_trust_level", "suspend_publisher", "unsuspend_publisher",
    "promote_admin", "demote_admin", "resolve_report",
  ];

  return (
    <div>
      <h1 className="mb-6 text-xl font-bold text-foreground">Audit Log ({total})</h1>

      <div className="mb-4">
        <select
          value={filterAction}
          onChange={(e) => { setFilterAction(e.target.value); setPage(1); }}
          className="rounded-md border border-border bg-card px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
        >
          <option value="">All actions</option>
          {allActions.map((a) => (
            <option key={a} value={a}>{ACTION_LABELS[a] || a}</option>
          ))}
        </select>
      </div>

      <div className="rounded-lg border border-border bg-card overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs text-muted">
              <th className="px-4 py-2.5">Time</th>
              <th className="px-4 py-2.5">Admin</th>
              <th className="px-4 py-2.5">Action</th>
              <th className="px-4 py-2.5">Target</th>
              <th className="px-4 py-2.5">Details</th>
              <th className="px-4 py-2.5">IP</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={6} className="px-4 py-8 text-center text-muted">Loading...</td></tr>
            ) : logs.length === 0 ? (
              <tr><td colSpan={6} className="px-4 py-8 text-center text-muted">No audit logs found.</td></tr>
            ) : (
              logs.map((log) => {
                const link = targetLink(log.target_type, log.target_id);
                return (
                  <tr key={log.id} className="border-b border-border/50 last:border-0 hover:bg-card/80">
                    <td className="px-4 py-2.5 text-muted text-xs whitespace-nowrap">
                      {log.created_at ? new Date(log.created_at).toLocaleString() : "-"}
                    </td>
                    <td className="px-4 py-2.5 font-medium text-foreground">{log.admin_username}</td>
                    <td className="px-4 py-2.5">
                      <span className={`rounded px-2 py-0.5 text-xs font-medium ${ACTION_COLORS[log.action] || "bg-muted/20 text-muted"}`}>
                        {ACTION_LABELS[log.action] || log.action}
                      </span>
                    </td>
                    <td className="px-4 py-2.5">
                      <span className="text-xs text-muted">{log.target_type}:</span>{" "}
                      {link ? (
                        <Link href={link} className="text-primary text-xs hover:underline">{log.target_id}</Link>
                      ) : (
                        <span className="text-xs text-foreground">{log.target_id}</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-xs text-muted max-w-[200px] truncate">
                      {log.metadata && Object.keys(log.metadata).length > 0
                        ? Object.entries(log.metadata).map(([k, v]) => `${k}: ${v}`).join(", ")
                        : "-"}
                    </td>
                    <td className="px-4 py-2.5 text-xs text-muted font-mono">{log.ip_address || "-"}</td>
                  </tr>
                );
              })
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
