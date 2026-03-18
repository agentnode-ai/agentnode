"use client";

import { useState, useEffect, useCallback, useRef, Suspense } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { fetchWithAuth } from "@/lib/api";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface UserInfo {
  id: string;
  username: string;
  publisher?: { slug: string; display_name: string } | null;
}

interface ToolEntry {
  name: string;
  description: string;
  capability_id: string;
  entrypoint: string;
  input_schema: string;
  output_schema: string;
}

interface CodeFile {
  path: string;
  content: string;
}

interface GuidedState {
  name: string;
  package_id: string;
  package_type: "toolpack" | "agent" | "upgrade";
  version: string;
  summary: string;
  description: string;
  tools: ToolEntry[];
  frameworks: string[];
  network: string;
  filesystem: string;
  code_execution: string;
  data_access: string;
  user_approval: string;
  tags: string;
}

interface ValidationResult {
  valid: boolean;
  errors: string[];
  warnings: string[];
}

interface CapabilityOption {
  id: string;
  name: string;
  category: string;
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function slugify(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, "")
    .replace(/\s+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 60);
}

function isValidSemver(v: string): boolean {
  return /^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?(\+[a-zA-Z0-9.]+)?$/.test(v);
}

const SLUG_PATTERN = /^[a-z0-9-]{3,60}$/;

const EMPTY_TOOL: ToolEntry = {
  name: "",
  description: "",
  capability_id: "",
  entrypoint: "",
  input_schema: "",
  output_schema: "",
};

const DEFAULT_GUIDED: GuidedState = {
  name: "",
  package_id: "",
  package_type: "toolpack",
  version: "1.0.0",
  summary: "",
  description: "",
  tools: [{ ...EMPTY_TOOL }],
  frameworks: ["generic"],
  network: "none",
  filesystem: "none",
  code_execution: "none",
  data_access: "input_only",
  user_approval: "never",
  tags: "",
};

function buildManifestFromGuided(g: GuidedState, publisherSlug: string): Record<string, unknown> {
  const tools = g.tools.map((t) => {
    const tool: Record<string, unknown> = {
      name: t.name,
      description: t.description,
      capability_id: t.capability_id,
    };
    if (t.entrypoint) tool.entrypoint = t.entrypoint;
    if (t.input_schema.trim()) {
      try { tool.input_schema = JSON.parse(t.input_schema); } catch { /* skip */ }
    }
    if (t.output_schema.trim()) {
      try { tool.output_schema = JSON.parse(t.output_schema); } catch { /* skip */ }
    }
    return tool;
  });

  const manifest: Record<string, unknown> = {
    manifest_version: "0.2",
    package_id: g.package_id,
    package_type: g.package_type,
    name: g.name,
    publisher: publisherSlug,
    version: g.version,
    summary: g.summary,
    runtime: "python",
    install_mode: "package",
    hosting_type: "agentnode_hosted",
    capabilities: { tools },
    compatibility: { frameworks: g.frameworks },
    permissions: {
      network: { level: g.network },
      filesystem: { level: g.filesystem },
      code_execution: { level: g.code_execution },
      data_access: { level: g.data_access },
      user_approval: { required: g.user_approval },
    },
  };

  if (g.description) manifest.description = g.description;
  if (g.tags.trim()) {
    manifest.tags = g.tags.split(",").map((t) => t.trim()).filter(Boolean);
  }

  return manifest;
}

function parseManifestToGuided(json: Record<string, unknown>): GuidedState {
  const g = { ...DEFAULT_GUIDED };

  if (typeof json.name === "string") g.name = json.name;
  if (typeof json.package_id === "string") g.package_id = json.package_id;
  if (json.package_type === "toolpack" || json.package_type === "agent" || json.package_type === "upgrade") {
    g.package_type = json.package_type;
  }
  if (typeof json.version === "string") g.version = json.version;
  if (typeof json.summary === "string") g.summary = json.summary;
  if (typeof json.description === "string") g.description = json.description;

  const caps = json.capabilities as Record<string, unknown> | undefined;
  if (caps && Array.isArray(caps.tools) && caps.tools.length > 0) {
    g.tools = caps.tools.map((t: Record<string, unknown>) => ({
      name: (t.name as string) || "",
      description: (t.description as string) || "",
      capability_id: (t.capability_id as string) || "",
      entrypoint: (t.entrypoint as string) || "",
      input_schema: t.input_schema ? JSON.stringify(t.input_schema, null, 2) : "",
      output_schema: t.output_schema ? JSON.stringify(t.output_schema, null, 2) : "",
    }));
  }

  const compat = json.compatibility as Record<string, unknown> | undefined;
  if (compat && Array.isArray(compat.frameworks) && compat.frameworks.length > 0) {
    g.frameworks = compat.frameworks as string[];
  }

  const perms = json.permissions as Record<string, unknown> | undefined;
  if (perms) {
    const net = perms.network as Record<string, string> | undefined;
    if (net?.level) g.network = net.level;
    const fs = perms.filesystem as Record<string, string> | undefined;
    if (fs?.level) g.filesystem = fs.level;
    const exec = perms.code_execution as Record<string, string> | undefined;
    if (exec?.level) g.code_execution = exec.level;
    const data = perms.data_access as Record<string, string> | undefined;
    if (data?.level) g.data_access = data.level;
    const approval = perms.user_approval as Record<string, string> | undefined;
    if (approval?.required) g.user_approval = approval.required;
  }

  if (Array.isArray(json.tags)) g.tags = json.tags.join(", ");

  return g;
}

/* ------------------------------------------------------------------ */
/*  Collapsible Panel                                                  */
/* ------------------------------------------------------------------ */

