"use client";

import { useState } from "react";
import { fetchWithAuth } from "@/lib/api";

interface CompleteFormProps {
  reviewId: string;
  onSuccess: (outcome: string) => void;
  onCancel: () => void;
}

export default function CompleteForm({ reviewId, onSuccess, onCancel }: CompleteFormProps) {
  const [outcome, setOutcome] = useState<"approved" | "changes_requested" | "rejected">("approved");
  const [securityPassed, setSecurityPassed] = useState(true);
  const [compatibilityPassed, setCompatibilityPassed] = useState(true);
  const [docsPassed, setDocsPassed] = useState(true);
  const [requiredChanges, setRequiredChanges] = useState<string[]>([""]);
  const [reviewerSummary, setReviewerSummary] = useState("");
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  function validate(): string | null {
    if (outcome === "approved") {
      if (!reviewerSummary.trim()) return "Reviewer summary is required for approved outcome";
    } else if (outcome === "changes_requested") {
      const nonEmpty = requiredChanges.filter((c) => c.trim());
      if (nonEmpty.length === 0) return "At least one required change is needed";
    } else if (outcome === "rejected") {
      if (!reviewerSummary.trim() && !notes.trim()) return "Reviewer summary or notes required for rejection";
    }
    return null;
  }

  async function submit() {
    const validationError = validate();
    if (validationError) {
      setError(validationError);
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const review_result: Record<string, any> = {
        security_passed: securityPassed,
        compatibility_passed: compatibilityPassed,
        docs_passed: docsPassed,
      };
      if (reviewerSummary.trim()) review_result.reviewer_summary = reviewerSummary.trim();
      const nonEmptyChanges = requiredChanges.filter((c) => c.trim());
      if (nonEmptyChanges.length > 0) review_result.required_changes = nonEmptyChanges;

      const res = await fetchWithAuth(`/admin/reviews/${reviewId}/complete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          outcome,
          notes: notes.trim() || null,
          review_result,
        }),
      });
      if (res.ok) {
        onSuccess(outcome);
      } else {
        const d = await res.json().catch(() => ({}));
        setError(d.error?.message || "Failed to complete review");
      }
    } catch {
      setError("Failed to complete review");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="rounded-lg border border-primary/20 bg-background p-4 space-y-3">
      <h3 className="text-sm font-medium text-foreground">Complete Review</h3>

      {error && (
        <div className="rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger">
          {error}
        </div>
      )}

      {/* Outcome */}
      <div>
        <label className="mb-1 block text-xs text-muted">Outcome</label>
        <div className="flex gap-2">
          {(["approved", "changes_requested", "rejected"] as const).map((o) => (
            <label
              key={o}
              className={`flex items-center gap-1.5 rounded border px-3 py-1.5 text-xs cursor-pointer transition-colors ${
                outcome === o
                  ? "border-primary bg-primary/10 text-foreground"
                  : "border-border text-muted hover:border-border/80"
              }`}
            >
              <input
                type="radio"
                name={`outcome-${reviewId}`}
                value={o}
                checked={outcome === o}
                onChange={() => setOutcome(o)}
                className="sr-only"
              />
              {o.replace(/_/g, " ")}
            </label>
          ))}
        </div>
      </div>

      {/* Checks */}
      <div>
        <label className="mb-1 block text-xs text-muted">Checks</label>
        <div className="flex gap-4">
          <label className="flex items-center gap-1.5 text-xs text-foreground cursor-pointer">
            <input type="checkbox" checked={securityPassed} onChange={(e) => setSecurityPassed(e.target.checked)} />
            Security
          </label>
          <label className="flex items-center gap-1.5 text-xs text-foreground cursor-pointer">
            <input type="checkbox" checked={compatibilityPassed} onChange={(e) => setCompatibilityPassed(e.target.checked)} />
            Compatibility
          </label>
          <label className="flex items-center gap-1.5 text-xs text-foreground cursor-pointer">
            <input type="checkbox" checked={docsPassed} onChange={(e) => setDocsPassed(e.target.checked)} />
            Docs
          </label>
        </div>
      </div>

      {/* Required Changes */}
      {outcome === "changes_requested" && (
        <div>
          <label className="mb-1 block text-xs text-muted">Required Changes</label>
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
                    onClick={() => setRequiredChanges(requiredChanges.filter((_, j) => j !== i))}
                    className="text-xs text-danger hover:text-danger/80"
                  >
                    Remove
                  </button>
                )}
              </div>
            ))}
            <button onClick={() => setRequiredChanges([...requiredChanges, ""])} className="text-xs text-primary hover:underline">
              + Add change
            </button>
          </div>
        </div>
      )}

      {/* Reviewer Summary */}
      <div>
        <label className="mb-1 block text-xs text-muted">
          Reviewer Summary {outcome === "approved" && <span className="text-danger">*</span>}
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
        <label className="mb-1 block text-xs text-muted">Notes (internal)</label>
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={2}
          className="w-full rounded border border-border bg-card px-2 py-1 text-xs text-foreground focus:border-primary focus:outline-none resize-none"
          placeholder="Optional internal notes..."
        />
      </div>

      <div className="flex gap-2 pt-1">
        <button
          onClick={submit}
          disabled={submitting}
          className="rounded bg-primary px-3 py-1.5 text-xs font-medium text-white hover:bg-primary/90 disabled:opacity-50"
        >
          {submitting ? "Submitting..." : "Submit Review"}
        </button>
        <button onClick={onCancel} className="text-xs text-muted hover:text-foreground">
          Cancel
        </button>
      </div>
    </div>
  );
}
