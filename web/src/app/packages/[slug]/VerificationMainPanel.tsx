"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { fetchWithAuth } from "@/lib/api";

/* eslint-disable @typescript-eslint/no-explicit-any */

interface VerificationStep {
  name: string;
  status: string;
  duration_ms?: number | null;
}

interface BreakdownItem {
  points: number;
  max: number;
  reason: string;
}

interface IsolationInfo {
  overall?: string | null;
  install?: string | null;
  import?: string | null;
  smoke?: string | null;
  tests?: string | null;
}

interface EnvironmentInfo {
  python_version?: string | null;
  system_capabilities?: Record<string, boolean>;
  sandbox_mode?: string | null;
  installer?: string | null;
  isolation?: IsolationInfo | null;
}

interface VerificationInfo {
  status: string;
  last_verified_at?: string | null;
  runner_version?: string | null;
  steps: VerificationStep[];
  score?: number | null;
  tier?: string | null;
  confidence?: string | null;
  score_breakdown?: {
    score?: number;
    tier?: string;
    confidence?: string;
    breakdown?: Record<string, BreakdownItem>;
    explanation?: string;
  } | Record<string, number> | null;
  smoke_reason?: string | null;
  verification_mode?: string | null;
  environment?: EnvironmentInfo | null;
}

function StepCard({ step }: { step: VerificationStep }) {
  const isPassed = step.status === "passed";
  const isFailed = step.status === "failed" || step.status === "error";

  const icon = isPassed ? "\u2714" : isFailed ? "\u2716" : step.status === "not_present" ? "\u2014" : "\u25CB";
  const color = isPassed
    ? "text-green-400 border-green-500/20 bg-green-500/5"
    : isFailed
      ? "text-red-400 border-red-500/20 bg-red-500/5"
      : "text-zinc-500 border-border bg-background";

  const duration = step.duration_ms != null
    ? step.duration_ms < 1000
      ? `${step.duration_ms}ms`
      : `${(step.duration_ms / 1000).toFixed(1)}s`
    : null;

  return (
    <div className={`flex flex-col items-center gap-1 rounded-lg border px-3 py-3 ${color}`}>
      <span className="text-lg">{icon}</span>
      <span className="text-xs font-medium capitalize">
        {step.name}
      </span>
      {duration && (
        <span className="text-[10px] font-mono opacity-70">
          {duration}
        </span>
      )}
    </div>
  );
}

