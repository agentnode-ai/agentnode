"use client";

import Link from "next/link";
import { slugify } from "../lib/manifest";
import { computeReadiness } from "../lib/readiness";
import { saveDraft } from "../hooks/useDraft";
import { StepIndicator } from "./StepIndicator";
import { ReadinessChecklist } from "./ReadinessChecklist";
import type { PublishFormState } from "../hooks/usePublishForm";

export function DraftReview({ form }: { form: PublishFormState }) {
  const {
    user,
    screen,
    setScreen,
    activeTab,
    guided,
    updateGuided,
    source,
    importConfidence,
    importDraftReady,
    importWarnings,
    importGroupedWarnings,
    importChanges,
    draftSaveBanner,
    setDraftSaveBanner,
    codeFiles,
    builderArtifactName,
    tarGzFile,
    uploadedFiles,
    artifact,
    error,
    setError,
    success,
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
    setOpenPanels,
    navigateToIssue,
    handlePublishFromDraft,
    buildCurrentDraft,
    descriptionText,
    importPlatform,
    importCode,
    manifestInput,
  } = form;

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
            {canPublish
              ? `Your ${guided.package_type === "agent" ? "agent" : "skill"} is ready to publish`
              : `Review your ${guided.package_type === "agent" ? "agent" : "skill"}`}
          </h1>
          {user?.publisher && (
            <p className="text-sm text-muted">
              Publishing as <span className="text-primary font-medium">@{user.publisher.slug}</span>
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={() => { setScreen("input"); setError(""); form.setSuccess(""); }}
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

      {/* Import conversion metadata (collapsed -- alert-fatigue reduction) */}
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
          {guided.name || `Untitled ${guided.package_type === "agent" ? "agent" : "skill"}`}
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

      {/* ---- Gold Eligibility Preview ---- */}
      {validation?.gold_eligibility && (
        <div className="mb-6 rounded-lg border border-border bg-card p-4">
          <h3 className="mb-3 text-sm font-medium text-foreground">Verification tier preview</h3>
          <div className="flex items-center gap-3 mb-3">
            <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold ${
              validation.gold_eligibility.max_tier === "gold"
                ? "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-300"
                : validation.gold_eligibility.max_tier === "verified"
                ? "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300"
                : "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300"
            }`}>
              {validation.gold_eligibility.max_tier === "gold" ? "⬥ Gold eligible" :
               validation.gold_eligibility.max_tier === "verified" ? "✓ Max: Verified" : "— Unverified"}
            </span>
            <span className="text-xs text-muted">
              Mode: {validation.gold_eligibility.verification_mode} · {validation.gold_eligibility.cases_count} case{validation.gold_eligibility.cases_count !== 1 ? "s" : ""}
            </span>
          </div>
          <p className="text-xs text-muted mb-2">{validation.gold_eligibility.explanation}</p>
          {validation.gold_eligibility.missing_items.length > 0 && (
            <ul className="space-y-1">
              {validation.gold_eligibility.missing_items.map((item, i) => (
                <li key={i} className="flex items-start gap-2 text-xs text-muted">
                  <span className="mt-0.5 shrink-0 text-warning">•</span>
                  <span>{item}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

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
              <p className="text-xs text-muted mb-3">Upload files or write code to make your {guided.package_type === "agent" ? "agent" : "skill"} installable</p>
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
                saveDraft(buildCurrentDraft());
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
