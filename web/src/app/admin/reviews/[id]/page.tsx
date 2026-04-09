"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { fetchWithAuth } from "@/lib/api";
import { useAdminUser } from "../../layout";
import CompleteForm from "../CompleteForm";
import RefundForm from "../RefundForm";

interface ReviewDetail {
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

// --- Checklists per tier ---
const SECURITY_CHECKS = [
  "Dependency audit (known CVEs, outdated packages)",
  "Permission review (requested capabilities vs. actual usage)",
  "Sandbox-escape analysis (file system, network, process spawning)",
];

const COMPATIBILITY_CHECKS = [
  ...SECURITY_CHECKS,
  "Provider compatibility (OpenAI, Anthropic, Google etc.)",
  "Edge-case handling (empty input, timeouts, rate limits)",
  "Error handling (graceful failures, meaningful messages)",
];

const FULL_CHECKS = [
  ...COMPATIBILITY_CHECKS,
  "Code quality (clean structure, no dead code)",
  "Documentation review (README, inline docs, examples)",
  "Best practices (idiomatic patterns, security defaults)",
];

const TIER_CHECKLISTS: Record<string, string[]> = {
  security: SECURITY_CHECKS,
  compatibility: COMPATIBILITY_CHECKS,
  full: FULL_CHECKS,
};

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function TimelineStep({ label, date, active, done }: { label: string; date?: string | null; active: boolean; done: boolean }) {
  return (
    <div className="flex items-center gap-2">
      <div
        className={`h-2.5 w-2.5 rounded-full shrink-0 ${
          done ? "bg-green-400" : active ? "bg-primary animate-pulse" : "bg-border"
        }`}
      />
      <div className="min-w-0">
        <span className={`text-xs font-medium ${done || active ? "text-foreground" : "text-muted"}`}>{label}</span>
        {date && <span className="ml-2 text-[10px] text-muted">{formatDate(date)}</span>}
      </div>
    </div>
  );
}

export default function AdminReviewDetailPage() {
  const params = useParams();
  const reviewId = params.id as string;
  const adminUser = useAdminUser();

  const [review, setReview] = useState<ReviewDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [showCompleteForm, setShowCompleteForm] = useState(false);
  const [showRefundForm, setShowRefundForm] = useState(false);

  // Checklist state (local only, not persisted)
  const [checkedItems, setCheckedItems] = useState<Record<number, boolean>>({});

  useEffect(() => {
    loadReview();
  }, [reviewId]);

  async function loadReview() {
    setLoading(true);
    try {
      const res = await fetchWithAuth(`/admin/reviews/${reviewId}`);
      if (res.ok) {
        setReview(await res.json());
      } else {
        setError("Failed to load review");
      }
    } catch {
      setError("Failed to load review");
    } finally {
      setLoading(false);
    }
  }

  async function assignToMe() {
    if (!adminUser || !review) return;
    setError("");
    setSuccess("");
    try {
      const res = await fetchWithAuth(`/admin/reviews/${review.id}/assign`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reviewer_id: adminUser.id }),
      });
      if (res.ok) {
        setSuccess("Review assigned to you");
        await loadReview();
      } else {
        const d = await res.json().catch(() => ({}));
        setError(d.error?.message || "Failed to assign review");
      }
    } catch {
      setError("Failed to assign review");
    }
  }

  function canRefund(r: ReviewDetail): boolean {
    return (
      ["paid", "in_review", "approved"].includes(r.status) &&
      r.refund_amount_cents === null &&
      r.status !== "refunded"
    );
  }

  if (loading) {
    return <div className="py-8 text-center text-muted">Loading...</div>;
  }

  if (!review) {
    return (
      <div className="py-8 text-center">
        <p className="text-sm text-muted">{error || "Review not found"}</p>
        <Link href="/admin/reviews" className="mt-2 inline-block text-xs text-primary hover:underline">
          Back to queue
        </Link>
      </div>
    );
  }

  const checklist = TIER_CHECKLISTS[review.tier] || SECURITY_CHECKS;
  const isActive = review.status === "in_review";
  const isPaid = review.status === "paid";
  const isCompleted = ["approved", "changes_requested", "rejected", "refunded"].includes(review.status);

  return (
    <div>
      {/* Back link */}
      <Link href="/admin/reviews" className="mb-4 inline-flex items-center gap-1 text-xs text-muted hover:text-foreground">
        &larr; Back to queue
      </Link>

      {/* Alerts */}
      {error && (
        <div className="mb-4 rounded-md border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">
          {error}{" "}
          <button onClick={() => setError("")} className="ml-2 underline">dismiss</button>
        </div>
      )}
      {success && (
        <div className="mb-4 rounded-md border border-success/30 bg-success/10 px-4 py-3 text-sm text-success">
          {success}{" "}
          <button onClick={() => setSuccess("")} className="ml-2 underline">dismiss</button>
        </div>
      )}

      {/* Header */}
      <div className="mb-6 rounded-lg border border-border bg-card p-5">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <div className="flex items-center gap-2 flex-wrap">
              {review.package_slug ? (
                <a
                  href={`/packages/${review.package_slug}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-base font-semibold text-primary hover:underline"
                >
                  {review.package_name || review.package_slug} &#8599;
                </a>
              ) : (
                <span className="text-base font-semibold text-foreground">Unknown Package</span>
              )}
              {review.version && (
                <span className="font-mono text-sm text-muted">v{review.version}</span>
              )}
            </div>
            <div className="mt-1 flex items-center gap-3 text-xs text-muted">
              {review.publisher_slug && (
                <span>
                  Publisher:{" "}
                  <Link href="/admin/publishers" className="text-primary hover:underline">
                    @{review.publisher_slug}
                  </Link>
                </span>
              )}
              <span className="font-mono">{review.order_id}</span>
            </div>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <span
              className={`rounded px-2.5 py-1 text-xs font-medium ${
                STATUS_BADGES[review.status] || "bg-muted/20 text-muted"
              }`}
            >
              {review.status === "refunded" ? "Refunded" : review.status.replace(/_/g, " ")}
            </span>
            <span className="rounded px-2.5 py-1 text-xs font-medium bg-card border border-border text-muted">
              {TIER_LABELS[review.tier] || review.tier}
            </span>
            {review.express && (
              <span className="rounded px-2.5 py-1 text-xs font-medium bg-yellow-500/20 text-yellow-400">
                Express
              </span>
            )}
            <span className="font-mono text-sm text-muted">
              ${(review.price_cents / 100).toFixed(0)} USD
            </span>
          </div>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        {/* Left column: Timeline + Verification + Actions */}
        <div className="space-y-4 lg:col-span-1">
          {/* Timeline */}
          <div className="rounded-lg border border-border bg-card p-4">
            <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted">Timeline</h2>
            <div className="space-y-2.5">
              <TimelineStep
                label="Paid"
                date={review.paid_at}
                active={isPaid}
                done={!isPaid}
              />
              <TimelineStep
                label="In Review"
                date={isActive || isCompleted ? review.paid_at : null}
                active={isActive}
                done={isCompleted}
              />
              <TimelineStep
                label="Completed"
                date={review.reviewed_at}
                active={false}
                done={isCompleted}
              />
            </div>
          </div>

          {/* Verification context */}
          {review.verification_tier && (
            <div className="rounded-lg border border-border bg-card p-4">
              <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted">Auto Verification</h2>
              <div className="space-y-1 text-xs">
                <div className="flex justify-between">
                  <span className="text-muted">Tier</span>
                  <span className="text-foreground">{review.verification_tier}</span>
                </div>
                {review.verification_score != null && (
                  <div className="flex justify-between">
                    <span className="text-muted">Score</span>
                    <span className="text-foreground">{review.verification_score}</span>
                  </div>
                )}
                {review.verification_status && (
                  <div className="flex justify-between">
                    <span className="text-muted">Status</span>
                    <span className="text-foreground">{review.verification_status}</span>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Refund info for refunded */}
          {review.status === "refunded" && review.refund_amount_cents != null && (
            <div className="rounded-lg border border-gray-500/30 bg-gray-500/5 p-4">
              <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-gray-400">Refund</h2>
              <div className="text-sm font-mono text-gray-400">
                ${(review.refund_amount_cents / 100).toFixed(2)} refunded
              </div>
            </div>
          )}

          {/* Actions */}
          <div className="rounded-lg border border-border bg-card p-4 space-y-2">
            <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted">Actions</h2>
            {isPaid && (
              <button
                onClick={assignToMe}
                className="w-full rounded bg-purple-500/20 px-3 py-2 text-xs font-medium text-purple-400 hover:bg-purple-500/30"
              >
                Assign to me
              </button>
            )}
            {isActive && !showCompleteForm && (
              <button
                onClick={() => setShowCompleteForm(true)}
                className="w-full rounded bg-primary/20 px-3 py-2 text-xs font-medium text-primary hover:bg-primary/30"
              >
                Complete Review
              </button>
            )}
            {canRefund(review) && !showRefundForm && (
              <button
                onClick={() => setShowRefundForm(true)}
                className="w-full rounded bg-danger/20 px-3 py-2 text-xs font-medium text-danger hover:bg-danger/30"
              >
                Refund
              </button>
            )}
            {!isPaid && !isActive && !canRefund(review) && (
              <p className="text-xs text-muted">No actions available</p>
            )}
          </div>
        </div>

        {/* Right column: Forms + Checklist + Results */}
        <div className="space-y-4 lg:col-span-2">
          {/* Complete form */}
          {showCompleteForm && isActive && (
            <CompleteForm
              reviewId={review.id}
              onSuccess={async (outcome) => {
                setSuccess(`Review ${outcome}`);
                setShowCompleteForm(false);
                await loadReview();
              }}
              onCancel={() => setShowCompleteForm(false)}
            />
          )}

          {/* Refund form */}
          {showRefundForm && canRefund(review) && (
            <RefundForm
              reviewId={review.id}
              priceCents={review.price_cents}
              onSuccess={async () => {
                setSuccess("Refund processed");
                setShowRefundForm(false);
                await loadReview();
              }}
              onCancel={() => setShowRefundForm(false)}
            />
          )}

          {/* Reviewer Checklist — shown for in_review (primary) and paid (optional) */}
          {(isActive || isPaid) && (
            <div className="rounded-lg border border-border bg-card p-4">
              <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted">
                Review Checklist ({TIER_LABELS[review.tier] || review.tier})
              </h2>
              <div className="space-y-2">
                {checklist.map((item, i) => {
                  // Show section headers
                  const isSecuritySection = i === 0;
                  const isCompatSection = review.tier !== "security" && i === SECURITY_CHECKS.length;
                  const isFullSection = review.tier === "full" && i === COMPATIBILITY_CHECKS.length;

                  return (
                    <div key={i}>
                      {isSecuritySection && (
                        <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-muted/60">Security</div>
                      )}
                      {isCompatSection && (
                        <div className="mt-3 mb-1 text-[10px] font-semibold uppercase tracking-wider text-muted/60">Compatibility</div>
                      )}
                      {isFullSection && (
                        <div className="mt-3 mb-1 text-[10px] font-semibold uppercase tracking-wider text-muted/60">Full</div>
                      )}
                      <label className="flex items-start gap-2 cursor-pointer group">
                        <input
                          type="checkbox"
                          checked={!!checkedItems[i]}
                          onChange={(e) => setCheckedItems((prev) => ({ ...prev, [i]: e.target.checked }))}
                          className="mt-0.5 shrink-0"
                        />
                        <span
                          className={`text-xs ${
                            checkedItems[i] ? "text-muted line-through" : "text-foreground"
                          } group-hover:text-foreground`}
                        >
                          {item}
                        </span>
                      </label>
                    </div>
                  );
                })}
              </div>
              <div className="mt-3 text-[10px] text-muted">
                {Object.values(checkedItems).filter(Boolean).length}/{checklist.length} checked
                {" "}&mdash; local only, not saved
              </div>
            </div>
          )}

          {/* Review Results (when completed) */}
          {isCompleted && review.review_result && (
            <div className="rounded-lg border border-border bg-card p-4">
              <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted">Review Result</h2>
              <div className="space-y-3">
                {/* Checks */}
                <div className="flex gap-3 flex-wrap">
                  {review.review_result.security_passed !== undefined && (
                    <span className={`rounded px-2 py-0.5 text-xs font-medium ${review.review_result.security_passed ? "bg-green-500/20 text-green-400" : "bg-red-500/20 text-red-400"}`}>
                      Security {review.review_result.security_passed ? "passed" : "failed"}
                    </span>
                  )}
                  {review.review_result.compatibility_passed !== undefined && (
                    <span className={`rounded px-2 py-0.5 text-xs font-medium ${review.review_result.compatibility_passed ? "bg-green-500/20 text-green-400" : "bg-red-500/20 text-red-400"}`}>
                      Compatibility {review.review_result.compatibility_passed ? "passed" : "failed"}
                    </span>
                  )}
                  {review.review_result.docs_passed !== undefined && (
                    <span className={`rounded px-2 py-0.5 text-xs font-medium ${review.review_result.docs_passed ? "bg-green-500/20 text-green-400" : "bg-red-500/20 text-red-400"}`}>
                      Docs {review.review_result.docs_passed ? "passed" : "failed"}
                    </span>
                  )}
                </div>

                {/* Required Changes */}
                {review.review_result.required_changes && review.review_result.required_changes.length > 0 && (
                  <div>
                    <h3 className="mb-1 text-xs font-medium text-muted">Required Changes</h3>
                    <ul className="list-disc list-inside space-y-0.5">
                      {review.review_result.required_changes.map((c: string, i: number) => (
                        <li key={i} className="text-xs text-foreground">{c}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Reviewer Summary */}
                {review.review_result.reviewer_summary && (
                  <div>
                    <h3 className="mb-1 text-xs font-medium text-muted">Summary</h3>
                    <p className="text-xs text-foreground whitespace-pre-wrap">{review.review_result.reviewer_summary}</p>
                  </div>
                )}

                {/* Notes */}
                {review.review_notes && (
                  <div>
                    <h3 className="mb-1 text-xs font-medium text-muted">Internal Notes</h3>
                    <p className="text-xs text-foreground/70 whitespace-pre-wrap">{review.review_notes}</p>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
