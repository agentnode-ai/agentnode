"use client";

import { useState, useEffect } from "react";
import { fetchWithAuth } from "@/lib/api";

interface Submission {
  id: string;
  package_name: string;
  package_registry: string;
  package_version: string | null;
  source_repo: string | null;
  status: string;
  report_status: string | null;
  server_status: string | null;
  report_summary: string | null;
  actions_high: number;
  actions_medium: number;
  tools_count: number;
  created_at: string;
}

interface ServerVerification {
  server_status: string | null;
  registry: string | null;
  package_name: string | null;
  package_exists: boolean;
  resolved_version: string | null;
  version_exists: boolean;
  shasum: string | null;
  integrity: string | null;
  registry_repo_url: string | null;
  declared_source_repo: string | null;
  repo_consistency: string;
  maintainer_list: string[];
  command_pinning: string;
  pinned_command: string[] | null;
  command_rewrite: string;
  warnings: string[];
  errors: string[];
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
  server_verification: ServerVerification | null;
  ownership: {
    status: string; // verified | revoked | expired | missing
    claim_id: string | null;
    method: string | null; // manual_admin | registry_challenge
    verified_at: string | null;
    expires_at: string | null;
  };
  reviewer_notes: string | null;
  maintainer_feedback: string | null;
  published_package_id: string | null;
  created_at: string;
  updated_at: string;
}

// Server-derived registry verification status. Authoritative — distinct from the
// maintainer-attested verification_report.
const SERVER_STATUS: Record<string, { label: string; cls: string }> = {
  verified: { label: "Server verified", cls: "text-green-400" },
  mismatch: { label: "Mismatch", cls: "text-red-400" },
  indeterminate: { label: "Indeterminate", cls: "text-amber-400" },
  unavailable: { label: "Unavailable", cls: "text-blue-400" },
};

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

