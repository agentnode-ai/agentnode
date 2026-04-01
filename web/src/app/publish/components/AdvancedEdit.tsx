"use client";

import Link from "next/link";
import { SLUG_PATTERN } from "../lib/constants";
import { slugify, isValidSemver, buildManifestFromGuided } from "../lib/manifest";
import { computePanelStatuses } from "../lib/readiness";
import { saveDraft } from "../hooks/useDraft";
import { StepIndicator } from "./StepIndicator";
import { CollapsiblePanel } from "./CollapsiblePanel";
import { ArtifactSection } from "./ArtifactSection";
import { CapabilityDropdown } from "./CapabilityDropdown";
import { StickyPublishBar } from "./StickyPublishBar";
import type { PublishFormState } from "../hooks/usePublishForm";

export function AdvancedEdit({ form }: { form: PublishFormState }) {
  const {
    user,
    setScreen,
    activeTab,
    guided,
    setGuided,
    updateGuided,
    updateTool,
    addTool,
    removeTool,
    source,
    openPanels,
    togglePanel,
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
    error,
    setError,
    success,
    setSuccess,
    loading,
    buildingArtifact,
    validation,
    validating,
    pubSlug,
    setPubSlug,
    pubDisplayName,
    setPubDisplayName,
    creatingPublisher,
    createPublisher,
    capabilities,
    toolAdvanced,
    setToolAdvanced,
    showManifest,
    setShowManifest,
    showPermissions,
    setShowPermissions,
    copied,
    copyManifest,
    permissionsTouched,
    setPermissionsTouched,
    showNoCodeConfirm,
    setShowNoCodeConfirm,
    schemaWarnings,
    handlePublish,
    buildCurrentDraft,
    descriptionText,
    importPlatform,
    importCode,
    manifestInput,
    importConfidence,
    importDraftReady,
    importWarnings,
    importGroupedWarnings,
    importChanges,
  } = form;

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

        {/* === CODE / FILES (first -- most important action) === */}
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
                saveDraft(buildCurrentDraft());
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
