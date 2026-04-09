"use client";

import { useState, useEffect, useRef } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
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

export default function TicketDetailPage() {
  const params = useParams();
  const router = useRouter();
  const ticketId = params.id as string;
  const bottomRef = useRef<HTMLDivElement>(null);

  const [ticket, setTicket] = useState<Ticket | null>(null);
  const [loading, setLoading] = useState(true);
  const [reply, setReply] = useState("");
  const [sending, setSending] = useState(false);
  const [closing, setClosing] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    loadTicket();
  }, [ticketId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [ticket?.messages.length]);

  async function loadTicket() {
    try {
      const res = await fetchWithAuth(`/support/tickets/${ticketId}`);
      if (!res.ok) {
        if (res.status === 401) {
          router.push("/auth/login");
          return;
        }
        throw new Error("Ticket not found");
      }
      setTicket(await res.json());
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
      const res = await fetchWithAuth(`/support/tickets/${ticketId}/reply`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: reply }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new Error(data?.error?.message || "Failed to send reply");
      }
      setReply("");
      await loadTicket();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to send reply");
    } finally {
      setSending(false);
    }
  }

  async function handleClose() {
    if (!confirm("Close this ticket? You won't be able to reopen it.")) return;
    setClosing(true);
    try {
      const res = await fetchWithAuth(`/support/tickets/${ticketId}/close`, {
        method: "POST",
      });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new Error(data?.error?.message || "Failed to close ticket");
      }
      await loadTicket();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to close ticket");
    } finally {
      setClosing(false);
    }
  }

  if (loading) {
    return (
      <div className="mx-auto max-w-3xl px-6 py-12 text-muted">Loading...</div>
    );
  }

  if (!ticket) {
    return (
      <div className="mx-auto max-w-3xl px-6 py-12">
        <p className="text-red-400">{error || "Ticket not found"}</p>
        <Link href="/dashboard/support" className="mt-4 text-sm text-primary underline">
          Back to tickets
        </Link>
      </div>
    );
  }

  const canReply = ticket.status === "open" || ticket.status === "in_progress";
  const canClose = ticket.status === "open" || ticket.status === "in_progress";

  return (
    <div className="mx-auto max-w-3xl px-6 py-12">
      {/* Header */}
      <div className="mb-6">
        <Link
          href="/dashboard/support"
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
            </p>
          </div>
          {canClose && (
            <button
              onClick={handleClose}
              disabled={closing}
              className="shrink-0 rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-muted transition-colors hover:border-red-500/30 hover:text-red-400 disabled:opacity-50"
            >
              {closing ? "Closing..." : "Close Ticket"}
            </button>
          )}
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
      {canReply ? (
        <form onSubmit={handleReply} className="mt-6">
          <textarea
            value={reply}
            onChange={(e) => setReply(e.target.value)}
            placeholder="Write a reply..."
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
              {sending ? "Sending..." : "Send Reply"}
            </button>
          </div>
        </form>
      ) : (
        <div className="mt-6 rounded-lg border border-border bg-card p-4 text-center text-sm text-muted">
          This ticket is closed. No further replies can be sent.
        </div>
      )}
    </div>
  );
}
