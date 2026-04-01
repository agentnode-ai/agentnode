"use client";

import { useState, useEffect, useCallback, useRef, Suspense } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import yaml from "js-yaml";
import { fetchWithAuth, search } from "@/lib/api";
import { PLATFORMS, convertClientSide, parseResult } from "@/lib/import-utils";
import { BUILDER_EXAMPLES, generateSkill, type BuilderResult } from "@/lib/builder-utils";

// Extracted modules
import type { UserInfo, ToolEntry, CodeFile, GuidedState, ValidationResult, CapabilityOption, InputTab } from "./lib/types";
import { MAX_UPLOAD_SIZE_MB, DRAFT_TTL, DRAFT_KEY, SLUG_PATTERN, EMPTY_TOOL, DEFAULT_GUIDED, CAPABILITY_FALLBACK } from "./lib/constants";
import { slugify, isValidSemver, buildManifestFromGuided, parseManifestToGuided } from "./lib/manifest";
import { computeReadiness, computePanelStatuses } from "./lib/readiness";
import { saveDraft, restoreDraft, clearDraft } from "./hooks/useDraft";
import { StepIndicator } from "./components/StepIndicator";
import { CollapsiblePanel } from "./components/CollapsiblePanel";
import { ReadinessChecklist } from "./components/ReadinessChecklist";
import { ArtifactSection } from "./components/ArtifactSection";
import { CapabilityDropdown } from "./components/CapabilityDropdown";
import { StickyPublishBar } from "./components/StickyPublishBar";

/* ------------------------------------------------------------------ */
/*  Main Content — all types, utilities, and sub-components            */
/*  are extracted to ./lib/ ./hooks/ ./components/                     */
/* ------------------------------------------------------------------ */

function PublishContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const fromSource = searchParams.get("from"); // "builder" | "import" | null
  const prefillParam = searchParams.get("manifest") || "";
  const tabParam = searchParams.get("tab");

  /* ---- Auth ---- */
  const [user, setUser] = useState<UserInfo | null>(null);
  const [authChecked, setAuthChecked] = useState(false);

  /* ---- Prefill from builder/import (backward compat) ---- */
  const [_prefillFiles, set_prefillFiles] = useState<CodeFile[] | null>(null);
  /* _prefillPlatform removed — source upgrade in prefill loading handles platform */
  const [importConfidence, setImportConfidence] = useState<{
    level: string; reasons: string[];
  } | null>(null);
  const [importDraftReady, setImportDraftReady] = useState<boolean | null>(null);
  const [importWarnings, setImportWarnings] = useState<string[]>([]);
  const [importGroupedWarnings, setImportGroupedWarnings] = useState<
    { message: string; category: "blocking" | "review" | "info" }[]
  >([]);
  const [importChanges, setImportChanges] = useState<string[]>([]);
  const [prefillText] = useState(() => {
    if (fromSource && typeof window !== "undefined") {
      const raw = sessionStorage.getItem("publish_prefill");
      if (raw) {
        sessionStorage.removeItem("publish_prefill");
        try {
          const prefill = JSON.parse(raw);
          // Extract code files from import flow (Phase 2 fix)
          if (prefill.originalFiles?.length) {
            set_prefillFiles(prefill.originalFiles);
          } else if (prefill.originalCode) {
            // Legacy single-file fallback
            const moduleId = (prefill.manifestText?.match(/package_id:\s*(\S+)/)?.[1] || "my-tool").replace(/-/g, "_");
            set_prefillFiles([{ path: `src/${moduleId}/tool.py`, content: prefill.originalCode }]);
          }
          if (prefill.importPlatform) {
            // Upgrade source from bare "import" to "import:PlatformName"
            const plat = PLATFORMS.find(p => p.id === prefill.importPlatform);
            if (plat) {
              setTimeout(() => setSource(`import:${plat.name}`), 0);
            }
          }
          // Import conversion metadata
          if (prefill.confidence) {
            setImportConfidence(prefill.confidence);
          }
          if (typeof prefill.draftReady === "boolean") {
            setImportDraftReady(prefill.draftReady);
          }
          if (Array.isArray(prefill.warnings)) {
            setImportWarnings(prefill.warnings);
          }
          if (Array.isArray(prefill.groupedWarnings)) {
            setImportGroupedWarnings(prefill.groupedWarnings);
          }
          if (Array.isArray(prefill.changes)) {
            setImportChanges(prefill.changes);
          }
          return prefill.manifestText || "";
        } catch { /* fall through */ }
      }
    }
    return prefillParam;
  });

  const hasPrefill = !!(fromSource || prefillParam);

  /* ---- Screen: "input" | "draft" | "edit" ---- */
  const [screen, setScreen] = useState<"input" | "draft" | "edit">(() => {
    if (hasPrefill && prefillText) return "draft";
    // Check for restored draft
    if (typeof window !== "undefined") {
      const draft = restoreDraft();
      if (draft?.guided && typeof draft.guided === "object" && Array.isArray(draft.guided.tools)) return "draft";
    }
    return "input";
  });

  /* ---- Input Hub state ---- */
  const [activeTab, setActiveTab] = useState<InputTab>(() => {
    if (tabParam === "import" || tabParam === "manifest") return tabParam;
    return "describe";
  });

  /* ---- Describe tab state ---- */
  const [descriptionText, setDescriptionText] = useState("");
  const [generating, setGenerating] = useState(false);
  const [builderResult, setBuilderResult] = useState<BuilderResult | null>(null);

  /* ---- Import tab state ---- */
  const [importPlatform, setImportPlatform] = useState("langchain");
  const [importCode, setImportCode] = useState("");

  /* ---- Manifest tab state ---- */
  const [manifestInput, setManifestInput] = useState("");

  /* ---- Source tracking ---- */
  const [source, setSource] = useState<string | null>(fromSource);

  /* ---- Draft expiry / save message ---- */
  const [draftExpired, setDraftExpired] = useState(false);
  const [draftSaveBanner, setDraftSaveBanner] = useState(false);
  const draftSaveShownRef = useRef(false);
  const restoredDraftTabRef = useRef<string | null>(null);

  /* ---- Form state ---- */
  const [guided, setGuided] = useState<GuidedState>(() => {
    // From builder/import prefill (backward compat)
    if (hasPrefill && prefillText) {
      try {
        let parsed;
        try { parsed = JSON.parse(prefillText); }
        catch { parsed = yaml.load(prefillText) as Record<string, unknown>; }
        if (parsed && typeof parsed === "object") return parseManifestToGuided(parsed as Record<string, unknown>);
      } catch { /* defaults */ }
    }
    // From restored draft
    if (typeof window !== "undefined") {
      const draft = restoreDraft();
      if (draft?.guided && typeof draft.guided === "object") {
        // Restore tab state too
        if (draft.tab) {
          restoredDraftTabRef.current = draft.tab;
          setTimeout(() => setActiveTab(draft.tab), 0);
        }
        if (draft.description) setTimeout(() => setDescriptionText(draft.description!), 0);
        if (draft.importPlatform) setTimeout(() => setImportPlatform(draft.importPlatform!), 0);
        if (draft.importCode) setTimeout(() => setImportCode(draft.importCode!), 0);
        if (draft.manifestText) setTimeout(() => setManifestInput(draft.manifestText!), 0);
        if (draft.source) setTimeout(() => setSource(draft.source!), 0);
        // Restore import conversion metadata
        if (draft.importConfidence) setTimeout(() => setImportConfidence(draft.importConfidence!), 0);
        if (typeof draft.importDraftReady === "boolean") setTimeout(() => setImportDraftReady(draft.importDraftReady!), 0);
        if (draft.importWarnings?.length) setTimeout(() => setImportWarnings(draft.importWarnings!), 0);
        if (draft.importGroupedWarnings?.length) setTimeout(() => setImportGroupedWarnings(draft.importGroupedWarnings!), 0);
        if (draft.importChanges?.length) setTimeout(() => setImportChanges(draft.importChanges!), 0);
        clearDraft();
        // Merge with defaults to prevent crashes from incomplete/corrupted drafts
        const defaults = { ...DEFAULT_GUIDED, tools: [{ ...EMPTY_TOOL }] };
        const g = draft.guided as unknown as Record<string, unknown>;
        const safeTools = Array.isArray(g.tools)
          ? g.tools.map((t: Record<string, unknown>) => ({ ...EMPTY_TOOL, ...Object.fromEntries(Object.entries(t).filter(([, v]) => typeof v === "string")) }))
          : defaults.tools;
        return {
          ...defaults,
          ...Object.fromEntries(Object.entries(g).filter(([k, v]) => k !== "tools" && typeof v === "string")),
          tools: safeTools,
          // Preserve arrays/non-string fields from defaults if not in draft
          frameworks: Array.isArray(g.frameworks) ? g.frameworks as string[] : defaults.frameworks,
          use_cases: Array.isArray(g.use_cases) ? g.use_cases as string[] : defaults.use_cases,
          examples: Array.isArray(g.examples) ? g.examples as { title: string; language: string; code: string }[] : defaults.examples,
          env_requirements: Array.isArray(g.env_requirements) ? g.env_requirements as { name: string; required: boolean; description: string }[] : defaults.env_requirements,
          package_type: (typeof g.package_type === "string" ? g.package_type : defaults.package_type) as GuidedState["package_type"],
        };
      }
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

  // Phase 2: populate code files from import prefill once available
  const prefillAppliedRef = useRef(false);
  useEffect(() => {
    if (_prefillFiles?.length && !prefillAppliedRef.current) {
      prefillAppliedRef.current = true;
      setCodeFiles(_prefillFiles);
    }
  }, [_prefillFiles]);
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
  const [permissionsTouched, setPermissionsTouched] = useState(false);

  /* ---- No-code inline warning (replaces window.confirm) ---- */
  const [showNoCodeConfirm, setShowNoCodeConfirm] = useState(false);

  /* ---- JSON schema validation warnings per tool index ---- */
  const [schemaWarnings, setSchemaWarnings] = useState<Record<number, { input?: boolean; output?: boolean }>>({});

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

  // Default tab: non-logged-in users land on "manifest" (free, no account required)
  // Builder and Import have their own dedicated pages; Publish defaults to paste-manifest.
  useEffect(() => {
    if (!authChecked) return;
    if (user) return; // logged-in users keep default
    if (tabParam) return; // URL param has priority (Rule 6)
    // Don't override if a draft was restored that was on "describe"
    if (restoredDraftTabRef.current === "describe") return;
    if (activeTab === "describe") {
      setActiveTab("manifest");
    }
  }, [authChecked, user, tabParam]); // eslint-disable-line react-hooks/exhaustive-deps

  // Load capabilities
  useEffect(() => {
    fetch("/api/v1/resolution/capabilities")
      .then((r) => (r.ok ? r.json() : []))
      .then((data: CapabilityOption[]) => { if (Array.isArray(data)) setCapabilities(data); })
      .catch(() => {});
  }, []);

  // Restore builder artifact from publish_prefill_artifact (backward compat)
  useEffect(() => {
    if (fromSource === "builder" && typeof window !== "undefined") {
      const raw = sessionStorage.getItem("publish_prefill_artifact");
      if (raw) {
        sessionStorage.removeItem("publish_prefill_artifact");
        try {
          const { artifactFiles, artifactName } = JSON.parse(raw);
          if (artifactFiles && artifactName) {
            fetch(artifactFiles)
              .then((r) => r.blob())
              .then((blob) => {
                setArtifact(new File([blob], artifactName, { type: "application/gzip" }));
                setBuilderArtifactName(artifactName);
                setArtifactMode("upload");
              })
              .catch(() => {});
          }
        } catch { /* ignore */ }
      }
    }
  }, [fromSource]);

  // Check for expired draft on mount
  useEffect(() => {
    if (typeof window !== "undefined" && !hasPrefill) {
      const raw = sessionStorage.getItem(DRAFT_KEY);
      if (raw) {
        try {
          const draft = JSON.parse(raw);
          if (Date.now() - draft.createdAt > DRAFT_TTL) {
            sessionStorage.removeItem(DRAFT_KEY);
            setDraftExpired(true);
          }
        } catch { /* ignore */ }
      }
    }
  }, [hasPrefill]);

  // Show draft-save banner once when entering draft review
  useEffect(() => {
    if (screen === "draft" && guided.name && !draftSaveShownRef.current) {
      draftSaveShownRef.current = true;
      setDraftSaveBanner(true);
      const timer = setTimeout(() => setDraftSaveBanner(false), 8000);
      return () => clearTimeout(timer);
    }
  }, [screen, guided.name]);

  // Background auto-validation on edit screen
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

  const hasValidatedOnce = useRef(false);
  useEffect(() => {
    if ((screen !== "edit" && screen !== "draft") || !user?.publisher) return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    const delay = hasValidatedOnce.current ? 800 : 0;
    debounceRef.current = setTimeout(() => {
      hasValidatedOnce.current = true;
      const manifest = buildManifestFromGuided(guided, user.publisher!.slug);
      runValidation(manifest);
    }, delay);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [guided, screen, user, runValidation]);

  // Reset no-code warning when code files become non-empty or screen changes
  useEffect(() => {
    const hasCode = codeFiles.some((f) => f.content.trim());
    if (hasCode) setShowNoCodeConfirm(false);
  }, [codeFiles]);
  useEffect(() => { setShowNoCodeConfirm(false); }, [screen]);

  // Validate JSON schemas per tool and track warnings
  useEffect(() => {
    const warnings: Record<number, { input?: boolean; output?: boolean }> = {};
    guided.tools.forEach((tool, i) => {
      const entry: { input?: boolean; output?: boolean } = {};
      if (tool.input_schema.trim()) {
        try { JSON.parse(tool.input_schema); } catch { entry.input = true; }
      }
      if (tool.output_schema.trim()) {
        try { JSON.parse(tool.output_schema); } catch { entry.output = true; }
      }
      if (entry.input || entry.output) warnings[i] = entry;
    });
    setSchemaWarnings(warnings);
  }, [guided.tools]);

  /* ================================================================ */
  /*  Handlers                                                         */
  /* ================================================================ */

  function navigateToIssue(target: "name" | "artifact" | "tools") {
    const panelMap: Record<string, string> = { name: "basics", artifact: "artifact", tools: "tools" };
    const panel = panelMap[target];
    setScreen("edit");
    setOpenPanels((prev) => new Set([...prev, panel]));
    // Scroll to panel after render
    setTimeout(() => {
      const el = document.querySelector(`[data-panel="${panel}"]`);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 100);
  }

  function togglePanel(panel: string) {
    setOpenPanels((prev) => {
      const next = new Set(prev);
      next.has(panel) ? next.delete(panel) : next.add(panel);
      return next;
    });
  }

  /* ---- Describe tab: Generate ---- */
  async function handleGenerate() {
    if (descriptionText.trim().length < 10) {
      setError("Please describe your tool in more detail (at least 10 characters).");
      return;
    }
    setGenerating(true);
    setError("");

    const res = await generateSkill(descriptionText);

    if (res.status === 401) {
      // Save draft and prompt login
      saveDraft({
        tab: "describe",
        description: descriptionText,
        createdAt: Date.now(),
      });
      setError("Sign in to generate your skill.");
      setGenerating(false);
      return;
    }

    if (res.ok && res.data) {
      setBuilderResult(res.data);
      if (res.data.code_files?.length) {
        setCodeFiles(res.data.code_files);
        set_prefillFiles(res.data.code_files);
      }
      const g = parseManifestToGuided(res.data.manifest_json);
      setGuided(g);
      setSource("builder");
      setScreen("draft");
    } else {
      setError(res.error || "Generation failed.");
    }
    setGenerating(false);
  }

  /* ---- Import tab: Convert ---- */
  const [importConverting, setImportConverting] = useState(false);

  function handleConvert() {
    if (!importCode.trim()) {
      setError("Paste your tool code above.");
      return;
    }
    setError("");
    setImportConverting(true);

    const doConvert = async () => {
      let usedFallback = false;
      let manifest = "";
      let apiCodeFiles: CodeFile[] = [];
      let apiConfidence: { level: string; reasons: string[] } | null = null;
      let apiWarnings: string[] = [];
      let apiGroupedWarnings: { message: string; category: "blocking" | "review" | "info" }[] = [];
      let apiChanges: string[] = [];
      let apiDraftReady: boolean | null = null;

      try {
        const res = await fetch("/api/v1/import/convert", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ platform: importPlatform, content: importCode }),
        });
        if (res.ok) {
          const data = await res.json();
          manifest = data.manifest_yaml || data.manifest || "";
          // Extract code_files from API response
          if (Array.isArray(data.code_files) && data.code_files.length > 0) {
            apiCodeFiles = data.code_files;
          }
          if (data.confidence) {
            apiConfidence = data.confidence;
          }
          if (Array.isArray(data.warnings)) {
            apiWarnings = data.warnings;
          }
          if (Array.isArray(data.grouped_warnings)) {
            apiGroupedWarnings = data.grouped_warnings;
          }
          if (Array.isArray(data.changes)) {
            apiChanges = data.changes;
          }
          if (typeof data.draft_ready === "boolean") {
            apiDraftReady = data.draft_ready;
          }
        } else {
          usedFallback = true;
          manifest = convertClientSide(importPlatform, importCode);
        }
      } catch {
        usedFallback = true;
        manifest = convertClientSide(importPlatform, importCode);
      }

      return { manifest, apiCodeFiles, apiConfidence, apiWarnings, apiGroupedWarnings, apiChanges, apiDraftReady, usedFallback };
    };

    // Clear stale import metadata before new conversion
    setImportConfidence(null);
    setImportDraftReady(null);
    setImportWarnings([]);
    setImportGroupedWarnings([]);
    setImportChanges([]);

    doConvert().then(({ manifest, apiCodeFiles, apiConfidence, apiWarnings, apiGroupedWarnings, apiChanges, apiDraftReady, usedFallback }) => {
      if (manifest.startsWith("# No tools") || !manifest.trim()) {
        setError("No tools detected. Check your input format and try again.");
        return;
      }

      // Populate code files from API response
      if (apiCodeFiles.length > 0) {
        setCodeFiles(apiCodeFiles);
        set_prefillFiles(apiCodeFiles);
      }
      // Populate import conversion metadata
      if (apiConfidence) {
        setImportConfidence(apiConfidence);
      }
      if (apiWarnings.length > 0) {
        setImportWarnings(apiWarnings);
      }
      if (apiGroupedWarnings.length > 0) {
        setImportGroupedWarnings(apiGroupedWarnings);
      }
      if (apiChanges.length > 0) {
        setImportChanges(apiChanges);
      }
      if (apiDraftReady !== null) {
        setImportDraftReady(apiDraftReady);
      }

      // Show fallback warning so user knows conversion was client-side only
      if (usedFallback) {
        setImportWarnings(prev => [
          "Server-side conversion unavailable. Used simplified client-side conversion (no code files generated, no dependency analysis).",
          ...prev,
        ]);
        setImportConfidence({ level: "low", reasons: ["Client-side fallback — no AST analysis performed"] });
      }

      const result = parseResult(manifest, importPlatform);
      // Parse YAML manifest to GuidedState
      try {
        const parsed = yaml.load(result.manifest) as Record<string, unknown>;
        if (parsed && typeof parsed === "object") {
          setGuided(parseManifestToGuided(parsed));
        }
      } catch {
        // Best-effort: set basics from result
        setGuided(prev => ({
          ...prev,
          package_id: result.packageId,
          name: result.packageId.split("-").map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(" "),
          tools: result.tools.map(t => ({
            ...EMPTY_TOOL,
            name: t.name,
            description: t.description,
            capability_id: t.capability_id,
          })),
        }));
      }
      const selectedPlatform = PLATFORMS.find(p => p.id === importPlatform);
      setSource(`import:${selectedPlatform?.name || importPlatform}`);
      setScreen("draft");
    }).finally(() => {
      setImportConverting(false);
    });
  }

  /* ---- Manifest tab: Continue ---- */
  function handleContinueWithManifest() {
    setError("");
    try {
      let parsed;
      try { parsed = JSON.parse(manifestInput); }
      catch { parsed = yaml.load(manifestInput) as Record<string, unknown>; }
      if (!parsed || typeof parsed !== "object") throw new Error("Invalid");
      setGuided(parseManifestToGuided(parsed as Record<string, unknown>));
      setSource("manifest");
      setOpenPanels(new Set());
      setScreen("draft");
    } catch {
      setError("Invalid JSON or YAML. Please check your manifest format.");
    }
  }

  /* ---- Start fresh ---- */
  function handleStartFresh() {
    setGuided({ ...DEFAULT_GUIDED, tools: [{ ...EMPTY_TOOL }] });
    setOpenPanels(new Set(["basics"]));
    setValidation(null);
    setError("");
    setSource(null);
    setImportConfidence(null);
    setImportDraftReady(null);
    setImportWarnings([]);
    setImportGroupedWarnings([]);
    setImportChanges([]);
    setCodeFiles([{ path: "my_tool/tool.py", content: "" }]);
    setScreen("edit");
  }

  /* ---- Publisher creation ---- */
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
      if (!artifactToSend && artifactMode === "code") {
        const nonEmpty = codeFiles.filter((f) => f.content.trim());
        if (nonEmpty.length === 0 && !showNoCodeConfirm) {
          setShowNoCodeConfirm(true);
          setLoading(false);
          return;
        }
      }
      const formData = new FormData();
      formData.append("manifest", JSON.stringify(parsed));
      if (artifactToSend) formData.append("artifact", artifactToSend);

      const res = await fetchWithAuth("/packages/publish", { method: "POST", body: formData });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error?.message || "Publish failed");

      clearDraft();
      setSuccess(`Published ${data.slug}@${data.version}`);

      // Post-publish invite callback: mark candidate as published in funnel
      try {
        const inviteCode = typeof window !== "undefined" ? sessionStorage.getItem("invite_code") : null;
        if (inviteCode) {
          sessionStorage.removeItem("invite_code");
          await fetchWithAuth(`/invites/${encodeURIComponent(inviteCode)}/published`, { method: "POST" })
            .catch(() => {}); // Don't break publish on callback failure
        }
      } catch {
        // Silently ignore — publish succeeded, callback is best-effort
      }

      setTimeout(() => router.push(`/packages/${data.slug}`), 1500);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }

  /* ---- Publish from Draft Review (with auth gate) ---- */
  function handlePublishFromDraft() {
    if (!user) {
      // Save full draft and prompt login
      saveDraft({
        tab: activeTab,
        description: descriptionText,
        importPlatform,
        importCode,
        manifestText: manifestInput,
        guided,
        source: source || undefined,
        createdAt: Date.now(),
        importConfidence: importConfidence || undefined,
        importDraftReady: importDraftReady ?? undefined,
        importWarnings: importWarnings.length ? importWarnings : undefined,
        importGroupedWarnings: importGroupedWarnings.length ? importGroupedWarnings : undefined,
        importChanges: importChanges.length ? importChanges : undefined,
      });
      setError("Sign in to publish your skill.");
      return;
    }
    if (!user.publisher) {
      // Publisher creation will be shown inline
      return;
    }
    handlePublish();
  }

  function copyManifest() {
    const text = JSON.stringify(buildManifestFromGuided(guided, user?.publisher?.slug || "your-publisher"), null, 2);
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
  /*  Render: SCREEN 1 — INPUT HUB                                    */
  /* ================================================================ */

  if (screen === "input") {
    const loginReturnTo = `/publish?tab=${activeTab}`;

    return (
      <div className="mx-auto max-w-3xl px-4 sm:px-6 py-12">
        <StepIndicator current={1} />
        {/* Header */}
        <div className="text-center mb-10">
          <h1 className="text-3xl font-bold tracking-tight text-foreground">
            Publish your skill
          </h1>
          <p className="mt-3 text-muted">
            Describe, import, or paste — we&apos;ll handle the rest.
          </p>
        </div>

        {/* Draft expired notice */}
        {draftExpired && (
          <div className="mb-6 rounded-lg border border-yellow-500/20 bg-yellow-500/5 px-4 py-3 text-sm text-yellow-500">
            Your temporary draft expired. Start again to continue.
          </div>
        )}

        {/* Tab selector */}
        <div className="mb-8 flex justify-center">
          <div className="inline-flex rounded-full border border-border bg-card p-1">
            {([
              { id: "describe" as InputTab, label: "Describe it" },
              { id: "import" as InputTab, label: "Import code" },
              { id: "manifest" as InputTab, label: "Paste manifest" },
            ]).map((tab) => (
              <button
                key={tab.id}
                onClick={() => { setActiveTab(tab.id); setError(""); setDraftExpired(false); }}
                className={`rounded-full px-5 py-2 text-sm font-medium transition-all ${
                  activeTab === tab.id
                    ? "bg-primary text-white shadow-sm"
                    : "text-muted hover:text-foreground"
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>

        {/* Error */}
        {error && (
          <div className="mb-4 rounded-md border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">
            {error}
            {error.includes("Sign in") && (
              <span className="ml-2">
                <Link href={`/auth/login?returnTo=${encodeURIComponent(loginReturnTo)}`} className="underline font-medium">
                  Sign in
                </Link>
                {" or "}
                <Link href={`/auth/register?returnTo=${encodeURIComponent(loginReturnTo)}`} className="underline font-medium">
                  create account
                </Link>
              </span>
            )}
          </div>
        )}

        {/* ---- TAB: Describe ---- */}
        {activeTab === "describe" && (
          <div className="space-y-6">
            <div>
              <label className="mb-2 block text-sm font-medium text-foreground">
                Describe what your skill does in plain English
              </label>
              <textarea
                rows={4}
                value={descriptionText}
                onChange={(e) => { setDescriptionText(e.target.value); setError(""); }}
                className="w-full rounded-xl border border-border bg-card px-4 py-3 text-sm text-foreground placeholder:text-muted/60 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary/30 resize-none"
                placeholder='e.g. "A tool that extracts email addresses from a webpage and returns them as a list"'
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleGenerate();
                }}
              />
            </div>

            {/* Example chips */}
            <div className="flex flex-wrap gap-2">
              <span className="text-xs text-muted">Try:</span>
              {BUILDER_EXAMPLES.slice(0, 4).map((ex) => (
                <button
                  key={ex}
                  onClick={() => setDescriptionText(ex)}
                  className="rounded-full border border-border bg-card px-3 py-1 text-xs text-muted transition-colors hover:border-primary/30 hover:text-foreground"
                >
                  {ex.length > 45 ? ex.slice(0, 45) + "\u2026" : ex}
                </button>
              ))}
            </div>

            <div className="flex items-center gap-4">
              <button
                onClick={handleGenerate}
                disabled={generating || descriptionText.trim().length < 10}
                className="rounded-xl bg-primary px-8 py-3 text-sm font-semibold text-white transition-all hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {generating ? (
                  <span className="flex items-center gap-2">
                    <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                    Generating&hellip;
                  </span>
                ) : (
                  "Generate"
                )}
              </button>
              <span className="text-xs text-muted">Ctrl+Enter &middot; Requires login</span>
            </div>
          </div>
        )}

        {/* ---- TAB: Import ---- */}
        {activeTab === "import" && (
          <div className="space-y-6">
            {/* Platform pills */}
            <div className="flex flex-wrap gap-2">
              {PLATFORMS.map((p) => (
                <button
                  key={p.id}
                  onClick={() => { setImportPlatform(p.id); setError(""); }}
                  className={`flex items-center gap-2 rounded-full border px-4 py-2 text-sm font-medium transition-all ${
                    importPlatform === p.id
                      ? "border-primary bg-primary/10 text-primary"
                      : "border-border text-muted hover:border-primary/30 hover:text-foreground"
                  }`}
                >
                  <span>{p.icon}</span>
                  {p.name}
                </button>
              ))}
            </div>

            {/* Code textarea */}
            <div className="relative">
              <textarea
                rows={10}
                value={importCode}
                onChange={(e) => { setImportCode(e.target.value); setError(""); }}
                className="w-full rounded-xl border border-border bg-card px-4 py-3 font-mono text-sm text-foreground placeholder:text-muted/40 focus:border-primary/50 focus:outline-none focus:ring-1 focus:ring-primary/20 transition-all"
                placeholder={`Paste your ${PLATFORMS.find(p => p.id === importPlatform)?.name || ""} tool code here...`}
                spellCheck={false}
              />
              <button
                onClick={() => {
                  const p = PLATFORMS.find(pl => pl.id === importPlatform);
                  if (p) setImportCode(p.example);
                }}
                className="absolute right-3 top-3 rounded-lg border border-primary/30 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary transition-colors hover:bg-primary/20"
              >
                Try example
              </button>
            </div>

            <div className="flex items-center gap-4">
              <button
                onClick={handleConvert}
                disabled={importConverting || !importCode.trim()}
                className="rounded-xl bg-primary px-8 py-3 text-sm font-semibold text-white transition-all hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {importConverting ? (
                  <span className="flex items-center gap-2">
                    <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                    Converting&hellip;
                  </span>
                ) : (
                  "Convert & continue"
                )}
              </button>
              <span className="text-xs text-muted">Free &mdash; no account required</span>
            </div>
          </div>
        )}

        {/* ---- TAB: Paste Manifest ---- */}
        {activeTab === "manifest" && (
          <div className="space-y-6">
            <div>
              <label className="mb-2 block text-sm font-medium text-foreground">
                Paste your manifest <span className="text-xs text-muted font-normal">(JSON or YAML)</span>
              </label>
              <textarea
                rows={12}
                value={manifestInput}
                onChange={(e) => { setManifestInput(e.target.value); setError(""); }}
                className="w-full rounded-xl border border-border bg-card px-4 py-3 font-mono text-sm text-foreground placeholder:text-muted/40 focus:border-primary/50 focus:outline-none focus:ring-1 focus:ring-primary/20 transition-all"
                placeholder={'{\n  "manifest_version": "0.2",\n  "package_id": "my-tool",\n  "name": "My Tool",\n  "version": "1.0.0",\n  "summary": "What my tool does",\n  "capabilities": {\n    "tools": [\n      { "name": "my_func", "capability_id": "..." }\n    ]\n  }\n}'}
                spellCheck={false}
              />
            </div>

            <div className="flex items-center gap-4">
              <button
                onClick={handleContinueWithManifest}
                disabled={!manifestInput.trim()}
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
        )}

        {/* SEO content */}
        <div className="mt-16 border-t border-border pt-10 space-y-10 text-sm text-muted">
          <div className="text-center max-w-2xl mx-auto space-y-3">
            <h2 className="text-lg font-semibold text-foreground">Publish AI skills for any agent framework</h2>
            <p>
              AgentNode is the open registry for agent skills. Publish tools, resources, and prompts that any AI agent can discover and install &mdash; across LangChain, CrewAI, AutoGen, OpenAI Agents, and more.
            </p>
          </div>

          <div className="grid gap-8 sm:grid-cols-3">
            <div>
              <h3 className="mb-2 font-medium text-foreground">Three ways to publish</h3>
              <p>
                <strong className="text-foreground">Describe it</strong> &mdash; tell us what your skill does in plain English and our AI generates the manifest and code scaffold.{" "}
                <strong className="text-foreground">Import code</strong> &mdash; paste an existing LangChain tool, CrewAI tool, or MCP server and we convert it automatically.{" "}
                <strong className="text-foreground">Paste manifest</strong> &mdash; drop in your ANP manifest JSON or YAML directly.
              </p>
            </div>
            <div>
              <h3 className="mb-2 font-medium text-foreground">What gets published</h3>
              <p>
                Every skill includes a versioned manifest, capability declarations, permission model, and optional code artifact. Agents use this metadata to discover, install, and safely execute your skill at runtime.
              </p>
            </div>
            <div>
              <h3 className="mb-2 font-medium text-foreground">Built for trust</h3>
              <p>
                Published skills go through automated verification, quality gates, and optional manual review. The permission system ensures agents only get the access they need &mdash; network, filesystem, code execution, and data access are all explicitly declared.
              </p>
            </div>
          </div>

          <div className="grid gap-8 sm:grid-cols-2">
            <div>
              <h3 className="mb-2 font-medium text-foreground">Supported frameworks</h3>
              <p>
                Import existing tools from <strong className="text-foreground">LangChain</strong>, <strong className="text-foreground">CrewAI</strong>, <strong className="text-foreground">AutoGen</strong>, <strong className="text-foreground">OpenAI Agents SDK</strong>, <strong className="text-foreground">Haystack</strong>, <strong className="text-foreground">MCP servers</strong>, and <strong className="text-foreground">smolagents</strong>. The converter handles schema mapping, capability detection, and runtime configuration automatically.
              </p>
            </div>
            <div>
              <h3 className="mb-2 font-medium text-foreground">Open and free</h3>
              <p>
                Publishing on AgentNode is free for all developers. Skills are discoverable via search, API, and the <Link href="/docs/cli" className="text-primary hover:underline">CLI</Link>. The registry is built on the open <Link href="/docs/anp" className="text-primary hover:underline">Agent Node Package (ANP)</Link> specification.
              </p>
            </div>
          </div>
        </div>
      </div>
    );
  }

  /* ================================================================ */
  /*  Render: SCREEN 2 — DRAFT REVIEW                                  */
  /* ================================================================ */

  if (screen === "draft") {
    const hasArtifact = !!(builderArtifactName || artifact || tarGzFile);
    const { canPublish, items } = computeReadiness(guided, hasArtifact, source, codeFiles);
    const toolCount = guided.tools.filter((t) => t.name).length;

    // Determine source banner text
    let sourceBanner: string | null = null;
    if (source === "builder") {
      sourceBanner = "Generated with AI Builder \u2014 review recommended before publishing";
    } else if (source?.startsWith("import:")) {
      const framework = source.split(":")[1];
      sourceBanner = `Converted from ${framework} \u2014 review recommended before publishing`;
    }

    // Auth state for inline gates
    const isAuthed = !!user;
    const hasPublisher = !!user?.publisher;
    const canDoPublish = canPublish && isAuthed && hasPublisher;
    const loginReturnTo = `/publish?tab=${activeTab}`;

    return (
      <div className="mx-auto max-w-3xl px-4 sm:px-6 py-12">
        <StepIndicator current={2} />
        {/* Header */}
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-foreground">
              {canPublish ? "Your skill is ready to publish" : "Review your skill"}
            </h1>
            {user?.publisher && (
              <p className="text-sm text-muted">
                Publishing as <span className="text-primary font-medium">@{user.publisher.slug}</span>
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={() => { setScreen("input"); setError(""); setSuccess(""); }}
            className="text-sm text-muted hover:text-foreground transition-colors"
          >
            &#8592; Start over
          </button>
        </div>

        {/* Source banner */}
        {sourceBanner && (
          <div className="mb-6 rounded-lg border border-primary/30 bg-primary/5 px-4 py-3 text-sm text-primary">
            {sourceBanner}
          </div>
        )}

        {/* Draft save banner */}
        {draftSaveBanner && (
          <div className="mb-6 rounded-lg border border-border bg-card px-4 py-3 text-xs text-muted flex items-center justify-between">
            <span>Draft saved to this tab. It will be lost if you close this tab.</span>
            <button type="button" onClick={() => setDraftSaveBanner(false)} className="text-muted hover:text-foreground ml-4">
              &#10005;
            </button>
          </div>
        )}

        {/* Import conversion metadata (collapsed — 3.6 alert-fatigue reduction) */}
        {source?.startsWith("import") && importConfidence && (() => {
          const platform = source?.startsWith("import:") ? source.split(":")[1] : "import";
          const noteCount = importGroupedWarnings.length + importWarnings.length + importChanges.length;
          const colorClass = importConfidence.level === "high" ? "border-green-500/30 bg-green-500/5"
            : importConfidence.level === "medium" ? "border-yellow-500/30 bg-yellow-500/5"
            : "border-red-500/30 bg-red-500/5";
          const textClass = importConfidence.level === "high" ? "text-green-400"
            : importConfidence.level === "medium" ? "text-yellow-400" : "text-red-400";

          return (
            <div className="mb-6">
              <details className={`rounded-lg border ${colorClass} group`}>
                <summary className="cursor-pointer px-4 py-3 flex items-center justify-between">
                  <span className="text-sm">
                    <span className={`font-semibold ${textClass}`}>
                      Converted from {platform}
                    </span>
                    <span className="text-muted"> &mdash; {importConfidence.level} confidence</span>
                    {noteCount > 0 && <span className="text-muted">, {noteCount} note{noteCount !== 1 ? "s" : ""}</span>}
                  </span>
                  <span className="text-xs text-muted group-open:hidden">Show details</span>
                  <span className="text-xs text-muted hidden group-open:inline">Hide details</span>
                </summary>
                <div className="px-4 pb-4 space-y-3 border-t border-border/50 pt-3">
                  {importConfidence.reasons.length > 0 && importConfidence.level !== "high" && (
                    <ul className="space-y-0.5">
                      {importConfidence.reasons.map((r, i) => (
                        <li key={i} className="text-xs text-muted">- {r}</li>
                      ))}
                    </ul>
                  )}
                  {importChanges.length > 0 && (
                    <div>
                      <div className="text-xs font-medium text-muted mb-1">Changes</div>
                      <ul className="space-y-0.5">
                        {importChanges.map((c, i) => (
                          <li key={i} className="text-xs text-foreground/80">- {c}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {importGroupedWarnings.length > 0 && (
                    <div>
                      {importGroupedWarnings.filter(w => w.category === "blocking").length > 0 && (
                        <div className="mb-2">
                          <div className="text-xs font-semibold text-red-400 mb-1">Blocking</div>
                          <ul className="space-y-0.5">
                            {importGroupedWarnings.filter(w => w.category === "blocking").map((w, i) => (
                              <li key={i} className="text-xs text-red-300/80">- {w.message}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                      {importGroupedWarnings.filter(w => w.category !== "blocking").map((w, i) => (
                        <div key={i} className="text-xs text-muted">- {w.message}</div>
                      ))}
                    </div>
                  )}
                  {!importGroupedWarnings.length && importWarnings.length > 0 && (
                    <div>
                      {importWarnings.map((w, i) => (
                        <div key={i} className="text-xs text-yellow-300/80">- {w}</div>
                      ))}
                    </div>
                  )}
                </div>
              </details>
            </div>
          );
        })()}

        {/* Alerts */}
        {error && (
          <div className="mb-4 rounded-md border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">
            {error}
            {error.includes("Sign in") && (
              <span className="ml-2">
                <Link href={`/auth/login?returnTo=${encodeURIComponent(loginReturnTo)}`} className="underline font-medium">
                  Sign in
                </Link>
                {" or "}
                <Link href={`/auth/register?returnTo=${encodeURIComponent(loginReturnTo)}`} className="underline font-medium">
                  create account
                </Link>
              </span>
            )}
          </div>
        )}
        {success && (
          <div className="mb-4 rounded-md border border-success/30 bg-success/10 px-4 py-3 text-sm text-success">{success}</div>
        )}

        {/* ---- Preview Card ---- */}
        <div className="mb-6 rounded-xl border border-border bg-card p-6">
          <h2 className="text-lg font-bold text-foreground truncate">
            {guided.name || "Untitled skill"}
          </h2>
          <div className="mt-0.5 font-mono text-xs text-muted">
            {guided.package_id || "no-package-id"} &middot; v{guided.version}
          </div>
          {guided.summary && (
            <p className="mt-2 text-sm text-muted line-clamp-2">{guided.summary}</p>
          )}
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {guided.tools.filter(t => t.capability_id).map((t, i) => (
              <span key={i} className="rounded-full border border-primary/30 bg-primary/5 px-2.5 py-0.5 text-xs text-primary">
                {t.capability_id}
              </span>
            ))}
            <span className="rounded-full border border-border bg-background px-2.5 py-0.5 text-xs text-muted">
              {toolCount} tool{toolCount !== 1 ? "s" : ""}
            </span>
          </div>
        </div>

        {/* ---- Readiness Checklist (interactive) ---- */}
        <div className="mb-6">
          <ReadinessChecklist items={items} onNavigate={navigateToIssue} />
        </div>

        {/* ---- Code Status ---- */}
        <div className="mb-6">
          {(() => {
            const nonEmptyCode = codeFiles.filter((f) => f.content.trim());
            const hasAnyCode = nonEmptyCode.length > 0 || !!builderArtifactName || !!tarGzFile || uploadedFiles.length > 0;
            if (hasAnyCode) {
              const fileNames = builderArtifactName
                ? [builderArtifactName]
                : tarGzFile
                ? [tarGzFile.name]
                : uploadedFiles.length > 0
                ? uploadedFiles.map((f) => f.name)
                : nonEmptyCode.map((f) => f.path);
              return (
                <div className="rounded-lg border border-border bg-card/50 px-4 py-3 flex items-center justify-between">
                  <div className="text-sm text-foreground">
                    <span className="text-success mr-1.5">&#10003;</span>
                    {fileNames.length} file{fileNames.length !== 1 ? "s" : ""}: {fileNames.slice(0, 3).join(", ")}
                    {fileNames.length > 3 && ` +${fileNames.length - 3} more`}
                  </div>
                  <button
                    type="button"
                    onClick={() => navigateToIssue("artifact")}
                    className="text-xs text-primary hover:underline"
                  >
                    Edit code
                  </button>
                </div>
              );
            }
            return (
              <div className="rounded-lg border border-primary/30 bg-primary/5 px-5 py-4 text-center">
                <p className="text-sm font-medium text-foreground mb-1">Add your code</p>
                <p className="text-xs text-muted mb-3">Upload files or write code to make your skill installable</p>
                <button
                  type="button"
                  onClick={() => navigateToIssue("artifact")}
                  className="rounded-md bg-primary px-5 py-2 text-sm font-medium text-white hover:bg-primary/90 transition-colors"
                >
                  Add code
                </button>
              </div>
            );
          })()}
        </div>

        {/* ---- Quick Edits ---- */}
        <div className="mb-6 rounded-xl border border-border p-5 space-y-4">
          <h3 className="text-sm font-medium text-foreground">Quick edits</h3>
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-xs text-muted">Name</label>
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
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
                placeholder="My PDF Extractor"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-muted">Summary</label>
              <input
                type="text"
                value={guided.summary}
                onChange={(e) => updateGuided("summary", e.target.value)}
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
                placeholder="Extract text from PDF files"
              />
            </div>
          </div>
        </div>

        {/* ---- Action Buttons ---- */}
        <div className="flex items-center justify-between pt-4 border-t border-border">
          <button
            type="button"
            onClick={() => {
              setOpenPanels(new Set(["basics", "artifact"]));
              setScreen("edit");
            }}
            className="rounded-md border border-border px-5 py-2.5 text-sm text-muted hover:text-foreground transition-colors"
          >
            Edit details &amp; add code
          </button>

          {!isAuthed ? (
            <div className="flex items-center gap-3">
              <span className="text-sm text-muted">Sign in to publish</span>
              <Link
                href={`/auth/login?returnTo=${encodeURIComponent(loginReturnTo)}`}
                onClick={() => {
                  saveDraft({
                    tab: activeTab,
                    description: descriptionText,
                    importPlatform,
                    importCode,
                    manifestText: manifestInput,
                    guided,
                    source: source || undefined,
                    createdAt: Date.now(),
                    importConfidence: importConfidence || undefined,
                    importDraftReady: importDraftReady ?? undefined,
                    importWarnings: importWarnings.length ? importWarnings : undefined,
                    importGroupedWarnings: importGroupedWarnings.length ? importGroupedWarnings : undefined,
                    importChanges: importChanges.length ? importChanges : undefined,
                  });
                }}
                className="rounded-md bg-primary px-6 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-primary/90"
              >
                Sign in
              </Link>
            </div>
          ) : !hasPublisher ? (
            <div className="text-right">
              <p className="text-xs text-muted mb-2">Create a publisher to continue</p>
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  value={pubDisplayName}
                  onChange={(e) => {
                    setPubDisplayName(e.target.value);
                    if (!pubSlug || pubSlug === pubDisplayName.toLowerCase().replace(/[^a-z0-9-]/g, "-").replace(/-+/g, "-")) {
                      setPubSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-").replace(/-+/g, "-"));
                    }
                  }}
                  className="rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
                  placeholder="Publisher name"
                />
                <button
                  onClick={createPublisher}
                  disabled={creatingPublisher || !pubDisplayName}
                  className="rounded-md bg-primary px-5 py-2 text-sm font-medium text-white hover:bg-primary/90 disabled:opacity-50"
                >
                  {creatingPublisher ? "Creating..." : "Create"}
                </button>
              </div>
            </div>
          ) : (
            <>
              <button
                type="button"
                onClick={handlePublishFromDraft}
                disabled={!canDoPublish || loading || buildingArtifact || (source?.startsWith("import") && importDraftReady === false)}
                className="rounded-md bg-primary px-8 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-primary/90 disabled:opacity-50"
              >
                {source?.startsWith("import") && importDraftReady === false
                  ? "Fix issues first"
                  : buildingArtifact ? "Building artifact..." : loading ? "Publishing..." : "Publish now"}
              </button>
              {source?.startsWith("import") && importDraftReady === false && (
                <p className="mt-2 text-center text-xs text-danger">
                  The import conversion has issues that must be resolved before publishing.
                </p>
              )}
            </>
          )}
        </div>

        {/* Validation status */}
        {validating && (
          <p className="mt-3 text-center text-xs text-muted">Validating...</p>
        )}
        {validation && !validation.valid && (
          <div className="mt-4 rounded-md border border-danger/30 bg-danger/5 px-4 py-3 text-sm">
            <div className="font-medium text-danger mb-1">Validation issues:</div>
            <ul className="space-y-0.5">
              {validation.errors.map((err, i) => (
                <li key={i} className="flex items-start gap-2 text-danger text-xs">
                  <span className="mt-0.5 shrink-0">&bull;</span>
                  <span>{err}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    );
  }

  /* ================================================================ */
  /*  Auth gates removed from Screen 3 — auth is now inline at the     */
  /*  Publish button only (Sprint 2B). Edit screen always accessible.  */
  /* ================================================================ */

  /* ================================================================ */
  /*  Render: SCREEN 3 — ADVANCED EDIT                                 */
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

  const panelStatuses = computePanelStatuses(guided, codeFiles, artifact, builderArtifactName, tarGzFile, uploadedFiles, permissionsTouched);

  return (
    <div className="mx-auto max-w-3xl px-4 sm:px-6 py-12">
      <StepIndicator current={3} />
      {/* Header */}
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Edit &amp; Publish</h1>
          {user?.publisher ? (
            <p className="text-sm text-muted">
              Publishing as <span className="text-primary font-medium">@{user.publisher.slug}</span>
            </p>
          ) : (
            <p className="text-sm text-muted">Review and edit your skill details</p>
          )}
        </div>
        <button
          type="button"
          onClick={() => { setScreen("draft"); setError(""); setSuccess(""); }}
          className="text-sm text-muted hover:text-foreground transition-colors"
        >
          &#8592; Back to review
        </button>
      </div>

      {/* Source banner */}
      {source === "builder" && (
        <div className="mb-6 rounded-lg border border-primary/30 bg-primary/5 px-4 py-3 text-sm text-primary">
          Generated with AgentNode Builder &mdash; review the details below and publish.
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
        </div>
      </div>

      {/* ---- Collapsible edit panels ---- */}
      <div className="space-y-3 mb-6">

        {/* === CODE / FILES (first — most important action) === */}
        <div data-panel="artifact">
          <CollapsiblePanel
            title="Code / Files"
            subtitle={artifactSummary}
            open={openPanels.has("artifact")}
            onToggle={() => togglePanel("artifact")}
            status={panelStatuses.artifact}
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

        {/* === BASICS === */}
        <div data-panel="basics">
          <CollapsiblePanel
            title="Basics"
            subtitle={guided.name ? `${guided.name} · v${guided.version}` : "Name, version, and description"}
            open={openPanels.has("basics")}
            onToggle={() => togglePanel("basics")}
            status={panelStatuses.basics}
          >
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
                placeholder="My PDF Extractor"
              />
              {guided.name && guided.name.trim().length > 0 && guided.name.trim().length < 3 && (
                <p className="mt-1 text-xs text-danger">Name must be at least 3 characters</p>
              )}
            </div>

            <div>
              <label className="mb-1 block text-sm font-medium text-foreground">
                Package ID <span className="text-danger">*</span>
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
              {guided.version && !isValidSemver(guided.version) && (
                <p className="mt-1 text-xs text-danger">Must be valid semver (e.g. 1.0.0)</p>
              )}
            </div>

            <div>
              <label className="mb-1 flex items-center justify-between text-sm font-medium text-foreground">
                <span>Summary <span className="text-danger">*</span> <span className="text-xs text-muted font-normal">(min 20 characters)</span></span>
                <span className={`text-xs font-normal ${guided.summary.length > 200 ? "text-danger" : guided.summary.length > 0 && guided.summary.trim().length < 20 ? "text-warning" : "text-muted"}`}>
                  {guided.summary.length}/200
                </span>
              </label>
              <textarea
                rows={2}
                value={guided.summary}
                onChange={(e) => updateGuided("summary", e.target.value)}
                className={`w-full rounded-md border bg-background px-3 py-2.5 text-foreground focus:border-primary focus:outline-none resize-none ${guided.summary.length > 200 ? "border-danger" : guided.summary.length > 0 && guided.summary.trim().length < 20 ? "border-warning" : "border-border"}`}
                placeholder="Extract text and tables from PDF files"
              />
              <p className="mt-1 text-xs text-muted">One sentence &mdash; shown in search results and package cards.</p>
            </div>

            <div>
              <label className="mb-1 block text-sm font-medium text-foreground">
                Description <span className="text-xs text-muted font-normal">(optional but recommended)</span>
              </label>
              <textarea
                rows={3}
                value={guided.description}
                onChange={(e) => updateGuided("description", e.target.value)}
                className="w-full rounded-md border border-border bg-background px-3 py-2.5 text-foreground focus:border-primary focus:outline-none resize-none"
                placeholder="Detailed description of what this skill does, how it works, and what makes it useful..."
              />
              <p className="mt-1 text-xs text-muted">Longer explanation &mdash; shown on your package detail page.</p>
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

            {/* Links & License */}
            <div className="rounded-lg border border-border bg-card/50 p-4 space-y-4">
              <div className="text-sm font-medium text-foreground">Links &amp; License</div>
              <div>
                <label className="mb-1 block text-xs text-muted">License</label>
                <select
                  value={guided.license || "MIT"}
                  onChange={(e) => updateGuided("license", e.target.value)}
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
                >
                  <option value="MIT">MIT</option>
                  <option value="Apache-2.0">Apache 2.0</option>
                  <option value="GPL-3.0">GPL 3.0</option>
                  <option value="BSD-3-Clause">BSD 3-Clause</option>
                  <option value="ISC">ISC</option>
                  <option value="proprietary">Proprietary</option>
                </select>
              </div>
              <div className="grid gap-3 sm:grid-cols-3">
                <div>
                  <label className="mb-1 block text-xs text-muted">Homepage URL</label>
                  <input
                    type="url"
                    value={guided.homepage_url}
                    onChange={(e) => updateGuided("homepage_url", e.target.value)}
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
                    placeholder="https://example.com"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs text-muted">Docs URL</label>
                  <input
                    type="url"
                    value={guided.docs_url}
                    onChange={(e) => updateGuided("docs_url", e.target.value)}
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
                    placeholder="https://docs.example.com"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs text-muted">Source URL</label>
                  <input
                    type="url"
                    value={guided.source_url}
                    onChange={(e) => updateGuided("source_url", e.target.value)}
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
                    placeholder="https://github.com/..."
                  />
                </div>
              </div>
            </div>
            <p className="mt-4 text-xs text-muted"><span className="text-danger">*</span> Required field</p>
          </CollapsiblePanel>
        </div>

        {/* === CONTENT (README, Use Cases, Examples, Env Requirements) === */}
        <div data-panel="content">
          <CollapsiblePanel
            title="Content"
            subtitle={guided.readme_md ? "README provided" : "README, use cases, examples"}
            open={openPanels.has("content")}
            onToggle={() => togglePanel("content")}
          >
            {/* README */}
            <div>
              <label className="mb-1 block text-xs font-medium text-muted">README (Markdown)</label>
              <textarea
                value={guided.readme_md}
                onChange={(e) => setGuided(prev => ({ ...prev, readme_md: e.target.value }))}
                rows={8}
                className="w-full rounded-lg border border-border bg-card px-3 py-2 font-mono text-xs text-foreground placeholder:text-muted/60 focus:border-primary focus:outline-none resize-y"
                placeholder={"# My Skill\n\nDescribe what your skill does, how to use it, and provide examples..."}
              />
            </div>

            {/* Use Cases */}
            <div>
              <label className="mb-1 block text-xs font-medium text-muted">Use Cases</label>
              <div className="space-y-2">
                {guided.use_cases.map((uc, i) => (
                  <div key={i} className="flex items-center gap-2">
                    <input
                      type="text"
                      value={uc}
                      onChange={(e) => {
                        const next = [...guided.use_cases];
                        next[i] = e.target.value;
                        setGuided(prev => ({ ...prev, use_cases: next }));
                      }}
                      className="flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
                      placeholder="e.g. Extract tables from PDF files"
                    />
                    <button
                      type="button"
                      onClick={() => setGuided(prev => ({ ...prev, use_cases: prev.use_cases.filter((_, j) => j !== i) }))}
                      className="shrink-0 text-xs text-muted hover:text-danger transition-colors"
                    >
                      Remove
                    </button>
                  </div>
                ))}
                <button
                  type="button"
                  onClick={() => setGuided(prev => ({ ...prev, use_cases: [...prev.use_cases, ""] }))}
                  className="text-xs text-primary hover:underline"
                >
                  + Add use case
                </button>
              </div>
            </div>

            {/* Examples */}
            <div>
              <label className="mb-1 block text-xs font-medium text-muted">Examples</label>
              <div className="space-y-3">
                {guided.examples.map((ex, i) => (
                  <div key={i} className="rounded-lg border border-border bg-card/50 p-3 space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-medium text-foreground">Example {i + 1}</span>
                      <button
                        type="button"
                        onClick={() => setGuided(prev => ({ ...prev, examples: prev.examples.filter((_, j) => j !== i) }))}
                        className="text-xs text-muted hover:text-danger transition-colors"
                      >
                        Remove
                      </button>
                    </div>
                    <div className="grid gap-2 sm:grid-cols-2">
                      <div>
                        <label className="mb-0.5 block text-xs text-muted">Title</label>
                        <input
                          type="text"
                          value={ex.title}
                          onChange={(e) => {
                            const next = [...guided.examples];
                            next[i] = { ...next[i], title: e.target.value };
                            setGuided(prev => ({ ...prev, examples: next }));
                          }}
                          className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm text-foreground focus:border-primary focus:outline-none"
                          placeholder="Basic usage"
                        />
                      </div>
                      <div>
                        <label className="mb-0.5 block text-xs text-muted">Language</label>
                        <input
                          type="text"
                          value={ex.language}
                          onChange={(e) => {
                            const next = [...guided.examples];
                            next[i] = { ...next[i], language: e.target.value };
                            setGuided(prev => ({ ...prev, examples: next }));
                          }}
                          className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm text-foreground focus:border-primary focus:outline-none"
                          placeholder="python"
                        />
                      </div>
                    </div>
                    <div>
                      <label className="mb-0.5 block text-xs text-muted">Code</label>
                      <textarea
                        value={ex.code}
                        onChange={(e) => {
                          const next = [...guided.examples];
                          next[i] = { ...next[i], code: e.target.value };
                          setGuided(prev => ({ ...prev, examples: next }));
                        }}
                        rows={4}
                        className="w-full rounded-md border border-border bg-background px-3 py-2 font-mono text-xs text-foreground focus:border-primary focus:outline-none resize-y"
                        placeholder="result = await skill.run_tool(...)"
                        spellCheck={false}
                      />
                    </div>
                  </div>
                ))}
                <button
                  type="button"
                  onClick={() => setGuided(prev => ({ ...prev, examples: [...prev.examples, { title: "", language: "python", code: "" }] }))}
                  className="text-xs text-primary hover:underline"
                >
                  + Add example
                </button>
              </div>
            </div>

            {/* Environment Requirements */}
            <div>
              <label className="mb-1 block text-xs font-medium text-muted">Environment Variables</label>
              <div className="space-y-2">
                {guided.env_requirements.map((env, i) => (
                  <div key={i} className="flex items-center gap-2">
                    <input
                      type="text"
                      value={env.name}
                      onChange={(e) => {
                        const next = [...guided.env_requirements];
                        next[i] = { ...next[i], name: e.target.value };
                        setGuided(prev => ({ ...prev, env_requirements: next }));
                      }}
                      className="w-32 shrink-0 rounded-md border border-border bg-background px-3 py-2 text-sm font-mono text-foreground focus:border-primary focus:outline-none"
                      placeholder="API_KEY"
                    />
                    <input
                      type="text"
                      value={env.description}
                      onChange={(e) => {
                        const next = [...guided.env_requirements];
                        next[i] = { ...next[i], description: e.target.value };
                        setGuided(prev => ({ ...prev, env_requirements: next }));
                      }}
                      className="flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
                      placeholder="Description"
                    />
                    <label className="flex items-center gap-1.5 shrink-0 text-xs text-muted">
                      <input
                        type="checkbox"
                        checked={env.required}
                        onChange={(e) => {
                          const next = [...guided.env_requirements];
                          next[i] = { ...next[i], required: e.target.checked };
                          setGuided(prev => ({ ...prev, env_requirements: next }));
                        }}
                        className="rounded border-border"
                      />
                      Required
                    </label>
                    <button
                      type="button"
                      onClick={() => setGuided(prev => ({ ...prev, env_requirements: prev.env_requirements.filter((_, j) => j !== i) }))}
                      className="shrink-0 text-xs text-muted hover:text-danger transition-colors"
                    >
                      Remove
                    </button>
                  </div>
                ))}
                <button
                  type="button"
                  onClick={() => setGuided(prev => ({ ...prev, env_requirements: [...prev.env_requirements, { name: "", required: false, description: "" }] }))}
                  className="text-xs text-primary hover:underline"
                >
                  + Add variable
                </button>
              </div>
            </div>
          </CollapsiblePanel>
        </div>

        {/* === TOOLS === */}
        <div data-panel="tools">
          <CollapsiblePanel
            title="Tools"
            subtitle={toolSummary}
            open={openPanels.has("tools")}
            onToggle={() => togglePanel("tools")}
            status={panelStatuses.tools}
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
                    <label className="mb-1 block text-xs text-muted">What does this tool do?</label>
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
                    Entrypoint
                  </label>
                  <input
                    type="text"
                    value={tool.entrypoint}
                    onChange={(e) => updateTool(i, "entrypoint", e.target.value)}
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm font-mono text-foreground focus:border-primary focus:outline-none"
                    placeholder="my_pack.tool:extract_text"
                  />
                  <p className="mt-1 text-xs text-muted">
                    Where your function lives in the code.
                    Format: <code className="text-primary/70">module.path:function_name</code>
                    {" "}&mdash; e.g. <code className="text-primary/70">pdf_reader.tool:extract_text</code>
                  </p>
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
                        className={`w-full rounded-md border bg-background px-3 py-2 font-mono text-xs text-foreground focus:border-primary focus:outline-none resize-none ${
                          schemaWarnings[i]?.input ? "border-danger" : "border-border"
                        }`}
                        placeholder='{"type": "object", "properties": {...}}'
                        spellCheck={false}
                      />
                      {schemaWarnings[i]?.input && (
                        <p className="mt-1 text-xs text-danger">Invalid JSON &mdash; schema will be ignored</p>
                      )}
                    </div>
                    <div>
                      <label className="mb-1 block text-xs text-muted">Output schema (JSON)</label>
                      <textarea
                        rows={4}
                        value={tool.output_schema}
                        onChange={(e) => updateTool(i, "output_schema", e.target.value)}
                        className={`w-full rounded-md border bg-background px-3 py-2 font-mono text-xs text-foreground focus:border-primary focus:outline-none resize-none ${
                          schemaWarnings[i]?.output ? "border-danger" : "border-border"
                        }`}
                        placeholder='{"type": "object", "properties": {...}}'
                        spellCheck={false}
                      />
                      {schemaWarnings[i]?.output && (
                        <p className="mt-1 text-xs text-danger">Invalid JSON &mdash; schema will be ignored</p>
                      )}
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
        </div>

        {/* === PERMISSIONS === */}
        <div data-panel="permissions">
          <CollapsiblePanel
            title="Permissions"
            subtitle={hasNonDefaultPerms ? "Custom permissions" : "All restricted (default)"}
            open={openPanels.has("permissions")}
            onToggle={() => togglePanel("permissions")}
            status={panelStatuses.permissions}
          >
            {!permissionsTouched && !hasNonDefaultPerms && (
              <div className="rounded-md border border-yellow-500/20 bg-yellow-500/5 px-3 py-2 text-xs text-yellow-400 mb-3">
                Defaults active &mdash; review recommended
              </div>
            )}
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
                      onClick={() => { setShowPermissions(true); setPermissionsTouched(true); }}
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
                      onClick={() => { setShowPermissions(true); setPermissionsTouched(true); }}
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
                  Permissions control what your tool is allowed to do. Start with defaults &mdash; only increase if needed.
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <div>
                    <label className="mb-1 block text-xs text-muted">Network <span className="text-muted/50">&mdash; does your tool call external APIs?</span></label>
                    <select
                      value={guided.network}
                      onChange={(e) => { updateGuided("network", e.target.value); setPermissionsTouched(true); }}
                      className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
                    >
                      <option value="none">none &mdash; no internet access</option>
                      <option value="restricted">restricted &mdash; specific domains only</option>
                      <option value="unrestricted">unrestricted &mdash; any URL</option>
                    </select>
                  </div>
                  <div>
                    <label className="mb-1 block text-xs text-muted">Filesystem <span className="text-muted/50">&mdash; does your tool read/write files?</span></label>
                    <select
                      value={guided.filesystem}
                      onChange={(e) => { updateGuided("filesystem", e.target.value); setPermissionsTouched(true); }}
                      className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
                    >
                      <option value="none">none &mdash; no file access</option>
                      <option value="temp">temp &mdash; temporary files only</option>
                      <option value="workspace_read">workspace_read &mdash; read project files</option>
                      <option value="workspace_write">workspace_write &mdash; read &amp; write project files</option>
                      <option value="any">any &mdash; full filesystem access</option>
                    </select>
                  </div>
                  <div>
                    <label className="mb-1 block text-xs text-muted">Code execution <span className="text-muted/50">&mdash; does your tool run subprocesses?</span></label>
                    <select
                      value={guided.code_execution}
                      onChange={(e) => { updateGuided("code_execution", e.target.value); setPermissionsTouched(true); }}
                      className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
                    >
                      <option value="none">none &mdash; no subprocess execution</option>
                      <option value="limited_subprocess">limited &mdash; sandboxed subprocesses</option>
                      <option value="shell">shell &mdash; full shell access</option>
                    </select>
                  </div>
                  <div>
                    <label className="mb-1 block text-xs text-muted">Data access <span className="text-muted/50">&mdash; what data can your tool see?</span></label>
                    <select
                      value={guided.data_access}
                      onChange={(e) => { updateGuided("data_access", e.target.value); setPermissionsTouched(true); }}
                      className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
                    >
                      <option value="input_only">input_only &mdash; only what is passed in</option>
                      <option value="connected_accounts">connected_accounts &mdash; linked services</option>
                      <option value="persistent">persistent &mdash; stored data across runs</option>
                    </select>
                  </div>
                  <div>
                    <label className="mb-1 block text-xs text-muted">User approval <span className="text-muted/50">&mdash; when to ask before running?</span></label>
                    <select
                      value={guided.user_approval}
                      onChange={(e) => { updateGuided("user_approval", e.target.value); setPermissionsTouched(true); }}
                      className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
                    >
                      <option value="never">never &mdash; run without asking</option>
                      <option value="high_risk_only">high_risk_only &mdash; ask for destructive actions</option>
                      <option value="once">once &mdash; ask on first use</option>
                      <option value="always">always &mdash; ask every time</option>
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
        </div>
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
              {JSON.stringify(buildManifestFromGuided(guided, user?.publisher?.slug || "your-publisher"), null, 2)}
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

      {/* ---- No-code inline warning ---- */}
      {showNoCodeConfirm && (
        <div className="mb-4 rounded-lg border border-yellow-500/30 bg-yellow-500/5 px-4 py-4">
          <p className="text-sm font-medium text-yellow-400 mb-1">No code files provided</p>
          <p className="text-xs text-yellow-400/80 mb-3">
            Publishing without code means the package won&apos;t be installable. Are you sure?
          </p>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={handlePublish}
              className="rounded-md bg-yellow-500/20 border border-yellow-500/30 px-4 py-2 text-xs font-medium text-yellow-400 hover:bg-yellow-500/30 transition-colors"
            >
              Publish without code
            </button>
            <button
              type="button"
              onClick={() => setShowNoCodeConfirm(false)}
              className="rounded-md border border-border px-4 py-2 text-xs text-muted hover:text-foreground transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* ---- Sticky Publish bar (with inline auth gates) ---- */}
      <StickyPublishBar onBack={() => { setScreen("draft"); setError(""); setSuccess(""); }}>
        {!user ? (
          <div className="flex items-center gap-3">
            <span className="text-sm text-muted">Sign in to publish</span>
            <Link
              href={`/auth/login?returnTo=${encodeURIComponent("/publish?tab=" + activeTab)}`}
              onClick={() => {
                saveDraft({
                  tab: activeTab,
                  description: descriptionText,
                  importPlatform,
                  importCode,
                  manifestText: manifestInput,
                  guided,
                  source: source || undefined,
                  createdAt: Date.now(),
                  importConfidence: importConfidence || undefined,
                  importDraftReady: importDraftReady ?? undefined,
                  importWarnings: importWarnings.length ? importWarnings : undefined,
                  importGroupedWarnings: importGroupedWarnings.length ? importGroupedWarnings : undefined,
                  importChanges: importChanges.length ? importChanges : undefined,
                });
              }}
              className="rounded-md bg-primary px-6 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-primary/90"
            >
              Sign in
            </Link>
          </div>
        ) : !user.publisher ? (
          <div className="flex items-center gap-2">
            <input
              type="text"
              value={pubDisplayName}
              onChange={(e) => {
                setPubDisplayName(e.target.value);
                if (!pubSlug || pubSlug === pubDisplayName.toLowerCase().replace(/[^a-z0-9-]/g, "-").replace(/-+/g, "-")) {
                  setPubSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-").replace(/-+/g, "-"));
                }
              }}
              className="rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
              placeholder="Publisher name"
            />
            <button
              onClick={createPublisher}
              disabled={creatingPublisher || !pubDisplayName}
              className="rounded-md bg-primary px-5 py-2 text-sm font-medium text-white hover:bg-primary/90 disabled:opacity-50"
            >
              {creatingPublisher ? "Creating..." : "Create & publish"}
            </button>
          </div>
        ) : (
          <button
            type="button"
            onClick={handlePublish}
            disabled={loading || buildingArtifact || (validation !== null && !validation.valid)}
            className="rounded-md bg-primary px-10 py-3 text-sm font-bold text-white transition-colors hover:bg-primary/90 disabled:opacity-50"
          >
            {buildingArtifact ? "Building artifact..." : loading ? "Publishing..." : "Publish"}
          </button>
        )}
      </StickyPublishBar>

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
