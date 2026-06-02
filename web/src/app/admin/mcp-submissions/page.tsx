"use client";

import { useState, useEffect, useCallback } from "react";
import { fetchWithAuth } from "@/lib/api";

interface Submission {
  id: string;
  package_name: string;
  package_registry: string;
  package_version: string | null;
  source_repo: string | null;
  status: string;
  report_status: string | null;
  report_summary: string | null;
  actions_high: number;
  actions_medium: number;
  tools_count: number;
  created_at: string;
}

interface SubmissionDetail {
  id: string;
  package_name: string;
  package_registry: string;
  package_version: string | null;
  source_repo: string | null;
  status: string;
  manifest: Record<string, unknown>;
  verification_report: Record<string, unknown>;
  reviewer_notes: string | null;
  created_at: string;
  updated_at: string;
}

const STATUS_COLORS: Record<string, string> = {
  pending: "bg-blue-500/20 text-blue-400",
  action_required: "bg-yellow-500/20 text-yellow-400",
  approved: "bg-green-500/20 text-green-400",
  rejected: "bg-red-500/20 text-red-400",
  needs_changes: "bg-orange-500/20 text-orange-400",
};

function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_COLORS[status] || "bg-zinc-500/20 text-zinc-400"}`}
    >
      {status.replace("_", " ")}
    </span>
  );
}

function formatDate(iso: string | null) {
  if (!iso) return "--";
  return new Date(iso).toLocaleDateString("de-DE", {
    day: "2-digit",
    month: "2-digit",
    year: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function McpSubmissionsPage() {
  const [submissions, setSubmissions] = useState<Submission[]>([]);
  const [total, setTotal] = useState(0);
  const [filterStatus, setFilterStatus] = useState<string>("");
  const [selected, setSelected] = useState<SubmissionDetail | null>(null);
  const [reviewStatus, setReviewStatus] = useState("");
  const [reviewNotes, setReviewNotes] = useState("");
  const [loading, setLoading] = useState(false);

  const loadSubmissions = useCallback(async () => {
    const params = new URLSearchParams();
    if (filterStatus) params.set("status", filterStatus);
    const res = await fetchWithAuth(`/admin/mcp/submissions?${params}`);
    if (res.ok) {
      const data = await res.json();
      setSubmissions(data.submissions);
      setTotal(data.total);
    }
  }, [filterStatus]);

  useEffect(() => {
    loadSubmissions();
  }, [loadSubmissions]);

  const loadDetail = async (id: string) => {
    const res = await fetchWithAuth(`/admin/mcp/submissions/${id}`);
    if (res.ok) {
      const data: SubmissionDetail = await res.json();
      setSelected(data);
      setReviewStatus(data.status);
      setReviewNotes(data.reviewer_notes || "");
    }
  };

  const submitReview = async () => {
    if (!selected || !reviewStatus) return;
    setLoading(true);
    const res = await fetchWithAuth(`/admin/mcp/submissions/${selected.id}/review`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: reviewStatus, notes: reviewNotes || null }),
    });
    setLoading(false);
    if (res.ok) {
      setSelected(null);
      loadSubmissions();
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-foreground">MCP Submissions</h1>
        <span className="text-sm text-muted">{total} total</span>
      </div>

      {/* Filter */}
      <div className="flex gap-2">
        {["", "pending", "action_required", "needs_changes", "approved", "rejected"].map(
          (s) => (
            <button
              key={s}
              onClick={() => setFilterStatus(s)}
              className={`rounded-md px-3 py-1 text-xs font-medium border transition-colors ${
                filterStatus === s
                  ? "bg-primary/10 border-primary/30 text-primary"
                  : "bg-card border-border text-muted hover:text-foreground"
              }`}
            >
              {s || "All"}
            </button>
          )
        )}
      </div>

      {/* Submissions list */}
      <div className="space-y-2">
        {submissions.map((s) => (
          <button
            key={s.id}
            onClick={() => loadDetail(s.id)}
            className={`w-full text-left rounded-lg border p-4 transition-colors ${
              selected?.id === s.id
                ? "border-primary/30 bg-primary/5"
                : "border-border bg-card hover:border-primary/20"
            }`}
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <span className="font-mono text-sm font-semibold text-foreground">
                  {s.package_name}
                </span>
                <span className="text-xs text-muted">{s.package_registry}</span>
                {s.package_version && (
                  <span className="text-xs text-muted">@{s.package_version}</span>
                )}
                <StatusBadge status={s.status} />
              </div>
              <div className="flex items-center gap-3 text-xs text-muted">
                {s.tools_count > 0 && <span>{s.tools_count} tools</span>}
                {s.actions_high > 0 && (
                  <span className="text-red-400">{s.actions_high} required</span>
                )}
                {s.actions_medium > 0 && (
                  <span className="text-yellow-400">{s.actions_medium} recommended</span>
                )}
                <span>{formatDate(s.created_at)}</span>
              </div>
            </div>
            {s.report_summary && (
              <p className="mt-1 text-xs text-muted">{s.report_summary}</p>
            )}
          </button>
        ))}
        {submissions.length === 0 && (
          <p className="text-center text-sm text-muted py-8">No submissions found.</p>
        )}
      </div>

      {/* Detail panel */}
      {selected && (
        <div className="rounded-xl border border-border bg-card p-6 space-y-6">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-bold text-foreground">
              {selected.package_name}
              <span className="ml-2 text-sm font-normal text-muted">
                @{selected.package_version}
              </span>
            </h2>
            <button
              onClick={() => setSelected(null)}
              className="text-xs text-muted hover:text-foreground"
            >
              Close
            </button>
          </div>

          {/* Verification Report Summary */}
          {(() => {
            const report = selected.verification_report as Record<string, unknown>;
            const checks = (report.checks || []) as Array<{
              name: string;
              passed: boolean;
              detail: string;
            }>;
            const actions = (report.actions || []) as Array<{
              severity: string;
              code: string;
              title: string;
              detail: string;
              fix: string;
            }>;
            const perms = (report.permissions as Record<string, unknown>) || {};
            const declared = (perms.declared || {}) as Record<string, string>;

            return (
              <>
                {/* Checks */}
                <div>
                  <h3 className="text-sm font-semibold text-foreground mb-2">
                    Verification Checks
                  </h3>
                  <div className="space-y-1">
                    {checks.map((c, i) => (
                      <div key={i} className="flex items-center gap-2 text-xs">
                        <span
                          className={
                            c.passed ? "text-green-400" : "text-yellow-400"
                          }
                        >
                          {c.passed ? "[OK]" : "[!!]"}
                        </span>
                        <span className="text-foreground">{c.name}</span>
                        {c.detail && (
                          <span className="text-muted">-- {c.detail}</span>
                        )}
                      </div>
                    ))}
                  </div>
                </div>

                {/* Permission Profile */}
                {declared.network && (
                  <div>
                    <h3 className="text-sm font-semibold text-foreground mb-2">
                      Permission Profile
                    </h3>
                    <div className="flex gap-4 text-xs">
                      {["network", "filesystem", "code_execution"].map((k) => (
                        <span key={k} className="text-muted">
                          {k}:{" "}
                          <span
                            className={`font-mono ${declared[k] === "none" ? "text-green-400" : "text-amber-400"}`}
                          >
                            {declared[k]}
                          </span>
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {/* Actions */}
                {actions.length > 0 && (
                  <div>
                    <h3 className="text-sm font-semibold text-foreground mb-2">
                      Maintainer Actions ({actions.length})
                    </h3>
                    <div className="space-y-2">
                      {actions.map((a, i) => (
                        <div
                          key={i}
                          className={`rounded-md border p-3 text-xs ${
                            a.severity === "high"
                              ? "border-red-500/20 bg-red-500/5"
                              : a.severity === "medium"
                                ? "border-yellow-500/20 bg-yellow-500/5"
                                : "border-border bg-background"
                          }`}
                        >
                          <div className="font-medium text-foreground">
                            [{a.code}] {a.title}
                          </div>
                          <div className="mt-1 text-muted">{a.detail}</div>
                          {a.fix && (
                            <div className="mt-1 text-primary/80">
                              Fix: {a.fix}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </>
            );
          })()}

          {/* Source repo */}
          {selected.source_repo && (
            <div className="text-xs text-muted">
              Source:{" "}
              <a
                href={selected.source_repo}
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary hover:underline"
              >
                {selected.source_repo}
              </a>
            </div>
          )}

          {/* Review form */}
          <div className="border-t border-border pt-4 space-y-3">
            <h3 className="text-sm font-semibold text-foreground">
              Review Decision
            </h3>
            <div className="flex gap-2">
              {["approved", "needs_changes", "rejected"].map((s) => (
                <button
                  key={s}
                  onClick={() => setReviewStatus(s)}
                  className={`rounded-md px-3 py-1.5 text-xs font-medium border transition-colors ${
                    reviewStatus === s
                      ? s === "approved"
                        ? "bg-green-500/10 border-green-500/30 text-green-400"
                        : s === "rejected"
                          ? "bg-red-500/10 border-red-500/30 text-red-400"
                          : "bg-orange-500/10 border-orange-500/30 text-orange-400"
                      : "bg-card border-border text-muted hover:text-foreground"
                  }`}
                >
                  {s.replace("_", " ")}
                </button>
              ))}
            </div>
            <textarea
              value={reviewNotes}
              onChange={(e) => setReviewNotes(e.target.value)}
              placeholder="Review notes (optional)"
              rows={3}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted/50 focus:border-primary/30 focus:outline-none"
            />
            <button
              onClick={submitReview}
              disabled={loading || !reviewStatus || reviewStatus === selected.status}
              className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {loading ? "Saving..." : "Save Review"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
