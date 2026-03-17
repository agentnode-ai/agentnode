"use client";

import { useState, useEffect, useRef } from "react";
import Link from "next/link";
import { fetchWithAuth } from "@/lib/api";

interface PackageItem {
  id: string;
  slug: string;
  name: string;
  summary?: string;
  package_type: string;
  publisher_slug: string | null;
  download_count: number;
  is_deprecated: boolean;
  created_at: string | null;
}

interface QuarantinedVersion {
  package_slug: string;
  version_number: string;
  quarantine_status: string;
  quarantined_at: string;
  quarantine_reason: string | null;
}

/* ------------------------------------------------------------------ */
/*  Modal / Dialog helpers                                            */
/* ------------------------------------------------------------------ */

function ConfirmDeleteDialog({
  slug,
  onConfirm,
  onCancel,
}: {
  slug: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const [typed, setTyped] = useState("");
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="w-full max-w-md rounded-lg border border-danger/30 bg-card p-6 shadow-xl">
        <h3 className="text-lg font-semibold text-foreground">Delete Package</h3>
        <p className="mt-2 text-sm text-muted">
          This action is <span className="font-semibold text-danger">permanent</span>. Type{" "}
          <span className="font-mono font-medium text-foreground">{slug}</span> to confirm.
        </p>
        <input
          autoFocus
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          placeholder={slug}
          className="mt-3 w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted/50 focus:border-danger focus:outline-none"
        />
        <div className="mt-4 flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="rounded border border-border px-4 py-1.5 text-sm text-muted hover:bg-card"
          >
            Cancel
          </button>
          <button
            disabled={typed !== slug}
            onClick={onConfirm}
            className="rounded bg-danger px-4 py-1.5 text-sm font-medium text-white hover:bg-danger/90 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            Delete Forever
          </button>
        </div>
      </div>
    </div>
  );
}

function QuarantineModal({
  slug,
  onConfirm,
  onCancel,
}: {
  slug: string;
  onConfirm: (version: string, reason: string) => void;
  onCancel: () => void;
}) {
  const [version, setVersion] = useState("");
  const [reason, setReason] = useState("");
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="w-full max-w-md rounded-lg border border-border bg-card p-6 shadow-xl">
        <h3 className="text-lg font-semibold text-foreground">
          Quarantine Version of <span className="font-mono text-primary">{slug}</span>
        </h3>
        <label className="mt-4 block text-xs font-medium text-muted">Version</label>
        <input
          autoFocus
          value={version}
          onChange={(e) => setVersion(e.target.value)}
          placeholder="e.g. 1.2.3"
          className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted/50 focus:border-primary focus:outline-none"
        />
        <label className="mt-3 block text-xs font-medium text-muted">Reason</label>
        <input
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Security vulnerability, malicious code, etc."
          className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted/50 focus:border-primary focus:outline-none"
        />
        <div className="mt-4 flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="rounded border border-border px-4 py-1.5 text-sm text-muted hover:bg-card"
          >
            Cancel
          </button>
          <button
            disabled={!version.trim()}
            onClick={() => onConfirm(version.trim(), reason.trim())}
            className="rounded bg-warning px-4 py-1.5 text-sm font-medium text-white hover:bg-warning/90 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            Quarantine
          </button>
        </div>
      </div>
    </div>
  );
}

