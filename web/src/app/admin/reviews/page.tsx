"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { fetchWithAuth } from "@/lib/api";
import { useAdminUser } from "../layout";

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

export default function AdminReviewsPage() {
  const adminUser = useAdminUser();
  const [reviews, setReviews] = useState<ReviewItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  // Complete form
  const [completeId, setCompleteId] = useState("");
  const [completeOutcome, setCompleteOutcome] = useState<"approved" | "changes_requested" | "rejected">("approved");
  const [securityPassed, setSecurityPassed] = useState(true);
  const [compatibilityPassed, setCompatibilityPassed] = useState(true);
  const [docsPassed, setDocsPassed] = useState(true);
  const [requiredChanges, setRequiredChanges] = useState<string[]>([""]);
  const [reviewerSummary, setReviewerSummary] = useState("");
  const [completeNotes, setCompleteNotes] = useState("");
  const [completing, setCompleting] = useState(false);

  // Refund form
  const [refundId, setRefundId] = useState("");
  const [refundType, setRefundType] = useState<"full" | "partial">("full");
  const [refundAmount, setRefundAmount] = useState("");
  const [refundReason, setRefundReason] = useState("");
  const [refunding, setRefunding] = useState(false);

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

  function openCompleteForm(reviewId: string) {
    setCompleteId(reviewId);
    setCompleteOutcome("approved");
    setSecurityPassed(true);
    setCompatibilityPassed(true);
    setDocsPassed(true);
    setRequiredChanges([""]);
    setReviewerSummary("");
    setCompleteNotes("");
  }

  function validateCompleteForm(): string | null {
    if (completeOutcome === "approved") {
      if (!reviewerSummary.trim()) return "Reviewer summary is required for approved outcome";
    } else if (completeOutcome === "changes_requested") {
      const nonEmpty = requiredChanges.filter((c) => c.trim());
      if (nonEmpty.length === 0) return "At least one required change is needed";
    } else if (completeOutcome === "rejected") {
      if (!reviewerSummary.trim() && !completeNotes.trim()) return "Reviewer summary or notes required for rejection";
    }
    return null;
  }

  async function submitComplete() {
    const validationError = validateCompleteForm();
    if (validationError) {
      setError(validationError);
      return;
    }
    setCompleting(true);
    setError("");
    setSuccess("");
    try {
      const review_result: Record<string, any> = {
        security_passed: securityPassed,
        compatibility_passed: compatibilityPassed,
        docs_passed: docsPassed,
      };
      if (reviewerSummary.trim()) review_result.reviewer_summary = reviewerSummary.trim();
      const nonEmptyChanges = requiredChanges.filter((c) => c.trim());
      if (nonEmptyChanges.length > 0) review_result.required_changes = nonEmptyChanges;

      const res = await fetchWithAuth(`/admin/reviews/${completeId}/complete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          outcome: completeOutcome,
          notes: completeNotes.trim() || null,
          review_result,
        }),
      });
      if (res.ok) {
        setSuccess(`Review ${completeOutcome}`);
        setCompleteId("");
        await loadReviews();
      } else {
        const d = await res.json().catch(() => ({}));
        setError(d.error?.message || "Failed to complete review");
      }
    } catch {
      setError("Failed to complete review");
    } finally {
      setCompleting(false);
    }
  }

  function canRefund(r: ReviewItem): boolean {
    return (
      ["paid", "in_review", "approved"].includes(r.status) &&
      r.refund_amount_cents === null &&
      r.status !== "refunded"
    );
  }

  async function submitRefund() {
    if (!refundReason.trim()) {
      setError("Refund reason is required");
      return;
    }
    setRefunding(true);
    setError("");
    setSuccess("");
    try {
      const body: Record<string, any> = { reason: refundReason.trim() };
      if (refundType === "partial") {
        const cents = Math.round(parseFloat(refundAmount) * 100);
        if (isNaN(cents) || cents <= 0) {
          setError("Invalid refund amount");
          setRefunding(false);
          return;
        }
        body.amount_cents = cents;
      }
      const res = await fetchWithAuth(`/admin/reviews/${refundId}/refund`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (res.ok) {
        setSuccess("Refund processed");
        setRefundId("");
        setRefundReason("");
        setRefundAmount("");
        await loadReviews();
      } else {
        const d = await res.json().catch(() => ({}));
        setError(d.error?.message || "Failed to process refund");
      }
    } catch {
      setError("Failed to process refund");
    } finally {
      setRefunding(false);
    }
  }

  const filteredReviews = reviews.filter((r) => {
    if (!filter) return true;
    return r.status === filter;
  });

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
        {[
          { value: "", label: "All" },
          { value: "paid", label: "Paid" },
          { value: "in_review", label: "In Review" },
        ].map((f) => (
          <button
            key={f.value}
            onClick={() => setFilter(f.value)}
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

      {reviews.length >= 100 && (
        <div className="mb-4 rounded-md border border-warning/30 bg-warning/10 px-4 py-2 text-xs text-warning">
          Showing first 100 reviews. Older reviews may not be displayed.
        </div>
      )}

      {loading ? (
        <div className="py-8 text-center text-muted">Loading...</div>
      ) : filteredReviews.length === 0 ? (
        <div className="rounded-lg border border-border bg-card p-8 text-center text-sm text-muted">
          No reviews in queue.
        </div>
      ) : (
        <div className="space-y-3">
          {filteredReviews.map((r) => (
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
                      {r.status.replace(/_/g, " ")}
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
                    <span className="text-sm font-medium text-foreground">
                      {r.package_name || r.package_slug || "Unknown"}
                    </span>
                    {r.version && (
                      <span className="font-mono text-xs text-muted">
                        v{r.version}
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
                        Paid: {new Date(r.paid_at).toLocaleDateString()}
                      </span>
                    )}
                    {r.assigned_reviewer_id && (
                      <span className="text-purple-400">Reviewer assigned</span>
                    )}
                  </div>
                </div>

                {/* Actions */}
                <div className="flex shrink-0 gap-2">
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
                      onClick={() => openCompleteForm(r.id)}
                      className="rounded bg-primary/20 px-3 py-1 text-xs font-medium text-primary hover:bg-primary/30"
                    >
                      Complete
                    </button>
                  )}
                  {canRefund(r) && refundId !== r.id && (
                    <button
                      onClick={() => {
                        setRefundId(r.id);
                        setRefundType("full");
                        setRefundReason("");
                        setRefundAmount("");
                      }}
                      className="rounded bg-danger/20 px-3 py-1 text-xs font-medium text-danger hover:bg-danger/30"
                    >
                      Refund
                    </button>
                  )}
                </div>
              </div>

              {/* Complete form */}
              {completeId === r.id && (
                <div className="mt-4 rounded-lg border border-primary/20 bg-background p-4 space-y-3">
                  <h3 className="text-sm font-medium text-foreground">
                    Complete Review
                  </h3>

                  {/* Outcome */}
                  <div>
                    <label className="mb-1 block text-xs text-muted">
                      Outcome
                    </label>
                    <div className="flex gap-2">
                      {(
                        [
                          "approved",
                          "changes_requested",
                          "rejected",
                        ] as const
                      ).map((o) => (
                        <label
                          key={o}
                          className={`flex items-center gap-1.5 rounded border px-3 py-1.5 text-xs cursor-pointer transition-colors ${
                            completeOutcome === o
                              ? "border-primary bg-primary/10 text-foreground"
                              : "border-border text-muted hover:border-border/80"
                          }`}
                        >
                          <input
                            type="radio"
                            name="outcome"
                            value={o}
                            checked={completeOutcome === o}
                            onChange={() => setCompleteOutcome(o)}
                            className="sr-only"
                          />
                          {o.replace(/_/g, " ")}
                        </label>
                      ))}
                    </div>
                  </div>

                  {/* Checkboxes */}
                  <div>
                    <label className="mb-1 block text-xs text-muted">
                      Checks
                    </label>
                    <div className="flex gap-4">
                      <label className="flex items-center gap-1.5 text-xs text-foreground cursor-pointer">
                        <input
                          type="checkbox"
                          checked={securityPassed}
                          onChange={(e) => setSecurityPassed(e.target.checked)}
                        />
                        Security
                      </label>
                      <label className="flex items-center gap-1.5 text-xs text-foreground cursor-pointer">
                        <input
                          type="checkbox"
                          checked={compatibilityPassed}
                          onChange={(e) =>
                            setCompatibilityPassed(e.target.checked)
                          }
                        />
                        Compatibility
                      </label>
                      <label className="flex items-center gap-1.5 text-xs text-foreground cursor-pointer">
                        <input
                          type="checkbox"
                          checked={docsPassed}
                          onChange={(e) => setDocsPassed(e.target.checked)}
                        />
                        Docs
                      </label>
                    </div>
                  </div>

                  {/* Required Changes (dynamic list) */}
                  {completeOutcome === "changes_requested" && (
                    <div>
                      <label className="mb-1 block text-xs text-muted">
                        Required Changes
                      </label>
                      <div className="space-y-1">
                        {requiredChanges.map((c, i) => (
                          <div key={i} className="flex gap-1">
                            <input
                              type="text"
                              value={c}
                              onChange={(e) => {
                                const updated = [...requiredChanges];
                                updated[i] = e.target.value;
                                setRequiredChanges(updated);
                              }}
                              placeholder={`Change ${i + 1}`}
                              className="flex-1 rounded border border-border bg-card px-2 py-1 text-xs text-foreground focus:border-primary focus:outline-none"
                            />
                            {requiredChanges.length > 1 && (
                              <button
                                onClick={() =>
                                  setRequiredChanges(
                                    requiredChanges.filter((_, j) => j !== i)
                                  )
                                }
                                className="text-xs text-danger hover:text-danger/80"
                              >
                                Remove
                              </button>
                            )}
                          </div>
                        ))}
                        <button
                          onClick={() =>
                            setRequiredChanges([...requiredChanges, ""])
                          }
                          className="text-xs text-primary hover:underline"
                        >
                          + Add change
                        </button>
                      </div>
                    </div>
                  )}

                  {/* Reviewer Summary */}
                  <div>
                    <label className="mb-1 block text-xs text-muted">
                      Reviewer Summary{" "}
                      {completeOutcome === "approved" && (
                        <span className="text-danger">*</span>
                      )}
                    </label>
                    <textarea
                      value={reviewerSummary}
                      onChange={(e) => setReviewerSummary(e.target.value)}
                      rows={2}
                      className="w-full rounded border border-border bg-card px-2 py-1 text-xs text-foreground focus:border-primary focus:outline-none resize-none"
                      placeholder="Summary of the review findings..."
                    />
                  </div>

                  {/* Notes */}
                  <div>
                    <label className="mb-1 block text-xs text-muted">
                      Notes (internal)
                    </label>
                    <textarea
                      value={completeNotes}
                      onChange={(e) => setCompleteNotes(e.target.value)}
                      rows={2}
                      className="w-full rounded border border-border bg-card px-2 py-1 text-xs text-foreground focus:border-primary focus:outline-none resize-none"
                      placeholder="Optional internal notes..."
                    />
                  </div>

                  <div className="flex gap-2 pt-1">
                    <button
                      onClick={submitComplete}
                      disabled={completing}
                      className="rounded bg-primary px-3 py-1.5 text-xs font-medium text-white hover:bg-primary/90 disabled:opacity-50"
                    >
                      {completing ? "Submitting..." : "Submit Review"}
                    </button>
                    <button
                      onClick={() => setCompleteId("")}
                      className="text-xs text-muted hover:text-foreground"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}

              {/* Refund form */}
              {refundId === r.id && (
                <div className="mt-4 rounded-lg border border-danger/20 bg-background p-4 space-y-3">
                  <h3 className="text-sm font-medium text-foreground">
                    Refund Review
                  </h3>

                  <div>
                    <label className="mb-1 block text-xs text-muted">
                      Refund Type
                    </label>
                    <div className="flex gap-2">
                      <label
                        className={`flex items-center gap-1.5 rounded border px-3 py-1.5 text-xs cursor-pointer ${
                          refundType === "full"
                            ? "border-primary bg-primary/10 text-foreground"
                            : "border-border text-muted"
                        }`}
                      >
                        <input
                          type="radio"
                          name="refundType"
                          value="full"
                          checked={refundType === "full"}
                          onChange={() => setRefundType("full")}
                          className="sr-only"
                        />
                        Full (${(r.price_cents / 100).toFixed(0)})
                      </label>
                      <label
                        className={`flex items-center gap-1.5 rounded border px-3 py-1.5 text-xs cursor-pointer ${
                          refundType === "partial"
                            ? "border-primary bg-primary/10 text-foreground"
                            : "border-border text-muted"
                        }`}
                      >
                        <input
                          type="radio"
                          name="refundType"
                          value="partial"
                          checked={refundType === "partial"}
                          onChange={() => setRefundType("partial")}
                          className="sr-only"
                        />
                        Partial
                      </label>
                    </div>
                  </div>

                  {refundType === "partial" && (
                    <div>
                      <label className="mb-1 block text-xs text-muted">
                        Amount (USD)
                      </label>
                      <input
                        type="number"
                        step="0.01"
                        min="0.01"
                        max={(r.price_cents / 100).toFixed(2)}
                        value={refundAmount}
                        onChange={(e) => setRefundAmount(e.target.value)}
                        className="w-32 rounded border border-border bg-card px-2 py-1 text-xs text-foreground focus:border-primary focus:outline-none"
                        placeholder="0.00"
                      />
                    </div>
                  )}

                  <div>
                    <label className="mb-1 block text-xs text-muted">
                      Reason <span className="text-danger">*</span>
                    </label>
                    <textarea
                      value={refundReason}
                      onChange={(e) => setRefundReason(e.target.value)}
                      rows={2}
                      className="w-full rounded border border-border bg-card px-2 py-1 text-xs text-foreground focus:border-primary focus:outline-none resize-none"
                      placeholder="Reason for refund..."
                    />
                  </div>

                  <div className="flex gap-2 pt-1">
                    <button
                      onClick={submitRefund}
                      disabled={refunding || !refundReason.trim()}
                      className="rounded bg-danger px-3 py-1.5 text-xs font-medium text-white hover:bg-danger/90 disabled:opacity-50"
                    >
                      {refunding ? "Processing..." : "Process Refund"}
                    </button>
                    <button
                      onClick={() => setRefundId("")}
                      className="text-xs text-muted hover:text-foreground"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
