"use client";

import { useState } from "react";
import { fetchWithAuth } from "@/lib/api";

interface RefundFormProps {
  reviewId: string;
  priceCents: number;
  onSuccess: () => void;
  onCancel: () => void;
}

export default function RefundForm({ reviewId, priceCents, onSuccess, onCancel }: RefundFormProps) {
  const [refundType, setRefundType] = useState<"full" | "partial">("full");
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function submit() {
    if (!reason.trim()) {
      setError("Refund reason is required");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const body: Record<string, any> = { reason: reason.trim() };
      if (refundType === "partial") {
        const cents = Math.round(parseFloat(amount) * 100);
        if (isNaN(cents) || cents <= 0) {
          setError("Invalid refund amount");
          setSubmitting(false);
          return;
        }
        body.amount_cents = cents;
      }
      const res = await fetchWithAuth(`/admin/reviews/${reviewId}/refund`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (res.ok) {
        onSuccess();
      } else {
        const d = await res.json().catch(() => ({}));
        setError(d.error?.message || "Failed to process refund");
      }
    } catch {
      setError("Failed to process refund");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="rounded-lg border border-danger/20 bg-background p-4 space-y-3">
      <h3 className="text-sm font-medium text-foreground">Refund Review</h3>

      {error && (
        <div className="rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger">
          {error}
        </div>
      )}

      <div>
        <label className="mb-1 block text-xs text-muted">Refund Type</label>
        <div className="flex gap-2">
          <label
            className={`flex items-center gap-1.5 rounded border px-3 py-1.5 text-xs cursor-pointer ${
              refundType === "full" ? "border-primary bg-primary/10 text-foreground" : "border-border text-muted"
            }`}
          >
            <input
              type="radio"
              name={`refundType-${reviewId}`}
              value="full"
              checked={refundType === "full"}
              onChange={() => setRefundType("full")}
              className="sr-only"
            />
            Full (${(priceCents / 100).toFixed(0)})
          </label>
          <label
            className={`flex items-center gap-1.5 rounded border px-3 py-1.5 text-xs cursor-pointer ${
              refundType === "partial" ? "border-primary bg-primary/10 text-foreground" : "border-border text-muted"
            }`}
          >
            <input
              type="radio"
              name={`refundType-${reviewId}`}
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
          <label className="mb-1 block text-xs text-muted">Amount (USD)</label>
          <input
            type="number"
            step="0.01"
            min="0.01"
            max={(priceCents / 100).toFixed(2)}
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
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
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          rows={2}
          className="w-full rounded border border-border bg-card px-2 py-1 text-xs text-foreground focus:border-primary focus:outline-none resize-none"
          placeholder="Reason for refund..."
        />
      </div>

      <div className="flex gap-2 pt-1">
        <button
          onClick={submit}
          disabled={submitting || !reason.trim()}
          className="rounded bg-danger px-3 py-1.5 text-xs font-medium text-white hover:bg-danger/90 disabled:opacity-50"
        >
          {submitting ? "Processing..." : "Process Refund"}
        </button>
        <button onClick={onCancel} className="text-xs text-muted hover:text-foreground">
          Cancel
        </button>
      </div>
    </div>
  );
}
