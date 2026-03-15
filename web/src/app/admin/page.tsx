"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";

interface QuarantinedVersion {
  package_slug: string;
  version_number: string;
  quarantine_status: string;
  quarantined_at: string;
  quarantine_reason: string | null;
}

interface SuspendedPublisher {
  slug: string;
  display_name: string;
  is_suspended: boolean;
  suspension_reason: string | null;
  trust_level: string;
}

export default function AdminPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  const [quarantined, setQuarantined] = useState<QuarantinedVersion[]>([]);
  const [suspended, setSuspended] = useState<SuspendedPublisher[]>([]);

  // Suspend form
  const [suspendSlug, setSuspendSlug] = useState("");
  const [suspendReason, setSuspendReason] = useState("");

  // Trust form
  const [trustSlug, setTrustSlug] = useState("");
  const [trustLevel, setTrustLevel] = useState("verified");

  function getAuthHeaders(): Record<string, string> {
    const token = localStorage.getItem("access_token");
    if (!token) return {};
    return { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
  }

  useEffect(() => {
    loadData();
  }, []);

  async function loadData() {
    try {
      const [qRes, sRes] = await Promise.all([
        fetch("/api/v1/admin/quarantined", { headers: getAuthHeaders() }),
        fetch("/api/v1/admin/publishers/suspended", { headers: getAuthHeaders() }),
      ]);

      if (qRes.status === 403 || sRes.status === 403) {
        router.push("/dashboard");
        return;
      }

      if (qRes.ok) setQuarantined(await qRes.json());
      if (sRes.ok) setSuspended(await sRes.json());
    } catch {
      setError("Failed to load admin data");
    } finally {
      setLoading(false);
    }
  }

  async function clearQuarantine(slug: string, version: string) {
    setError("");
    setSuccess("");
    const res = await fetch(`/api/v1/admin/packages/${slug}/versions/${version}/clear`, {
      method: "POST",
      headers: getAuthHeaders(),
    });
    if (res.ok) {
      setSuccess(`Quarantine cleared for ${slug}@${version}`);
      await loadData();
    } else {
      const data = await res.json();
      setError(data.error?.message || "Failed to clear quarantine");
    }
  }

  async function rejectVersion(slug: string, version: string) {
    setError("");
    setSuccess("");
    const res = await fetch(`/api/v1/admin/packages/${slug}/versions/${version}/reject`, {
      method: "POST",
      headers: getAuthHeaders(),
    });
    if (res.ok) {
      setSuccess(`Version ${slug}@${version} rejected`);
      await loadData();
    } else {
      const data = await res.json();
      setError(data.error?.message || "Failed to reject version");
    }
  }

  async function suspendPublisher() {
    if (!suspendSlug || !suspendReason) return;
    setError("");
    setSuccess("");
    const res = await fetch(`/api/v1/admin/publishers/${suspendSlug}/suspend`, {
      method: "POST",
      headers: getAuthHeaders(),
      body: JSON.stringify({ reason: suspendReason }),
    });
    if (res.ok) {
      setSuccess(`Publisher '${suspendSlug}' suspended`);
      setSuspendSlug("");
      setSuspendReason("");
      await loadData();
    } else {
      const data = await res.json();
      setError(data.error?.message || "Failed to suspend publisher");
    }
  }

  async function unsuspendPublisher(slug: string) {
    setError("");
    setSuccess("");
    const res = await fetch(`/api/v1/admin/publishers/${slug}/unsuspend`, {
      method: "POST",
      headers: getAuthHeaders(),
    });
    if (res.ok) {
      setSuccess(`Publisher '${slug}' unsuspended`);
      await loadData();
    } else {
      const data = await res.json();
      setError(data.error?.message || "Failed to unsuspend publisher");
    }
  }

  async function setPublisherTrust() {
    if (!trustSlug) return;
    setError("");
    setSuccess("");
    const res = await fetch(`/api/v1/admin/publishers/${trustSlug}/trust`, {
      method: "PUT",
      headers: getAuthHeaders(),
      body: JSON.stringify({ trust_level: trustLevel }),
    });
    if (res.ok) {
      setSuccess(`Publisher '${trustSlug}' trust level set to '${trustLevel}'`);
      setTrustSlug("");
    } else {
      const data = await res.json();
      setError(data.error?.message || "Failed to set trust level");
    }
  }

  if (loading) {
    return (
      <div className="mx-auto max-w-5xl px-6 py-24 text-center text-muted">Loading...</div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl px-6 py-12">
      <h1 className="mb-8 text-2xl font-bold text-foreground">Admin Panel</h1>

      {error && (
        <div className="mb-4 rounded-md border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">
          {error}
        </div>
      )}
      {success && (
        <div className="mb-4 rounded-md border border-success/30 bg-success/10 px-4 py-3 text-sm text-success">
          {success}
        </div>
      )}

      {/* Quarantine Queue */}
      <section className="mb-8 rounded-lg border border-border bg-card p-6">
        <h2 className="mb-4 text-lg font-semibold text-foreground">
          Quarantine Queue ({quarantined.length})
        </h2>
        {quarantined.length === 0 ? (
          <p className="text-sm text-muted">No quarantined versions.</p>
        ) : (
          <div className="space-y-3">
            {quarantined.map((v) => (
              <div
                key={`${v.package_slug}-${v.version_number}`}
                className="flex items-center justify-between rounded bg-background px-4 py-3"
              >
                <div>
                  <span className="font-medium text-foreground">
                    {v.package_slug}@{v.version_number}
                  </span>
                  {v.quarantine_reason && (
                    <span className="ml-2 text-sm text-muted">({v.quarantine_reason})</span>
                  )}
                  <div className="text-xs text-muted">
                    {v.quarantined_at ? new Date(v.quarantined_at).toLocaleString() : ""}
                  </div>
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={() => clearQuarantine(v.package_slug, v.version_number)}
                    className="rounded bg-success/20 px-3 py-1 text-xs font-medium text-success hover:bg-success/30"
                  >
                    Clear
                  </button>
                  <button
                    onClick={() => rejectVersion(v.package_slug, v.version_number)}
                    className="rounded bg-danger/20 px-3 py-1 text-xs font-medium text-danger hover:bg-danger/30"
                  >
                    Reject
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Suspended Publishers */}
      <section className="mb-8 rounded-lg border border-border bg-card p-6">
        <h2 className="mb-4 text-lg font-semibold text-foreground">
          Suspended Publishers ({suspended.length})
        </h2>
        {suspended.length === 0 ? (
          <p className="text-sm text-muted">No suspended publishers.</p>
        ) : (
          <div className="space-y-3">
            {suspended.map((p) => (
              <div
                key={p.slug}
                className="flex items-center justify-between rounded bg-background px-4 py-3"
              >
                <div>
                  <span className="font-medium text-foreground">{p.display_name}</span>
                  <span className="ml-2 text-sm text-muted">@{p.slug}</span>
                  {p.suspension_reason && (
                    <div className="text-xs text-muted">{p.suspension_reason}</div>
                  )}
                </div>
                <button
                  onClick={() => unsuspendPublisher(p.slug)}
                  className="rounded bg-primary/20 px-3 py-1 text-xs font-medium text-primary hover:bg-primary/30"
                >
                  Unsuspend
                </button>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Suspend Publisher */}
      <section className="mb-8 rounded-lg border border-border bg-card p-6">
        <h2 className="mb-4 text-lg font-semibold text-foreground">Suspend Publisher</h2>
        <div className="flex gap-2">
          <input
            type="text"
            value={suspendSlug}
            onChange={(e) => setSuspendSlug(e.target.value)}
            className="w-40 rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
            placeholder="Publisher slug"
          />
          <input
            type="text"
            value={suspendReason}
            onChange={(e) => setSuspendReason(e.target.value)}
            className="flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
            placeholder="Reason for suspension"
          />
          <button
            onClick={suspendPublisher}
            disabled={!suspendSlug || !suspendReason}
            className="rounded bg-danger px-4 py-2 text-sm text-white hover:bg-danger/90 disabled:opacity-50"
          >
            Suspend
          </button>
        </div>
      </section>

      {/* Set Trust Level */}
      <section className="mb-8 rounded-lg border border-border bg-card p-6">
        <h2 className="mb-4 text-lg font-semibold text-foreground">Set Trust Level</h2>
        <div className="flex gap-2">
          <input
            type="text"
            value={trustSlug}
            onChange={(e) => setTrustSlug(e.target.value)}
            className="w-40 rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
            placeholder="Publisher slug"
          />
          <select
            value={trustLevel}
            onChange={(e) => setTrustLevel(e.target.value)}
            className="rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
          >
            <option value="unverified">unverified</option>
            <option value="verified">verified</option>
            <option value="trusted">trusted</option>
            <option value="curated">curated</option>
          </select>
          <button
            onClick={setPublisherTrust}
            disabled={!trustSlug}
            className="rounded bg-primary px-4 py-2 text-sm text-white hover:bg-primary/90 disabled:opacity-50"
          >
            Set trust
          </button>
        </div>
      </section>
    </div>
  );
}
