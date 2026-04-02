"use client";

import { useEffect, useState, useCallback } from "react";
import { fetchWithAuth } from "@/lib/api";

interface VerificationData {
  status: string;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  install_status: string | null;
  import_status: string | null;
  smoke_status: string | null;
  tests_status: string | null;
  error_summary: string | null;
  warnings_count: number;
  warnings_summary: string | null;
  runner_version: string | null;
  python_version: string | null;
  runner_platform: string | null;
  triggered_by: string | null;
  verification_run_count: number | null;
  last_verified_at: string | null;
  // Logs — only present for owner
  install_log: string | null;
  import_log: string | null;
  smoke_log: string | null;
  tests_log: string | null;
}

// ── Step status display ──────────────────────────────────────────────

function StepIcon({ status }: { status: string | null }) {
  if (!status) return <span className="text-zinc-600">&mdash;</span>;
  switch (status) {
    case "passed":
      return <span className="text-green-400">&#10004;</span>;
    case "failed":
      return <span className="text-red-400">&#10006;</span>;
    case "inconclusive":
      return <span className="text-yellow-400">&#9888;</span>;
    case "error":
      return <span className="text-red-400">!</span>;
    case "not_present":
      return <span className="text-zinc-500">&mdash;</span>;
    case "skipped":
      return <span className="text-zinc-500">&mdash;</span>;
    default:
      return <span className="text-zinc-600">?</span>;
  }
}

function stepLabel(status: string | null): string {
  if (!status) return "N/A";
  switch (status) {
    case "passed":
      return "Passed";
    case "failed":
      return "Failed";
    case "inconclusive":
      return "Inconclusive";
    case "error":
      return "Error";
    case "not_present":
      return "No tests found";
    case "skipped":
      return "Skipped";
    default:
      return status;
  }
}

function stepColor(status: string | null): string {
  if (!status) return "text-zinc-500";
  switch (status) {
    case "passed":
      return "text-green-400";
    case "failed":
    case "error":
      return "text-red-400";
    case "inconclusive":
      return "text-yellow-400";
    default:
      return "text-zinc-500";
  }
}

// ── Time helpers ─────────────────────────────────────────────────────

import { timeAgoShort } from "@/lib/time";

// ── Log viewer ───────────────────────────────────────────────────────

function LogTab({
  label,
  log,
  active,
  onClick,
}: {
  label: string;
  log: string | null;
  active: boolean;
  onClick: () => void;
}) {
  const hasContent = log && log.trim().length > 0;
  return (
    <button
      onClick={onClick}
      className={`px-3 py-1.5 text-xs font-medium rounded-t-md border-b-2 transition-colors ${
        active
          ? "border-primary text-primary bg-primary/5"
          : hasContent
            ? "border-transparent text-muted hover:text-foreground"
            : "border-transparent text-zinc-600 cursor-default"
      }`}
      disabled={!hasContent}
    >
      {label}
    </button>
  );
}

// ── Main component ───────────────────────────────────────────────────

