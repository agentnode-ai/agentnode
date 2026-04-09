"use client";

import { useState, useEffect, useRef } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { fetchWithAuth } from "@/lib/api";

interface Message {
  id: string;
  is_admin: boolean;
  body: string;
  created_at: string;
  author_name: string | null;
}

interface Ticket {
  id: string;
  ticket_number: number;
  category: string;
  subject: string;
  status: string;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
  messages: Message[];
}

const STATUS_STYLES: Record<string, string> = {
  open: "bg-blue-500/10 text-blue-400",
  in_progress: "bg-purple-500/10 text-purple-400",
  resolved: "bg-green-500/10 text-green-400",
  closed: "bg-neutral-500/10 text-neutral-400",
};

const STATUSES = ["open", "in_progress", "resolved", "closed"];

export default function AdminTicketDetailPage() {
  const params = useParams();
  const ticketId = params.id as string;
  const bottomRef = useRef<HTMLDivElement>(null);

  const [ticket, setTicket] = useState<Ticket | null>(null);
  const [loading, setLoading] = useState(true);
  const [reply, setReply] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [statusUpdating, setStatusUpdating] = useState(false);

  useEffect(() => {
    loadTicket();
  }, [ticketId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [ticket?.messages.length]);

  async function loadTicket() {
    try {
      const res = await fetchWithAuth(`/admin/support/tickets/${ticketId}`);
      if (res.ok) {
        setTicket(await res.json());
      }
    } catch {
      setError("Failed to load ticket");
    } finally {
      setLoading(false);
    }
  }

  async function handleReply(e: React.FormEvent) {
    e.preventDefault();
    if (!reply.trim()) return;
    setSending(true);
    setError("");
    try {
      const res = await fetchWithAuth(
        `/admin/support/tickets/${ticketId}/reply`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: reply }),
        }
      );
      if (!res.ok) throw new Error("Failed to send reply");
      setReply("");
      await loadTicket();
    } catch {
      setError("Failed to send reply");
    } finally {
      setSending(false);
    }
  }

  async function handleStatusChange(newStatus: string) {
    setStatusUpdating(true);
    setError("");
    try {
      const res = await fetchWithAuth(
        `/admin/support/tickets/${ticketId}/status`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: newStatus }),
        }
      );
      if (!res.ok) throw new Error("Failed to update status");
      await loadTicket();
    } catch {
      setError("Failed to update status");
    } finally {
      setStatusUpdating(false);
    }
  }

  if (loading) {
    return <div className="text-sm text-muted">Loading...</div>;
  }

  if (!ticket) {
    return (
      <div>
        <p className="text-red-400">{error || "Ticket not found"}</p>
        <Link href="/admin/support" className="mt-2 text-sm text-primary underline">
          Back to tickets
        </Link>
      </div>
    );
  }

  return (
    <div>
      {/* Header */}
      <div className="mb-6">
        <Link
          href="/admin/support"
          className="text-sm text-muted transition-colors hover:text-foreground"
        >
          &larr; Back to tickets
        </Link>
        <div className="mt-3 flex items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <span className="font-mono text-sm text-muted">
                #{ticket.ticket_number}
              </span>
              <span
                className={`rounded px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[ticket.status] || ""}`}
              >
                {ticket.status.replace("_", " ")}
              </span>
              <span className="rounded bg-card px-2 py-0.5 text-xs text-muted">
                {ticket.category}
              </span>
            </div>
            <h1 className="mt-1 text-xl font-bold text-foreground">
              {ticket.subject}
            </h1>
            <p className="mt-1 text-xs text-muted">
              Opened {new Date(ticket.created_at).toLocaleString()}
              {ticket.resolved_at && (
                <>
                  {" · "}Resolved{" "}
                  {new Date(ticket.resolved_at).toLocaleString()}
                </>
              )}
            </p>
          </div>

          {/* Status dropdown */}
          <div className="flex items-center gap-2">
            <label className="text-xs text-muted">Status:</label>
            <select
              value={ticket.status}
              onChange={(e) => handleStatusChange(e.target.value)}
              disabled={statusUpdating}
              className="rounded-lg border border-border bg-background px-2 py-1 text-sm text-foreground disabled:opacity-50"
            >
              {STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s.replace("_", " ")}
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {error && (
        <div className="mb-4 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-400">
          {error}
        </div>
      )}

      {/* Messages */}
      <div className="space-y-4">
        {ticket.messages.map((msg) => (
          <div
            key={msg.id}
            className={`rounded-lg border p-4 ${
              msg.is_admin
                ? "border-primary/30 bg-primary/5"
                : "border-border bg-card"
            }`}
          >
            <div className="mb-2 flex items-center justify-between">
              <span
                className={`text-sm font-medium ${
                  msg.is_admin ? "text-primary" : "text-foreground"
                }`}
              >
                {msg.author_name || "Unknown"}
                {msg.is_admin && (
                  <span className="ml-1.5 rounded bg-primary/10 px-1.5 py-0.5 text-xs text-primary">
                    Admin
                  </span>
                )}
              </span>
              <span className="text-xs text-muted">
                {new Date(msg.created_at).toLocaleString()}
              </span>
            </div>
            <p className="whitespace-pre-wrap text-sm leading-relaxed text-muted">
              {msg.body}
            </p>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {/* Reply form */}
      <form onSubmit={handleReply} className="mt-6">
        <textarea
          value={reply}
          onChange={(e) => setReply(e.target.value)}
          placeholder="Reply as Support Team..."
          rows={3}
          maxLength={5000}
          required
          className="w-full rounded-lg border border-border bg-background px-4 py-3 text-sm text-foreground placeholder:text-muted"
        />
        <div className="mt-2 flex justify-end">
          <button
            type="submit"
            disabled={sending || !reply.trim()}
            className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-primary/90 disabled:opacity-50"
          >
            {sending ? "Sending..." : "Reply as Support Team"}
          </button>
        </div>
      </form>
    </div>
  );
}
