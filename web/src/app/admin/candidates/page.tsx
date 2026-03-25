"use client";

import { useState, useEffect, useCallback } from "react";
import { fetchWithAuth } from "@/lib/api";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface FunnelData {
  discovered: number;
  contacted: number;
  engaged: number;
  signed_up: number;
  published: number;
  verified: number;
  conversion_rates: Record<string, number>;
}

interface Candidate {
  id: string;
  source: string;
  source_url: string;
  repo_owner: string | null;
  repo_name: string | null;
  display_name: string | null;
  description: string | null;
  detected_tools: Array<{ name: string; description?: string; capability_id?: string }> | null;
  detected_format: string | null;
  license_spdx: string | null;
  stars: number | null;
  contact_email: string | null;
  contact_name: string | null;
  contact_channel: string | null;
  assigned_admin_id: string | null;
  outreach_status: string;
  contacted_at: string | null;
  published_package_id: string | null;
  last_event_at: string | null;
  last_event_type: string | null;
  admin_notes: string | null;
  skip_reason: string | null;
  created_at: string;
  updated_at: string;
  invite_code: string | null;
  invite_status: string | null;
  click_count: number | null;
}

interface CandidateEvent {
  id: string;
  candidate_id: string;
  event_type: string;
  metadata: Record<string, unknown> | null;
  actor_user_id: string | null;
  created_at: string;
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

const STATUS_COLORS: Record<string, string> = {
  discovered: "bg-zinc-500/20 text-zinc-400",
  contacted: "bg-blue-500/20 text-blue-400",
  engaged: "bg-yellow-500/20 text-yellow-400",
  signed_up: "bg-purple-500/20 text-purple-400",
  published: "bg-green-500/20 text-green-400",
  declined: "bg-red-500/20 text-red-400",
  skipped: "bg-zinc-700/20 text-zinc-500",
};

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_COLORS[status] || "bg-zinc-500/20 text-zinc-400"}`}>
      {status}
    </span>
  );
}

function formatDate(iso: string | null) {
  if (!iso) return "\u2014";
  return new Date(iso).toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit" });
}

/* ------------------------------------------------------------------ */
/*  Funnel Overview                                                    */
/* ------------------------------------------------------------------ */

function FunnelOverview({ data }: { data: FunnelData }) {
  const steps = [
    { label: "Discovered", value: data.discovered },
    { label: "Contacted", value: data.contacted },
    { label: "Clicked", value: data.engaged },
    { label: "Signed Up", value: data.signed_up },
    { label: "Published", value: data.published },
    { label: "Verified", value: data.verified },
  ];

  const rates = [
    null,
    null,
    data.conversion_rates.contacted_to_clicked,
    data.conversion_rates.clicked_to_signed_up,
    data.conversion_rates.signed_up_to_published,
    data.conversion_rates.published_to_verified,
  ];

  return (
    <div className="mb-8">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {steps.map((step, i) => (
          <div key={step.label} className="rounded-lg border border-border bg-card p-4">
            <div className="text-xs font-medium text-muted">{step.label}</div>
            <div className="mt-1 text-2xl font-bold text-foreground">{step.value}</div>
            {rates[i] != null && (
              <div className="mt-1 text-xs text-primary">{rates[i]}% conv.</div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Event Timeline                                                     */
/* ------------------------------------------------------------------ */

const EVENT_ICONS: Record<string, string> = {
  candidate_discovered: "\u25CB",
  invite_created: "\u2709",
  email_sent: "\u2709",
  invite_link_clicked: "\u2192",
  invite_viewed: "\u25C9",
  account_registered: "\u2713",
  invite_claimed: "\u2713",
  package_published: "\u25A0",
  verification_passed: "\u2713",
  verification_failed: "\u2717",
  security_scan_passed: "\u2713",
  security_scan_failed: "\u2717",
  invite_revoked: "\u2717",
  note_added: "\u270E",
  declined: "\u2717",
};

function EventTimeline({ events }: { events: CandidateEvent[] }) {
  if (events.length === 0) {
    return <p className="text-sm text-muted">No events yet.</p>;
  }

  return (
    <div className="space-y-2 max-h-80 overflow-y-auto pr-2">
      {events.map((e) => (
        <div key={e.id} className="flex items-start gap-3 rounded-md border border-border bg-background px-3 py-2 text-sm">
          <span className="mt-0.5 w-4 text-center text-xs text-muted">
            {EVENT_ICONS[e.event_type] || "\u2022"}
          </span>
          <div className="min-w-0 flex-1">
            <span className="font-medium text-foreground">{e.event_type}</span>
            {e.metadata && Object.keys(e.metadata).length > 0 && (
              <span className="ml-2 text-xs text-muted">
                {Object.entries(e.metadata).map(([k, v]) => `${k}: ${v}`).join(", ")}
              </span>
            )}
          </div>
          <span className="shrink-0 text-xs text-muted">{formatDate(e.created_at)}</span>
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Detail Panel                                                       */
/* ------------------------------------------------------------------ */

function DetailPanel({
  candidate,
  events,
  onClose,
  onGenerateInvite,
  onGenerateAndSend,
  onFollowup,
  onEmailSent,
  onUpdateNotes,
}: {
  candidate: Candidate;
  events: CandidateEvent[];
  onClose: () => void;
  onGenerateInvite: () => void;
  onGenerateAndSend: () => void;
  onFollowup: () => void;
  onEmailSent: () => void;
  onUpdateNotes: (notes: string) => void;
}) {
  const [notes, setNotes] = useState(candidate.admin_notes || "");
  const [editingNotes, setEditingNotes] = useState(false);
  const [showPrefill, setShowPrefill] = useState(false);
  const [showEmailPreview, setShowEmailPreview] = useState(false);

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-end bg-black/50" onClick={onClose}>
      <div
        className="h-full w-full max-w-2xl overflow-y-auto border-l border-border bg-background p-6"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="mb-6 flex items-start justify-between">
          <div>
            <h2 className="text-xl font-bold text-foreground">
              {candidate.display_name || candidate.repo_name || "Unknown"}
            </h2>
            <a
              href={candidate.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm text-primary hover:underline"
            >
              {candidate.source_url}
            </a>
          </div>
          <button onClick={onClose} className="text-muted hover:text-foreground">&times;</button>
        </div>

        {/* Info grid */}
        <div className="mb-6 grid grid-cols-2 gap-4 text-sm">
          <div>
            <span className="text-muted">Status</span>
            <div className="mt-1"><StatusBadge status={candidate.outreach_status} /></div>
          </div>
          <div>
            <span className="text-muted">Source</span>
            <div className="mt-1 text-foreground">{candidate.source}</div>
          </div>
          <div>
            <span className="text-muted">Stars</span>
            <div className="mt-1 text-foreground">{candidate.stars ?? "\u2014"}</div>
          </div>
          <div>
            <span className="text-muted">Format</span>
            <div className="mt-1 text-foreground">{candidate.detected_format || "\u2014"}</div>
          </div>
          <div>
            <span className="text-muted">License</span>
            <div className="mt-1 text-foreground">{candidate.license_spdx || "\u2014"}</div>
          </div>
          <div>
            <span className="text-muted">Contact</span>
            <div className="mt-1 text-foreground">{candidate.contact_email || candidate.contact_name || "\u2014"}</div>
          </div>
        </div>

        {/* Description */}
        {candidate.description && (
          <div className="mb-6">
            <h3 className="text-sm font-medium text-muted">Description</h3>
            <p className="mt-1 text-sm text-foreground">{candidate.description}</p>
          </div>
        )}

        {/* Detected tools */}
        {candidate.detected_tools && candidate.detected_tools.length > 0 && (
          <div className="mb-6">
            <h3 className="text-sm font-medium text-muted">Detected Tools ({candidate.detected_tools.length})</h3>
            <div className="mt-2 flex flex-wrap gap-2">
              {candidate.detected_tools.map((t, i) => (
                <span key={i} className="rounded-full bg-primary/10 px-2.5 py-1 text-xs font-medium text-primary">
                  {t.name}
                  {t.capability_id && <span className="ml-1 text-primary/60">({t.capability_id})</span>}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Invite info */}
        <div className="mb-6 rounded-lg border border-border bg-card p-4">
          <h3 className="text-sm font-medium text-muted mb-2">Invite</h3>
          {candidate.invite_code ? (
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <code className="rounded bg-background px-2 py-1 text-xs font-mono text-foreground">
                  {candidate.invite_code}
                </code>
                <StatusBadge status={candidate.invite_status || "unknown"} />
                <button
                  onClick={() => {
                    navigator.clipboard.writeText(`https://agentnode.net/i/${candidate.invite_code}`);
                  }}
                  className="text-xs text-primary hover:underline"
                >
                  Copy tracking URL
                </button>
              </div>
              {candidate.click_count != null && candidate.click_count > 0 && (
                <div className="text-xs text-muted">{candidate.click_count} click(s)</div>
              )}
            </div>
          ) : (
            <p className="text-xs text-muted">No invite generated yet.</p>
          )}
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              onClick={onGenerateInvite}
              className="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-white hover:bg-primary/90"
            >
              {candidate.invite_code ? "Regenerate Invite" : "Generate Invite"}
            </button>
            {candidate.contact_email && (
              <button
                onClick={onGenerateAndSend}
                className="rounded-md bg-green-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-green-500"
              >
                {candidate.invite_code ? "Regenerate & Send Email" : "Generate & Send Email"}
              </button>
            )}
            {candidate.invite_code && candidate.contact_email && (
              <button
                onClick={onFollowup}
                className="rounded-md bg-yellow-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-yellow-500"
              >
                Send Follow-up
              </button>
            )}
            <button
              onClick={onEmailSent}
              className="rounded-md border border-border px-3 py-1.5 text-xs font-medium text-foreground hover:bg-card"
            >
              Mark email sent
            </button>
          </div>
          {!candidate.contact_email && (
            <p className="mt-2 text-xs text-yellow-500">No contact email — cannot send automatically.</p>
          )}
        </div>

        {/* Email preview */}
        <div className="mb-6">
          <button
            onClick={() => setShowEmailPreview(!showEmailPreview)}
            className="text-sm font-medium text-muted hover:text-foreground"
          >
            {showEmailPreview ? "\u25BC" : "\u25B6"} Email Preview
          </button>
          {showEmailPreview && (
            <div className="mt-2 rounded-lg border border-border bg-card p-4 text-sm">
              <div className="mb-2 text-xs text-muted">
                <span className="font-medium">To:</span> {candidate.contact_email || "(no email)"}
              </div>
              <div className="mb-3 text-xs text-muted">
                <span className="font-medium">Subject:</span> Publish {candidate.display_name || candidate.repo_name || "your tool"} on AgentNode
              </div>
              <div className="rounded-md border border-border bg-background p-3 text-xs text-foreground space-y-2">
                <p className="font-bold text-white">Your tool on AgentNode</p>
                <p>Hi {candidate.contact_name || "(name)"},</p>
                <p>
                  We discovered <strong className="text-white">{candidate.display_name || candidate.repo_name || "your tool"}</strong> and think it would be a great addition to AgentNode — the open registry where AI agents discover and install capabilities across every framework.
                </p>
                {candidate.description && <p className="text-zinc-300">{candidate.description}</p>}
                {candidate.source_url && (
                  <p className="text-zinc-500">Source: {candidate.source_url}</p>
                )}
                <p>We&apos;ve pre-filled your tool&apos;s metadata so publishing takes just a few clicks. Review it, adjust anything you like, and publish under your own name.</p>
                <div className="text-center py-2">
                  <span className="inline-block rounded-lg bg-indigo-500/20 px-4 py-2 text-xs font-medium text-indigo-400">
                    [Publish {candidate.display_name || candidate.repo_name} on AgentNode →]
                  </span>
                </div>
                <p className="text-zinc-500 text-[11px]">Nothing is published automatically. You have full control over the listing.</p>
              </div>
            </div>
          )}
        </div>

        {/* Published package link */}
        {candidate.published_package_id && (
          <div className="mb-6">
            <h3 className="text-sm font-medium text-muted">Published Package</h3>
            <p className="mt-1 text-xs text-primary">ID: {candidate.published_package_id}</p>
          </div>
        )}

        {/* Prefill preview */}
        <div className="mb-6">
          <button
            onClick={() => setShowPrefill(!showPrefill)}
            className="text-sm font-medium text-muted hover:text-foreground"
          >
            {showPrefill ? "\u25BC" : "\u25B6"} Prefill Preview
          </button>
          {showPrefill && candidate.detected_tools && (
            <pre className="mt-2 max-h-60 overflow-auto rounded-lg border border-border bg-background p-3 text-xs font-mono text-foreground">
              {JSON.stringify(
                {
                  anp_version: "0.2",
                  package_id: `${candidate.repo_owner || "unknown"}/${candidate.repo_name || "tool"}`,
                  version: "0.1.0",
                  name: candidate.display_name || candidate.repo_name || "Untitled Tool",
                  summary: (candidate.description || "").slice(0, 140) || "Imported tool",
                  description: candidate.description || "",
                  capabilities: {
                    tools: candidate.detected_tools.map((t) => ({
                      name: t.name,
                      description: t.description || "",
                      ...(t.capability_id ? { capability_id: t.capability_id } : {}),
                    })),
                  },
                },
                null,
                2
              )}
            </pre>
          )}
        </div>

        {/* Admin notes */}
        <div className="mb-6">
          <h3 className="text-sm font-medium text-muted mb-2">Admin Notes</h3>
          {editingNotes ? (
            <div className="space-y-2">
              <textarea
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                rows={3}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
              />
              <div className="flex gap-2">
                <button
                  onClick={() => { onUpdateNotes(notes); setEditingNotes(false); }}
                  className="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-white"
                >
                  Save
                </button>
                <button
                  onClick={() => { setNotes(candidate.admin_notes || ""); setEditingNotes(false); }}
                  className="rounded-md border border-border px-3 py-1.5 text-xs font-medium text-foreground"
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <div
              onClick={() => setEditingNotes(true)}
              className="min-h-[2rem] cursor-pointer rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground hover:border-primary/50"
            >
              {candidate.admin_notes || <span className="text-muted">Click to add notes...</span>}
            </div>
          )}
        </div>

        {/* Event timeline */}
        <div>
          <h3 className="text-sm font-medium text-muted mb-2">Event Timeline</h3>
          <EventTimeline events={events} />
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main Page                                                          */
/* ------------------------------------------------------------------ */

