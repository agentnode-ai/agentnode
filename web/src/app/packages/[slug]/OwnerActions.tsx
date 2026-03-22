"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { fetchWithAuth, fetchAllVersions } from "@/lib/api";

interface VersionInfo {
  version_number: string;
  channel: string;
  published_at: string;
  is_yanked?: boolean;
  quarantine_status?: string;
  verification_status?: string | null;
}

interface PackageMetadata {
  name: string;
  summary: string;
  description: string;
  tags: string[];
}

export default function OwnerActions({
  slug,
  publisherSlug,
  isDeprecated,
  currentMetadata,
}: {
  slug: string;
  publisherSlug: string;
  isDeprecated: boolean;
  currentMetadata: PackageMetadata;
}) {
  const router = useRouter();
  const [isOwner, setIsOwner] = useState(false);
  const [versions, setVersions] = useState<VersionInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [deprecating, setDeprecating] = useState(false);
  const [yankingVersion, setYankingVersion] = useState<string | null>(null);
  const [confirmAction, setConfirmAction] = useState<{ type: "deprecate" | "yank"; version?: string } | null>(null);
  const [error, setError] = useState("");
  const [deprecated, setDeprecated] = useState(isDeprecated);

  // Edit state
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editSuccess, setEditSuccess] = useState("");
  const [editForm, setEditForm] = useState({
    name: currentMetadata.name,
    summary: currentMetadata.summary,
    description: currentMetadata.description,
    tags: currentMetadata.tags.join(", "),
  });

  useEffect(() => {
    let cancelled = false;
    async function checkOwner() {
      try {
        const res = await fetchWithAuth("/auth/me");
        if (!res.ok) return;
        const user = await res.json();
        if (user.publisher?.slug === publisherSlug) {
          setIsOwner(true);
          // Load all versions including quarantined/yanked
          const vRes = await fetchAllVersions(slug);
          if (vRes.ok) {
            const data = await vRes.json();
            if (!cancelled) setVersions(data.versions ?? []);
          }
        }
      } catch {
        // Not logged in or not owner
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    checkOwner();
    return () => { cancelled = true; };
  }, [slug, publisherSlug]);

  if (loading || !isOwner) return null;

  function handleCancelEdit() {
    setEditing(false);
    setError("");
    setEditForm({
      name: currentMetadata.name,
      summary: currentMetadata.summary,
      description: currentMetadata.description,
      tags: currentMetadata.tags.join(", "),
    });
  }

  async function handleSaveEdit() {
    setSaving(true);
    setError("");
    setEditSuccess("");
    try {
      const tags = editForm.tags
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean);

      const payload: Record<string, unknown> = {};
      if (editForm.name !== currentMetadata.name) payload.name = editForm.name;
      if (editForm.summary !== currentMetadata.summary) payload.summary = editForm.summary;
      if (editForm.description !== currentMetadata.description) payload.description = editForm.description;
      if (editForm.tags !== currentMetadata.tags.join(", ")) payload.tags = tags;

      if (Object.keys(payload).length === 0) {
        setEditing(false);
        return;
      }

      const res = await fetchWithAuth(`/packages/${encodeURIComponent(slug)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        setEditing(false);
        setEditSuccess("Changes saved");
        setTimeout(() => setEditSuccess(""), 3000);
        router.refresh();
      } else {
        const data = await res.json().catch(() => ({}));
        setError(data.error?.message || data.detail || "Failed to save changes");
      }
    } finally {
      setSaving(false);
    }
  }

  async function handleDeprecate() {
    setDeprecating(true);
    setError("");
    try {
      const res = await fetchWithAuth(`/packages/${encodeURIComponent(slug)}/deprecate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      if (res.ok) {
        setDeprecated(true);
        setConfirmAction(null);
        router.refresh();
      } else {
        const data = await res.json().catch(() => ({}));
        setError(data.error?.message || data.detail || "Failed to deprecate package");
      }
    } finally {
      setDeprecating(false);
    }
  }

  async function handleYank(version: string) {
    setYankingVersion(version);
    setError("");
    try {
      const res = await fetchWithAuth(`/packages/${encodeURIComponent(slug)}/versions/${encodeURIComponent(version)}/yank`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      if (res.ok) {
        setVersions((prev) =>
          prev.map((v) => v.version_number === version ? { ...v, is_yanked: true } : v)
        );
        setConfirmAction(null);
        router.refresh();
      } else {
        const data = await res.json().catch(() => ({}));
        setError(data.error?.message || data.detail || "Failed to yank version");
      }
    } finally {
      setYankingVersion(null);
    }
  }

  return (
    <section className="mb-6 rounded-xl border border-border bg-card p-4 sm:p-6">
      <h2 className="mb-1 text-sm font-semibold text-foreground">Owner Actions</h2>
      <p className="mb-4 text-xs text-muted">Manage your package metadata and visibility.</p>

      {error && (
        <div className="mb-3 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-400">
          {error}
        </div>
      )}
      {editSuccess && (
        <div className="mb-3 rounded-md border border-green-500/30 bg-green-500/10 px-3 py-2 text-xs text-green-400">
          {editSuccess}
        </div>
      )}

      {/* Edit Metadata */}
      <div className="mb-4">
        {editing ? (
          <div className="space-y-3 rounded-lg border border-border bg-background p-4">
            <h3 className="text-xs font-medium text-muted uppercase tracking-wider">Edit Metadata</h3>
            <div>
              <label className="mb-1 block text-xs text-muted">Display name</label>
              <input
                type="text"
                value={editForm.name}
                onChange={(e) => setEditForm((f) => ({ ...f, name: e.target.value }))}
                maxLength={100}
                className="w-full rounded border border-border bg-card px-3 py-1.5 text-sm text-foreground placeholder:text-muted focus:border-primary focus:outline-none"
              />
            </div>
            <div>
              <label className="mb-1 flex items-center justify-between text-xs text-muted">
                <span>Summary</span>
                <span className={editForm.summary.length > 200 ? "text-red-400" : ""}>{editForm.summary.length}/200</span>
              </label>
              <input
                type="text"
                value={editForm.summary}
                onChange={(e) => setEditForm((f) => ({ ...f, summary: e.target.value }))}
                maxLength={200}
                className="w-full rounded border border-border bg-card px-3 py-1.5 text-sm text-foreground placeholder:text-muted focus:border-primary focus:outline-none"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-muted">Description</label>
              <textarea
                value={editForm.description}
                onChange={(e) => setEditForm((f) => ({ ...f, description: e.target.value }))}
                maxLength={5000}
                rows={3}
                className="w-full rounded border border-border bg-card px-3 py-1.5 text-sm text-foreground placeholder:text-muted focus:border-primary focus:outline-none resize-y"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-muted">Tags (comma-separated, max 20)</label>
              <input
                type="text"
                value={editForm.tags}
                onChange={(e) => setEditForm((f) => ({ ...f, tags: e.target.value }))}
                placeholder="ai, automation, data"
                className="w-full rounded border border-border bg-card px-3 py-1.5 text-sm text-foreground placeholder:text-muted focus:border-primary focus:outline-none"
              />
            </div>
            <div className="flex gap-2 pt-1">
              <button
                onClick={handleSaveEdit}
                disabled={saving}
                className="rounded bg-primary px-3 py-1.5 text-xs font-medium text-white hover:bg-primary/90 disabled:opacity-50"
              >
                {saving ? "Saving..." : "Save changes"}
              </button>
              <button
                onClick={handleCancelEdit}
                className="rounded border border-border px-3 py-1.5 text-xs text-muted hover:bg-card"
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <button
            onClick={() => setEditing(true)}
            className="rounded border border-border px-3 py-1.5 text-xs font-medium text-foreground hover:bg-card transition-colors"
          >
            Edit metadata
          </button>
        )}
      </div>

      {/* Danger Zone */}
      <div className="rounded-lg border border-red-500/20 bg-red-500/5 p-4">
        <h3 className="mb-3 text-xs font-medium text-red-400 uppercase tracking-wider">Danger Zone</h3>

        {/* Deprecate */}
        <div className="mb-4">
          {deprecated ? (
            <div className="flex items-center gap-2">
              <span className="rounded-full bg-red-500/10 px-2.5 py-0.5 text-xs font-medium text-red-400">Deprecated</span>
              <span className="text-xs text-muted">This package is deprecated</span>
            </div>
          ) : confirmAction?.type === "deprecate" ? (
            <div className="rounded-lg border border-red-500/30 bg-card p-3">
              <p className="mb-2 text-sm text-foreground">Are you sure? This action is irreversible.</p>
              <div className="flex gap-2">
                <button
                  onClick={handleDeprecate}
                  disabled={deprecating}
                  className="rounded bg-red-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-red-600 disabled:opacity-50"
                >
                  {deprecating ? "Deprecating..." : "Yes, deprecate"}
                </button>
                <button
                  onClick={() => setConfirmAction(null)}
                  className="rounded border border-border px-3 py-1.5 text-xs text-muted hover:bg-card"
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <button
              onClick={() => setConfirmAction({ type: "deprecate" })}
              className="rounded border border-red-500/30 px-3 py-1.5 text-xs font-medium text-red-400 hover:bg-red-500/10 transition-colors"
            >
              Deprecate package
            </button>
          )}
        </div>

        {/* Yank Versions */}
        {versions.length > 0 && (
          <div>
            <h3 className="mb-2 text-xs font-medium text-muted uppercase tracking-wider">Versions</h3>
            <div className="space-y-1.5">
              {versions.map((v) => (
                <div
                  key={v.version_number}
                  className="flex items-center justify-between rounded-lg border border-border bg-card px-3 py-2"
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <span className={`font-mono text-xs ${v.is_yanked ? "line-through text-muted" : "text-foreground"}`}>
                      v{v.version_number}
                    </span>
                    {v.is_yanked && (
                      <span className="rounded-full bg-red-500/10 px-2 py-0.5 text-[10px] font-medium text-red-400">
                        yanked
                      </span>
                    )}
                    {v.quarantine_status === "quarantined" && !v.is_yanked && (
                      <span className="rounded-full bg-yellow-500/10 px-2 py-0.5 text-[10px] font-medium text-yellow-400">
                        quarantined
                      </span>
                    )}
                    {v.channel !== "stable" && (
                      <span className="rounded bg-card px-1.5 py-0.5 text-[10px] text-muted border border-border">
                        {v.channel}
                      </span>
                    )}
                  </div>
                  {!v.is_yanked && (
                    confirmAction?.type === "yank" && confirmAction.version === v.version_number ? (
                      <div className="flex gap-1.5">
                        <button
                          onClick={() => handleYank(v.version_number)}
                          disabled={yankingVersion === v.version_number}
                          className="rounded bg-red-500 px-2 py-1 text-[10px] font-medium text-white hover:bg-red-600 disabled:opacity-50"
                        >
                          {yankingVersion === v.version_number ? "..." : "Confirm"}
                        </button>
                        <button
                          onClick={() => setConfirmAction(null)}
                          className="rounded border border-border px-2 py-1 text-[10px] text-muted hover:bg-card"
                        >
                          Cancel
                        </button>
                      </div>
                    ) : (
                      <button
                        onClick={() => setConfirmAction({ type: "yank", version: v.version_number })}
                        className="rounded border border-red-500/30 px-2 py-1 text-[10px] font-medium text-red-400 hover:bg-red-500/10 transition-colors"
                      >
                        Yank
                      </button>
                    )
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
