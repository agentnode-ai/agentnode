"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { fetchWithAuth } from "@/lib/api";

interface UserItem {
  id: string;
  email: string;
  username: string;
  is_admin: boolean;
  is_email_verified: boolean;
  two_factor_enabled: boolean;
  publisher_slug: string | null;
  created_at: string | null;
}

export default function AdminUsersPage() {
  const [users, setUsers] = useState<UserItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [confirmAction, setConfirmAction] = useState<{ userId: string; username: string; action: "promote" | "demote" } | null>(null);

  useEffect(() => { loadUsers(); }, [page, search]);

  async function loadUsers() {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(page), per_page: "50" });
      if (search) params.set("search", search);
      const res = await fetchWithAuth(`/admin/users?${params}`);
      if (res.ok) { const data = await res.json(); setUsers(data.users); setTotal(data.total); }
    } catch { setError("Failed to load users"); }
    finally { setLoading(false); }
  }

  async function executeAction() {
    if (!confirmAction) return;
    setError(""); setSuccess("");
    const res = await fetchWithAuth(`/admin/users/${confirmAction.userId}/${confirmAction.action}`, {
      method: "POST",
    });
    if (res.ok) {
      const data = await res.json();
      setSuccess(data.message);
      await loadUsers();
    } else {
      const data = await res.json();
      setError(data.error?.message || `Failed to ${confirmAction.action} user`);
    }
    setConfirmAction(null);
  }

  const totalPages = Math.ceil(total / 50);

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-bold text-foreground">Users ({total})</h1>
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

      {/* Confirmation dialog */}
      {confirmAction && (
        <div className="mb-4 rounded-lg border border-warning/30 bg-warning/5 p-4">
          <p className="text-sm text-foreground">
            {confirmAction.action === "promote"
              ? <>Are you sure you want to <span className="font-semibold text-warning">promote</span> <span className="font-mono font-medium">{confirmAction.username}</span> to admin?</>
              : <>Are you sure you want to <span className="font-semibold text-danger">demote</span> <span className="font-mono font-medium">{confirmAction.username}</span> from admin?</>
            }
          </p>
          <div className="mt-3 flex gap-2">
            <button
              onClick={executeAction}
              className={`rounded px-4 py-1.5 text-sm font-medium text-white ${confirmAction.action === "promote" ? "bg-primary hover:bg-primary/90" : "bg-danger hover:bg-danger/90"}`}
            >
              Confirm {confirmAction.action}
            </button>
            <button onClick={() => setConfirmAction(null)} className="rounded border border-border px-4 py-1.5 text-sm text-muted hover:bg-card">
              Cancel
            </button>
          </div>
        </div>
      )}

      <input
        type="text"
        placeholder="Search by username or email..."
        value={search}
        onChange={(e) => { setSearch(e.target.value); setPage(1); }}
        className="mb-4 w-full rounded-md border border-border bg-card px-4 py-2 text-sm text-foreground placeholder:text-muted/50 focus:border-primary focus:outline-none"
      />

      <div className="rounded-lg border border-border bg-card overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs text-muted">
              <th className="px-4 py-2.5">Username</th>
              <th className="px-4 py-2.5">Email</th>
              <th className="px-4 py-2.5 text-center">Admin</th>
              <th className="px-4 py-2.5 text-center">2FA</th>
              <th className="px-4 py-2.5 text-center">Verified</th>
              <th className="px-4 py-2.5">Publisher</th>
              <th className="px-4 py-2.5">Joined</th>
              <th className="px-4 py-2.5"></th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={8} className="px-4 py-8 text-center text-muted">Loading...</td></tr>
            ) : users.length === 0 ? (
              <tr><td colSpan={8} className="px-4 py-8 text-center text-muted">No users found.</td></tr>
            ) : (
              users.map((u) => (
                <tr key={u.id} className="border-b border-border/50 last:border-0 hover:bg-card/80">
                  <td className="px-4 py-2.5 font-medium text-foreground">{u.username}</td>
                  <td className="px-4 py-2.5 text-muted">{u.email}</td>
                  <td className="px-4 py-2.5 text-center">
                    {u.is_admin
                      ? <span className="rounded bg-primary/20 px-2 py-0.5 text-xs font-medium text-primary">Admin</span>
                      : <span className="text-muted text-xs">-</span>}
                  </td>
                  <td className="px-4 py-2.5 text-center">
                    {u.two_factor_enabled
                      ? <span className="rounded bg-success/20 px-2 py-0.5 text-xs font-medium text-success">On</span>
                      : <span className="text-muted text-xs">Off</span>}
                  </td>
                  <td className="px-4 py-2.5 text-center">
                    {u.is_email_verified
                      ? <span className="rounded bg-success/20 px-2 py-0.5 text-xs font-medium text-success">Yes</span>
                      : <span className="rounded bg-warning/20 px-2 py-0.5 text-xs font-medium text-warning">No</span>}
                  </td>
                  <td className="px-4 py-2.5">
                    {u.publisher_slug ? (
                      <Link href="/admin/publishers" className="text-primary text-xs hover:underline">@{u.publisher_slug}</Link>
                    ) : (
                      <span className="text-muted text-xs">-</span>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-muted text-xs">{u.created_at ? new Date(u.created_at).toLocaleDateString() : "-"}</td>
                  <td className="px-4 py-2.5">
                    <button
                      onClick={() => setConfirmAction({ userId: u.id, username: u.username, action: u.is_admin ? "demote" : "promote" })}
                      className={`rounded px-2 py-1 text-xs font-medium ${
                        u.is_admin
                          ? "bg-danger/20 text-danger hover:bg-danger/30"
                          : "bg-primary/20 text-primary hover:bg-primary/30"
                      }`}
                    >
                      {u.is_admin ? "Demote" : "Promote"}
                    </button>
                  </td>
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