function Fact({
  label,
  value,
  bool,
  mono,
  cls,
}: {
  label: string;
  value?: string | null;
  bool?: boolean;
  mono?: boolean;
  cls?: string;
}) {
  let display: string;
  let color = cls || "text-foreground";
  if (typeof bool === "boolean") {
    display = bool ? "yes" : "no";
    color = bool ? "text-green-400" : "text-red-400";
  } else {
    display = value || "--";
  }
  return (
    <div className="flex justify-between gap-2">
      <span className="shrink-0 text-muted">{label}</span>
      <span className={`min-w-0 break-all text-right ${mono ? "font-mono" : ""} ${color}`}>
        {display}
      </span>
    </div>
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
  const [maintainerFeedback, setMaintainerFeedback] = useState("");
  const [loading, setLoading] = useState(false);

  const loadSubmissions = async () => {
    const params = new URLSearchParams();
    if (filterStatus) params.set("status", filterStatus);
    const res = await fetchWithAuth(`/admin/mcp/submissions?${params}`);
    if (res.ok) {
      const data = await res.json();
      setSubmissions(data.submissions);
      setTotal(data.total);
    }
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const params = new URLSearchParams();
      if (filterStatus) params.set("status", filterStatus);
      const res = await fetchWithAuth(`/admin/mcp/submissions?${params}`);
      if (res.ok && !cancelled) {
        const data = await res.json();
        setSubmissions(data.submissions);
        setTotal(data.total);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [filterStatus]);

  const loadDetail = async (id: string) => {
    const res = await fetchWithAuth(`/admin/mcp/submissions/${id}`);
    if (res.ok) {
      const data: SubmissionDetail = await res.json();
      setSelected(data);
      setReviewStatus(data.status);
      setReviewNotes(data.reviewer_notes || "");
      setMaintainerFeedback(data.maintainer_feedback || "");
    }
  };

  const submitReview = async () => {
    if (!selected || !reviewStatus) return;
    setLoading(true);
    const res = await fetchWithAuth(`/admin/mcp/submissions/${selected.id}/review`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: reviewStatus, notes: reviewNotes || null, maintainer_feedback: maintainerFeedback || null }),
    });
    setLoading(false);
    if (res.ok) {
      setSelected(null);
      loadSubmissions();
    }
  };

  const verifyOwnership = async () => {
    if (!selected) return;
    const reason = window.prompt(
      "Reason for manual ownership verification (audited, stored on the claim):"
    );
    if (!reason) return;
    setLoading(true);
    const res = await fetchWithAuth(`/admin/mcp/submissions/${selected.id}/verify-ownership`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason }),
    });
    setLoading(false);
    if (res.ok) {
      await loadDetail(selected.id);
    } else {
      const err = await res.json().catch(() => ({ error: { message: res.statusText } }));
      alert(`Failed: ${err.error?.message || "Unknown error"}`);
    }
  };

  const revokeOwnership = async () => {
    if (!selected || !selected.ownership.claim_id) return;
    const reason = window.prompt(
      "Reason for revoking ownership (audited, stored on the claim):"
    );
    if (!reason) return;
    setLoading(true);
    const res = await fetchWithAuth(`/admin/mcp/claims/${selected.ownership.claim_id}/revoke`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason }),
    });
    setLoading(false);
    if (res.ok) {
      await loadDetail(selected.id);
    } else {
      const err = await res.json().catch(() => ({ error: { message: res.statusText } }));
      alert(`Failed: ${err.error?.message || "Unknown error"}`);
    }
  };

  const publishToCatalog = async () => {
    if (!selected) return;
    const report = selected.verification_report as Record<string, unknown>;
    const actions = (report.actions || []) as Array<{ severity: string }>;
    const mediumCount = actions.filter((a) => a.severity === "medium").length;
    const serverStatus = selected.server_verification?.server_status;

    if (serverStatus === "indeterminate") {
      const confirmed = window.confirm(
        "Server verification is indeterminate (registry metadata incomplete). Publish anyway?"
      );
      if (!confirmed) return;
    }
    if (mediumCount > 0) {
      const confirmed = window.confirm(
        `This submission has ${mediumCount} medium-severity warning(s). Publish anyway?`
      );
      if (!confirmed) return;
    }

    setLoading(true);
    const res = await fetchWithAuth(`/admin/mcp/submissions/${selected.id}/publish`, {
      method: "POST",
    });
    setLoading(false);

    if (res.ok) {
      const data = await res.json();
      alert(`Published: ${data.package_slug} — ${data.message}`);
      setSelected(null);
      loadSubmissions();
    } else {
      const err = await res.json().catch(() => ({ error: { message: res.statusText } }));
      alert(`Publish failed: ${err.error?.message || "Unknown error"}`);
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
                {s.server_status && (
                  <span
                    className={`text-xs font-medium ${SERVER_STATUS[s.server_status]?.cls || "text-muted"}`}
                    title="Server-side registry verification"
                  >
                    ● {SERVER_STATUS[s.server_status]?.label || s.server_status}
                  </span>
                )}
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

          {/* Server-verified vs maintainer-attested */}
          {(() => {
            const report = selected.verification_report as Record<string, unknown>;
            const sv = selected.server_verification;
            const ss = sv?.server_status || null;
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
                {/* Trust banner */}
                {ss === "mismatch" && (
                  <div className="rounded-md border border-red-500/30 bg-red-500/10 p-3">
                    <p className="text-sm font-semibold text-red-400">Trust mismatch detected</p>
                    <p className="mt-1 text-xs text-muted">
                      Server verification contradicts the maintainer-submitted report. Publishing is blocked.
                    </p>
                    {sv?.errors?.length ? (
                      <ul className="mt-2 list-inside list-disc text-xs text-red-300/80">
                        {sv.errors.map((e, i) => (
                          <li key={i}>{e}</li>
                        ))}
                      </ul>
                    ) : null}
                  </div>
                )}
                {ss === "indeterminate" && (
                  <div className="rounded-md border border-amber-500/30 bg-amber-500/10 p-3">
                    <p className="text-sm font-semibold text-amber-400">Registry metadata incomplete</p>
                    <p className="mt-1 text-xs text-muted">
                      Some facts could not be verified from npm/PyPI metadata. Admin review required.
                    </p>
                  </div>
                )}
                {ss === "unavailable" && (
                  <div className="rounded-md border border-blue-500/30 bg-blue-500/10 p-3">
                    <p className="text-sm font-semibold text-blue-400">Registry verification unavailable</p>
                    <p className="mt-1 text-xs text-muted">
                      Retry server verification before publishing.
                    </p>
                  </div>
                )}

                {/* Panel 1: Server-verified registry facts (authoritative) */}
                <div className="rounded-lg border border-border bg-background/40 p-4">
                  <div className="mb-3 flex items-center justify-between">
                    <h3 className="text-sm font-semibold text-foreground">
                      Server-verified registry facts
                    </h3>
                    {ss && (
                      <span className={`text-xs font-medium ${SERVER_STATUS[ss]?.cls || "text-muted"}`}>
                        {SERVER_STATUS[ss]?.label || ss}
                      </span>
                    )}
                  </div>
                  {sv ? (
                    <>
                      <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs">
                        <Fact label="Registry" value={sv.registry} />
                        <Fact label="Package exists" bool={sv.package_exists} />
                        <Fact label="Version exists" bool={sv.version_exists} />
                        <Fact label="Resolved version" value={sv.resolved_version} mono />
                        <Fact
                          label="Repository consistency"
                          value={sv.repo_consistency}
                          cls={
                            sv.repo_consistency === "match"
                              ? "text-green-400"
                              : sv.repo_consistency === "mismatch"
                                ? "text-red-400"
                                : "text-amber-400"
                          }
                        />
                        <Fact label="Command pinning" value={sv.command_pinning} />
                        <Fact
                          label="Command rewrite"
                          value={sv.command_rewrite}
                          cls={sv.command_rewrite === "pinned" ? "text-green-400" : "text-muted"}
                        />
                        <Fact
                          label="Integrity (provenance)"
                          value={sv.shasum ? sv.shasum.slice(0, 16) + "…" : null}
                          mono
                        />
                      </div>
                      <div className="mt-3 space-y-1 text-xs">
                        <div className="text-muted">
                          Declared source:{" "}
                          <span className="break-all font-mono text-foreground">{sv.declared_source_repo || "--"}</span>
                        </div>
                        <div className="text-muted">
                          Registry repo:{" "}
                          <span className="break-all font-mono text-foreground">{sv.registry_repo_url || "--"}</span>
                        </div>
                      </div>
                      {sv.warnings?.length ? (
                        <ul className="mt-2 list-inside list-disc text-xs text-amber-300/80">
                          {sv.warnings.map((w, i) => (
                            <li key={i}>{w}</li>
                          ))}
                        </ul>
                      ) : null}
                    </>
                  ) : (
                    <p className="text-xs text-muted">
                      No server verification on record — run re-verify.
                    </p>
                  )}
                </div>

                {/* Panel 2: Maintainer-attested runtime checks (advisory) */}
                <div className="rounded-lg border border-border bg-background/40 p-4">
                  <h3 className="mb-1 text-sm font-semibold text-foreground">
                    Maintainer-attested runtime checks
                  </h3>
                  <p className="mb-3 text-xs text-muted">
                    Self-reported by the submitter via{" "}
                    <span className="font-mono">agentnode mcp verify</span>. Not independently
                    verified by the server.
                  </p>
                  <div className="space-y-1">
                    {checks.map((c, i) => (
                      <div key={i} className="flex items-center gap-2 text-xs">
                        <span className="text-muted">claimed</span>
                        <span className="text-foreground">
                          {c.name === "owner_verified"
                            ? "source matches registry metadata"
                            : c.name}
                        </span>
                        {c.detail && <span className="text-muted">-- {c.detail}</span>}
                      </div>
                    ))}
                    {checks.length === 0 && (
                      <p className="text-xs text-muted">No checks reported.</p>
                    )}
                  </div>
                  {declared.network && (
                    <div className="mt-3">
                      <p className="mb-1 text-xs text-muted">Declared permission profile</p>
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
                </div>

                {/* Actions (maintainer-reported) */}
                {actions.length > 0 && (
                  <div>
                    <h3 className="mb-2 text-sm font-semibold text-foreground">
                      Maintainer actions ({actions.length})
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
                          {a.fix && <div className="mt-1 text-primary/80">Fix: {a.fix}</div>}
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
            <div>
              <label className="text-xs text-muted mb-1 block">Message to maintainer (visible to submitter)</label>
              <textarea
                value={maintainerFeedback}
                onChange={(e) => setMaintainerFeedback(e.target.value)}
                placeholder="e.g. Please fix PyPI project URLs and resubmit"
                rows={2}
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted/50 focus:border-primary/30 focus:outline-none"
              />
            </div>
            <div>
              <label className="text-xs text-muted mb-1 block">Internal notes (admin-only, not visible to maintainer)</label>
              <textarea
                value={reviewNotes}
                onChange={(e) => setReviewNotes(e.target.value)}
                placeholder="Internal review notes..."
                rows={2}
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted/50 focus:border-primary/30 focus:outline-none"
              />
            </div>
            <button
              onClick={submitReview}
              disabled={loading || !reviewStatus || reviewStatus === selected.status}
              className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {loading ? "Saving..." : "Save Review"}
            </button>
          </div>

          {/* Publish to Catalog */}
          {selected.status === "approved" && !selected.published_package_id && (
            <div className="border-t border-border pt-4 space-y-2">
              <p className="text-xs">
                Package ownership:{" "}
                <span
                  className={
                    selected.ownership.status === "verified"
                      ? "text-green-400"
                      : selected.ownership.status === "expired"
                        ? "text-amber-400"
                        : "text-red-400"
                  }
                >
                  {selected.ownership.status === "verified"
                    ? selected.ownership.method === "manual_admin"
                      ? "manually verified by admin"
                      : "registry challenge verified"
                    : selected.ownership.status}
                </span>
                <span className="ml-2 text-muted">(independent of repository consistency)</span>
              </p>

              {selected.server_verification?.server_status === "mismatch" ? (
                <p className="text-xs text-red-400">
                  Publishing blocked: the server-verification mismatch must be resolved first.
                </p>
              ) : selected.ownership.status !== "verified" ? (
                <div>
                  <button
                    onClick={verifyOwnership}
                    disabled={loading}
                    className="rounded-md border border-amber-500/30 bg-amber-500/10 px-4 py-2 text-sm font-medium text-amber-300 hover:bg-amber-500/20 disabled:opacity-50 transition-colors"
                  >
                    {loading ? "Saving..." : "Mark ownership manually verified"}
                  </button>
                  <p className="mt-1 text-xs text-muted">
                    Package control is not proven. Publishing requires a verified ownership
                    claim — confirm it manually (audited) or wait for a registry challenge.
                    Repository consistency does not count as ownership.
                  </p>
                </div>
              ) : (
                <div className="space-y-2">
                  <div className="flex gap-2">
                    <button
                      onClick={publishToCatalog}
                      disabled={loading}
                      className="rounded-md bg-green-600 px-4 py-2 text-sm font-medium text-white hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                    >
                      {loading ? "Publishing..." : "Publish to Catalog"}
                    </button>
                    <button
                      onClick={revokeOwnership}
                      disabled={loading}
                      className="rounded-md border border-red-500/30 bg-red-500/10 px-4 py-2 text-sm font-medium text-red-300 hover:bg-red-500/20 disabled:opacity-50 transition-colors"
                    >
                      Revoke ownership
                    </button>
                  </div>
                  <p className="text-xs text-muted">
                    Publishes to the registry. The publish gate uses server verification and a
                    verified ownership claim, not the maintainer-submitted report.
                  </p>
                </div>
              )}
            </div>
          )}
          {selected.published_package_id && (
            <div className="border-t border-border pt-4">
              <span className="text-sm text-green-400">Published</span>
              <a
                href={`/packages/${selected.package_name}`}
                className="ml-2 text-xs text-primary hover:underline"
              >
                View package
              </a>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
