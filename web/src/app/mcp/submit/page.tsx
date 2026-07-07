"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { fetchWithAuth } from "@/lib/api";
import { parseManifestInput } from "@/lib/import-utils";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface UserInfo {
  username: string;
  publisher?: { slug: string; display_name: string };
}

interface SubmissionSummary {
  id: string;
  package_name: string;
  package_registry: string;
  package_version: string | null;
  status: string;
  report_status: string | null;
  server_status: string | null;
  actions_high: number;
  actions_medium: number;
  maintainer_feedback: string | null;
  published_package_id: string | null;
  created_at: string;
}

interface SubmitResult {
  id: string;
  status: string;
  package_name: string;
  message: string;
}

/* ------------------------------------------------------------------ */
/*  Status pill                                                        */
/* ------------------------------------------------------------------ */

const STATUS_STYLES: Record<string, string> = {
  pending: "border-yellow-500/40 bg-yellow-500/10 text-yellow-400",
  action_required: "border-orange-500/40 bg-orange-500/10 text-orange-400",
  needs_changes: "border-orange-500/40 bg-orange-500/10 text-orange-400",
  approved: "border-green-500/40 bg-green-500/10 text-green-400",
  rejected: "border-red-500/40 bg-red-500/10 text-red-400",
};

function StatusPill({ status }: { status: string }) {
  return (
    <span
      className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${
        STATUS_STYLES[status] || "border-border bg-card text-muted"
      }`}
    >
      {status.replace(/_/g, " ")}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/*  Page                                                               */
/* ------------------------------------------------------------------ */

export default function McpSubmitPage() {
  const [user, setUser] = useState<UserInfo | null>(null);
  const [authChecked, setAuthChecked] = useState(false);

  const [manifestText, setManifestText] = useState("");
  const [reportText, setReportText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [submitResult, setSubmitResult] = useState<SubmitResult | null>(null);

  const [submissions, setSubmissions] = useState<SubmissionSummary[]>([]);
  const [listLoaded, setListLoaded] = useState(false);

  const isPublisher = !!user?.publisher;

  /* ---- Auth + own submissions ---- */
  const loadSubmissions = useCallback(async () => {
    try {
      const res = await fetchWithAuth("/mcp/submissions?per_page=50");
      if (res.ok) {
        const data = await res.json();
        setSubmissions(data.submissions || []);
      }
    } catch {
      /* list stays empty */
    } finally {
      setListLoaded(true);
    }
  }, []);

  useEffect(() => {
    fetchWithAuth("/auth/me")
      .then(async (res) => {
        if (res.ok) {
          const me = await res.json();
          setUser(me);
          if (me?.publisher) await loadSubmissions();
        }
      })
      .catch(() => {})
      .finally(() => setAuthChecked(true));
  }, [loadSubmissions]);

  /* ---- Submit ---- */
  async function handleSubmit() {
    setError("");
    setSubmitResult(null);

    const parsed = parseManifestInput(manifestText);
    if (!parsed.ok) {
      setError(parsed.error);
      return;
    }
    const manifest = parsed.manifest;
    if (manifest.runtime !== "mcp") {
      setError('This manifest has no runtime: "mcp" — MCP submissions require it.');
      return;
    }
    if (!manifest.mcp_server || typeof manifest.mcp_server !== "object") {
      setError("This manifest has no mcp_server block.");
      return;
    }

    // Advisory client report. The web form runs no local verification —
    // the server re-verifies authoritatively either way. Pasting the JSON
    // output of `agentnode mcp verify --json` upgrades the attestation.
    let report: Record<string, unknown> = {
      status: "REVIEW_NEEDED",
      summary: "Submitted via web form — no client-side verification was run.",
      checks: [],
      actions: [],
      warnings: [],
      errors: [],
    };
    if (reportText.trim()) {
      try {
        const parsedReport = JSON.parse(reportText);
        if (!parsedReport || typeof parsedReport !== "object" || !parsedReport.status) {
          setError("The verification report must be the JSON output of `agentnode mcp verify --json`.");
          return;
        }
        report = parsedReport;
      } catch {
        setError("Could not parse the verification report as JSON.");
        return;
      }
    }

    setSubmitting(true);
    try {
      const res = await fetchWithAuth("/mcp/submit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ manifest, verification_report: report }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.status === 201) {
        setSubmitResult(data);
        setManifestText("");
        setReportText("");
        await loadSubmissions();
      } else if (res.status === 401) {
        setError("Sign in to submit an MCP server.");
      } else if (res.status === 403) {
        setError("Submitting requires a publisher account. Create one in your dashboard first.");
      } else {
        setError(data?.error?.message || data?.detail || "Submission failed. Please try again.");
      }
    } catch {
      setError("Network error. Please check your connection.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="min-h-screen bg-background">
      {/* Hero */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-3xl px-4 sm:px-6 pt-16 pb-8 text-center">
          <h1 className="text-3xl font-bold tracking-tight text-foreground sm:text-4xl">
            Submit your <span className="text-primary">MCP server</span>
          </h1>
          <p className="mx-auto mt-4 max-w-2xl text-muted">
            List your MCP server in the AgentNode catalog. Every submission is
            reviewed before it goes live: the server independently re-verifies
            your package against the npm/PyPI registry, and an ownership check
            plus a human review happen before publication. Nothing is published
            automatically.
          </p>
        </div>
      </section>

      <section className="border-b border-border">
        <div className="mx-auto max-w-3xl px-4 sm:px-6 py-10">
          {/* Auth states */}
          {authChecked && !user && (
            <div className="mb-6 rounded-lg border border-yellow-500/30 bg-yellow-500/5 px-4 py-3 text-sm text-yellow-300">
              Submitting requires a publisher account.{" "}
              <Link href="/auth/login?returnTo=%2Fmcp%2Fsubmit" className="underline font-medium hover:text-yellow-200">
                Sign in
              </Link>{" "}
              or{" "}
              <Link href="/auth/register?returnTo=%2Fmcp%2Fsubmit" className="underline font-medium hover:text-yellow-200">
                create an account
              </Link>{" "}
              to continue.
            </div>
          )}
          {authChecked && user && !isPublisher && (
            <div className="mb-6 rounded-lg border border-yellow-500/30 bg-yellow-500/5 px-4 py-3 text-sm text-yellow-300">
              You are signed in, but submitting needs a publisher profile.{" "}
              <Link href="/dashboard" className="underline font-medium hover:text-yellow-200">
                Create one in your dashboard
              </Link>
              .
            </div>
          )}

          {/* Manifest input */}
          <label className="mb-2 block text-sm font-semibold text-foreground">
            agentnode.yaml manifest
          </label>
          <textarea
            value={manifestText}
            onChange={(e) => { setManifestText(e.target.value); setError(""); }}
            placeholder={'Paste your agentnode.yaml here (runtime: "mcp" with an mcp_server block)...'}
            rows={12}
            className="w-full rounded-xl border border-border bg-card p-4 font-mono text-sm text-foreground placeholder:text-muted/40 focus:border-primary/50 focus:outline-none focus:ring-1 focus:ring-primary/20"
            spellCheck={false}
          />

          {/* Optional verification report */}
          <label className="mt-6 mb-2 block text-sm font-semibold text-foreground">
            Verification report{" "}
            <span className="font-normal text-muted">(optional, recommended)</span>
          </label>
          <p className="mb-2 text-xs text-muted">
            Paste the JSON output of{" "}
            <code className="rounded bg-card px-1.5 py-0.5 font-mono text-foreground">
              agentnode mcp verify . --test --json
            </code>
            . Without it, your submission is filed as an unverified draft
            (REVIEW_NEEDED) — the review team will usually ask for a CLI-verified
            (TESTED) report before an approved server can be published.
          </p>
          <textarea
            value={reportText}
            onChange={(e) => { setReportText(e.target.value); setError(""); }}
            placeholder="Optional: paste the verify --json report here..."
            rows={5}
            className="w-full rounded-xl border border-border bg-card p-4 font-mono text-sm text-foreground placeholder:text-muted/40 focus:border-primary/50 focus:outline-none focus:ring-1 focus:ring-primary/20"
            spellCheck={false}
          />

          {/* Error / result */}
          {error && (
            <div className="mt-4 rounded-lg border border-red-500/30 bg-red-500/5 px-4 py-3 text-sm text-red-400">
              {error}
              {error.includes("Sign in") && (
                <span className="ml-2">
                  <Link href="/auth/login?returnTo=%2Fmcp%2Fsubmit" className="underline font-medium">
                    Sign in
                  </Link>
                </span>
              )}
            </div>
          )}
          {submitResult && (
            <div className="mt-4 rounded-lg border border-green-500/30 bg-green-500/5 px-4 py-3 text-sm text-green-400">
              <p className="font-semibold">
                Submitted: {submitResult.package_name} — status{" "}
                {submitResult.status.replace(/_/g, " ")}
              </p>
              <p className="mt-1 text-green-300/80">{submitResult.message}</p>
              <p className="mt-1 font-mono text-xs text-green-300/60">
                Submission ID: {submitResult.id}
              </p>
            </div>
          )}

          <div className="mt-5 flex items-center gap-4">
            <button
              onClick={handleSubmit}
              disabled={submitting || !manifestText.trim() || !isPublisher}
              className="rounded-xl bg-primary px-8 py-3 text-sm font-semibold text-white transition-all hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {submitting ? "Submitting..." : "Submit for review"}
            </button>
            <span className="text-xs text-muted">
              Requires a publisher account &middot; reviewed before listing
            </span>
          </div>

          <p className="mt-4 text-xs text-muted">
            Prefer the CLI? <code className="rounded bg-card px-1.5 py-0.5 font-mono">agentnode mcp verify .</code>{" "}
            &rarr; <code className="rounded bg-card px-1.5 py-0.5 font-mono">agentnode mcp submit .</code>{" "}
            &rarr; <code className="rounded bg-card px-1.5 py-0.5 font-mono">agentnode mcp status &lt;id&gt;</code>{" "}
            — see the <Link href="/docs/cli" className="text-primary hover:underline">CLI reference</Link>.
          </p>
        </div>
      </section>

      {/* My submissions */}
      {isPublisher && (
        <section className="border-b border-border">
          <div className="mx-auto max-w-3xl px-4 sm:px-6 py-10">
            <h2 className="mb-4 text-xl font-bold text-foreground">
              My MCP submissions
            </h2>
            {!listLoaded ? (
              <p className="text-sm text-muted">Loading…</p>
            ) : submissions.length === 0 ? (
              <p className="text-sm text-muted">
                No submissions yet. Submit your first MCP server above.
              </p>
            ) : (
              <div className="space-y-3">
                {submissions.map((s) => (
                  <div
                    key={s.id}
                    className="rounded-xl border border-border bg-card px-4 py-3"
                  >
                    <div className="flex flex-wrap items-center gap-3">
                      <span className="font-mono text-sm font-semibold text-foreground">
                        {s.package_name}
                        {s.package_version ? `@${s.package_version}` : ""}
                      </span>
                      <span className="text-xs text-muted">{s.package_registry}</span>
                      <StatusPill status={s.status} />
                      {s.published_package_id && (
                        <span className="rounded-full border border-green-500/40 bg-green-500/10 px-2.5 py-0.5 text-xs font-medium text-green-400">
                          published
                        </span>
                      )}
                      <span className="ml-auto text-xs text-muted">
                        {s.created_at ? new Date(s.created_at).toLocaleDateString() : ""}
                      </span>
                    </div>
                    {(s.actions_high > 0 || s.actions_medium > 0) && (
                      <p className="mt-2 text-xs text-yellow-400">
                        Open actions from verification: {s.actions_high} high,{" "}
                        {s.actions_medium} medium — check{" "}
                        <code className="rounded bg-background px-1 py-0.5 font-mono">
                          agentnode mcp status {s.id}
                        </code>
                      </p>
                    )}
                    {s.maintainer_feedback && (
                      <p className="mt-2 rounded-lg border border-border bg-background px-3 py-2 text-xs text-foreground/80">
                        <span className="font-semibold text-foreground">Review feedback:</span>{" "}
                        {s.maintainer_feedback}
                      </p>
                    )}
                    <p className="mt-2 font-mono text-[11px] text-muted/60">
                      ID: {s.id}
                    </p>
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>
      )}

      {/* How review works */}
      <section>
        <div className="mx-auto max-w-3xl px-4 sm:px-6 py-12">
          <h2 className="mb-4 text-lg font-bold text-foreground">How review works</h2>
          <ol className="list-decimal space-y-2 pl-5 text-sm text-muted">
            <li>
              You submit the manifest. The server independently re-verifies the
              package against the npm/PyPI registry (existence, version pinning,
              repository consistency) — your report is advisory, the server
              result is authoritative.
            </li>
            <li>
              The submission lands as <em>pending</em> or <em>action required</em>.
              A reviewer checks it; package ownership must be verified before
              anything can be published.
            </li>
            <li>
              You track the status here or via{" "}
              <code className="rounded bg-card px-1 py-0.5 font-mono">agentnode mcp status &lt;id&gt;</code>.
              Feedback from the review team appears on your submission.
            </li>
            <li>Only an approved, ownership-verified submission is published to the catalog.</li>
          </ol>
        </div>
      </section>
    </main>
  );
}
