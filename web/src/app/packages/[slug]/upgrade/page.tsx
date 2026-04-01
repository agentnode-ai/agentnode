"use client";

import { useState, useEffect, useCallback, Suspense } from "react";
import Link from "next/link";
import { useRouter, useParams } from "next/navigation";
import { fetchWithAuth, search } from "@/lib/api";

import type { GuidedState, ToolEntry, CodeFile, CapabilityOption } from "@/app/publish/lib/types";
import { SLUG_PATTERN, EMPTY_TOOL, DEFAULT_GUIDED } from "@/app/publish/lib/constants";
import { slugify, isValidSemver, buildManifestFromGuided } from "@/app/publish/lib/manifest";
import { computeReadiness } from "@/app/publish/lib/readiness";
import { CollapsiblePanel } from "@/app/publish/components/CollapsiblePanel";
import { CapabilityDropdown } from "@/app/publish/components/CapabilityDropdown";

/* ------------------------------------------------------------------ */
/*  Upgrade page — create an upgrade package for a specific package    */
/* ------------------------------------------------------------------ */

function UpgradeContent() {
  const router = useRouter();
  const params = useParams();
  const parentSlug = params.slug as string;

  /* ---- Auth ---- */
  const [user, setUser] = useState<{ email: string; publisher?: { slug: string; display_name: string } } | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [isOwner, setIsOwner] = useState(false);
  const [parentPackage, setParentPackage] = useState<{ name: string; slug: string; publisher_slug: string; package_type: string } | null>(null);
  const [loading, setLoading] = useState(true);

  /* ---- Form state ---- */
  const [guided, setGuided] = useState<GuidedState>({
    ...DEFAULT_GUIDED,
    package_type: "upgrade",
    upgrade_recommended_for: parentSlug,
  });
  const [codeFiles, setCodeFiles] = useState<CodeFile[]>([{ path: "main.py", content: "" }]);
  const [capabilities, setCapabilities] = useState<CapabilityOption[]>([]);
  const [openPanels, setOpenPanels] = useState<Set<string>>(new Set(["upgrade", "basics", "tools"]));
  const [toolAdvanced, setToolAdvanced] = useState<Set<number>>(new Set());

  /* ---- Publish state ---- */
  const [publishing, setPublishing] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  /* ---- Helpers ---- */
  const updateGuided = useCallback(<K extends keyof GuidedState>(key: K, value: GuidedState[K]) => {
    setGuided((prev) => ({ ...prev, [key]: value }));
  }, []);

  const togglePanel = useCallback((panel: string) => {
    setOpenPanels((prev) => {
      const next = new Set(prev);
      if (next.has(panel)) next.delete(panel);
      else next.add(panel);
      return next;
    });
  }, []);

  /* ---- Auth + ownership check ---- */
  useEffect(() => {
    let cancelled = false;
    async function init() {
      try {
        // Check auth
        const authRes = await fetchWithAuth("/auth/me");
        if (!authRes.ok) { setAuthChecked(true); setLoading(false); return; }
        const userData = await authRes.json();
        if (!cancelled) setUser(userData);

        // Fetch parent package info
        const pkgRes = await fetch(`/api/v1/packages/${encodeURIComponent(parentSlug)}`);
        if (!pkgRes.ok) { setLoading(false); setAuthChecked(true); return; }
        const pkgData = await pkgRes.json();
        if (!cancelled) {
          setParentPackage({
            name: pkgData.name,
            slug: pkgData.slug,
            publisher_slug: pkgData.publisher?.slug || "",
            package_type: pkgData.package_type || "toolpack",
          });
          // Guard against undefined === undefined false-positive
          setIsOwner(
            !!userData.publisher?.slug &&
            !!pkgData.publisher?.slug &&
            userData.publisher.slug === pkgData.publisher.slug
          );
        }
      } catch {
        // Not logged in
      } finally {
        if (!cancelled) { setAuthChecked(true); setLoading(false); }
      }
    }
    init();
    return () => { cancelled = true; };
  }, [parentSlug]);

  // Load capabilities
  useEffect(() => {
    fetch("/api/v1/resolution/capabilities")
      .then((r) => (r.ok ? r.json() : []))
      .then((data: CapabilityOption[]) => { if (Array.isArray(data)) setCapabilities(data); })
      .catch(() => {});
  }, []);

  /* ---- Publish handler ---- */
  async function handlePublish() {
    setPublishing(true);
    setError("");
    setSuccess("");
    try {
      const manifest = buildManifestFromGuided(guided, user!.publisher!.slug);
      const formData = new FormData();
      formData.append("manifest", JSON.stringify(manifest));

      // Build artifact from code files if any have content
      const hasCode = codeFiles.some((f) => f.content.trim());
      if (hasCode) {
        const artifactRes = await fetchWithAuth("/builder/artifact", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            package_id: guided.package_id,
            manifest_json: manifest,
            code_files: codeFiles.filter((f) => f.content.trim()),
          }),
        });
        if (!artifactRes.ok) {
          const errData = await artifactRes.json().catch(() => ({}));
          throw new Error(errData.error?.message || errData.detail || "Failed to build artifact from code files");
        }
        const blob = await artifactRes.blob();
        formData.append("artifact", blob, `${guided.package_id}.tar.gz`);
      }

      const res = await fetchWithAuth("/packages/publish", {
        method: "POST",
        body: formData,
      });

      if (res.ok) {
        const data = await res.json();
        const newSlug = data.slug || guided.package_id;
        setSuccess(`Upgrade published! Redirecting to ${newSlug}...`);
        setTimeout(() => router.push(`/packages/${newSlug}`), 3000);
      } else {
        const data = await res.json().catch(() => ({}));
        setError(data.error?.message || data.detail || "Failed to publish upgrade");
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Network error — please try again");
    } finally {
      setPublishing(false);
    }
  }

  /* ---- Loading / auth gates ---- */
  if (loading) {
    return (
      <div className="mx-auto max-w-3xl px-4 sm:px-6 py-20 text-center">
        <div className="animate-pulse text-muted">Loading...</div>
      </div>
    );
  }

  if (!authChecked || !user) {
    return (
      <div className="mx-auto max-w-3xl px-4 sm:px-6 py-20 text-center space-y-4">
        <h1 className="text-2xl font-bold text-foreground">Sign in required</h1>
        <p className="text-muted">You must be signed in to create an upgrade package.</p>
        <Link
          href={`/auth/login?returnTo=${encodeURIComponent(`/packages/${parentSlug}/upgrade`)}`}
          className="inline-block rounded-lg bg-primary px-6 py-2.5 text-sm font-medium text-white hover:bg-primary/90"
        >
          Sign in
        </Link>
      </div>
    );
  }

  if (!user.publisher) {
    return (
      <div className="mx-auto max-w-3xl px-4 sm:px-6 py-20 text-center space-y-4">
        <h1 className="text-2xl font-bold text-foreground">Publisher profile required</h1>
        <p className="text-muted">You need a publisher profile to create upgrade packages. Create one from your dashboard first.</p>
        <Link
          href="/dashboard"
          className="inline-block rounded-lg bg-primary px-6 py-2.5 text-sm font-medium text-white hover:bg-primary/90"
        >
          Go to dashboard
        </Link>
      </div>
    );
  }

  if (!isOwner) {
    return (
      <div className="mx-auto max-w-3xl px-4 sm:px-6 py-20 text-center space-y-4">
        <h1 className="text-2xl font-bold text-foreground">Not authorized</h1>
        <p className="text-muted">Only the package owner can create upgrades for this package.</p>
        <Link
          href={`/packages/${parentSlug}`}
          className="inline-block rounded-lg border border-border px-6 py-2.5 text-sm font-medium text-foreground hover:bg-card"
        >
          Back to package
        </Link>
      </div>
    );
  }

  if (parentPackage && parentPackage.package_type !== "toolpack") {
    return (
      <div className="mx-auto max-w-3xl px-4 sm:px-6 py-20 text-center space-y-4">
        <h1 className="text-2xl font-bold text-foreground">Not supported</h1>
        <p className="text-muted">Upgrades can only be created for toolpack packages.</p>
        <Link
          href={`/packages/${parentSlug}`}
          className="inline-block rounded-lg border border-border px-6 py-2.5 text-sm font-medium text-foreground hover:bg-card"
        >
          Back to package
        </Link>
      </div>
    );
  }

  /* ---- Readiness ---- */
  const hasCodeContent = codeFiles.some((f) => f.content.trim());
  const { canPublish, items } = computeReadiness(guided, false, null, codeFiles);

  /* ---- Tool CRUD ---- */
  function addTool() {
    updateGuided("tools", [...guided.tools, { ...EMPTY_TOOL }]);
  }
  function removeTool(idx: number) {
    const next = guided.tools.filter((_, i) => i !== idx);
    updateGuided("tools", next.length ? next : [{ ...EMPTY_TOOL }]);
  }
  function updateTool(idx: number, key: keyof ToolEntry, value: string) {
    const next = [...guided.tools];
    next[idx] = { ...next[idx], [key]: value };
    updateGuided("tools", next);
  }

  return (
    <div className="mx-auto max-w-3xl px-4 sm:px-6 py-12">
      {/* Breadcrumb */}
      <nav className="mb-4 text-xs text-muted">
        <Link href="/search" className="hover:text-foreground transition-colors">Packages</Link>
        <span className="mx-1.5">/</span>
        <Link href={`/packages/${parentSlug}`} className="hover:text-foreground transition-colors">{parentPackage?.name || parentSlug}</Link>
        <span className="mx-1.5">/</span>
        <span className="text-foreground">Create upgrade</span>
      </nav>

      {/* Header */}
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Create upgrade</h1>
          <p className="text-sm text-muted">
            An upgrade extends <Link href={`/packages/${parentSlug}`} className="text-primary hover:underline font-medium">{parentPackage?.name || parentSlug}</Link> with new capabilities. This creates a separate package that enhances the original.
          </p>
        </div>
        <Link
          href={`/packages/${parentSlug}`}
          className="text-sm text-muted hover:text-foreground transition-colors"
        >
          &larr; Back to {parentPackage?.name || parentSlug}
        </Link>
      </div>

      {/* Error / Success */}
      {error && (
        <div className="mb-4 rounded-md border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">
          {error}
        </div>
      )}
      {success && (
        <div className="mb-4 rounded-md border border-green-500/30 bg-green-500/10 px-4 py-3 text-sm text-green-400">
          {success}
        </div>
      )}

      {/* Panels */}
      <div className="space-y-3 mb-6">

        {/* Upgrade Configuration */}
        <CollapsiblePanel
          title="Upgrade Configuration"
          subtitle={`Extends ${parentSlug}`}
          open={openPanels.has("upgrade")}
          onToggle={() => togglePanel("upgrade")}
        >
          <div className="space-y-4">
            <div>
              <label className="mb-1 block text-sm font-medium text-foreground">
                Extends package <span className="text-danger">*</span>
              </label>
              <input
                type="text"
                value={guided.upgrade_recommended_for}
                readOnly
                className="w-full rounded-md border border-border bg-muted/10 px-3 py-2.5 font-mono text-sm text-muted cursor-not-allowed"
              />
              <p className="mt-1 text-xs text-muted">This upgrade enhances the package above.</p>
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium text-foreground">
                Replaces <span className="text-xs text-muted font-normal">(optional — slug of a package this replaces)</span>
              </label>
              <input
                type="text"
                value={guided.upgrade_replaces}
                onChange={(e) => updateGuided("upgrade_replaces", e.target.value.toLowerCase().replace(/[^a-z0-9-,\s]/g, ""))}
                className="w-full rounded-md border border-border bg-background px-3 py-2.5 font-mono text-sm text-foreground focus:border-primary focus:outline-none"
                placeholder="old-package-slug"
              />
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label className="mb-1 block text-sm font-medium text-foreground">
                  Roles <span className="text-xs text-muted font-normal">(comma-separated)</span>
                </label>
                <input
                  type="text"
                  value={guided.upgrade_roles}
                  onChange={(e) => updateGuided("upgrade_roles", e.target.value)}
                  className="w-full rounded-md border border-border bg-background px-3 py-2.5 text-sm text-foreground focus:border-primary focus:outline-none"
                  placeholder="enhancer, optimizer"
                />
              </div>
              <div>
                <label className="mb-1 block text-sm font-medium text-foreground">Install strategy</label>
                <select
                  value={guided.upgrade_install_strategy}
                  onChange={(e) => updateGuided("upgrade_install_strategy", e.target.value)}
                  className="w-full rounded-md border border-border bg-background px-3 py-2.5 text-sm text-foreground focus:border-primary focus:outline-none"
                >
                  <option value="local">local</option>
                  <option value="global">global</option>
                  <option value="managed">managed</option>
                </select>
              </div>
            </div>
          </div>
        </CollapsiblePanel>

        {/* Basics */}
        <CollapsiblePanel
          title="Basics"
          subtitle={guided.name || "Name, version, summary"}
          open={openPanels.has("basics")}
          onToggle={() => togglePanel("basics")}
        >
          <div className="space-y-4">
            <div>
              <label className="mb-1 block text-sm font-medium text-foreground">Name <span className="text-danger">*</span></label>
              <input
                type="text"
                value={guided.name}
                onChange={(e) => {
                  updateGuided("name", e.target.value);
                  const autoSlug = slugify(guided.name);
                  if (!guided.package_id || guided.package_id === autoSlug) {
                    updateGuided("package_id", slugify(e.target.value));
                  }
                }}
                className={`w-full rounded-md border bg-background px-3 py-2.5 text-foreground focus:border-primary focus:outline-none ${guided.name && guided.name.trim().length > 0 && guided.name.trim().length < 3 ? "border-danger" : "border-border"}`}
                placeholder={`${parentPackage?.name || parentSlug} Enhancer`}
              />
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium text-foreground">
                Package ID <span className="text-danger">*</span>
                <span className="ml-2 text-xs text-muted font-normal">a-z, 0-9, dashes</span>
              </label>
              <input
                type="text"
                value={guided.package_id}
                onChange={(e) => updateGuided("package_id", e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ""))}
                className="w-full rounded-md border border-border bg-background px-3 py-2.5 font-mono text-foreground focus:border-primary focus:outline-none"
                placeholder={`${parentSlug}-upgrade`}
              />
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium text-foreground">
                Version <span className="text-danger">*</span> <span className="text-xs text-muted font-normal">semver</span>
              </label>
              <input
                type="text"
                value={guided.version}
                onChange={(e) => updateGuided("version", e.target.value)}
                className="w-full rounded-md border border-border bg-background px-3 py-2.5 font-mono text-foreground focus:border-primary focus:outline-none"
                placeholder="1.0.0"
              />
            </div>
            <div>
              <label className="mb-1 flex items-center justify-between text-sm font-medium text-foreground">
                <span>Summary <span className="text-danger">*</span> <span className="text-xs text-muted font-normal">(min 20 characters)</span></span>
                <span className={`text-xs font-normal ${guided.summary.length > 200 ? "text-danger" : "text-muted"}`}>
                  {guided.summary.length}/200
                </span>
              </label>
              <textarea
                rows={2}
                value={guided.summary}
                onChange={(e) => updateGuided("summary", e.target.value)}
                className="w-full rounded-md border border-border bg-background px-3 py-2.5 text-foreground focus:border-primary focus:outline-none resize-none"
                placeholder={`Enhanced capabilities for ${parentPackage?.name || parentSlug}`}
              />
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium text-foreground">
                Description <span className="text-xs text-muted font-normal">(optional)</span>
              </label>
              <textarea
                rows={3}
                value={guided.description}
                onChange={(e) => updateGuided("description", e.target.value)}
                className="w-full rounded-md border border-border bg-background px-3 py-2.5 text-foreground focus:border-primary focus:outline-none resize-y"
                placeholder="Detailed description of what this upgrade adds..."
              />
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium text-foreground">
                Tags <span className="text-xs text-muted font-normal">(comma-separated)</span>
              </label>
              <input
                type="text"
                value={guided.tags}
                onChange={(e) => updateGuided("tags", e.target.value)}
                className="w-full rounded-md border border-border bg-background px-3 py-2.5 text-foreground focus:border-primary focus:outline-none"
                placeholder="upgrade, enhancement"
              />
            </div>
            <p className="text-xs text-muted"><span className="text-danger">*</span> Required field</p>
          </div>
        </CollapsiblePanel>

        {/* Code */}
        <CollapsiblePanel
          title="Code (optional)"
          subtitle={hasCodeContent ? `${codeFiles.filter((f) => f.content.trim()).length} file(s)` : "Add your upgrade implementation"}
          open={openPanels.has("code")}
          onToggle={() => togglePanel("code")}
        >
          <div className="space-y-3">
            {codeFiles.map((file, i) => (
              <div key={i} className="rounded-lg border border-border bg-background p-3">
                <div className="mb-2 flex items-center gap-2">
                  <input
                    type="text"
                    value={file.path}
                    onChange={(e) => {
                      const next = [...codeFiles];
                      next[i] = { ...next[i], path: e.target.value };
                      setCodeFiles(next);
                    }}
                    className="flex-1 rounded border border-border bg-card px-2 py-1 text-xs font-mono text-foreground focus:border-primary focus:outline-none"
                    placeholder="main.py"
                  />
                  {codeFiles.length > 1 && (
                    <button
                      onClick={() => setCodeFiles(codeFiles.filter((_, j) => j !== i))}
                      className="text-xs text-muted hover:text-danger"
                    >
                      Remove
                    </button>
                  )}
                </div>
                <textarea
                  rows={10}
                  value={file.content}
                  onChange={(e) => {
                    const next = [...codeFiles];
                    next[i] = { ...next[i], content: e.target.value };
                    setCodeFiles(next);
                  }}
                  className="w-full rounded border border-border bg-card px-3 py-2 text-xs font-mono text-foreground focus:border-primary focus:outline-none resize-y"
                  placeholder="# Your upgrade code here..."
                />
              </div>
            ))}
            <button
              onClick={() => setCodeFiles([...codeFiles, { path: "", content: "" }])}
              className="w-full rounded-lg border border-dashed border-border py-2 text-xs text-muted hover:text-foreground hover:border-primary/30 transition-colors"
            >
              + Add file
            </button>
          </div>
        </CollapsiblePanel>

        {/* Tools */}
        <CollapsiblePanel
          title="Tools"
          subtitle={`${guided.tools.filter((t) => t.name).length} tool(s)`}
          open={openPanels.has("tools")}
          onToggle={() => togglePanel("tools")}
        >
          <div className="space-y-4">
            {guided.tools.map((tool, i) => (
              <div key={i} className="rounded-lg border border-border bg-background p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium text-muted">Tool {i + 1}</span>
                  {guided.tools.length > 1 && (
                    <button onClick={() => removeTool(i)} className="text-xs text-muted hover:text-danger">Remove</button>
                  )}
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <div>
                    <label className="mb-1 block text-xs text-muted">Name</label>
                    <input
                      type="text"
                      value={tool.name}
                      onChange={(e) => updateTool(i, "name", e.target.value)}
                      className="w-full rounded border border-border bg-card px-3 py-2 text-sm font-mono text-foreground focus:border-primary focus:outline-none"
                      placeholder="my_function"
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-xs text-muted">What does this tool do?</label>
                    <CapabilityDropdown
                      capabilities={capabilities}
                      value={tool.capability_id}
                      onChange={(val) => updateTool(i, "capability_id", val)}
                    />
                  </div>
                </div>
                <div>
                  <label className="mb-1 block text-xs text-muted">Description</label>
                  <input
                    type="text"
                    value={tool.description || ""}
                    onChange={(e) => updateTool(i, "description", e.target.value)}
                    className="w-full rounded border border-border bg-card px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
                    placeholder="What this tool does..."
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs text-muted">Entrypoint</label>
                  <input
                    type="text"
                    value={tool.entrypoint || ""}
                    onChange={(e) => updateTool(i, "entrypoint", e.target.value)}
                    className="w-full rounded border border-border bg-card px-3 py-2 text-sm font-mono text-foreground focus:border-primary focus:outline-none"
                    placeholder="module.path:function_name"
                  />
                </div>
              </div>
            ))}
            <button
              onClick={addTool}
              className="w-full rounded-lg border border-dashed border-border py-2 text-xs text-muted hover:text-foreground hover:border-primary/30 transition-colors"
            >
              + Add tool
            </button>
          </div>
        </CollapsiblePanel>
      </div>

      {/* Readiness + Publish */}
      <div className="space-y-4">
        <div className="rounded-lg border border-border bg-card p-4">
          <h3 className="mb-3 text-sm font-medium text-foreground">Readiness checklist</h3>
          <ul className="space-y-2">
            {items.map((item) => (
              <li key={item.label} className="flex items-center gap-2">
                <span
                  className={`inline-block h-4 w-4 rounded-full text-center text-[10px] leading-4 font-bold ${
                    item.ok
                      ? "bg-green-500/20 text-green-400"
                      : item.required
                      ? "bg-red-500/20 text-red-400"
                      : "bg-muted/20 text-muted"
                  }`}
                >
                  {item.ok ? "\u2713" : item.required ? "!" : "\u2013"}
                </span>
                <span className={`text-sm ${item.ok ? "text-muted" : item.required ? "text-foreground" : "text-muted"}`}>
                  {item.label}
                  {item.required && <span className="text-danger ml-0.5">*</span>}
                </span>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-xs text-muted"><span className="text-danger">*</span> Required field</p>
        </div>

        <div className="flex items-center justify-between">
          <Link
            href={`/packages/${parentSlug}`}
            className="text-sm text-muted hover:text-foreground transition-colors"
          >
            &larr; Back to {parentPackage?.name || parentSlug}
          </Link>
          <span className="text-xs text-muted hidden sm:inline">Publishing as @{user?.publisher?.slug}</span>
          <button
            onClick={handlePublish}
            disabled={!canPublish || publishing}
            className="rounded-lg bg-primary px-8 py-3 text-sm font-semibold text-white hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {publishing ? "Publishing..." : "Publish upgrade"}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function UpgradePage() {
  return (
    <Suspense fallback={
      <div className="mx-auto max-w-3xl px-4 sm:px-6 py-20 text-center">
        <div className="animate-pulse text-muted">Loading...</div>
      </div>
    }>
      <UpgradeContent />
    </Suspense>
  );
}