function DeleteVersionDialog({
  slug,
  version,
  onConfirm,
  onCancel,
}: {
  slug: string;
  version: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="w-full max-w-md rounded-lg border border-danger/30 bg-card p-6 shadow-xl">
        <h3 className="text-lg font-semibold text-foreground">Delete Version</h3>
        <p className="mt-2 text-sm text-muted">
          Permanently delete version{" "}
          <span className="font-mono font-medium text-foreground">
            {slug}@{version}
          </span>
          ? This cannot be undone.
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="rounded border border-border px-4 py-1.5 text-sm text-muted hover:bg-card"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className="rounded bg-danger px-4 py-1.5 text-sm font-medium text-white hover:bg-danger/90"
          >
            Delete Version
          </button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Inline Edit Row                                                   */
/* ------------------------------------------------------------------ */

function InlineEditRow({
  pkg,
  onSave,
  onCancel,
}: {
  pkg: PackageItem;
  onSave: (name: string, summary: string) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(pkg.name);
  const [summary, setSummary] = useState(pkg.summary ?? "");
  return (
    <tr className="border-b border-border/50 bg-primary/5">
      <td className="px-4 py-2.5" colSpan={2}>
        <label className="block text-xs text-muted mb-1">Name</label>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="w-full rounded border border-border bg-background px-2 py-1 text-sm text-foreground focus:border-primary focus:outline-none"
        />
        <label className="mt-2 block text-xs text-muted mb-1">Summary</label>
        <input
          value={summary}
          onChange={(e) => setSummary(e.target.value)}
          className="w-full rounded border border-border bg-background px-2 py-1 text-sm text-foreground focus:border-primary focus:outline-none"
        />
      </td>
      <td className="px-4 py-2.5" colSpan={5}>
        <div className="flex items-end gap-2 h-full">
          <button
            onClick={() => onSave(name.trim(), summary.trim())}
            disabled={!name.trim()}
            className="rounded bg-primary px-3 py-1 text-xs font-medium text-white hover:bg-primary/90 disabled:opacity-40"
          >
            Save
          </button>
          <button
            onClick={onCancel}
            className="rounded border border-border px-3 py-1 text-xs text-muted hover:bg-card"
          >
            Cancel
          </button>
        </div>
      </td>
    </tr>
  );
}

/* ------------------------------------------------------------------ */
/*  Actions dropdown                                                  */
/* ------------------------------------------------------------------ */

function ActionsDropdown({
  pkg,
  onDelete,
  onDeprecate,
  onUndeprecate,
  onEdit,
  onQuarantine,
}: {
  pkg: PackageItem;
  onDelete: () => void;
  onDeprecate: () => void;
  onUndeprecate: () => void;
  onEdit: () => void;
  onQuarantine: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    if (open) document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="rounded border border-border px-2.5 py-1 text-xs text-muted hover:bg-card hover:text-foreground"
      >
        Actions&nbsp;&#9662;
      </button>
      {open && (
        <div className="absolute right-0 z-40 mt-1 w-44 rounded-md border border-border bg-card shadow-lg">
          <button
            onClick={() => { setOpen(false); onEdit(); }}
            className="block w-full px-4 py-2 text-left text-sm text-foreground hover:bg-primary/10"
          >
            Edit
          </button>
          {pkg.is_deprecated ? (
            <button
              onClick={() => { setOpen(false); onUndeprecate(); }}
              className="block w-full px-4 py-2 text-left text-sm text-success hover:bg-success/10"
            >
              Undeprecate
            </button>
          ) : (
            <button
              onClick={() => { setOpen(false); onDeprecate(); }}
              className="block w-full px-4 py-2 text-left text-sm text-warning hover:bg-warning/10"
            >
              Deprecate
            </button>
          )}
          <button
            onClick={() => { setOpen(false); onQuarantine(); }}
            className="block w-full px-4 py-2 text-left text-sm text-warning hover:bg-warning/10"
          >
            Quarantine Version
          </button>
          <div className="border-t border-border" />
          <button
            onClick={() => { setOpen(false); onDelete(); }}
            className="block w-full px-4 py-2 text-left text-sm text-danger hover:bg-danger/10"
          >
            Delete Package
          </button>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main Page                                                          */
/* ------------------------------------------------------------------ */

export default function AdminPackagesPage() {
  const [packages, setPackages] = useState<PackageItem[]>([]);
  const [quarantined, setQuarantined] = useState<QuarantinedVersion[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [tab, setTab] = useState<"all" | "quarantine">("all");

  // Quarantine queue action confirm (clear / reject)
  const [confirmAction, setConfirmAction] = useState<{
    slug: string;
    version: string;
    action: "clear" | "reject";
  } | null>(null);

  // Delete package dialog
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);

  // Quarantine-from-list modal
  const [quarantineTarget, setQuarantineTarget] = useState<string | null>(null);

  // Delete version dialog
  const [deleteVersionTarget, setDeleteVersionTarget] = useState<{
    slug: string;
    version: string;
  } | null>(null);

  // Inline editing
  const [editingSlug, setEditingSlug] = useState<string | null>(null);

  useEffect(() => {
    loadData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, search]);

  // Auto-dismiss success messages
  useEffect(() => {
    if (!success) return;
    const t = setTimeout(() => setSuccess(""), 5000);
    return () => clearTimeout(t);
  }, [success]);

  async function loadData() {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(page), per_page: "50" });
      if (search) params.set("search", search);
      const [pkgRes, qRes] = await Promise.all([
        fetchWithAuth(`/admin/packages?${params}`),
        fetchWithAuth("/admin/quarantined"),
      ]);
      if (pkgRes.ok) {
        const d = await pkgRes.json();
        setPackages(d.packages);
        setTotal(d.total);
      }
      if (qRes.ok) {
        setQuarantined(await qRes.json());
      }
    } catch {
      setError("Failed to load data");
    } finally {
      setLoading(false);
    }
  }

  /* ---- Quarantine queue: clear / reject ---- */

  async function executeQuarantineAction() {
    if (!confirmAction) return;
    setError("");
    setSuccess("");
    const { slug, version, action } = confirmAction;
    const res = await fetchWithAuth(
      `/admin/packages/${slug}/versions/${version}/${action}`,
      { method: "POST" }
    );
    if (res.ok) {
      setSuccess(
        action === "clear"
          ? `Quarantine cleared: ${slug}@${version}`
          : `Rejected: ${slug}@${version}`
      );
      await loadData();
    } else {
      const d = await res.json().catch(() => ({ error: { message: "Failed" } }));
      setError(d.error?.message || "Failed");
    }
    setConfirmAction(null);
  }

  /* ---- Delete package ---- */

  async function handleDeletePackage() {
    if (!deleteTarget) return;
    setError("");
    setSuccess("");
    const res = await fetchWithAuth(`/admin/packages/${deleteTarget}`, {
      method: "DELETE",
    });
    if (res.ok) {
      setSuccess(`Deleted package: ${deleteTarget}`);
      await loadData();
    } else {
      const d = await res.json().catch(() => ({ error: { message: "Delete failed" } }));
      setError(d.error?.message || "Delete failed");
    }
    setDeleteTarget(null);
  }

  /* ---- Deprecate / Undeprecate ---- */

  async function handleDeprecate(slug: string) {
    setError("");
    setSuccess("");
    const res = await fetchWithAuth(`/admin/packages/${slug}/deprecate`, {
      method: "POST",
    });
    if (res.ok) {
      setSuccess(`Deprecated: ${slug}`);
      await loadData();
    } else {
      const d = await res.json().catch(() => ({ error: { message: "Deprecate failed" } }));
      setError(d.error?.message || "Deprecate failed");
    }
  }

  async function handleUndeprecate(slug: string) {
    setError("");
    setSuccess("");
    const res = await fetchWithAuth(`/admin/packages/${slug}/undeprecate`, {
      method: "POST",
    });
    if (res.ok) {
      setSuccess(`Undeprecated: ${slug}`);
      await loadData();
    } else {
      const d = await res.json().catch(() => ({ error: { message: "Undeprecate failed" } }));
      setError(d.error?.message || "Undeprecate failed");
    }
  }

  /* ---- Edit package ---- */

  async function handleEditSave(slug: string, name: string, summary: string) {
    setError("");
    setSuccess("");
    const res = await fetchWithAuth(`/admin/packages/${slug}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, summary }),
    });
    if (res.ok) {
      setSuccess(`Updated: ${slug}`);
      setEditingSlug(null);
      await loadData();
    } else {
      const d = await res.json().catch(() => ({ error: { message: "Update failed" } }));
      setError(d.error?.message || "Update failed");
    }
  }

  /* ---- Quarantine from list ---- */

  async function handleQuarantineVersion(slug: string, version: string, reason: string) {
    setError("");
    setSuccess("");
    const res = await fetchWithAuth(
      `/admin/packages/${slug}/versions/${version}/quarantine`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason }),
      }
    );
    if (res.ok) {
      setSuccess(`Quarantined: ${slug}@${version}`);
      await loadData();
    } else {
      const d = await res.json().catch(() => ({ error: { message: "Quarantine failed" } }));
      setError(d.error?.message || "Quarantine failed");
    }
    setQuarantineTarget(null);
  }

  /* ---- Delete version ---- */

  async function handleDeleteVersion() {
    if (!deleteVersionTarget) return;
    setError("");
    setSuccess("");
    const { slug, version } = deleteVersionTarget;
    const res = await fetchWithAuth(
      `/admin/packages/${slug}/versions/${version}`,
      { method: "DELETE" }
    );
    if (res.ok) {
      setSuccess(`Deleted version: ${slug}@${version}`);
      await loadData();
    } else {
      const d = await res.json().catch(() => ({ error: { message: "Delete version failed" } }));
      setError(d.error?.message || "Delete version failed");
    }
    setDeleteVersionTarget(null);
  }

  const totalPages = Math.ceil(total / 50);

  return (
    <div>
      {/* ---- Modals ---- */}
      {deleteTarget && (
        <ConfirmDeleteDialog
          slug={deleteTarget}
          onConfirm={handleDeletePackage}
          onCancel={() => setDeleteTarget(null)}
        />
      )}
      {quarantineTarget && (
        <QuarantineModal
          slug={quarantineTarget}
          onConfirm={(v, r) => handleQuarantineVersion(quarantineTarget, v, r)}
          onCancel={() => setQuarantineTarget(null)}
        />
      )}
      {deleteVersionTarget && (
        <DeleteVersionDialog
          slug={deleteVersionTarget.slug}
          version={deleteVersionTarget.version}
          onConfirm={handleDeleteVersion}
          onCancel={() => setDeleteVersionTarget(null)}
        />
      )}

      {/* ---- Header ---- */}
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-bold text-foreground">Packages ({total})</h1>
        {quarantined.length > 0 && (
          <span className="rounded-full bg-danger/20 px-3 py-1 text-xs font-medium text-danger">
            {quarantined.length} quarantined
          </span>
        )}
      </div>

      {/* ---- Alerts ---- */}
      {error && (
        <div className="mb-4 rounded-md border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">
          {error}{" "}
          <button onClick={() => setError("")} className="ml-2 underline">
            dismiss
          </button>
        </div>
      )}
      {success && (
        <div className="mb-4 rounded-md border border-success/30 bg-success/10 px-4 py-3 text-sm text-success">
          {success}{" "}
          <button onClick={() => setSuccess("")} className="ml-2 underline">
            dismiss
          </button>
        </div>
      )}

      {/* Confirmation dialog for quarantine queue clear/reject */}
      {confirmAction && (
        <div
          className={`mb-4 rounded-lg border p-4 ${
            confirmAction.action === "reject"
              ? "border-danger/30 bg-danger/5"
              : "border-success/30 bg-success/5"
          }`}
        >
          <p className="text-sm text-foreground">
            {confirmAction.action === "clear" ? (
              <>
                Clear quarantine for{" "}
                <span className="font-mono font-medium">
                  {confirmAction.slug}@{confirmAction.version}
                </span>
                ? This will make the version available again.
              </>
            ) : (
              <>
                Reject{" "}
                <span className="font-mono font-medium">
                  {confirmAction.slug}@{confirmAction.version}
                </span>
                ? This version will be permanently rejected.
              </>
            )}
          </p>
          <div className="mt-3 flex gap-2">
            <button
              onClick={executeQuarantineAction}
              className={`rounded px-4 py-1.5 text-sm font-medium text-white ${
                confirmAction.action === "clear"
                  ? "bg-success hover:bg-success/90"
                  : "bg-danger hover:bg-danger/90"
              }`}
            >
              Confirm {confirmAction.action === "clear" ? "Clear" : "Reject"}
            </button>
            <button
              onClick={() => setConfirmAction(null)}
              className="rounded border border-border px-4 py-1.5 text-sm text-muted hover:bg-card"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* ---- Tabs ---- */}
      <div className="mb-4 flex gap-1 border-b border-border">
        <button
          onClick={() => setTab("all")}
          className={`px-4 py-2 text-sm border-b-2 transition-colors ${
            tab === "all"
              ? "border-primary text-foreground"
              : "border-transparent text-muted hover:text-foreground"
          }`}
        >
          All Packages
        </button>
        <button
          onClick={() => setTab("quarantine")}
          className={`px-4 py-2 text-sm border-b-2 transition-colors ${
            tab === "quarantine"
              ? "border-primary text-foreground"
              : "border-transparent text-muted hover:text-foreground"
          }`}
        >
          Quarantine Queue {quarantined.length > 0 && `(${quarantined.length})`}
        </button>
      </div>

      {/* ============================================================ */}
      {/*  ALL PACKAGES TAB                                            */}
      {/* ============================================================ */}
      {tab === "all" ? (
        <>
          <input
            type="text"
            placeholder="Search packages..."
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
            className="mb-4 w-full rounded-md border border-border bg-card px-4 py-2 text-sm text-foreground placeholder:text-muted/50 focus:border-primary focus:outline-none"
          />
          <div className="rounded-lg border border-border bg-card overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs text-muted">
                  <th className="px-4 py-2.5">Slug</th>
                  <th className="px-4 py-2.5">Name</th>
                  <th className="px-4 py-2.5">Type</th>
                  <th className="px-4 py-2.5">Publisher</th>
                  <th className="px-4 py-2.5 text-right">Downloads</th>
                  <th className="px-4 py-2.5">Created</th>
                  <th className="px-4 py-2.5 text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr>
                    <td colSpan={7} className="px-4 py-8 text-center text-muted">
                      Loading...
                    </td>
                  </tr>
                ) : packages.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="px-4 py-8 text-center text-muted">
                      No packages found.
                    </td>
                  </tr>
                ) : (
                  packages.map((p) =>
                    editingSlug === p.slug ? (
                      <InlineEditRow
                        key={p.id}
                        pkg={p}
                        onSave={(name, summary) => handleEditSave(p.slug, name, summary)}
                        onCancel={() => setEditingSlug(null)}
                      />
                    ) : (
                      <tr
                        key={p.id}
                        className="border-b border-border/50 last:border-0 hover:bg-card/80"
                      >
                        <td className="px-4 py-2.5">
                          <Link
                            href={`/packages/${p.slug}`}
                            className="font-medium text-primary hover:underline"
                          >
                            {p.slug}
                          </Link>
                          {p.is_deprecated && (
                            <span className="ml-2 rounded bg-danger/20 px-1.5 py-0.5 text-xs text-danger">
                              deprecated
                            </span>
                          )}
                        </td>
                        <td className="px-4 py-2.5 text-foreground">{p.name}</td>
                        <td className="px-4 py-2.5">
                          <span className="rounded bg-primary/10 px-2 py-0.5 text-xs text-primary">
                            {p.package_type}
                          </span>
                        </td>
                        <td className="px-4 py-2.5">
                          {p.publisher_slug ? (
                            <Link
                              href="/admin/publishers"
                              className="text-primary text-xs hover:underline"
                            >
                              @{p.publisher_slug}
                            </Link>
                          ) : (
                            <span className="text-muted text-xs">-</span>
                          )}
                        </td>
                        <td className="px-4 py-2.5 text-right font-mono text-foreground">
                          {p.download_count.toLocaleString()}
                        </td>
                        <td className="px-4 py-2.5 text-muted text-xs">
                          {p.created_at
                            ? new Date(p.created_at).toLocaleDateString()
                            : "-"}
                        </td>
                        <td className="px-4 py-2.5 text-right">
                          <ActionsDropdown
                            pkg={p}
                            onEdit={() => setEditingSlug(p.slug)}
                            onDelete={() => setDeleteTarget(p.slug)}
                            onDeprecate={() => handleDeprecate(p.slug)}
                            onUndeprecate={() => handleUndeprecate(p.slug)}
                            onQuarantine={() => setQuarantineTarget(p.slug)}
                          />
                        </td>
                      </tr>
                    )
                  )
                )}
              </tbody>
            </table>
          </div>
          {totalPages > 1 && (
            <div className="mt-4 flex items-center justify-between text-sm text-muted">
              <span>
                Page {page} of {totalPages}
              </span>
              <div className="flex gap-2">
                <button
                  onClick={() => setPage(Math.max(1, page - 1))}
                  disabled={page <= 1}
                  className="rounded border border-border px-3 py-1 hover:bg-card disabled:opacity-50"
                >
                  Prev
                </button>
                <button
                  onClick={() => setPage(Math.min(totalPages, page + 1))}
                  disabled={page >= totalPages}
                  className="rounded border border-border px-3 py-1 hover:bg-card disabled:opacity-50"
                >
                  Next
                </button>
              </div>
            </div>
          )}
        </>
      ) : (
        /* ============================================================ */
        /*  QUARANTINE QUEUE TAB                                        */
        /* ============================================================ */
        <div className="space-y-3">
          {quarantined.length === 0 ? (
            <div className="rounded-lg border border-border bg-card p-8 text-center text-sm text-muted">
              No quarantined versions.
            </div>
          ) : (
            quarantined.map((v) => (
              <div
                key={`${v.package_slug}-${v.version_number}`}
                className="flex items-center justify-between rounded-lg border border-border bg-card px-4 py-3"
              >
                <div>
                  <Link
                    href={`/packages/${v.package_slug}`}
                    className="font-medium text-primary hover:underline"
                  >
                    {v.package_slug}@{v.version_number}
                  </Link>
                  {v.quarantine_reason && (
                    <span className="ml-2 text-sm text-muted">
                      ({v.quarantine_reason})
                    </span>
                  )}
                  <div className="text-xs text-muted">
                    {v.quarantined_at
                      ? new Date(v.quarantined_at).toLocaleString()
                      : ""}
                  </div>
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={() =>
                      setConfirmAction({
                        slug: v.package_slug,
                        version: v.version_number,
                        action: "clear",
                      })
                    }
                    className="rounded bg-success/20 px-3 py-1 text-xs font-medium text-success hover:bg-success/30"
                  >
                    Clear
                  </button>
                  <button
                    onClick={() =>
                      setConfirmAction({
                        slug: v.package_slug,
                        version: v.version_number,
                        action: "reject",
                      })
                    }
                    className="rounded bg-danger/20 px-3 py-1 text-xs font-medium text-danger hover:bg-danger/30"
                  >
                    Reject
                  </button>
                  <button
                    onClick={() =>
                      setDeleteVersionTarget({
                        slug: v.package_slug,
                        version: v.version_number,
                      })
                    }
                    className="rounded bg-danger/20 px-3 py-1 text-xs font-medium text-danger hover:bg-danger/30"
                  >
                    Delete Version
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
