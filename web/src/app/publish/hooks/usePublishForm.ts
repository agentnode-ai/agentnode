"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import yaml from "js-yaml";
import { fetchWithAuth } from "@/lib/api";
import { PLATFORMS, convertClientSide, parseResult } from "@/lib/import-utils";
import { BUILDER_EXAMPLES, generateSkill, type BuilderResult } from "@/lib/builder-utils";

import type { UserInfo, ToolEntry, CodeFile, GuidedState, ValidationResult, CapabilityOption, InputTab, PublishDraft } from "../lib/types";
import { MAX_UPLOAD_SIZE_MB, DRAFT_TTL, DRAFT_KEY, SLUG_PATTERN, EMPTY_TOOL, DEFAULT_GUIDED, CAPABILITY_FALLBACK } from "../lib/constants";
import { slugify, isValidSemver, buildManifestFromGuided, parseManifestToGuided } from "../lib/manifest";
import { computeReadiness, computePanelStatuses } from "../lib/readiness";
import { saveDraft, restoreDraft, clearDraft } from "./useDraft";

export function usePublishForm() {
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
          if (prefill.originalFiles?.length) {
            set_prefillFiles(prefill.originalFiles);
          } else if (prefill.originalCode) {
            const moduleId = (prefill.manifestText?.match(/package_id:\s*(\S+)/)?.[1] || "my-tool").replace(/-/g, "_");
            set_prefillFiles([{ path: `src/${moduleId}/tool.py`, content: prefill.originalCode }]);
          }
          if (prefill.importPlatform) {
            const plat = PLATFORMS.find(p => p.id === prefill.importPlatform);
            if (plat) {
              setTimeout(() => setSource(`import:${plat.name}`), 0);
            }
          }
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
    if (hasPrefill && prefillText) {
      try {
        let parsed;
        try { parsed = JSON.parse(prefillText); }
        catch { parsed = yaml.load(prefillText) as Record<string, unknown>; }
        if (parsed && typeof parsed === "object") return parseManifestToGuided(parsed as Record<string, unknown>);
      } catch { /* defaults */ }
    }
    if (typeof window !== "undefined") {
      const draft = restoreDraft();
      if (draft?.guided && typeof draft.guided === "object") {
        if (draft.tab) {
          restoredDraftTabRef.current = draft.tab;
          setTimeout(() => setActiveTab(draft.tab), 0);
        }
        if (draft.description) setTimeout(() => setDescriptionText(draft.description!), 0);
        if (draft.importPlatform) setTimeout(() => setImportPlatform(draft.importPlatform!), 0);
        if (draft.importCode) setTimeout(() => setImportCode(draft.importCode!), 0);
        if (draft.manifestText) setTimeout(() => setManifestInput(draft.manifestText!), 0);
        if (draft.source) setTimeout(() => setSource(draft.source!), 0);
        if (draft.importConfidence) setTimeout(() => setImportConfidence(draft.importConfidence!), 0);
        if (typeof draft.importDraftReady === "boolean") setTimeout(() => setImportDraftReady(draft.importDraftReady!), 0);
        if (draft.importWarnings?.length) setTimeout(() => setImportWarnings(draft.importWarnings!), 0);
        if (draft.importGroupedWarnings?.length) setTimeout(() => setImportGroupedWarnings(draft.importGroupedWarnings!), 0);
        if (draft.importChanges?.length) setTimeout(() => setImportChanges(draft.importChanges!), 0);
        clearDraft();
        const defaults = { ...DEFAULT_GUIDED, tools: [{ ...EMPTY_TOOL }] };
        const g = draft.guided as unknown as Record<string, unknown>;
        const safeTools = Array.isArray(g.tools)
          ? g.tools.map((t: Record<string, unknown>) => ({ ...EMPTY_TOOL, ...Object.fromEntries(Object.entries(t).filter(([, v]) => typeof v === "string")) }))
          : defaults.tools;
        return {
          ...defaults,
          ...Object.fromEntries(Object.entries(g).filter(([k, v]) => k !== "tools" && typeof v === "string")),
          tools: safeTools,
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

  /* ---- Import converting ---- */
  const [importConverting, setImportConverting] = useState(false);

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

  // Default tab: non-logged-in users land on "manifest"
  useEffect(() => {
    if (!authChecked) return;
    if (user) return;
    if (tabParam) return;
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

  // Restore builder artifact
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

      if (apiCodeFiles.length > 0) {
        setCodeFiles(apiCodeFiles);
        set_prefillFiles(apiCodeFiles);
      }
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

      if (usedFallback) {
        setImportWarnings(prev => [
          "Server-side conversion unavailable. Used simplified client-side conversion (no code files generated, no dependency analysis).",
          ...prev,
        ]);
        setImportConfidence({ level: "low", reasons: ["Client-side fallback — no AST analysis performed"] });
      }

      const result = parseResult(manifest, importPlatform);
      try {
        const parsed = yaml.load(result.manifest) as Record<string, unknown>;
        if (parsed && typeof parsed === "object") {
          setGuided(parseManifestToGuided(parsed));
        }
      } catch {
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

      try {
        const inviteCode = typeof window !== "undefined" ? sessionStorage.getItem("invite_code") : null;
        if (inviteCode) {
          sessionStorage.removeItem("invite_code");
          await fetchWithAuth(`/invites/${encodeURIComponent(inviteCode)}/published`, { method: "POST" })
            .catch(() => {});
        }
      } catch {
        // Silently ignore
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

  function buildCurrentDraft(): PublishDraft {
    return {
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
    };
  }

  return {
    // Router
    router,

    // Auth
    user,
    authChecked,

    // Screen
    screen,
    setScreen,

    // Input Hub
    activeTab,
    setActiveTab,
    descriptionText,
    setDescriptionText,
    generating,
    builderResult,
    importPlatform,
    setImportPlatform,
    importCode,
    setImportCode,
    manifestInput,
    setManifestInput,
    importConverting,

    // Source & import metadata
    source,
    importConfidence,
    importDraftReady,
    importWarnings,
    importGroupedWarnings,
    importChanges,

    // Draft
    draftExpired,
    setDraftExpired,
    draftSaveBanner,
    setDraftSaveBanner,

    // Form
    guided,
    setGuided,
    updateGuided,
    updateTool,
    addTool,
    removeTool,

    // Panels
    openPanels,
    setOpenPanels,
    togglePanel,

    // Artifact
    artifact,
    builderArtifactName,
    setBuilderArtifactName,
    setArtifact,
    artifactMode,
    setArtifactMode,
    codeFiles,
    setCodeFiles,
    uploadedFiles,
    setUploadedFiles,
    tarGzFile,
    setTarGzFile,

    // Validation
    validation,
    validating,

    // Publish
    error,
    setError,
    success,
    setSuccess,
    loading,
    buildingArtifact,

    // Publisher
    pubSlug,
    setPubSlug,
    pubDisplayName,
    setPubDisplayName,
    creatingPublisher,
    createPublisher,

    // Capabilities
    capabilities,

    // Tool advanced
    toolAdvanced,
    setToolAdvanced,

    // Manifest preview
    showManifest,
    setShowManifest,
    showPermissions,
    setShowPermissions,
    copied,
    copyManifest,
    permissionsTouched,
    setPermissionsTouched,

    // No-code confirm
    showNoCodeConfirm,
    setShowNoCodeConfirm,

    // Schema warnings
    schemaWarnings,

    // Handlers
    navigateToIssue,
    handleGenerate,
    handleConvert,
    handleContinueWithManifest,
    handleStartFresh,
    handlePublish,
    handlePublishFromDraft,
    buildCurrentDraft,

    // Computed
    buildManifestFromGuided,
  };
}

export type PublishFormState = ReturnType<typeof usePublishForm>;