export default function VerificationPanel({ slug }: { slug: string }) {
  const [data, setData] = useState<VerificationData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [logsOpen, setLogsOpen] = useState(false);
  const [activeLog, setActiveLog] = useState<string>("install");
  const [reverifying, setReverifying] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      // Try authenticated first (owner gets logs)
      const authRes = await fetchWithAuth(`/packages/${slug}/verification`);
      if (authRes.ok) {
        setData(await authRes.json());
        setLoading(false);
        return;
      }
      // Fall back to public (no auth)
      const res = await fetch(`/api/v1/packages/${slug}/verification`);
      if (res.ok) {
        setData(await res.json());
      } else if (res.status === 404) {
        setData(null);
      } else {
        setError("Could not load verification status");
      }
    } catch {
      // No verification data available
      setData(null);
    }
    setLoading(false);
  }, [slug]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- Fetch verification data on mount
    fetchData();
  }, [fetchData]);

  // Poll while pending/running
  useEffect(() => {
    if (!data || !["pending", "running"].includes(data.status)) return;
    const interval = setInterval(fetchData, 5000);
    return () => clearInterval(interval);
  }, [data, fetchData]);

  if (loading) {
    return (
      <section className="rounded-xl border border-border bg-card p-4 sm:p-6 animate-pulse">
        <div className="h-5 w-40 bg-border rounded mb-4" />
        <div className="space-y-2">
          <div className="h-4 w-full bg-border rounded" />
          <div className="h-4 w-full bg-border rounded" />
          <div className="h-4 w-3/4 bg-border rounded" />
        </div>
      </section>
    );
  }

  if (error) {
    return (
      <section className="rounded-xl border border-border bg-card p-4 sm:p-6">
        <p className="text-sm text-muted">{error}</p>
      </section>
    );
  }

  if (!data) return null;

  const isOwner = !!(data.install_log || data.import_log || data.smoke_log || data.tests_log);
  const hasLogs = isOwner;
  const isTerminal = ["passed", "failed", "error", "skipped"].includes(data.status);
  const hasWarnings = data.warnings_count > 0;

  // Hide pending/running verification from public users — looks unfinished
  if (!isOwner && (data.status === "pending" || data.status === "running")) return null;

  const overallColor =
    data.status === "passed"
      ? "border-green-500/20"
      : data.status === "failed" || data.status === "error"
        ? "border-red-500/20"
        : data.status === "running" || data.status === "pending"
          ? "border-yellow-500/20"
          : "border-border";

  const statusIcon =
    data.status === "passed"
      ? "\u2714"
      : data.status === "failed" || data.status === "error"
        ? "\u2716"
        : data.status === "running"
          ? "\u25F7"
          : "\u25CB";

  const statusColor =
    data.status === "passed"
      ? "text-green-400"
      : data.status === "failed" || data.status === "error"
        ? "text-red-400"
        : data.status === "running"
          ? "text-yellow-400"
          : "text-zinc-400";

  const statusLabel =
    data.status === "passed"
      ? "Verified"
      : data.status === "failed"
        ? "Verification Failed"
        : data.status === "error"
          ? "Verification Error"
          : data.status === "running"
            ? "Verifying\u2026"
            : data.status === "pending"
              ? "Verification Pending"
              : "Skipped";

  const steps = [
    { key: "install", label: "Install", status: data.install_status },
    { key: "import", label: "Import", status: data.import_status },
    { key: "smoke", label: "Smoke", status: data.smoke_status },
    { key: "tests", label: "Tests", status: data.tests_status },
  ];

  const logMap: Record<string, string | null> = {
    install: data.install_log,
    import: data.import_log,
    smoke: data.smoke_log,
    tests: data.tests_log,
  };

  async function handleReverify() {
    setReverifying(true);
    try {
      const res = await fetchWithAuth(
        `/admin/packages/${slug}/versions/latest/reverify`,
        { method: "POST" }
      );
      if (res.ok) {
        // Start polling
        setTimeout(fetchData, 2000);
      }
    } catch {
      // ignore
    }
    setReverifying(false);
  }

  return (
    <section className={`rounded-xl border ${overallColor} bg-card p-4 sm:p-6`}>
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-foreground">Verification</h2>
        <div className="flex items-center gap-2">
          <span className={`text-sm font-medium ${statusColor}`}>
            {statusIcon} {statusLabel}
          </span>
        </div>
      </div>

      {/* 4-step bar — visible to everyone */}
      {isTerminal && (
        <div className="grid grid-cols-4 gap-1 mb-4">
          {steps.map((step) => (
            <div
              key={step.key}
              className="flex flex-col items-center gap-1 rounded-lg bg-background border border-border px-2 py-2.5"
            >
              <StepIcon status={step.status} />
              <span className="text-[11px] font-medium text-muted">
                {step.label}
              </span>
              <span className={`text-[10px] ${stepColor(step.status)}`}>
                {stepLabel(step.status)}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Running / pending state */}
      {data.status === "running" && (
        <div className="flex items-center gap-2 mb-4 rounded-lg bg-yellow-500/5 border border-yellow-500/10 px-3 py-2">
          <span className="inline-block h-2 w-2 rounded-full bg-yellow-400 animate-pulse" />
          <span className="text-xs text-yellow-400">
            Verification in progress&hellip;
          </span>
        </div>
      )}

      {/* Error summary */}
      {data.error_summary && (
        <div className="mb-4 rounded-lg bg-red-500/5 border border-red-500/10 px-3 py-2">
          <p className="text-xs text-red-400">{data.error_summary}</p>
        </div>
      )}

      {/* Warnings */}
      {hasWarnings && (
        <div className="mb-4 rounded-lg bg-yellow-500/5 border border-yellow-500/10 px-3 py-2">
          <p className="text-xs text-yellow-400">
            {data.warnings_count} warning{data.warnings_count > 1 ? "s" : ""}
            {data.warnings_summary && (
              <span className="text-yellow-400/70 ml-1">
                &mdash; {data.warnings_summary}
              </span>
            )}
          </p>
        </div>
      )}

      {/* Context note */}
      {isTerminal && (
        <p className="text-[11px] text-zinc-500 mb-3">
          Only install and import failures block packages. Other results are shown for transparency.
        </p>
      )}

      {/* Last verified + run count (compact) */}
      {data.last_verified_at && (
        <div className="flex items-center gap-3 text-[11px] text-muted mb-3">
          <span>Verified {timeAgoShort(data.last_verified_at)}</span>
          {data.verification_run_count && data.verification_run_count > 0 && (
            <span className="text-zinc-600">
              &middot; {data.verification_run_count} run{data.verification_run_count > 1 ? "s" : ""}
            </span>
          )}
          {data.triggered_by && (
            <span className="text-zinc-600">
              &middot; {data.triggered_by === "publish"
                ? "on publish"
                : data.triggered_by === "admin_reverify"
                  ? "admin re-verify"
                  : data.triggered_by}
            </span>
          )}
        </div>
      )}

      {/* ── Owner/Admin section ─────────────────────────────────── */}

      {/* Details accordion */}
      {isOwner && isTerminal && (
        <div className="border-t border-border pt-3 mt-3">
          <button
            onClick={() => setDetailsOpen(!detailsOpen)}
            className="flex items-center gap-2 text-xs text-muted hover:text-foreground transition-colors w-full"
          >
            <span
              className="transition-transform"
              style={{ transform: detailsOpen ? "rotate(90deg)" : "rotate(0)" }}
            >
              &#9656;
            </span>
            Runner details
          </button>

          {detailsOpen && (
            <div className="mt-2 space-y-1.5 pl-4">
              {data.runner_version && (
                <div className="flex justify-between text-[11px]">
                  <span className="text-zinc-500">Runner</span>
                  <span className="text-foreground font-mono">v{data.runner_version}</span>
                </div>
              )}
              {data.python_version && (
                <div className="flex justify-between text-[11px]">
                  <span className="text-zinc-500">Python</span>
                  <span className="text-foreground font-mono text-right max-w-[60%] truncate">
                    {data.python_version.split(" ")[0]}
                  </span>
                </div>
              )}
              {data.runner_platform && (
                <div className="flex justify-between text-[11px]">
                  <span className="text-zinc-500">Platform</span>
                  <span className="text-foreground font-mono text-right max-w-[60%] truncate">
                    {data.runner_platform}
                  </span>
                </div>
              )}
              {data.duration_ms != null && (
                <div className="flex justify-between text-[11px]">
                  <span className="text-zinc-500">Duration</span>
                  <span className="text-foreground font-mono">
                    {(data.duration_ms / 1000).toFixed(1)}s
                  </span>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Logs accordion */}
      {hasLogs && isTerminal && (
        <div className="border-t border-border pt-3 mt-3">
          <button
            onClick={() => setLogsOpen(!logsOpen)}
            className="flex items-center gap-2 text-xs text-muted hover:text-foreground transition-colors w-full"
          >
            <span
              className="transition-transform"
              style={{ transform: logsOpen ? "rotate(90deg)" : "rotate(0)" }}
            >
              &#9656;
            </span>
            Verification logs
          </button>

          {logsOpen && (
            <div className="mt-2">
              {/* Tab bar */}
              <div className="flex gap-0 border-b border-border">
                {steps.map((step) => (
                  <LogTab
                    key={step.key}
                    label={step.label}
                    log={logMap[step.key]}
                    active={activeLog === step.key}
                    onClick={() => setActiveLog(step.key)}
                  />
                ))}
              </div>

              {/* Log content */}
              <div className="mt-2 max-h-60 overflow-auto rounded-lg bg-background border border-border p-3">
                <pre className="text-[11px] font-mono text-muted whitespace-pre-wrap break-all leading-relaxed">
                  {logMap[activeLog]?.trim() || "No log output"}
                </pre>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Re-verify button (owner/admin) */}
      {isOwner && isTerminal && (
        <div className="border-t border-border pt-3 mt-3">
          <button
            onClick={handleReverify}
            disabled={reverifying}
            className="text-xs text-muted hover:text-primary transition-colors disabled:opacity-50"
          >
            {reverifying ? "Triggering\u2026" : "\u21BB Re-run verification"}
          </button>
        </div>
      )}
    </section>
  );
}
