"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
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
}

const STATUS_STYLES: Record<string, string> = {
  open: "bg-blue-500/10 text-blue-400",
  in_progress: "bg-purple-500/10 text-purple-400",
  resolved: "bg-green-500/10 text-green-400",
  closed: "bg-neutral-500/10 text-neutral-400",
};

const CATEGORIES = [
  { value: "account", label: "Account" },
  { value: "publishing", label: "Publishing" },
  { value: "reviews", label: "Reviews" },
  { value: "billing", label: "Billing" },
  { value: "bug", label: "Bug Report" },
  { value: "other", label: "Other" },
];

export default function DashboardSupportPage() {
  const router = useRouter();
  const [tickets, setTickets] = useState<TicketListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  // Form
  const [category, setCategory] = useState("other");
  const [subject, setSubject] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    loadTickets();
  }, []);

  async function loadTickets() {
    try {
      const res = await fetchWithAuth("/support/tickets");
      if (!res.ok) {
        if (res.status === 401) {
          router.push("/auth/login");
          return;
        }
        throw new Error("Failed to load tickets");
      }
      setTickets(await res.json());
    } catch {
      setError("Failed to load tickets");
    } finally {
      setLoading(false);
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      const res = await fetchWithAuth("/support/tickets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ category, subject, message }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new Error(data?.error?.message || "Failed to create ticket");
      }
      const ticket = await res.json();
      setShowForm(false);
      setSubject("");
      setMessage("");
      setCategory("other");
      router.push(`/dashboard/support/${ticket.id}`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to create ticket");
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <div className="mx-auto max-w-4xl px-6 py-12">
        <div className="text-muted">Loading...</div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl px-6 py-12">
      <div className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Support Tickets</h1>
          <p className="mt-1 text-sm text-muted">
            Need help? Check the{" "}
            <Link href="/faq" className="text-primary underline">
              FAQ
            </Link>{" "}
            first, or create a ticket below.
          </p>
        </div>
        {!showForm && (
          <button
            onClick={() => setShowForm(true)}
            className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-primary/90"
          >
            New Ticket
          </button>
        )}
      </div>

      {error && (
        <div className="mb-4 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-400">
          {error}
        </div>
      )}

      {/* Create form */}
      {showForm && (
        <form
          onSubmit={handleSubmit}
          className="mb-8 rounded-lg border border-border bg-card p-6"
        >
          <h2 className="mb-4 text-lg font-semibold text-foreground">
            New Support Ticket
          </h2>

          <div className="mb-4">
            <label className="mb-1 block text-sm font-medium text-foreground">
              Category
            </label>
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground"
            >
              {CATEGORIES.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </select>
          </div>

          <div className="mb-4">
            <label className="mb-1 block text-sm font-medium text-foreground">
              Subject
            </label>
            <input
              type="text"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              placeholder="Brief description of your issue"
              minLength={5}
              maxLength={200}
              required
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted"
            />
          </div>

          <div className="mb-4">
            <label className="mb-1 block text-sm font-medium text-foreground">
              Message
            </label>
            <textarea
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              placeholder="Describe your issue in detail..."
              minLength={10}
              maxLength={5000}
              rows={5}
              required
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted"
            />
          </div>

          <div className="flex items-center gap-3">
            <button
              type="submit"
              disabled={submitting}
              className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-primary/90 disabled:opacity-50"
            >
              {submitting ? "Creating..." : "Create Ticket"}
            </button>
            <button
              type="button"
              onClick={() => setShowForm(false)}
              className="rounded-lg border border-border px-4 py-2 text-sm font-medium text-foreground transition-colors hover:bg-card"
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      {/* Ticket list */}
      {tickets.length === 0 && !showForm ? (
        <div className="rounded-lg border border-border bg-card p-8 text-center text-sm text-muted">
          No support tickets yet. Click &quot;New Ticket&quot; to get started.
        </div>
      ) : (
        <div className="space-y-2">
          {tickets.map((t) => (
            <Link
              key={t.id}
              href={`/dashboard/support/${t.id}`}
              className="flex items-center justify-between rounded-lg border border-border bg-card p-4 transition-colors hover:border-primary/30"
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-xs text-muted">
                    #{t.ticket_number}
                  </span>
                  <span
                    className={`rounded px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[t.status] || ""}`}
                  >
                    {t.status.replace("_", " ")}
                  </span>
                  <span className="rounded bg-card px-2 py-0.5 text-xs text-muted">
                    {t.category}
                  </span>
                </div>
                <p className="mt-1 truncate text-sm font-medium text-foreground">
                  {t.subject}
                </p>
                <p className="mt-0.5 text-xs text-muted">
                  {t.message_count} message{t.message_count !== 1 ? "s" : ""}
                  {" · "}
                  Updated {new Date(t.updated_at).toLocaleDateString()}
                  {t.last_reply_is_admin && t.status !== "closed" && (
                    <span className="ml-2 text-primary">New reply</span>
                  )}
                </p>
              </div>
              <span className="ml-4 text-muted">&rarr;</span>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