function ConfidenceBadge({ confidence }: { confidence: string }) {
  const colors: Record<string, string> = {
    high: "text-green-400 bg-green-500/10 border-green-500/20",
    medium: "text-yellow-400 bg-yellow-500/10 border-yellow-500/20",
    low: "text-zinc-400 bg-zinc-500/10 border-zinc-500/20",
  };
  return (
    <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded border ${colors[confidence] || colors.low}`}>
      {confidence} confidence
    </span>
  );
}

import { timeAgoShort } from "@/lib/time";

function isNewFormat(breakdown: any): breakdown is { breakdown: Record<string, BreakdownItem>; explanation?: string; confidence?: string } {
  return breakdown && typeof breakdown === "object" && "breakdown" in breakdown;
}

export default function VerificationMainPanel({
  slug,
  verification,
  publisherSlug,
}: {
  slug: string;
  verification: VerificationInfo | null;
  publisherSlug?: string;
}) {
  const router = useRouter();
  const [isOwner, setIsOwner] = useState(false);
  const [reverifyLoading, setReverifyLoading] = useState(false);
  const [reverifyMessage, setReverifyMessage] = useState("");
  const [reverifyError, setReverifyError] = useState("");

  useEffect(() => {
    if (!publisherSlug) return;
    let cancelled = false;
    async function checkOwner() {
      try {
        const res = await fetchWithAuth("/auth/me");
        if (!res.ok) return;
        const user = await res.json();
        if (!cancelled && user.publisher?.slug === publisherSlug) {
          setIsOwner(true);
        }
      } catch {
        // Not logged in
      }
    }
    checkOwner();
    return () => { cancelled = true; };
  }, [publisherSlug]);

  async function handleReverify() {
    setReverifyLoading(true);
    setReverifyError("");
    setReverifyMessage("");
    try {
      const res = await fetchWithAuth(`/packages/${encodeURIComponent(slug)}/request-reverify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        setReverifyMessage(data.version ? `Verification requested for v${data.version}` : "Verification requested");
        router.refresh();
      } else {
        setReverifyError(data.error?.message || data.detail || "Failed to request verification");
      }
    } finally {
      setReverifyLoading(false);
    }
  }

  if (!verification) return null;

  const status = verification.status;
  const tier = verification.tier;
  const score = verification.score;
  const confidence = verification.confidence || (verification.score_breakdown && isNewFormat(verification.score_breakdown) ? verification.score_breakdown.confidence : null);
  const isVerified = status === "verified" || status === "passed";
  const isFailed = status === "failed" || status === "error";
  const isPending = status === "pending" || status === "running";

  if (isPending) {
    return (
      <section className="rounded-xl border border-yellow-500/20 bg-yellow-500/5 p-4 sm:p-6">
        <div className="flex items-center gap-3">
          <span className="inline-block h-2.5 w-2.5 rounded-full bg-yellow-400 animate-pulse" />
          <h2 className="text-lg font-semibold text-foreground">Verification in progress...</h2>
        </div>
        <p className="mt-2 text-sm text-muted">
          This package is being verified. Install, import, and runtime checks are running.
        </p>
      </section>
    );
  }

  const borderColor = isVerified
    ? "border-green-500/20"
    : isFailed
      ? "border-red-500/20"
      : "border-border";

  // Extract breakdown data — support both old (flat) and new (nested) formats
  const breakdownData = verification.score_breakdown;
  const hasNewBreakdown = breakdownData && isNewFormat(breakdownData);
  const explanation = hasNewBreakdown ? breakdownData.explanation : null;
  const breakdownItems = hasNewBreakdown
    ? breakdownData.breakdown
    : null;

  // Old format: {install: 15, import: 15, smoke: 25, ...}
  const flatBreakdown = !hasNewBreakdown && breakdownData
    ? (breakdownData as Record<string, number>)
    : null;

  return (
    <section className={`rounded-xl border ${borderColor} bg-card p-4 sm:p-6`}>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-foreground">Verification</h2>
        <div className="flex items-center gap-2">
          {confidence && <ConfidenceBadge confidence={confidence} />}
          {score != null && (
            <span className="text-xs font-mono text-muted">{score}/100</span>
          )}
          <span
            className={`text-sm font-medium ${
              tier === "gold" ? "text-yellow-400"
                : tier === "verified" ? "text-green-400"
                : tier === "partial" ? "text-yellow-400"
                : isFailed ? "text-red-400"
                : tier === "unverified" ? "text-zinc-400"
                : isVerified ? "text-green-400"
                : "text-muted"
            }`}
          >
            {tier === "gold" ? "\u2605 Gold Verified"
              : tier === "verified" ? "\u2714 Verified"
              : tier === "partial" ? "\u25CB Partially Verified"
              : isFailed ? "\u2716 Failed"
              : tier === "unverified" ? "Unverified"
              : isVerified ? "\u2714 Verified"
              : status}
          </span>
        </div>
      </div>

      {/* Score breakdown — new format with reasons */}
      {breakdownItems && score != null && (
        <div className="mb-4 rounded-lg bg-background border border-border p-3">
          <div className="flex items-center gap-2 mb-2">
            <div className="flex-1 h-2 rounded-full bg-zinc-800 overflow-hidden">
              <div
                className={`h-full rounded-full transition-all ${
                  score >= 90 ? "bg-yellow-400" : score >= 70 ? "bg-green-400" : score >= 50 ? "bg-yellow-400" : "bg-zinc-500"
                }`}
                style={{ width: `${score}%` }}
              />
            </div>
          </div>
          <div className="space-y-1">
            {Object.entries(breakdownItems).map(([key, item]) => (
              <div key={key} className="flex items-center justify-between text-[11px]">
                <div className="flex items-center gap-2">
                  <span className="text-zinc-500 capitalize w-20">{key}</span>
                  <span className="text-zinc-600 truncate max-w-[200px]">{item.reason}</span>
                </div>
                <span className={`font-mono ${item.points > 0 ? "text-green-400" : item.points < 0 ? "text-red-400" : "text-zinc-600"}`}>
                  {item.points > 0 ? `+${item.points}` : item.points}/{item.max}
                </span>
              </div>
            ))}
          </div>
          {explanation && (
            <p className="mt-2 text-[11px] text-zinc-500 border-t border-border pt-2">
              {explanation}
            </p>
          )}
        </div>
      )}

      {/* Fallback: old-format score breakdown */}
      {flatBreakdown && !breakdownItems && score != null && (
        <div className="mb-4 rounded-lg bg-background border border-border p-3">
          <div className="flex items-center gap-2 mb-2">
            <div className="flex-1 h-2 rounded-full bg-zinc-800 overflow-hidden">
              <div
                className={`h-full rounded-full transition-all ${
                  score >= 90 ? "bg-yellow-400" : score >= 70 ? "bg-green-400" : score >= 50 ? "bg-yellow-400" : "bg-zinc-500"
                }`}
                style={{ width: `${score}%` }}
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px]">
            {Object.entries(flatBreakdown).map(([key, val]) => (
              <div key={key} className="flex justify-between">
                <span className="text-zinc-500 capitalize">{key}</span>
                <span className={`font-mono ${val > 0 ? "text-green-400" : val < 0 ? "text-red-400" : "text-zinc-600"}`}>
                  {val > 0 ? `+${val}` : val}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Step cards */}
      {verification.steps.length > 0 && (
        <div className="grid grid-cols-4 gap-2 mb-4">
          {verification.steps.map((step) => (
            <StepCard key={step.name} step={step} />
          ))}
        </div>
      )}

      {/* Context note */}
      {tier === "partial" && verification.smoke_reason && (
        <p className="text-xs text-yellow-400/70 mb-2">
          {(verification.smoke_reason === "credential_boundary_reached" || verification.smoke_reason === "needs_credentials") && "This package requires API credentials for full verification. Install and import checks passed."}
          {verification.smoke_reason === "missing_system_dependency" && "This package requires system dependencies (e.g., Chromium, ffmpeg) not available in the sandbox."}
          {verification.smoke_reason === "needs_binary_input" && "This package requires binary file inputs (e.g., PDF, images) for full verification."}
          {verification.smoke_reason === "external_network_blocked" && "This package requires network access that is blocked during verification."}
          {!["credential_boundary_reached", "needs_credentials", "missing_system_dependency", "needs_binary_input", "external_network_blocked"].includes(verification.smoke_reason) && "Partial verification — some checks could not be completed in the sandbox environment."}
        </p>
      )}
      {isVerified && (tier === "gold" || tier === "verified") && (
        <p className="text-xs text-muted">
          This package was executed and validated by AgentNode before listing.
          Install, import, and runtime checks passed.
        </p>
      )}
      {tier === "unverified" && !isFailed && (
        <p className="text-xs text-zinc-500">
          This package scored below the verification threshold. Some checks may have failed or produced inconclusive results.
        </p>
      )}
      {isFailed && (
        <p className="text-xs text-red-400/70">
          Verification failed. This package may have issues with installation or runtime behavior.
        </p>
      )}

      {/* Verification mode badge */}
      {verification.verification_mode && verification.verification_mode !== "real" && (
        <p className="text-[10px] text-zinc-500 mt-1">
          Verified in {verification.verification_mode} mode
          {verification.verification_mode === "limited" && " (sandbox limitations active)"}
          {verification.verification_mode === "mock" && " (mock APIs used)"}
        </p>
      )}

      {/* Environment info */}
      {verification.environment && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {verification.environment.python_version && (
            <span className="text-[10px] text-zinc-600 bg-zinc-800/50 px-1.5 py-0.5 rounded">
              Python {verification.environment.python_version.split(" ")[0]}
            </span>
          )}
          {verification.environment.system_capabilities && Object.entries(verification.environment.system_capabilities)
            .filter(([, available]) => available)
            .map(([name]) => (
              <span key={name} className="text-[10px] text-zinc-600 bg-zinc-800/50 px-1.5 py-0.5 rounded">
                {name}
              </span>
            ))
          }
          {verification.environment.installer === "uv" && (
            <span className="text-[10px] text-purple-500/70 bg-purple-500/10 px-1.5 py-0.5 rounded">
              uv
            </span>
          )}
          {verification.environment.isolation?.overall && (
            <span className={`text-[10px] px-1.5 py-0.5 rounded ${
              verification.environment.isolation.overall === "enforced"
                ? "text-green-500/70 bg-green-500/10"
                : verification.environment.isolation.overall === "best_effort"
                  ? "text-yellow-500/70 bg-yellow-500/10"
                  : "text-zinc-600 bg-zinc-800/50"
            }`}>
              Network: {verification.environment.isolation.overall === "enforced"
                ? "isolated"
                : verification.environment.isolation.overall === "best_effort"
                  ? "best effort"
                  : "not isolated"}
            </span>
          )}
        </div>
      )}

      {/* Last verified */}
      {verification.last_verified_at && (
        <p className="mt-2 text-[11px] text-zinc-500">
          Last verified {timeAgoShort(verification.last_verified_at)}
          {verification.runner_version && (
            <span className="ml-2">
              &middot; Runner v{verification.runner_version}
            </span>
          )}
        </p>
      )}

      {/* Owner re-verify button */}
      {isOwner && !isPending && (
        <div className="mt-3 border-t border-border pt-3">
          {reverifyError && (
            <p className="mb-2 text-xs text-red-400">{reverifyError}</p>
          )}
          {reverifyMessage && (
            <p className="mb-2 text-xs text-green-400">{reverifyMessage}</p>
          )}
          <button
            onClick={handleReverify}
            disabled={reverifyLoading}
            className="rounded border border-border px-3 py-1.5 text-xs font-medium text-foreground hover:bg-background transition-colors disabled:opacity-50"
          >
            {reverifyLoading ? "Requesting..." : "Request Re-Verification"}
          </button>
        </div>
      )}
    </section>
  );
}