export default function CandidatesPage() {
  const [funnel, setFunnel] = useState<FunnelData | null>(null);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);

  // Filters
  const [statusFilter, setStatusFilter] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [formatFilter, setFormatFilter] = useState("");
  const [funnelFilter, setFunnelFilter] = useState("");

  // Detail view
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedCandidate, setSelectedCandidate] = useState<Candidate | null>(null);
  const [selectedEvents, setSelectedEvents] = useState<CandidateEvent[]>([]);

  // Create candidate modal
  const [showCreate, setShowCreate] = useState(false);
  const [createUrl, setCreateUrl] = useState("");
  const [createSource, setCreateSource] = useState("manual");

  // Bulk send
  const [showBulk, setShowBulk] = useState(false);
  const [bulkMinStars, setBulkMinStars] = useState(100);
  const [bulkSource, setBulkSource] = useState("");
  const [bulkFormat, setBulkFormat] = useState("");
  const [bulkLimit, setBulkLimit] = useState(50);
  const [bulkSendEmail, setBulkSendEmail] = useState(false);
  const [bulkRunning, setBulkRunning] = useState(false);
  const [bulkResult, setBulkResult] = useState<{ invites_created: number; emails_sent: number; emails_failed: number; candidates: Array<{ display_name: string; contact_email: string; stars: number; status: string }> } | null>(null);
  const [creating, setCreating] = useState(false);

  const perPage = 50;

  const loadFunnel = useCallback(async () => {
    try {
      const res = await fetchWithAuth("/admin/candidates/funnel");
      if (res.ok) setFunnel(await res.json());
    } catch { /* ignore */ }
  }, []);

  const loadCandidates = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(page), per_page: String(perPage) });
      if (statusFilter) params.set("outreach_status", statusFilter);
      if (sourceFilter) params.set("source", sourceFilter);
      if (formatFilter) params.set("detected_format", formatFilter);
      if (funnelFilter) params.set("funnel_filter", funnelFilter);

      const res = await fetchWithAuth(`/admin/candidates?${params}`);
      if (res.ok) {
        const data = await res.json();
        setCandidates(data.items);
        setTotal(data.total);
      }
    } catch { /* ignore */ }
    setLoading(false);
  }, [page, statusFilter, sourceFilter, formatFilter, funnelFilter]);

  useEffect(() => { loadFunnel(); }, [loadFunnel]);
  useEffect(() => { loadCandidates(); }, [loadCandidates]);

  // Load detail
  useEffect(() => {
    if (!selectedId) return;
    const c = candidates.find((c) => c.id === selectedId);
    setSelectedCandidate(c || null);

    fetchWithAuth(`/admin/candidates/${selectedId}/events`)
      .then((res) => res.ok ? res.json() : { items: [] })
      .then((data) => setSelectedEvents(data.items || []))
      .catch(() => setSelectedEvents([]));
  }, [selectedId, candidates]);

  const handleGenerateInvite = async (sendEmail = false) => {
    if (!selectedId) return;
    const res = await fetchWithAuth(`/admin/candidates/${selectedId}/invite`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ send_email: sendEmail }),
    });
    if (res.ok) {
      const data = await res.json();
      navigator.clipboard.writeText(data.tracking_url);
      if (sendEmail && data.email_sent) {
        alert("Invite generated and email sent!");
      } else if (sendEmail && !data.email_sent) {
        alert("Invite generated but email could not be sent (check SMTP config).");
      }
      loadCandidates();
      // Reload events
      const evRes = await fetchWithAuth(`/admin/candidates/${selectedId}/events`);
      if (evRes.ok) { const d = await evRes.json(); setSelectedEvents(d.items || []); }
    }
  };

  const handleFollowup = async () => {
    if (!selectedId) return;
    if (!confirm("Send follow-up email to this candidate?")) return;
    const res = await fetchWithAuth(`/admin/candidates/${selectedId}/followup`, { method: "POST" });
    if (res.ok) {
      const data = await res.json();
      if (data.email_sent) alert("Follow-up email sent!");
      else alert("Follow-up could not be sent (check SMTP).");
    } else {
      const err = await res.json().catch(() => ({}));
      alert(err.detail || "Failed to send follow-up.");
    }
    const evRes = await fetchWithAuth(`/admin/candidates/${selectedId}/events`);
    if (evRes.ok) { const d = await evRes.json(); setSelectedEvents(d.items || []); }
  };

  const handleEmailSent = async () => {
    if (!selectedId) return;
    await fetchWithAuth(`/admin/candidates/${selectedId}/email-sent`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ channel: "email" }),
    });
    // Reload events
    const evRes = await fetchWithAuth(`/admin/candidates/${selectedId}/events`);
    if (evRes.ok) { const d = await evRes.json(); setSelectedEvents(d.items || []); }
  };

  const handleUpdateNotes = async (notes: string) => {
    if (!selectedId) return;
    await fetchWithAuth(`/admin/candidates/${selectedId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ admin_notes: notes }),
    });
    loadCandidates();
  };

  const handleBulkSend = async () => {
    if (bulkSendEmail && !confirm(`This will send real emails to up to ${bulkLimit} creators. Continue?`)) return;
    setBulkRunning(true);
    setBulkResult(null);
    try {
      const body: Record<string, unknown> = {
        min_stars: bulkMinStars,
        limit: bulkLimit,
        send_email: bulkSendEmail,
      };
      if (bulkSource) body.source = bulkSource;
      if (bulkFormat) body.detected_format = bulkFormat;

      const res = await fetchWithAuth("/admin/candidates/bulk-send", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (res.ok) {
        const data = await res.json();
        setBulkResult(data);
        loadCandidates();
        loadFunnel();
      }
    } catch { /* ignore */ }
    setBulkRunning(false);
  };

  const handleCreate = async () => {
    if (!createUrl.trim()) return;
    setCreating(true);

    // Parse GitHub URL
    const match = createUrl.match(/github\.com\/([^/]+)\/([^/\s?#]+)/);
    const body: Record<string, unknown> = {
      source: createSource,
      source_url: createUrl.trim(),
    };
    if (match) {
      body.repo_owner = match[1];
      body.repo_name = match[2].replace(/\.git$/, "");
      body.display_name = body.repo_name;
    }

    const res = await fetchWithAuth("/admin/candidates", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (res.ok) {
      setShowCreate(false);
      setCreateUrl("");
      loadCandidates();
      loadFunnel();
    }
    setCreating(false);
  };

  const totalPages = Math.ceil(total / perPage);

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold text-foreground">Creator Acquisition</h1>
        <div className="flex gap-2">
          <button
            onClick={() => setShowBulk(!showBulk)}
            className="rounded-lg border border-border px-4 py-2 text-sm font-medium text-foreground hover:bg-card"
          >
            Bulk Send
          </button>
          <button
            onClick={() => setShowCreate(true)}
            className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-white hover:bg-primary/90"
          >
            + Add Candidate
          </button>
        </div>
      </div>

      {/* Funnel */}
      {funnel && <FunnelOverview data={funnel} />}

      {/* Bulk Send Panel */}
      {showBulk && (
        <div className="mb-6 rounded-xl border border-border bg-card p-4">
          <h3 className="mb-3 text-sm font-bold text-foreground">Bulk Invite & Email</h3>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
            <div>
              <label className="block text-xs text-muted mb-1">Min Stars</label>
              <input type="number" value={bulkMinStars} onChange={(e) => setBulkMinStars(Number(e.target.value))} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground" />
            </div>
            <div>
              <label className="block text-xs text-muted mb-1">Source</label>
              <select value={bulkSource} onChange={(e) => setBulkSource(e.target.value)} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground">
                <option value="">All</option>
                <option value="awesome-mcp-servers">awesome-mcp-servers</option>
                <option value="awesome-langchain">awesome-langchain</option>
                <option value="awesome-crewai">awesome-crewai</option>
                <option value="github-topic">github-topic</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-muted mb-1">Format</label>
              <select value={bulkFormat} onChange={(e) => setBulkFormat(e.target.value)} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground">
                <option value="">All</option>
                <option value="mcp">MCP</option>
                <option value="langchain">LangChain</option>
                <option value="crewai">CrewAI</option>
                <option value="openai">OpenAI</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-muted mb-1">Limit</label>
              <input type="number" value={bulkLimit} onChange={(e) => setBulkLimit(Number(e.target.value))} min={1} max={500} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground" />
            </div>
            <div className="flex items-end gap-2">
              <button onClick={() => { setBulkSendEmail(false); handleBulkSend(); }} disabled={bulkRunning} className="rounded-md bg-primary px-3 py-2 text-xs font-medium text-white disabled:opacity-50">
                {bulkRunning ? "Running..." : "Dry Run"}
              </button>
              <button onClick={() => { setBulkSendEmail(true); handleBulkSend(); }} disabled={bulkRunning} className="rounded-md bg-green-600 px-3 py-2 text-xs font-medium text-white disabled:opacity-50 hover:bg-green-500">
                {bulkRunning ? "Sending..." : "Send Emails"}
              </button>
            </div>
          </div>
          {bulkResult && (
            <div className="mt-3">
              <div className="flex gap-4 text-xs text-muted mb-2">
                <span>Invites: <strong className="text-foreground">{bulkResult.invites_created}</strong></span>
                <span>Emails sent: <strong className="text-green-400">{bulkResult.emails_sent}</strong></span>
                {bulkResult.emails_failed > 0 && <span>Failed: <strong className="text-red-400">{bulkResult.emails_failed}</strong></span>}
              </div>
              <div className="max-h-40 overflow-y-auto rounded-md border border-border bg-background">
                <table className="w-full text-xs">
                  <thead><tr className="text-left text-muted border-b border-border"><th className="px-2 py-1">Tool</th><th className="px-2 py-1">Email</th><th className="px-2 py-1">Stars</th><th className="px-2 py-1">Status</th></tr></thead>
                  <tbody>
                    {bulkResult.candidates.map((c, i) => (
                      <tr key={i} className="border-b border-border/50">
                        <td className="px-2 py-1 text-foreground">{c.display_name}</td>
                        <td className="px-2 py-1 text-muted">{c.contact_email}</td>
                        <td className="px-2 py-1 text-muted">{c.stars}</td>
                        <td className="px-2 py-1"><span className={c.status === "email_sent" ? "text-green-400" : c.status === "email_failed" ? "text-red-400" : "text-blue-400"}>{c.status}</span></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Filters */}
      <div className="mb-4 flex flex-wrap gap-3">
        <select
          value={statusFilter}
          onChange={(e) => { setStatusFilter(e.target.value); setPage(1); }}
          className="rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground"
        >
          <option value="">All statuses</option>
          <option value="discovered">Discovered</option>
          <option value="contacted">Contacted</option>
          <option value="engaged">Engaged</option>
          <option value="signed_up">Signed Up</option>
          <option value="published">Published</option>
          <option value="declined">Declined</option>
          <option value="skipped">Skipped</option>
        </select>

        <select
          value={sourceFilter}
          onChange={(e) => { setSourceFilter(e.target.value); setPage(1); }}
          className="rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground"
        >
          <option value="">All sources</option>
          <option value="awesome-mcp-servers">awesome-mcp-servers</option>
          <option value="github-topic">github-topic</option>
          <option value="manual">manual</option>
        </select>

        <select
          value={formatFilter}
          onChange={(e) => { setFormatFilter(e.target.value); setPage(1); }}
          className="rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground"
        >
          <option value="">All formats</option>
          <option value="mcp">MCP</option>
          <option value="langchain">LangChain</option>
          <option value="crewai">CrewAI</option>
          <option value="openai">OpenAI</option>
        </select>

        <select
          value={funnelFilter}
          onChange={(e) => { setFunnelFilter(e.target.value); setPage(1); }}
          className="rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground"
        >
          <option value="">All funnel stages</option>
          <option value="clicked_not_signed_up">Clicked but not signed up</option>
          <option value="signed_up_not_published">Signed up but not published</option>
          <option value="published_not_verified">Published but not verified</option>
        </select>
      </div>

      {/* Table */}
      <div className="overflow-x-auto rounded-xl border border-border">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-card text-left text-xs font-medium uppercase tracking-wider text-muted">
              <th className="px-4 py-3">Tool</th>
              <th className="px-4 py-3">Source</th>
              <th className="px-4 py-3">Stars</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Invite</th>
              <th className="px-4 py-3">Clicks</th>
              <th className="px-4 py-3">Last Activity</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={7} className="px-4 py-8 text-center text-muted">Loading...</td></tr>
            ) : candidates.length === 0 ? (
              <tr><td colSpan={7} className="px-4 py-8 text-center text-muted">No candidates found.</td></tr>
            ) : (
              candidates.map((c) => (
                <tr
                  key={c.id}
                  onClick={() => setSelectedId(c.id)}
                  className="cursor-pointer border-b border-border transition-colors hover:bg-card"
                >
                  <td className="px-4 py-3">
                    <div className="font-medium text-foreground">{c.display_name || c.repo_name || "Unknown"}</div>
                    {c.detected_format && (
                      <span className="text-xs text-muted">{c.detected_format}</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-muted">{c.source}</td>
                  <td className="px-4 py-3 text-muted">{c.stars ?? "\u2014"}</td>
                  <td className="px-4 py-3"><StatusBadge status={c.outreach_status} /></td>
                  <td className="px-4 py-3">
                    {c.invite_code ? (
                      <span className="text-xs text-muted">{c.invite_status}</span>
                    ) : (
                      <span className="text-xs text-muted">\u2014</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-muted">{c.click_count || 0}</td>
                  <td className="px-4 py-3 text-xs text-muted">{formatDate(c.last_event_at)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="mt-4 flex items-center justify-between text-sm">
          <span className="text-muted">{total} candidates</span>
          <div className="flex gap-2">
            <button
              onClick={() => setPage(Math.max(1, page - 1))}
              disabled={page === 1}
              className="rounded-md border border-border px-3 py-1.5 text-xs disabled:opacity-30"
            >
              Prev
            </button>
            <span className="px-2 py-1.5 text-xs text-muted">
              {page} / {totalPages}
            </span>
            <button
              onClick={() => setPage(Math.min(totalPages, page + 1))}
              disabled={page === totalPages}
              className="rounded-md border border-border px-3 py-1.5 text-xs disabled:opacity-30"
            >
              Next
            </button>
          </div>
        </div>
      )}

      {/* Create modal */}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowCreate(false)}>
          <div className="w-full max-w-md rounded-xl border border-border bg-background p-6" onClick={(e) => e.stopPropagation()}>
            <h2 className="mb-4 text-lg font-bold text-foreground">Add Candidate</h2>
            <div className="space-y-3">
              <div>
                <label className="block text-xs font-medium text-muted mb-1">Source URL (GitHub)</label>
                <input
                  type="text"
                  value={createUrl}
                  onChange={(e) => setCreateUrl(e.target.value)}
                  placeholder="https://github.com/owner/repo"
                  className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-muted mb-1">Source</label>
                <select
                  value={createSource}
                  onChange={(e) => setCreateSource(e.target.value)}
                  className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground"
                >
                  <option value="manual">Manual</option>
                  <option value="awesome-mcp-servers">awesome-mcp-servers</option>
                  <option value="github-topic">github-topic</option>
                </select>
              </div>
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => setShowCreate(false)}
                className="rounded-md border border-border px-4 py-2 text-sm text-foreground"
              >
                Cancel
              </button>
              <button
                onClick={handleCreate}
                disabled={creating || !createUrl.trim()}
                className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
              >
                {creating ? "Adding..." : "Add"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Detail panel */}
      {selectedCandidate && (
        <DetailPanel
          candidate={selectedCandidate}
          events={selectedEvents}
          onClose={() => { setSelectedId(null); setSelectedCandidate(null); }}
          onGenerateInvite={() => handleGenerateInvite(false)}
          onGenerateAndSend={() => handleGenerateInvite(true)}
          onFollowup={handleFollowup}
          onEmailSent={handleEmailSent}
          onUpdateNotes={handleUpdateNotes}
        />
      )}
    </div>
  );
}
