"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { fetchWithAuth } from "@/lib/api";
import { useAdminUser } from "../layout";
import CompleteForm from "./CompleteForm";
import RefundForm from "./RefundForm";

interface ReviewItem {
  id: string;
  order_id: string;
  package_slug: string | null;
  package_name: string | null;
  version: string | null;
  tier: string;
  express: boolean;
  price_cents: number;
  currency: string;
  status: string;
  review_notes: string | null;
  review_result: Record<string, any> | null;
  refund_amount_cents: number | null;
  paid_at: string | null;
  reviewed_at: string | null;
  created_at: string;
  publisher_slug: string | null;
  publisher_name: string | null;
  assigned_reviewer_id: string | null;
  verification_status: string | null;
  verification_tier: string | null;
  verification_score: number | null;
}

const STATUS_BADGES: Record<string, string> = {
  paid: "bg-blue-500/20 text-blue-400",
  in_review: "bg-purple-500/20 text-purple-400",
  approved: "bg-green-500/20 text-green-400",
  changes_requested: "bg-orange-500/20 text-orange-400",
  rejected: "bg-red-500/20 text-red-400",
  refunded: "bg-gray-500/20 text-gray-400",
};

const TIER_LABELS: Record<string, string> = {
  security: "Security",
  compatibility: "Compatibility",
  full: "Full",
};

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const minutes = Math.floor(diff / 60000);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export default function AdminReviewsPage() {
  const adminUser = useAdminUser();
  const [reviews, setReviews] = useState<ReviewItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  // History (completed reviews)
  const [historyReviews, setHistoryReviews] = useState<ReviewItem[]>([]);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);

  // Forms
  const [completeId, setCompleteId] = useState("");
  const [refundId, setRefundId] = useState("");

  // Filter
  const [filter, setFilter] = useState("");

  useEffect(() => {
    loadReviews();
  }, []);

  async function loadReviews() {
    setLoading(true);
    try {
      const res = await fetchWithAuth("/admin/reviews/queue");
      if (res.ok) {
        const data = await res.json();
        setReviews(data || []);
      } else {
        setError("Failed to load review queue");
      }
    } catch {
      setError("Failed to load review queue");
    } finally {
      setLoading(false);
    }
  }

  async function loadHistory() {
    if (historyLoaded) return;
    setHistoryLoading(true);
    try {
      const res = await fetchWithAuth("/admin/reviews/history?per_page=100");
      if (res.ok) {
        const data = await res.json();
        setHistoryReviews(data || []);
        setHistoryLoaded(true);
      } else {
        setError("Failed to load review history");
      }
    } catch {
      setError("Failed to load review history");
    } finally {
      setHistoryLoading(false);
    }
  }

  async function assignToMe(reviewId: string) {
    if (!adminUser) return;
    setError("");
    setSuccess("");
    try {
      const res = await fetchWithAuth(`/admin/reviews/${reviewId}/assign`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reviewer_id: adminUser.id }),
      });
      if (res.ok) {
        setSuccess("Review assigned to you");
        await loadReviews();
      } else {
        const d = await res.json().catch(() => ({}));
        setError(d.error?.message || "Failed to assign review");
      }
    } catch {
      setError("Failed to assign review");
    }
  }

  function canRefund(r: ReviewItem): boolean {
    return (
      ["paid", "in_review", "approved"].includes(r.status) &&
      r.refund_amount_cents === null &&
      r.status !== "refunded"
    );
  }

  // Determine which list to display
  const isCompletedTab = filter === "completed";
  const displayReviews = isCompletedTab
    ? historyReviews
    : reviews.filter((r) => {
        if (!filter) return true;
        if (filter === "mine") return r.assigned_reviewer_id === adminUser?.id;
        return r.status === filter;
      });

  const tabs = [
    { value: "", label: "All" },
    { value: "paid", label: "Paid" },
    { value: "in_review", label: "In Review" },
    { value: "mine", label: "Assigned to me" },
    { value: "completed", label: "Completed" },
  ];

  return (
    <div>
      <h1 className="mb-6 text-xl font-bold text-foreground">
        Review Queue ({reviews.length})
      </h1>

      {error && (
        <div className="mb-4 rounded-md border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">
          {error}{" "}
          <button onClick={() => setError("")} className="ml-2 underline">
            dismiss
          </button>
        </div>
      )}
      {success && (
        <div className="mb-4 rounded-md border border-success/30 bg-success/10 px-4 py-3 text-sm text-success">
          {success}{" "}
          <button onClick={() => setSuccess("")} className="ml-2 underline">
            dismiss
          </button>
        </div>
      )}

      {/* Filter tabs */}
      <div className="mb-4 flex gap-1 border-b border-border">
        {tabs.map((f) => (
          <button
            key={f.value}
            onClick={() => {
              setFilter(f.value);
              if (f.value === "completed") loadHistory();
            }}
            className={`px-4 py-2 text-sm border-b-2 transition-colors ${
              filter === f.value
                ? "border-primary text-foreground"
                : "border-transparent text-muted hover:text-foreground"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      {!isCompletedTab && reviews.length >= 100 && (
        <div className="mb-4 rounded-md border border-warning/30 bg-warning/10 px-4 py-2 text-xs text-warning">
          Showing first 100 reviews. Older reviews may not be displayed.
        </div>
      )}

      {(isCompletedTab ? historyLoading : loading) ? (
        <div className="py-8 text-center text-muted">Loading...</div>
      ) : displayReviews.length === 0 ? (
        <div className="rounded-lg border border-border bg-card p-8 text-center text-sm text-muted">
          {isCompletedTab ? "No completed reviews yet." : "No reviews in queue."}
        </div>
      ) : (
        <div className="space-y-3">
          {displayReviews.map((r) => (
            <div
              key={r.id}
              className="rounded-lg border border-border bg-card p-4"
            >
              {/* Header */}
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span
                      className={`rounded px-2 py-0.5 text-xs font-medium ${
                        STATUS_BADGES[r.status] || "bg-muted/20 text-muted"
                      }`}
                    >
                      {r.status === "refunded" ? "Refunded" : r.status.replace(/_/g, " ")}
                    </span>
                    <span className="rounded px-2 py-0.5 text-xs font-medium bg-card border border-border text-muted">
                      {TIER_LABELS[r.tier] || r.tier}
                    </span>
                    {r.express && (
                      <span className="rounded px-2 py-0.5 text-xs font-medium bg-yellow-500/20 text-yellow-400">
                        Express
                      </span>
                    )}
                    <span className="font-mono text-xs text-muted">
                      ${(r.price_cents / 100).toFixed(0)} USD
                    </span>
                  </div>
                  <div className="mt-1 flex items-center gap-2">
                    {r.package_slug ? (
                      <a
                        href={`/packages/${r.package_slug}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-sm font-medium text-primary hover:underline"
                      >
                        {r.package_name || r.package_slug} &#8599;
                      </a>
                    ) : (
                      <span className="text-sm font-medium text-foreground">Unknown</span>
                    )}
                    {r.version && (
                      <span className="font-mono text-xs text-muted">
                        v{r.version}
                      </span>
                    )}
                    {r.verification_tier && (
                      <span className="text-[10px] text-muted">
                        Auto: {r.verification_tier}{r.verification_score != null ? ` (${r.verification_score})` : ""}
                      </span>
                    )}
                  </div>
                  <div className="mt-1 flex items-center gap-4 text-xs text-muted">
                    {r.publisher_slug && (
                      <span>
                        Publisher:{" "}
                        <Link
                          href={`/admin/publishers`}
                          className="text-primary hover:underline"
                        >
                          @{r.publisher_slug}
                        </Link>
                      </span>
                    )}
                    {r.paid_at && (
                      <span>
                        Paid {timeAgo(r.paid_at)}
                      </span>
                    )}
                    {r.reviewed_at && isCompletedTab && (
                      <span>
                        Reviewed {timeAgo(r.reviewed_at)}
                      </span>
                    )}
                    {r.assigned_reviewer_id && (
                      <span className="text-purple-400">Reviewer assigned</span>
                    )}
                  </div>
                </div>

                {/* Actions */}
                <div className="flex shrink-0 gap-2 items-center">
                  <Link
                    href={`/admin/reviews/${r.id}`}
                    className="rounded border border-border px-3 py-1 text-xs text-muted hover:text-foreground hover:border-border/80"
                  >
                    View details &rarr;
                  </Link>
                  {!isCompletedTab && (
                    <>
                      {r.status === "paid" && (
                        <button
                          onClick={() => assignToMe(r.id)}
                          className="rounded bg-purple-500/20 px-3 py-1 text-xs font-medium text-purple-400 hover:bg-purple-500/30"
                        >
                          Assign to me
                        </button>
                      )}
                      {r.status === "in_review" && completeId !== r.id && (
                        <button
                          onClick={() => setCompleteId(r.id)}
                          className="rounded bg-primary/20 px-3 py-1 text-xs font-medium text-primary hover:bg-primary/30"
                        >
                          Complete
                        </button>
                      )}
                      {canRefund(r) && refundId !== r.id && (
                        <button
                          onClick={() => setRefundId(r.id)}
                          className="rounded bg-danger/20 px-3 py-1 text-xs font-medium text-danger hover:bg-danger/30"
                        >
                          Refund
                        </button>
                      )}
                    </>
                  )}
                  {/* Completed tab: only allow refund if eligible */}
                  {isCompletedTab && canRefund(r) && refundId !== r.id && (
                    <button
                      onClick={() => setRefundId(r.id)}
                      className="rounded bg-danger/20 px-3 py-1 text-xs font-medium text-danger hover:bg-danger/30"
                    >
                      Refund
                    </button>
                  )}
                </div>
              </div>

              {/* Inline review result for completed tab */}
              {isCompletedTab && r.review_result && (
                <div className="mt-3 rounded border border-border/50 bg-background p-3 space-y-2">
                  <div className="flex gap-2 flex-wrap">
                    {r.review_result.security_passed !== undefined && (
                      <span className={`rounded px-2 py-0.5 text-[10px] font-medium ${r.review_result.security_passed ? "bg-green-500/20 text-green-400" : "bg-red-500/20 text-red-400"}`}>
                        Security {r.review_result.security_passed ? "pass" : "fail"}
                      </span>
                    )}
                    {r.review_result.compatibility_passed !== undefined && (
                      <span className={`rounded px-2 py-0.5 text-[10px] font-medium ${r.review_result.compatibility_passed ? "bg-green-500/20 text-green-400" : "bg-red-500/20 text-red-400"}`}>
                        Compat {r.review_result.compatibility_passed ? "pass" : "fail"}
                      </span>
                    )}
                    {r.review_result.docs_passed !== undefined && (
                      <span className={`rounded px-2 py-0.5 text-[10px] font-medium ${r.review_result.docs_passed ? "bg-green-500/20 text-green-400" : "bg-red-500/20 text-red-400"}`}>
                        Docs {r.review_result.docs_passed ? "pass" : "fail"}
                      </span>
                    )}
                  </div>
                  {r.review_result.required_changes && r.review_result.required_changes.length > 0 && (
                    <div>
                      <span className="text-[10px] text-muted">Required Changes: </span>
                      <span className="text-xs text-foreground">{r.review_result.required_changes.join("; ")}</span>
                    </div>
                  )}
                  {r.review_result.reviewer_summary && (
                    <p className="text-xs text-foreground/80">{r.review_result.reviewer_summary}</p>
                  )}
                </div>
              )}

              {/* Complete form (inline, queue only) */}
              {completeId === r.id && !isCompletedTab && (
                <div className="mt-4">
                  <CompleteForm
                    reviewId={r.id}
                    onSuccess={async (outcome) => {
                      setSuccess(`Review ${outcome}`);
                      setCompleteId("");
                      await loadReviews();
                    }}
                    onCancel={() => setCompleteId("")}
                  />
                </div>
              )}

              {/* Refund form (inline) */}
              {refundId === r.id && (
                <div className="mt-4">
                  <RefundForm
                    reviewId={r.id}
                    priceCents={r.price_cents}
                    onSuccess={async () => {
                      setSuccess("Refund processed");
                      setRefundId("");
                      if (isCompletedTab) {
                        setHistoryLoaded(false);
                        await loadHistory();
                      } else {
                        await loadReviews();
                      }
                    }}
                    onCancel={() => setRefundId("")}
                  />
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
