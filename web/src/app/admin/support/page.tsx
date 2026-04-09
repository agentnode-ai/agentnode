"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { fetchWithAuth } from "@/lib/api";

interface TicketListItem {
  id: string;
  ticket_number: number;
  category: string;
  subject: string;
  status: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  last_reply_is_admin: boolean;
  username: string | null;
}

const STATUS_STYLES: Record<string, string> = {
  open: "bg-blue-500/10 text-blue-400",
  in_progress: "bg-purple-500/10 text-purple-400",
  resolved: "bg-green-500/10 text-green-400",
  closed: "bg-neutral-500/10 text-neutral-400",
};

const TABS = [
  { value: "", label: "All" },
  { value: "open", label: "Open" },
  { value: "in_progress", label: "In Progress" },
  { value: "resolved", label: "Resolved" },
  { value: "closed", label: "Closed" },
];

export default function AdminSupportPage() {
  const [tickets, setTickets] = useState<TicketListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    loadTickets();
  }, [statusFilter]);

  async function loadTickets() {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams();
      if (statusFilter) params.set("status", statusFilter);
      const res = await fetchWithAuth(`/admin/support/tickets?${params}`);
      if (res.ok) {
        setTickets(await res.json());
      } else {
        setError("Failed to load tickets");
      }
    } catch {
      setError("Failed to load tickets");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-foreground">Support Tickets</h1>
        <p className="mt-1 text-sm text-muted">
          Manage user support tickets and respond to inquiries.
        </p>
      </div>

      {/* Filter tabs */}
      <div className="mb-4 flex flex-wrap gap-1">
        {TABS.map((tab) => (
          <button
            key={tab.value}
            onClick={() => setStatusFilter(tab.value)}
            className={`rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
              statusFilter === tab.value
                ? "bg-primary/10 text-primary"
                : "text-muted hover:text-foreground hover:bg-card"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {error && (
        <div className="mb-4 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-400">
          {error}
        </div>
      )}

      {loading ? (
        <div className="text-sm text-muted">Loading...</div>
      ) : tickets.length === 0 ? (
        <div className="rounded-lg border border-border bg-card p-8 text-center text-sm text-muted">
          No tickets found.
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-card/50">
                <th className="px-4 py-3 text-left font-medium text-muted">#</th>
                <th className="px-4 py-3 text-left font-medium text-muted">User</th>
                <th className="px-4 py-3 text-left font-medium text-muted">Subject</th>
                <th className="px-4 py-3 text-left font-medium text-muted">Category</th>
                <th className="px-4 py-3 text-left font-medium text-muted">Status</th>
                <th className="px-4 py-3 text-left font-medium text-muted">Messages</th>
                <th className="px-4 py-3 text-left font-medium text-muted">Updated</th>
                <th className="px-4 py-3 text-left font-medium text-muted"></th>
              </tr>
            </thead>
            <tbody>
              {tickets.map((t) => (
                <tr
                  key={t.id}
                  className="border-b border-border/50 transition-colors hover:bg-card/30"
                >
                  <td className="px-4 py-3 font-mono text-xs text-muted">
                    {t.ticket_number}
                  </td>
                  <td className="px-4 py-3 text-sm text-foreground">
                    {t.username || "—"}
                  </td>
                  <td className="px-4 py-3">
                    <Link
                      href={`/admin/support/${t.id}`}
                      className="font-medium text-foreground hover:text-primary"
                    >
                      {t.subject}
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-xs text-muted">{t.category}</td>
                  <td className="px-4 py-3">
                    <span
                      className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[t.status] || ""}`}
                    >
                      {t.status.replace("_", " ")}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-xs text-muted">
                    {t.message_count}
                    {!t.last_reply_is_admin && t.status !== "closed" && (
                      <span className="ml-1.5 inline-block rounded bg-yellow-500/10 px-1.5 py-0.5 text-xs text-yellow-400">
                        Awaiting reply
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-xs text-muted">
                    {new Date(t.updated_at).toLocaleDateString()}
                  </td>
                  <td className="px-4 py-3">
                    <Link
                      href={`/admin/support/${t.id}`}
                      className="text-xs text-primary hover:underline"
                    >
                      View
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