function CollapsiblePanel({
  title,
  subtitle,
  open,
  onToggle,
  children,
}: {
  title: string;
  subtitle?: string;
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-border overflow-hidden">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center justify-between px-5 py-4 text-left hover:bg-card/50 transition-colors"
      >
        <div className="min-w-0 pr-4">
          <div className="text-sm font-medium text-foreground">{title}</div>
          {subtitle && !open && (
            <div className="mt-0.5 text-xs text-muted truncate">{subtitle}</div>
          )}
        </div>
        <svg
          className={`h-4 w-4 shrink-0 text-muted transition-transform ${open ? "rotate-180" : ""}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {open && (
        <div className="border-t border-border px-5 py-5 space-y-4">{children}</div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Artifact Section                                                   */
/* ------------------------------------------------------------------ */

function ArtifactSection({
  artifactMode,
  onModeChange,
  codeFiles,
  onCodeFilesChange,
  uploadedFiles,
  onUploadedFilesChange,
  tarGzFile,
  onTarGzChange,
  builderArtifactName,
  onBuilderArtifactClear,
  packageId,
}: {
  artifactMode: "code" | "upload";
  onModeChange: (mode: "code" | "upload") => void;
  codeFiles: CodeFile[];
  onCodeFilesChange: (files: CodeFile[]) => void;
  uploadedFiles: File[];
  onUploadedFilesChange: (files: File[]) => void;
  tarGzFile: File | null;
  onTarGzChange: (f: File | null) => void;
  builderArtifactName?: string;
  onBuilderArtifactClear?: () => void;
  packageId?: string;
}) {
  const dropRef = useRef<HTMLDivElement>(null);
  const [dragOver, setDragOver] = useState(false);

  function updateCodeFile(index: number, field: "path" | "content", value: string) {
    const next = [...codeFiles];
    next[index] = { ...next[index], [field]: value };
    onCodeFilesChange(next);
  }

  function addCodeFile() {
    const moduleName = packageId ? packageId.replace(/-/g, "_") : "my_tool";
    const existingCount = codeFiles.length;
    const defaultPath = existingCount === 0
      ? `${moduleName}/tool.py`
      : `${moduleName}/helper_${existingCount}.py`;
    onCodeFilesChange([...codeFiles, { path: defaultPath, content: "" }]);
  }

  function removeCodeFile(index: number) {
    if (codeFiles.length <= 1) return;
    onCodeFilesChange(codeFiles.filter((_, i) => i !== index));
  }

  function handleFileDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragOver(false);
    processUploadedFiles(Array.from(e.dataTransfer.files));
  }

  function handleFileSelect(e: React.ChangeEvent<HTMLInputElement>) {
    processUploadedFiles(Array.from(e.target.files || []));
  }

  function processUploadedFiles(files: File[]) {
    if (files.length === 1 && (files[0].name.endsWith(".tar.gz") || files[0].name.endsWith(".tgz"))) {
      onTarGzChange(files[0]);
      onUploadedFilesChange([]);
      return;
    }
    onTarGzChange(null);
    onUploadedFilesChange([...uploadedFiles, ...files]);
  }

  function removeUploadedFile(index: number) {
    onUploadedFilesChange(uploadedFiles.filter((_, i) => i !== index));
  }

  if (builderArtifactName && onBuilderArtifactClear) {
    return (
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2 rounded-md border border-primary/30 bg-primary/5 px-4 py-2.5 text-sm">
          <span className="text-primary">&#10003;</span>
          <span className="font-mono text-foreground">{builderArtifactName}</span>
          <span className="text-muted">(from Builder)</span>
        </div>
        <button type="button" onClick={onBuilderArtifactClear} className="text-xs text-muted hover:text-foreground">
          Remove
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* Mode toggle */}
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => onModeChange("code")}
          className={`rounded-full border px-4 py-1.5 text-xs font-medium transition-colors ${
            artifactMode === "code"
              ? "border-primary bg-primary/10 text-primary"
              : "border-border text-muted hover:border-primary/30"
          }`}
        >
          Write code
        </button>
        <button
          type="button"
          onClick={() => onModeChange("upload")}
          className={`rounded-full border px-4 py-1.5 text-xs font-medium transition-colors ${
            artifactMode === "upload"
              ? "border-primary bg-primary/10 text-primary"
              : "border-border text-muted hover:border-primary/30"
          }`}
        >
          Upload files
        </button>
      </div>

      {/* Code mode */}
      {artifactMode === "code" && (
        <div className="space-y-3">
          {codeFiles.map((file, i) => (
            <div key={i} className="rounded-lg border border-border bg-card overflow-hidden">
              <div className="flex items-center gap-2 border-b border-border px-3 py-2 bg-card">
                <input
                  type="text"
                  value={file.path}
                  onChange={(e) => updateCodeFile(i, "path", e.target.value)}
                  className="flex-1 bg-transparent text-xs font-mono text-foreground focus:outline-none placeholder:text-muted/50"
                  placeholder="my_tool/tool.py"
                />
                {codeFiles.length > 1 && (
                  <button type="button" onClick={() => removeCodeFile(i)} className="text-xs text-muted hover:text-danger transition-colors">
                    Remove
                  </button>
                )}
              </div>
              <textarea
                rows={10}
                value={file.content}
                onChange={(e) => updateCodeFile(i, "content", e.target.value)}
                className="w-full bg-[#0d1117] px-4 py-3 font-mono text-xs text-gray-300 focus:outline-none resize-none"
                placeholder="# Paste or write your Python code here..."
                spellCheck={false}
              />
            </div>
          ))}
          <button
            type="button"
            onClick={addCodeFile}
            className="w-full rounded-md border border-dashed border-border py-2.5 text-xs text-muted hover:border-primary/30 hover:text-foreground transition-colors"
          >
            + Add file
          </button>
          <p className="text-xs text-muted">
            Your code will be automatically packaged. Include at least one .py file.
          </p>
        </div>
      )}

      {/* Upload mode */}
      {artifactMode === "upload" && (
        <div className="space-y-3">
          <div
            ref={dropRef}
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={handleFileDrop}
            className={`rounded-lg border-2 border-dashed px-6 py-8 text-center transition-colors ${
              dragOver ? "border-primary bg-primary/5" : "border-border hover:border-primary/30"
            }`}
          >
            <div className="text-sm text-muted">
              Drag & drop files here, or{" "}
              <label className="cursor-pointer text-primary hover:underline">
                browse
                <input type="file" multiple accept=".py,.toml,.yaml,.yml,.cfg,.txt,.md,.tar.gz,.tgz" onChange={handleFileSelect} className="hidden" />
              </label>
            </div>
            <p className="mt-2 text-xs text-muted/60">
              Individual files (.py, .toml, .yaml) or a single .tar.gz archive
            </p>
          </div>

          {tarGzFile && (
            <div className="flex items-center gap-3 rounded-md border border-primary/30 bg-primary/5 px-4 py-2.5 text-sm">
              <span className="text-primary">&#10003;</span>
              <span className="font-mono text-foreground">{tarGzFile.name}</span>
              <span className="text-xs text-muted">({(tarGzFile.size / 1024).toFixed(1)} KB)</span>
              <button type="button" onClick={() => onTarGzChange(null)} className="ml-auto text-xs text-muted hover:text-foreground">
                Remove
              </button>
            </div>
          )}

          {uploadedFiles.length > 0 && (
            <div className="rounded-lg border border-border divide-y divide-border">
              {uploadedFiles.map((file, i) => (
                <div key={i} className="flex items-center gap-3 px-4 py-2 text-sm">
                  <span className="font-mono text-xs text-foreground">{file.name}</span>
                  <span className="text-xs text-muted">({(file.size / 1024).toFixed(1)} KB)</span>
                  <button type="button" onClick={() => removeUploadedFile(i)} className="ml-auto text-xs text-muted hover:text-danger transition-colors">
                    Remove
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Capability Search Dropdown                                         */
/* ------------------------------------------------------------------ */

function CapabilityDropdown({
  value,
  onChange,
  capabilities,
}: {
  value: string;
  onChange: (v: string) => void;
  capabilities: CapabilityOption[];
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const filtered = capabilities.filter(
    (c) =>
      c.id.toLowerCase().includes(search.toLowerCase()) ||
      c.name.toLowerCase().includes(search.toLowerCase()) ||
      c.category.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div ref={ref} className="relative">
      <input
        type="text"
        value={open ? search : value}
        onChange={(e) => {
          setSearch(e.target.value);
          if (!open) setOpen(true);
        }}
        onFocus={() => {
          setOpen(true);
          setSearch(value);
        }}
        placeholder="Search capabilities or type custom ID..."
        className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm font-mono text-foreground focus:border-primary focus:outline-none"
      />
      {open && (
        <div className="absolute z-50 mt-1 max-h-48 w-full overflow-auto rounded-md border border-border bg-card shadow-lg">
          {filtered.length === 0 ? (
            <div className="px-3 py-2 text-xs text-muted">
              {search ? "No matches. You can type a custom ID." : "Loading..."}
            </div>
          ) : (
            filtered.slice(0, 50).map((c) => (
              <button
                key={c.id}
                type="button"
                onClick={() => { onChange(c.id); setOpen(false); setSearch(""); }}
                className={`block w-full px-3 py-1.5 text-left text-sm hover:bg-primary/10 ${
                  c.id === value ? "bg-primary/5 text-primary" : "text-foreground"
                }`}
              >
                <span className="font-mono text-xs">{c.id}</span>
                <span className="ml-2 text-xs text-muted">{c.category}</span>
              </button>
            ))
          )}
          {search && !filtered.some((c) => c.id === search) && (
            <button
              type="button"
              onClick={() => { onChange(search); setOpen(false); setSearch(""); }}
              className="block w-full border-t border-border px-3 py-1.5 text-left text-xs text-primary hover:bg-primary/10"
            >
              Use &quot;{search}&quot; as custom ID
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main Content                                                       */
/* ------------------------------------------------------------------ */

function PublishContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const fromBuilder = searchParams.get("from") === "builder";
  const prefillParam = searchParams.get("manifest") || "";

  /* ---- Auth ---- */
  const [user, setUser] = useState<UserInfo | null>(null);
  const [authChecked, setAuthChecked] = useState(false);

  /* ---- Prefill from builder/import ---- */
  const [manifestText, setManifestText] = useState(() => {
    if (fromBuilder && typeof window !== "undefined") {
      const stored = sessionStorage.getItem("builder_manifest");
      if (stored) { sessionStorage.removeItem("builder_manifest"); return stored; }
    }
    return prefillParam;
  });

  const hasPrefill = !!(fromBuilder || prefillParam);

  /* ---- Screen: "input" or "review" ---- */
  const [screen, setScreen] = useState<"input" | "review">(hasPrefill ? "review" : "input");
  const [inputText, setInputText] = useState("");

  /* ---- Form state ---- */
  const [guided, setGuided] = useState<GuidedState>(() => {
    if (hasPrefill && manifestText) {
      try { return parseManifestToGuided(JSON.parse(manifestText)); } catch { /* defaults */ }
    }
    return { ...DEFAULT_GUIDED, tools: [{ ...EMPTY_TOOL }] };
  });

  /* ---- Panels ---- */
  const [openPanels, setOpenPanels] = useState<Set<string>>(
    () => hasPrefill ? new Set<string>() : new Set(["basics"])
  );

  /* ---- Artifact state ---- */
  const [artifact, setArtifact] = useState<File | null>(null);
  const [builderArtifactName, setBuilderArtifactName] = useState("");
  const [artifactMode, setArtifactMode] = useState<"code" | "upload">("code");
  const [codeFiles, setCodeFiles] = useState<CodeFile[]>([{ path: "my_tool/tool.py", content: "" }]);
  const [uploadedFiles, setUploadedFiles] = useState<File[]>([]);
  const [tarGzFile, setTarGzFile] = useState<File | null>(null);

  /* ---- Validation ---- */
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [validating, setValidating] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  /* ---- Publish state ---- */
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [loading, setLoading] = useState(false);
  const [buildingArtifact, setBuildingArtifact] = useState(false);

  /* ---- Publisher creation ---- */
  const [pubSlug, setPubSlug] = useState("");
  const [pubDisplayName, setPubDisplayName] = useState("");
  const [creatingPublisher, setCreatingPublisher] = useState(false);

  /* ---- Capabilities list ---- */
  const [capabilities, setCapabilities] = useState<CapabilityOption[]>([]);

  /* ---- Tool advanced toggles ---- */
  const [toolAdvanced, setToolAdvanced] = useState<Set<number>>(new Set());

  /* ---- Manifest preview ---- */
  const [showManifest, setShowManifest] = useState(false);
  const [showPermissions, setShowPermissions] = useState(false);
  const [copied, setCopied] = useState(false);

  /* ================================================================ */
  /*  Effects                                                          */
  /* ================================================================ */

  // Auth check
  useEffect(() => {
    fetchWithAuth("/auth/me")
      .then((res) => { if (!res.ok) throw new Error(); return res.json(); })
      .then((data) => setUser(data))
      .catch(() => setUser(null))
      .finally(() => setAuthChecked(true));
  }, []);

  // Load capabilities
  useEffect(() => {
    fetch("/api/v1/resolution/capabilities")
      .then((r) => (r.ok ? r.json() : []))
      .then((data: CapabilityOption[]) => { if (Array.isArray(data)) setCapabilities(data); })
      .catch(() => {});
  }, []);

  // Restore builder artifact
  useEffect(() => {
    if (fromBuilder && typeof window !== "undefined") {
      const dataUrl = sessionStorage.getItem("builder_artifact");
      const name = sessionStorage.getItem("builder_artifact_name");
      if (dataUrl && name) {
        sessionStorage.removeItem("builder_artifact");
        sessionStorage.removeItem("builder_artifact_name");
        fetch(dataUrl)
          .then((r) => r.blob())
          .then((blob) => {
            setArtifact(new File([blob], name, { type: "application/gzip" }));
            setBuilderArtifactName(name);
            setArtifactMode("upload");
          })
          .catch(() => {});
      }
    }
  }, [fromBuilder]);

  // Background auto-validation on review screen
  const runValidation = useCallback((manifest: Record<string, unknown>) => {
    setValidating(true);
    fetchWithAuth("/packages/validate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ manifest }),
    })
      .then(async (res) => {
        const data = await res.json();
        if (res.ok) setValidation(data);
        else setValidation({ valid: false, errors: [data.error?.message || "Validation failed"], warnings: [] });
      })
      .catch(() => setValidation({ valid: false, errors: ["Network error during validation"], warnings: [] }))
      .finally(() => setValidating(false));
  }, []);

  useEffect(() => {
    if (screen !== "review" || !user?.publisher) return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      const manifest = buildManifestFromGuided(guided, user.publisher!.slug);
      runValidation(manifest);
    }, 800);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [guided, screen, user, runValidation]);

  /* ================================================================ */
  /*  Handlers                                                         */
  /* ================================================================ */

  function togglePanel(panel: string) {
    setOpenPanels((prev) => {
      const next = new Set(prev);
      next.has(panel) ? next.delete(panel) : next.add(panel);
      return next;
    });
  }

  function handleContinueWithManifest() {
    setError("");
    try {
      const parsed = JSON.parse(inputText);
      setGuided(parseManifestToGuided(parsed));
      setManifestText(inputText);
      setOpenPanels(new Set());
      setScreen("review");
    } catch {
      setError("Invalid JSON. Please check your manifest format.");
    }
  }

  function handleStartFresh() {
    setGuided({ ...DEFAULT_GUIDED, tools: [{ ...EMPTY_TOOL }] });
    setOpenPanels(new Set(["basics"]));
    setValidation(null);
    setError("");
    setScreen("review");
  }

  async function createPublisher() {
    if (!pubSlug || !pubDisplayName) return;
    setCreatingPublisher(true);
    setError("");
    try {
      const res = await fetchWithAuth("/publishers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ display_name: pubDisplayName, slug: pubSlug }),
      });
      if (res.ok) {
        const meRes = await fetchWithAuth("/auth/me");
        if (meRes.ok) setUser(await meRes.json());
      } else {
        const data = await res.json();
        setError(data.error?.message || "Failed to create publisher");
      }
    } finally {
      setCreatingPublisher(false);
    }
  }

  function updateGuided<K extends keyof GuidedState>(key: K, value: GuidedState[K]) {
    setGuided((prev) => ({ ...prev, [key]: value }));
  }

  function updateTool(index: number, field: keyof ToolEntry, value: string) {
    setGuided((prev) => {
      const tools = [...prev.tools];
      tools[index] = { ...tools[index], [field]: value };
      return { ...prev, tools };
    });
  }

  function addTool() {
    setGuided((prev) => ({ ...prev, tools: [...prev.tools, { ...EMPTY_TOOL }] }));
  }

  function removeTool(index: number) {
    if (guided.tools.length <= 1) return;
    setGuided((prev) => ({ ...prev, tools: prev.tools.filter((_, i) => i !== index) }));
    setToolAdvanced((prev) => {
      const next = new Set<number>();
      prev.forEach((i) => {
        if (i < index) next.add(i);
        else if (i > index) next.add(i - 1);
      });
      return next;
    });
  }

  async function resolveArtifact(parsed: Record<string, unknown>): Promise<File | null> {
    if (builderArtifactName && artifact) return artifact;
    if (tarGzFile) return tarGzFile;

    const filesToBuild: { path: string; content: string }[] = [];
    if (artifactMode === "code") {
      const nonEmpty = codeFiles.filter((f) => f.content.trim());
      if (nonEmpty.length === 0) return null;
      filesToBuild.push(...nonEmpty);
    } else if (uploadedFiles.length > 0) {
      for (const file of uploadedFiles) {
        const text = await file.text();
        filesToBuild.push({ path: file.name, content: text });
      }
    }

    if (filesToBuild.length === 0) return null;

    const pkgId = (parsed.package_id as string) || "my-tool";
    setBuildingArtifact(true);
    try {
      const buildRes = await fetchWithAuth("/builder/artifact", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ package_id: pkgId, manifest_json: parsed, code_files: filesToBuild }),
      });
      if (!buildRes.ok) throw new Error("Failed to build artifact from files");
      const blob = await buildRes.blob();
      return new File([blob], `${pkgId}.tar.gz`, { type: "application/gzip" });
    } finally {
      setBuildingArtifact(false);
    }
  }

  async function handlePublish() {
    setError("");
    setSuccess("");
    setLoading(true);
    try {
      if (!user?.publisher) throw new Error("No publisher");
      const parsed = buildManifestFromGuided(guided, user.publisher.slug);
      if (!parsed.publisher) parsed.publisher = user.publisher.slug;

      const artifactToSend = await resolveArtifact(parsed);
      const formData = new FormData();
      formData.append("manifest", JSON.stringify(parsed));
      if (artifactToSend) formData.append("artifact", artifactToSend);

      const res = await fetchWithAuth("/packages/publish", { method: "POST", body: formData });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error?.message || "Publish failed");

      setSuccess(`Published ${data.slug}@${data.version}`);
      setTimeout(() => router.push(`/packages/${data.slug}`), 1500);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }

  function copyManifest() {
    const text = user?.publisher
      ? JSON.stringify(buildManifestFromGuided(guided, user.publisher.slug), null, 2)
      : "";
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  /* ================================================================ */
  /*  Render: Loading                                                  */
  /* ================================================================ */

  if (!authChecked) {
    return <div className="mx-auto max-w-2xl px-4 py-24 text-center text-muted">Loading...</div>;
  }

  /* ================================================================ */
  /*  Render: Login gate                                               */
  /* ================================================================ */

  if (!user) {
    const returnTo = manifestText
      ? `/publish?manifest=${encodeURIComponent(manifestText)}`
      : "/publish";

    return (
      <div className="mx-auto max-w-lg px-4 sm:px-6 py-24 text-center">
        <div className="mb-6 text-4xl">&#128640;</div>
        <h1 className="mb-3 text-2xl font-bold text-foreground">Publish on AgentNode</h1>
        <p className="mb-8 text-muted">
          Make your AI skill discoverable, installable, and usable across all agent frameworks.
        </p>

        <div className="mb-6 grid gap-3 text-left rounded-lg border border-border bg-card p-5 text-sm">
          {[
            "Works across LangChain, CrewAI, MCP, and more",
            "Download counters, trust badges, and discovery",
            "Version management and artifact hosting",
          ].map((text) => (
            <div key={text} className="flex items-start gap-3">
              <span className="mt-0.5 text-success">&#10003;</span>
              <span className="text-muted">{text}</span>
            </div>
          ))}
          <div className="flex items-start gap-3">
            <span className="mt-0.5 text-success">&#10003;</span>
            <span className="text-muted">CLI publishing: <code className="text-primary">agentnode publish</code></span>
          </div>
        </div>

        <div className="flex flex-col gap-3">
          <Link
            href={`/auth/register?returnTo=${encodeURIComponent(returnTo)}`}
            className="rounded-md bg-primary px-6 py-3 text-sm font-semibold text-white hover:bg-primary/90 transition-colors"
          >
            Create account to publish
          </Link>
          <Link
            href={`/auth/login?returnTo=${encodeURIComponent(returnTo)}`}
            className="rounded-md border border-border px-6 py-3 text-sm text-muted hover:text-foreground hover:border-primary/30 transition-colors"
          >
            Already have an account? Sign in
          </Link>
        </div>

        <p className="mt-8 text-xs text-muted">
          Don&apos;t have a manifest yet?{" "}
          <Link href="/import" className="text-primary hover:underline">Import your existing tools</Link>
        </p>
      </div>
    );
  }

  /* ================================================================ */
  /*  Render: Publisher creation gate                                   */
  /* ================================================================ */

  if (!user.publisher) {
    return (
      <div className="mx-auto max-w-lg px-4 sm:px-6 py-16">
        <h1 className="mb-2 text-2xl font-bold text-foreground">Almost there!</h1>
        <p className="mb-6 text-muted text-sm">
          Create a publisher profile to start publishing skills. This takes 10 seconds.
        </p>

        {error && (
          <div className="mb-4 rounded-md border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">{error}</div>
        )}

        <div className="rounded-lg border border-border bg-card p-6">
          <div className="mb-4 flex items-center gap-3">
            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-primary/20 text-sm font-bold text-primary">1</div>
            <span className="text-sm font-medium text-foreground">Set up your publisher identity</span>
          </div>
          <div className="space-y-4">
            <div>
              <label className="mb-1 block text-sm text-muted">Display name</label>
              <input
                type="text"
                value={pubDisplayName}
                onChange={(e) => {
                  setPubDisplayName(e.target.value);
                  if (!pubSlug || pubSlug === pubDisplayName.toLowerCase().replace(/[^a-z0-9-]/g, "-").replace(/-+/g, "-")) {
                    setPubSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-").replace(/-+/g, "-"));
                  }
                }}
                className="w-full rounded-md border border-border bg-background px-3 py-2.5 text-foreground focus:border-primary focus:outline-none"
                placeholder="My Company"
              />
            </div>
            <div>
              <label className="mb-1 block text-sm text-muted">
                Slug <span className="text-muted/50">(used in package URLs: @your-slug)</span>
              </label>
              <input
                type="text"
                value={pubSlug}
                onChange={(e) => setPubSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ""))}
                className="w-full rounded-md border border-border bg-background px-3 py-2.5 font-mono text-foreground focus:border-primary focus:outline-none"
                placeholder="my-company"
                pattern="[a-z0-9-]+"
              />
            </div>
            <button
              onClick={createPublisher}
              disabled={creatingPublisher || !pubSlug || !pubDisplayName}
              className="w-full rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-white hover:bg-primary/90 disabled:opacity-50"
            >
              {creatingPublisher ? "Creating..." : "Create publisher & continue"}
            </button>
          </div>
        </div>
      </div>
    );
  }

  /* ================================================================ */
  /*  Render: INPUT SCREEN                                             */
  /* ================================================================ */

  if (screen === "input") {
    return (
      <div className="mx-auto max-w-2xl px-4 sm:px-6 py-16">
        <div className="text-center mb-10">
          <h1 className="text-3xl font-bold tracking-tight text-foreground">
            Publish your skill
          </h1>
          <p className="mt-3 text-muted">
            Make your AI tool discoverable and installable across all agent frameworks.
          </p>
        </div>

        {error && (
          <div className="mb-4 rounded-md border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">{error}</div>
        )}

        <div className="space-y-6">
          {/* Manifest paste area */}
          <div>
            <label className="mb-2 block text-sm font-medium text-foreground">
              Paste your manifest JSON
            </label>
            <textarea
              rows={12}
              value={inputText}
              onChange={(e) => { setInputText(e.target.value); setError(""); }}
              className="w-full rounded-xl border border-border bg-card px-4 py-3 font-mono text-sm text-foreground placeholder:text-muted/40 focus:border-primary/50 focus:outline-none focus:ring-1 focus:ring-primary/20 transition-all"
              placeholder={'{\n  "manifest_version": "0.2",\n  "package_id": "my-tool",\n  "name": "My Tool",\n  "version": "1.0.0",\n  "summary": "What my tool does",\n  "capabilities": {\n    "tools": [\n      { "name": "my_func", "capability_id": "..." }\n    ]\n  }\n}'}
              spellCheck={false}
            />
          </div>

          {/* Action buttons */}
          <div className="flex items-center gap-4">
            <button
              onClick={handleContinueWithManifest}
              disabled={!inputText.trim()}
              className="rounded-xl bg-primary px-6 py-3 text-sm font-semibold text-white transition-all hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Continue to review
            </button>
            <span className="text-xs text-muted">or</span>
            <button
              onClick={handleStartFresh}
              className="rounded-xl border border-border px-6 py-3 text-sm font-medium text-muted transition-all hover:text-foreground hover:border-primary/30"
            >
              Start from scratch
            </button>
          </div>
        </div>

        {/* Footer links */}
        <div className="mt-12 border-t border-border pt-8 text-center space-y-2">
          <p className="text-sm text-muted">
            Coming from another platform?{" "}
            <Link href="/import" className="text-primary hover:underline">
              Import from LangChain, MCP, CrewAI, or OpenAI
            </Link>
          </p>
          <p className="text-xs text-muted">
            Or publish via CLI: <code className="rounded bg-card px-1.5 py-0.5 text-primary">agentnode publish</code>
          </p>
        </div>
      </div>
    );
  }

  /* ================================================================ */
  /*  Render: REVIEW SCREEN                                            */
  /* ================================================================ */

  const toolCount = guided.tools.filter((t) => t.name).length;
  const toolSummary = `${toolCount} tool${toolCount !== 1 ? "s" : ""} configured`;
  const hasNonDefaultPerms =
    guided.network !== "none" ||
    guided.filesystem !== "none" ||
    guided.code_execution !== "none" ||
    guided.data_access !== "input_only";
  const artifactSummary = builderArtifactName
    ? `Builder: ${builderArtifactName}`
    : tarGzFile
    ? tarGzFile.name
    : uploadedFiles.length > 0
    ? `${uploadedFiles.length} file${uploadedFiles.length > 1 ? "s" : ""}`
    : artifactMode === "code"
    ? "Write code"
    : "Upload files";

  return (
    <div className="mx-auto max-w-3xl px-4 sm:px-6 py-12">
      {/* Header */}
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Review & Publish</h1>
          <p className="text-sm text-muted">
            Publishing as <span className="text-primary font-medium">@{user.publisher.slug}</span>
          </p>
        </div>
        <button
          type="button"
          onClick={() => { setScreen("input"); setError(""); setSuccess(""); }}
          className="text-sm text-muted hover:text-foreground transition-colors"
        >
          &#8592; Back
        </button>
      </div>

      {/* Builder prefill banner */}
      {fromBuilder && (
        <div className="mb-6 rounded-lg border border-primary/30 bg-primary/5 px-4 py-3 text-sm text-primary">
          Generated with AgentNode Builder — review the details below and publish.
        </div>
      )}

      {/* Alerts */}
      {error && (
        <div className="mb-4 rounded-md border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">{error}</div>
      )}
      {success && (
        <div className="mb-4 rounded-md border border-success/30 bg-success/10 px-4 py-3 text-sm text-success">{success}</div>
      )}

      {/* ---- Review Card ---- */}
      <div
        className={`mb-6 rounded-xl border p-6 transition-colors ${
          validation?.valid
            ? "border-success/30 bg-success/5"
            : validation && !validation.valid
            ? "border-danger/20 bg-danger/5"
            : "border-border bg-card"
        }`}
      >
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h2 className="text-lg font-bold text-foreground truncate">
              {guided.name || "Untitled skill"}
            </h2>
            <div className="mt-0.5 font-mono text-xs text-muted">
              {guided.package_id || "no-package-id"} &middot; v{guided.version}
            </div>
          </div>
          <div className="shrink-0">
            {validating ? (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-card border border-border px-3 py-1 text-xs text-muted">
                <span className="inline-block h-2.5 w-2.5 animate-spin rounded-full border-[1.5px] border-muted/30 border-t-muted" />
                Validating
              </span>
            ) : validation ? (
              <span
                className={`rounded-full px-3 py-1 text-xs font-medium ${
                  validation.valid
                    ? "bg-success/10 text-success border border-success/20"
                    : "bg-danger/10 text-danger border border-danger/20"
                }`}
              >
                {validation.valid ? "Valid" : `${validation.errors.length} error${validation.errors.length !== 1 ? "s" : ""}`}
              </span>
            ) : null}
          </div>
        </div>

        {guided.summary && (
          <p className="mt-2 text-sm text-muted line-clamp-2">{guided.summary}</p>
        )}

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <span className="rounded-full border border-border bg-background px-2.5 py-0.5 text-xs text-muted">
            {toolCount} tool{toolCount !== 1 ? "s" : ""}
          </span>
          <span className="rounded-full border border-border bg-background px-2.5 py-0.5 text-xs text-muted">
            {guided.package_type}
          </span>
        </div>
      </div>

      {/* ---- Collapsible edit panels ---- */}
      <div className="space-y-3 mb-6">

        {/* === BASICS === */}
        <CollapsiblePanel
          title="Basics"
          subtitle={guided.name ? `${guided.name} · v${guided.version}` : "Name, version, and description"}
          open={openPanels.has("basics")}
          onToggle={() => togglePanel("basics")}
        >
          <div>
            <label className="mb-1 block text-sm font-medium text-foreground">Name</label>
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
              className="w-full rounded-md border border-border bg-background px-3 py-2.5 text-foreground focus:border-primary focus:outline-none"
              placeholder="My PDF Extractor"
            />
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium text-foreground">
              Package ID
              <span className="ml-2 text-xs text-muted font-normal">a-z, 0-9, dashes, 3-60 chars</span>
            </label>
            <input
              type="text"
              value={guided.package_id}
              onChange={(e) => updateGuided("package_id", e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ""))}
              className="w-full rounded-md border border-border bg-background px-3 py-2.5 font-mono text-foreground focus:border-primary focus:outline-none"
              placeholder="my-pdf-extractor"
            />
            {guided.package_id && !SLUG_PATTERN.test(guided.package_id) && (
              <p className="mt-1 text-xs text-danger">Must be 3-60 chars, lowercase letters, numbers, and dashes only</p>
            )}
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-sm font-medium text-foreground">Type</label>
              <select
                value={guided.package_type}
                onChange={(e) => updateGuided("package_type", e.target.value as GuidedState["package_type"])}
                className="w-full rounded-md border border-border bg-background px-3 py-2.5 text-foreground focus:border-primary focus:outline-none"
              >
                <option value="toolpack">toolpack</option>
                <option value="agent">agent</option>
                <option value="upgrade">upgrade</option>
              </select>
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium text-foreground">
                Version <span className="text-xs text-muted font-normal">semver</span>
              </label>
              <input
                type="text"
                value={guided.version}
                onChange={(e) => updateGuided("version", e.target.value)}
                className="w-full rounded-md border border-border bg-background px-3 py-2.5 font-mono text-foreground focus:border-primary focus:outline-none"
                placeholder="1.0.0"
              />
              {guided.version && !isValidSemver(guided.version) && (
                <p className="mt-1 text-xs text-danger">Must be valid semver (e.g. 1.0.0)</p>
              )}
            </div>
          </div>

          <div>
            <label className="mb-1 flex items-center justify-between text-sm font-medium text-foreground">
              <span>Summary</span>
              <span className={`text-xs font-normal ${guided.summary.length > 200 ? "text-danger" : "text-muted"}`}>
                {guided.summary.length}/200
              </span>
            </label>
            <textarea
              rows={2}
              value={guided.summary}
              onChange={(e) => updateGuided("summary", e.target.value)}
              className="w-full rounded-md border border-border bg-background px-3 py-2.5 text-foreground focus:border-primary focus:outline-none resize-none"
              placeholder="Extract text and tables from PDF files"
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
              className="w-full rounded-md border border-border bg-background px-3 py-2.5 text-foreground focus:border-primary focus:outline-none resize-none"
              placeholder="Detailed description of what this skill does..."
            />
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium text-foreground">
              Tags <span className="text-xs text-muted font-normal">(comma-separated, optional)</span>
            </label>
            <input
              type="text"
              value={guided.tags}
              onChange={(e) => updateGuided("tags", e.target.value)}
              className="w-full rounded-md border border-border bg-background px-3 py-2.5 text-foreground focus:border-primary focus:outline-none"
              placeholder="pdf, extraction, text"
            />
          </div>
        </CollapsiblePanel>

        {/* === TOOLS === */}
        <CollapsiblePanel
          title="Tools"
          subtitle={toolSummary}
          open={openPanels.has("tools")}
          onToggle={() => togglePanel("tools")}
        >
          {guided.tools.map((tool, i) => (
            <div key={i} className="rounded-lg border border-border bg-card/50 p-4 space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium text-foreground">Tool {i + 1}</span>
                {guided.tools.length > 1 && (
                  <button type="button" onClick={() => removeTool(i)} className="text-xs text-muted hover:text-danger transition-colors">
                    Remove
                  </button>
                )}
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <label className="mb-1 block text-xs text-muted">Name</label>
                  <input
                    type="text"
                    value={tool.name}
                    onChange={(e) => updateTool(i, "name", e.target.value)}
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
                    placeholder="extract_text"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs text-muted">Capability ID</label>
                  <CapabilityDropdown
                    value={tool.capability_id}
                    onChange={(v) => updateTool(i, "capability_id", v)}
                    capabilities={capabilities}
                  />
                </div>
              </div>

              <div>
                <label className="mb-1 block text-xs text-muted">Description</label>
                <input
                  type="text"
                  value={tool.description}
                  onChange={(e) => updateTool(i, "description", e.target.value)}
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
                  placeholder="Extract text from PDF files"
                />
              </div>

              <div>
                <label className="mb-1 block text-xs text-muted">
                  Entrypoint <span className="text-muted/50">module.path:function_name</span>
                </label>
                <input
                  type="text"
                  value={tool.entrypoint}
                  onChange={(e) => updateTool(i, "entrypoint", e.target.value)}
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm font-mono text-foreground focus:border-primary focus:outline-none"
                  placeholder="my_pack.tool:extract_text"
                />
              </div>

              <button
                type="button"
                onClick={() =>
                  setToolAdvanced((prev) => {
                    const next = new Set(prev);
                    next.has(i) ? next.delete(i) : next.add(i);
                    return next;
                  })
                }
                className="text-xs text-muted hover:text-foreground transition-colors"
              >
                {toolAdvanced.has(i) ? "Hide" : "Show"} schemas
              </button>

              {toolAdvanced.has(i) && (
                <div className="grid gap-3 sm:grid-cols-2">
                  <div>
                    <label className="mb-1 block text-xs text-muted">Input schema (JSON)</label>
                    <textarea
                      rows={4}
                      value={tool.input_schema}
                      onChange={(e) => updateTool(i, "input_schema", e.target.value)}
                      className="w-full rounded-md border border-border bg-background px-3 py-2 font-mono text-xs text-foreground focus:border-primary focus:outline-none resize-none"
                      placeholder='{"type": "object", "properties": {...}}'
                      spellCheck={false}
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-xs text-muted">Output schema (JSON)</label>
                    <textarea
                      rows={4}
                      value={tool.output_schema}
                      onChange={(e) => updateTool(i, "output_schema", e.target.value)}
                      className="w-full rounded-md border border-border bg-background px-3 py-2 font-mono text-xs text-foreground focus:border-primary focus:outline-none resize-none"
                      placeholder='{"type": "object", "properties": {...}}'
                      spellCheck={false}
                    />
                  </div>
                </div>
              )}
            </div>
          ))}

          <button
            type="button"
            onClick={addTool}
            className="w-full rounded-md border border-dashed border-border py-3 text-sm text-muted hover:border-primary/30 hover:text-foreground transition-colors"
          >
            + Add tool
          </button>
        </CollapsiblePanel>

        {/* === PERMISSIONS === */}
        <CollapsiblePanel
          title="Permissions"
          subtitle={hasNonDefaultPerms ? "Custom permissions" : "All restricted (default)"}
          open={openPanels.has("permissions")}
          onToggle={() => togglePanel("permissions")}
        >
          {!showPermissions ? (
            <div>
              {hasNonDefaultPerms ? (
                <div className="space-y-3">
                  <div className="flex flex-wrap gap-2">
                    {guided.network !== "none" && (
                      <span className="rounded-full border border-yellow-500/20 bg-yellow-500/5 px-2.5 py-0.5 text-xs text-yellow-500">
                        Network: {guided.network}
                      </span>
                    )}
                    {guided.filesystem !== "none" && (
                      <span className="rounded-full border border-yellow-500/20 bg-yellow-500/5 px-2.5 py-0.5 text-xs text-yellow-500">
                        Filesystem: {guided.filesystem}
                      </span>
                    )}
                    {guided.code_execution !== "none" && (
                      <span className="rounded-full border border-yellow-500/20 bg-yellow-500/5 px-2.5 py-0.5 text-xs text-yellow-500">
                        Code exec: {guided.code_execution}
                      </span>
                    )}
                    {guided.data_access !== "input_only" && (
                      <span className="rounded-full border border-yellow-500/20 bg-yellow-500/5 px-2.5 py-0.5 text-xs text-yellow-500">
                        Data: {guided.data_access}
                      </span>
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={() => setShowPermissions(true)}
                    className="text-xs text-primary hover:underline"
                  >
                    Edit permissions
                  </button>
                </div>
              ) : (
                <div className="flex items-center justify-between">
                  <p className="text-sm text-muted">
                    No network, filesystem, or code execution access. Safe defaults for most skills.
                  </p>
                  <button
                    type="button"
                    onClick={() => setShowPermissions(true)}
                    className="shrink-0 text-xs text-primary hover:underline"
                  >
                    Customize
                  </button>
                </div>
              )}
            </div>
          ) : (
            <div className="space-y-3">
              <div className="rounded-md border border-blue-500/20 bg-blue-500/5 px-3 py-2 text-xs text-blue-400">
                Defaults are maximally restrictive. Only increase if your skill genuinely needs it.
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <label className="mb-1 block text-xs text-muted">Network</label>
                  <select
                    value={guided.network}
                    onChange={(e) => updateGuided("network", e.target.value)}
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
                  >
                    <option value="none">none</option>
                    <option value="restricted">restricted</option>
                    <option value="unrestricted">unrestricted</option>
                  </select>
                </div>
                <div>
                  <label className="mb-1 block text-xs text-muted">Filesystem</label>
                  <select
                    value={guided.filesystem}
                    onChange={(e) => updateGuided("filesystem", e.target.value)}
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
                  >
                    <option value="none">none</option>
                    <option value="temp">temp</option>
                    <option value="workspace_read">workspace_read</option>
                    <option value="workspace_write">workspace_write</option>
                    <option value="any">any</option>
                  </select>
                </div>
                <div>
                  <label className="mb-1 block text-xs text-muted">Code execution</label>
                  <select
                    value={guided.code_execution}
                    onChange={(e) => updateGuided("code_execution", e.target.value)}
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
                  >
                    <option value="none">none</option>
                    <option value="limited_subprocess">limited_subprocess</option>
                    <option value="shell">shell</option>
                  </select>
                </div>
                <div>
                  <label className="mb-1 block text-xs text-muted">Data access</label>
                  <select
                    value={guided.data_access}
                    onChange={(e) => updateGuided("data_access", e.target.value)}
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
                  >
                    <option value="input_only">input_only</option>
                    <option value="connected_accounts">connected_accounts</option>
                    <option value="persistent">persistent</option>
                  </select>
                </div>
                <div>
                  <label className="mb-1 block text-xs text-muted">User approval</label>
                  <select
                    value={guided.user_approval}
                    onChange={(e) => updateGuided("user_approval", e.target.value)}
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
                  >
                    <option value="always">always</option>
                    <option value="high_risk_only">high_risk_only</option>
                    <option value="once">once</option>
                    <option value="never">never</option>
                  </select>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setShowPermissions(false)}
                className="text-xs text-muted hover:text-foreground"
              >
                Done
              </button>
            </div>
          )}
        </CollapsiblePanel>

        {/* === ARTIFACT === */}
        <CollapsiblePanel
          title="Artifact"
          subtitle={artifactSummary}
          open={openPanels.has("artifact")}
          onToggle={() => togglePanel("artifact")}
        >
          <ArtifactSection
            artifactMode={artifactMode}
            onModeChange={setArtifactMode}
            codeFiles={codeFiles}
            onCodeFilesChange={setCodeFiles}
            uploadedFiles={uploadedFiles}
            onUploadedFilesChange={setUploadedFiles}
            tarGzFile={tarGzFile}
            onTarGzChange={setTarGzFile}
            builderArtifactName={builderArtifactName}
            onBuilderArtifactClear={() => { setArtifact(null); setBuilderArtifactName(""); }}
            packageId={guided.package_id}
          />
        </CollapsiblePanel>
      </div>

      {/* ---- Manifest preview ---- */}
      <div className="mb-6">
        <button
          type="button"
          onClick={() => setShowManifest(!showManifest)}
          className="flex items-center gap-2 text-sm text-muted hover:text-foreground transition-colors"
        >
          <span className={`inline-block transition-transform ${showManifest ? "rotate-90" : ""}`}>&#9654;</span>
          {showManifest ? "Hide" : "Show"} manifest JSON
        </button>
        {showManifest && (
          <div className="relative mt-3 overflow-hidden rounded-lg border border-border bg-[#0d1117]">
            <div className="flex items-center justify-between border-b border-border/50 px-4 py-2">
              <span className="font-mono text-xs text-muted">manifest.json</span>
              <button
                type="button"
                onClick={copyManifest}
                className="rounded px-2 py-1 text-xs text-muted transition-colors hover:text-foreground"
              >
                {copied ? "Copied!" : "Copy"}
              </button>
            </div>
            <pre className="max-h-72 overflow-auto p-4 font-mono text-xs leading-relaxed text-gray-300">
              {JSON.stringify(buildManifestFromGuided(guided, user.publisher.slug), null, 2)}
            </pre>
          </div>
        )}
      </div>

      {/* ---- Validation details ---- */}
      {validation && !validation.valid && (
        <div className="mb-4 rounded-md border border-danger/30 bg-danger/5 px-4 py-3 text-sm">
          <div className="font-medium text-danger mb-1">Validation errors:</div>
          <ul className="space-y-0.5">
            {validation.errors.map((err, i) => (
              <li key={i} className="flex items-start gap-2 text-danger">
                <span className="mt-0.5 shrink-0">&bull;</span>
                <span>{err}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {validation && validation.warnings.length > 0 && (
        <div className="mb-4 rounded-md border border-yellow-500/20 bg-yellow-500/5 px-4 py-3">
          {validation.warnings.map((w, i) => (
            <div key={i} className="flex items-start gap-2 text-yellow-500 text-xs">
              <span className="mt-0.5 shrink-0">&#9888;</span>
              <span>{w}</span>
            </div>
          ))}
        </div>
      )}

      {/* ---- Publish bar ---- */}
      <div className="flex items-center justify-between pt-4 border-t border-border">
        <button
          type="button"
          onClick={() => { setScreen("input"); setError(""); setSuccess(""); }}
          className="rounded-md border border-border px-5 py-2.5 text-sm text-muted hover:text-foreground transition-colors"
        >
          &#8592; Back
        </button>
        <button
          type="button"
          onClick={handlePublish}
          disabled={loading || buildingArtifact || (validation !== null && !validation.valid)}
          className="rounded-md bg-primary px-8 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-primary/90 disabled:opacity-50"
        >
          {buildingArtifact ? "Building artifact..." : loading ? "Publishing..." : "Publish"}
        </button>
      </div>

      <p className="mt-6 text-center text-xs text-muted">
        Or publish via CLI: <code className="rounded bg-card px-1.5 py-0.5 text-primary">agentnode publish</code>
        {" "}&middot;{" "}
        <Link href="/import" className="text-primary hover:underline">Import from another platform</Link>
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Page wrapper                                                       */
/* ------------------------------------------------------------------ */

export default function PublishPage() {
  return (
    <Suspense fallback={<div className="py-24 text-center text-muted">Loading...</div>}>
      <PublishContent />
    </Suspense>
  );
}
