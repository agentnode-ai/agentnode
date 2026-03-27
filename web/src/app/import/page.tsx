"use client";

import { useState, useRef } from "react";
import { useRouter } from "next/navigation";
import {
  PLATFORMS,
  type ConversionResult,
  convertClientSide,
  parseResult,
} from "@/lib/import-utils";

/* ------------------------------------------------------------------ */
/*  Types for API response                                             */
/* ------------------------------------------------------------------ */

interface CodeFile {
  path: string;
  content: string;
}

interface ConversionConfidence {
  level: "high" | "medium" | "low";
  reasons: string[];
}

interface DetectedToolInfo {
  name: string;
  original_name: string;
  description: string;
  has_return_dict: boolean;
}

interface WarningItem {
  message: string;
  category: "blocking" | "review" | "info";
}

interface ApiConvertResponse {
  code_files: CodeFile[];
  manifest_yaml: string;
  manifest_json: Record<string, unknown>;
  confidence: ConversionConfidence;
  draft_ready: boolean;
  requires_manual_review: boolean;
  warnings: string[];
  grouped_warnings: WarningItem[];
  changes: string[];
  detected_dependencies: string[];
  unknown_imports: string[];
  detected_tools: DetectedToolInfo[];
  package_id: string;
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function ImportPage() {
  const router = useRouter();
  const [platform, setPlatform] = useState("langchain");
  const [code, setCode] = useState("");
  const [result, setResult] = useState<ConversionResult | null>(null);
  const [apiResponse, setApiResponse] = useState<ApiConvertResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const [activeFileTab, setActiveFileTab] = useState(0);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const selectedPlatform = PLATFORMS.find((p) => p.id === platform)!;

  const handleConvert = async () => {
    if (!code.trim()) {
      setError("Paste your tool code above.");
      return;
    }
    setLoading(true);
    setError("");
    setResult(null);
    setApiResponse(null);

    try {
      const res = await fetch("/api/v1/import/convert", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ platform, content: code }),
      });
      if (res.ok) {
        const data: ApiConvertResponse = await res.json();
        setApiResponse(data);
        setResult({
          manifest: data.manifest_yaml,
          tools: data.detected_tools.map((t) => ({
            name: t.name,
            description: t.description,
            capability_id: "",
          })),
          detectedFramework: platform,
          packageId: data.package_id,
          toolCount: data.detected_tools.length,
        });
        if (!data.draft_ready && data.detected_tools.length === 0) {
          setError("No tools detected. Check your input format and try again.");
          setResult(null);
          setApiResponse(null);
        }
      } else {
        // API error — fall back to client-side conversion
        _fallbackConvert();
      }
    } catch {
      _fallbackConvert();
    } finally {
      setLoading(false);
    }
  };

  const _fallbackConvert = () => {
    const manifest = convertClientSide(platform, code);
    if (manifest.startsWith("# No tools")) {
      setError("No tools detected. Check your input format and try again.");
    } else {
      setResult(parseResult(manifest, platform));
      setApiResponse(null);
    }
  };

  const handleTryExample = () => {
    setCode(selectedPlatform.example);
    setResult(null);
    setApiResponse(null);
    setError("");
    textareaRef.current?.focus();
  };

  const handleCopy = () => {
    if (result) {
      navigator.clipboard.writeText(result.manifest);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const handlePublish = () => {
    const payload: Record<string, unknown> = {
      source: "import",
      importPlatform: platform,
    };

    if (apiResponse) {
      payload.manifestText = apiResponse.manifest_yaml;
      payload.originalFiles = apiResponse.code_files;
      payload.confidence = apiResponse.confidence;
      payload.draftReady = apiResponse.draft_ready;
      payload.requiresManualReview = apiResponse.requires_manual_review;
      payload.warnings = apiResponse.warnings;
      payload.groupedWarnings = apiResponse.grouped_warnings;
      payload.changes = apiResponse.changes;
    } else if (result) {
      payload.manifestText = result.manifest;
      payload.originalFiles = [
        {
          path: `src/${(result.packageId || "my_tool").replace(/-/g, "_")}/tool.py`,
          content: code,
        },
      ];
    }

    const encoded = JSON.stringify(payload);
    const payloadSize = new Blob([encoded]).size;
    if (payloadSize > 1_000_000) {
      sessionStorage.setItem(
        "publish_prefill",
        JSON.stringify({ source: "import", manifestText: result?.manifest || "" })
      );
    } else {
      sessionStorage.setItem("publish_prefill", encoded);
    }
    router.push("/publish?from=import");
  };

  const shareText = result
    ? `I just converted my ${selectedPlatform.name} tool into a portable ANP package on @agentnode_ai — any AI agent can now discover and install it.\n\nhttps://agentnode.net/import`
    : "";

  /* ---- Confidence helpers ---- */
  const confidenceColor = (level: string) => {
    if (level === "high") return "border-green-500/40 bg-green-500/10 text-green-400";
    if (level === "medium") return "border-yellow-500/40 bg-yellow-500/10 text-yellow-400";
    return "border-red-500/40 bg-red-500/10 text-red-400";
  };
  const confidenceLabel = (level: string) => {
    if (level === "high") return "High confidence";
    if (level === "medium") return "Medium confidence";
    return "Low confidence";
  };

  return (
    <div className="flex flex-col">
      {/* ============================================================ */}
      {/*  HERO                                                         */}
      {/* ============================================================ */}
      <section className="relative overflow-hidden border-b border-border">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-primary/10 via-transparent to-transparent" />
        <div className="relative mx-auto max-w-4xl px-4 sm:px-6 pt-16 pb-6 text-center">
          <h1 className="text-4xl font-bold leading-tight tracking-tight text-foreground sm:text-5xl">
            <span className="text-primary">Agent Skill Import</span>
            <br />
            Turn Any Tool Into an AgentNode Package
          </h1>
          <p className="mx-auto mt-5 max-w-2xl text-lg text-muted">
            Best for self-contained LangChain and CrewAI tools in a single file.
          </p>
        </div>
      </section>

      {/* ============================================================ */}
      {/*  CONVERTER                                                    */}
      {/* ============================================================ */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-5xl px-4 sm:px-6 py-10">
          {/* Platform tabs */}
          <div className="mb-6 flex flex-wrap gap-2">
            {PLATFORMS.map((p) => (
              <button
                key={p.id}
                onClick={() => {
                  setPlatform(p.id);
                  setResult(null);
                  setApiResponse(null);
                  setError("");
                }}
                className={`flex items-center gap-2 rounded-full border px-4 py-2 text-sm font-medium transition-all ${
                  platform === p.id
                    ? "border-primary bg-primary/10 text-primary"
                    : "border-border text-muted hover:border-primary/30 hover:text-foreground"
                }`}
              >
                <span>{p.icon}</span>
                {p.name}
              </button>
            ))}
          </div>

          {/* Input area */}
          <div className="relative">
            <textarea
              ref={textareaRef}
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder={`Paste your ${selectedPlatform.name} tool code here...\n\nOr click "Try example" to see it in action`}
              className="h-64 w-full rounded-xl border border-border bg-card p-5 font-mono text-sm text-foreground placeholder:text-muted/40 focus:border-primary/50 focus:outline-none focus:ring-1 focus:ring-primary/20 transition-all"
              spellCheck={false}
            />
            <button
              onClick={handleTryExample}
              className="absolute right-3 top-3 rounded-lg border border-primary/30 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary transition-colors hover:bg-primary/20"
            >
              Try example
            </button>
          </div>

          {/* Convert button */}
          <div className="mt-4 flex items-center gap-4">
            <button
              onClick={handleConvert}
              disabled={loading || !code.trim()}
              className="rounded-xl bg-primary px-8 py-3 text-sm font-semibold text-white transition-all hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {loading ? (
                <span className="flex items-center gap-2">
                  <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                  Converting...
                </span>
              ) : (
                "Convert to ANP"
              )}
            </button>
            <span className="text-xs text-muted">Free — no account required</span>
          </div>

          {/* Error */}
          {error && (
            <div className="mt-4 rounded-lg border border-red-500/30 bg-red-500/5 px-4 py-3 text-sm text-red-400">
              {error}
            </div>
          )}
        </div>
      </section>

      {/* ============================================================ */}
      {/*  RESULT                                                       */}
      {/* ============================================================ */}
      {result && (
        <section className="border-b border-border bg-card/30">
          <div className="mx-auto max-w-5xl px-4 sm:px-6 py-10">
            {/* Draft-only banner */}
            {apiResponse && (
              <div className="mb-6 rounded-lg border border-yellow-500/30 bg-yellow-500/5 px-4 py-3 text-sm text-yellow-300">
                This is a draft only. Review all generated files before publishing.
              </div>
            )}

            {/* Header with confidence badge */}
            <div className="mb-6 flex items-start gap-4">
              <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-green-500/10">
                <svg className="h-6 w-6 text-green-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                </svg>
              </div>
              <div className="flex-1">
                <div className="flex items-center gap-3 flex-wrap">
                  <h2 className="text-xl font-bold text-foreground">
                    {apiResponse?.detected_tools.length
                      ? `${apiResponse.detected_tools.length} tool${apiResponse.detected_tools.length > 1 ? "s" : ""} extracted`
                      : "Your tool is ANP-compatible"}
                  </h2>
                  {apiResponse && (
                    <span className={`rounded-full border px-3 py-0.5 text-xs font-medium ${confidenceColor(apiResponse.confidence.level)}`}>
                      {confidenceLabel(apiResponse.confidence.level)}
                    </span>
                  )}
                </div>
                <p className="mt-1 text-sm text-muted">
                  {apiResponse?.draft_ready
                    ? "Draft generated — review all files before publishing."
                    : apiResponse
                    ? "Needs manual fixes before publishing."
                    : `Converted from ${selectedPlatform.name} — review before publishing.`}
                </p>
              </div>
            </div>

            {/* Confidence reasons */}
            {apiResponse && apiResponse.confidence.reasons.length > 0 && apiResponse.confidence.level !== "high" && (
              <div className="mb-4 rounded-lg border border-border bg-card px-4 py-3">
                <div className="text-xs font-medium uppercase tracking-wider text-muted mb-2">Confidence details</div>
                <ul className="space-y-1">
                  {apiResponse.confidence.reasons.map((r, i) => (
                    <li key={i} className="text-xs text-muted">- {r}</li>
                  ))}
                </ul>
              </div>
            )}

            {/* Grouped warnings */}
            {apiResponse && apiResponse.grouped_warnings && apiResponse.grouped_warnings.length > 0 && (() => {
              const blocking = apiResponse.grouped_warnings.filter(w => w.category === "blocking");
              const review = apiResponse.grouped_warnings.filter(w => w.category === "review");
              const info = apiResponse.grouped_warnings.filter(w => w.category === "info");
              return (
                <div className="mb-4 space-y-3">
                  {blocking.length > 0 && (
                    <div className="rounded-lg border border-red-500/30 bg-red-500/5 px-4 py-3">
                      <div className="text-xs font-semibold uppercase tracking-wider text-red-400 mb-2">Blocking issues</div>
                      <ul className="space-y-1.5">
                        {blocking.map((w, i) => (
                          <li key={i} className="text-xs text-red-300/80">- {w.message}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {review.length > 0 && (
                    <div className="rounded-lg border border-yellow-500/30 bg-yellow-500/5 px-4 py-3">
                      <div className="text-xs font-semibold uppercase tracking-wider text-yellow-400 mb-2">Needs review</div>
                      <ul className="space-y-1.5">
                        {review.map((w, i) => (
                          <li key={i} className="text-xs text-yellow-300/80">- {w.message}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {info.length > 0 && (
                    <div className="rounded-lg border border-border bg-card px-4 py-3">
                      <div className="text-xs font-medium uppercase tracking-wider text-muted mb-2">Informational</div>
                      <ul className="space-y-1.5">
                        {info.map((w, i) => (
                          <li key={i} className="text-xs text-muted">- {w.message}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              );
            })()}

            {/* What Changed */}
            {apiResponse && apiResponse.changes.length > 0 && (
              <div className="mb-6 rounded-lg border border-border bg-card px-4 py-3">
                <div className="text-xs font-medium uppercase tracking-wider text-muted mb-2">What changed</div>
                <ul className="space-y-1">
                  {apiResponse.changes.map((c, i) => (
                    <li key={i} className="text-xs text-foreground/80">- {c}</li>
                  ))}
                </ul>
              </div>
            )}

            {/* Detection cards */}
            <div className="mb-6 grid gap-3 sm:grid-cols-4">
              <div className="rounded-lg border border-border bg-card px-4 py-3">
                <div className="text-xs font-medium uppercase tracking-wider text-muted">Source</div>
                <div className="mt-1 text-sm font-semibold text-foreground">{selectedPlatform.name}</div>
              </div>
              <div className="rounded-lg border border-border bg-card px-4 py-3">
                <div className="text-xs font-medium uppercase tracking-wider text-muted">Tools detected</div>
                <div className="mt-1 text-sm font-semibold text-foreground">{result.toolCount}</div>
              </div>
              <div className="rounded-lg border border-border bg-card px-4 py-3">
                <div className="text-xs font-medium uppercase tracking-wider text-muted">Package ID</div>
                <div className="mt-1 text-sm font-semibold text-primary">{result.packageId}</div>
              </div>
              <div className="rounded-lg border border-border bg-card px-4 py-3">
                <div className="text-xs font-medium uppercase tracking-wider text-muted">Format</div>
                <div className="mt-1 text-sm font-semibold text-green-400">
                  ANP v{String(apiResponse?.manifest_json?.manifest_version || "0.2")}
                </div>
              </div>
            </div>

            {/* Dependencies */}
            {apiResponse && (apiResponse.detected_dependencies.length > 0 || apiResponse.unknown_imports.length > 0) && (
              <div className="mb-6 flex flex-wrap gap-4">
                {apiResponse.detected_dependencies.length > 0 && (
                  <div>
                    <span className="text-xs font-medium text-muted mr-2">Confirmed deps:</span>
                    {apiResponse.detected_dependencies.map((d) => (
                      <span key={d} className="mr-1 rounded-full border border-green-500/30 bg-green-500/5 px-2 py-0.5 text-xs text-green-400">{d}</span>
                    ))}
                  </div>
                )}
                {apiResponse.unknown_imports.length > 0 && (
                  <div>
                    <span className="text-xs font-medium text-muted mr-2">Unknown (not in pyproject):</span>
                    {apiResponse.unknown_imports.map((d) => (
                      <span key={d} className="mr-1 rounded-full border border-yellow-500/30 bg-yellow-500/5 px-2 py-0.5 text-xs text-yellow-400">{d}</span>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Detected tools */}
            {result.tools.length > 0 && (
              <div className="mb-6">
                <h3 className="mb-2 text-sm font-semibold uppercase tracking-wider text-muted">Detected Tools</h3>
                <div className="flex flex-wrap gap-2">
                  {(apiResponse?.detected_tools || result.tools).map((t) => (
                    <div key={t.name} className="rounded-full border border-border bg-card px-3 py-1 text-xs">
                      <span className="font-medium text-foreground">{t.name}</span>
                      {t.description && (
                        <span className="ml-1.5 text-muted">{t.description.slice(0, 50)}</span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Tabbed file preview */}
            {apiResponse && apiResponse.code_files.length > 0 ? (
              <div className="relative overflow-hidden rounded-xl border border-border bg-[#0d1117]">
                <div className="flex items-center border-b border-border/50 overflow-x-auto">
                  {apiResponse.code_files.map((f, i) => {
                    const label = f.path.split("/").pop() || f.path;
                    return (
                      <button
                        key={f.path}
                        onClick={() => setActiveFileTab(i)}
                        className={`shrink-0 px-4 py-2 text-xs font-mono transition-colors ${
                          activeFileTab === i
                            ? "bg-[#161b22] text-foreground border-b-2 border-primary"
                            : "text-muted hover:text-foreground"
                        }`}
                      >
                        {label}
                      </button>
                    );
                  })}
                  <div className="flex-1" />
                  <button
                    onClick={() => {
                      const file = apiResponse.code_files[activeFileTab];
                      if (file) {
                        navigator.clipboard.writeText(file.content);
                        setCopied(true);
                        setTimeout(() => setCopied(false), 2000);
                      }
                    }}
                    className="shrink-0 rounded border border-border mx-2 px-2 py-1 text-xs text-muted transition-colors hover:text-foreground"
                  >
                    {copied ? "Copied!" : "Copy"}
                  </button>
                </div>
                <pre className="max-h-72 overflow-auto p-4 font-mono text-xs leading-relaxed text-gray-300">
                  {apiResponse.code_files[activeFileTab]?.content || ""}
                </pre>
              </div>
            ) : (
              /* Fallback: manifest-only preview */
              <div className="relative overflow-hidden rounded-xl border border-border bg-[#0d1117]">
                <div className="flex items-center justify-between border-b border-border/50 px-4 py-2">
                  <div className="flex items-center gap-2">
                    <div className="h-3 w-3 rounded-full bg-red-500/60" />
                    <div className="h-3 w-3 rounded-full bg-yellow-500/60" />
                    <div className="h-3 w-3 rounded-full bg-green-500/60" />
                    <span className="ml-2 font-mono text-xs text-muted">agentnode.yaml</span>
                  </div>
                  <button
                    onClick={handleCopy}
                    className="rounded border border-border px-2 py-1 text-xs text-muted transition-colors hover:text-foreground"
                  >
                    {copied ? "Copied!" : "Copy"}
                  </button>
                </div>
                <pre className="max-h-72 overflow-auto p-4 font-mono text-xs leading-relaxed text-gray-300">
                  {result.manifest}
                </pre>
              </div>
            )}

            {/* CTAs */}
            <div className="mt-8 rounded-xl border border-primary/30 bg-gradient-to-r from-primary/5 to-primary/10 p-6">
              <div className="flex flex-col gap-5 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <h3 className="text-lg font-bold text-foreground">
                    {apiResponse?.draft_ready === false
                      ? "Manual fixes needed before publishing"
                      : "Publish this package on AgentNode"}
                  </h3>
                  <p className="mt-1 text-sm text-muted">
                    {apiResponse?.draft_ready === false
                      ? "Review the warnings above and fix the highlighted issues, then convert again."
                      : "Make it discoverable and installable by any AI agent."}
                  </p>
                </div>
                <button
                  onClick={handlePublish}
                  disabled={apiResponse?.draft_ready === false}
                  className="shrink-0 rounded-xl bg-primary px-8 py-3 text-center text-sm font-bold text-white transition-colors hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  {apiResponse?.draft_ready === false ? "Not ready" : "Publish on AgentNode"}
                </button>
              </div>

              <div className="mt-5 flex flex-wrap gap-4">
                <button
                  onClick={handleCopy}
                  className="rounded-lg border border-border px-4 py-2 text-sm text-muted transition-colors hover:text-foreground"
                >
                  {copied ? "Copied!" : "Copy manifest"}
                </button>
                <a
                  href={`https://twitter.com/intent/tweet?text=${encodeURIComponent(shareText)}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="rounded-lg border border-border px-4 py-2 text-sm text-muted transition-colors hover:text-foreground"
                >
                  Share on X
                </a>
                <button
                  onClick={() => {
                    setResult(null);
                    setApiResponse(null);
                    setCode("");
                    setError("");
                    setActiveFileTab(0);
                  }}
                  className="rounded-lg border border-border px-4 py-2 text-sm text-muted transition-colors hover:text-foreground"
                >
                  Convert another tool
                </button>
              </div>
            </div>
          </div>
        </section>
      )}

      {/* ============================================================ */}
      {/*  BEFORE / AFTER                                               */}
      {/* ============================================================ */}
      {!result && (
        <>
          <section className="border-b border-border bg-card/30">
            <div className="mx-auto max-w-5xl px-4 sm:px-6 py-14">
              <h2 className="mb-8 text-center text-2xl font-bold text-foreground">
                From framework-locked to universally installable
              </h2>
              <div className="grid gap-6 sm:grid-cols-2">
                <div className="rounded-xl border border-border bg-card p-6">
                  <div className="mb-3 text-sm font-semibold uppercase tracking-wider text-red-400">
                    Before
                  </div>
                  <ul className="space-y-3 text-sm">
                    {[
                      "Tool works in one framework only",
                      "No way for agents to discover it",
                      "No permission model or trust badges",
                      "Manual sharing via GitHub or PyPI",
                    ].map((t) => (
                      <li key={t} className="flex items-center gap-2 text-muted">
                        <span className="text-red-400">&#10005;</span> {t}
                      </li>
                    ))}
                  </ul>
                </div>
                <div className="rounded-xl border border-primary/30 bg-primary/5 p-6">
                  <div className="mb-3 text-sm font-semibold uppercase tracking-wider text-primary">
                    After AgentNode Import
                  </div>
                  <ul className="space-y-3 text-sm">
                    {[
                      "Portable ANP package — works with any Python agent framework",
                      "Discoverable by any AI agent automatically",
                      "Permission declarations + trust verification",
                      "Installable with one command or API call",
                    ].map((t) => (
                      <li key={t} className="flex items-center gap-2 text-foreground">
                        <span className="text-green-400">&#10003;</span> {t}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </div>
          </section>

          {/* HOW IT WORKS */}
          <section className="border-b border-border">
            <div className="mx-auto max-w-5xl px-4 sm:px-6 py-14">
              <h2 className="mb-10 text-center text-2xl font-bold text-foreground">
                Three steps to a published package
              </h2>
              <div className="grid gap-8 sm:grid-cols-3">
                <div className="text-center">
                  <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-primary/10 text-xl font-bold text-primary">
                    1
                  </div>
                  <h3 className="mb-2 text-base font-semibold text-foreground">Paste your code</h3>
                  <p className="text-sm text-muted">
                    Paste a LangChain or CrewAI tool. We detect tools, extract parameters, and analyze imports automatically.
                  </p>
                </div>
                <div className="text-center">
                  <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-primary/10 text-xl font-bold text-primary">
                    2
                  </div>
                  <h3 className="mb-2 text-base font-semibold text-foreground">Review the conversion</h3>
                  <p className="text-sm text-muted">
                    See what changed, check confidence level and warnings, preview all generated files.
                  </p>
                </div>
                <div className="text-center">
                  <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-primary/10 text-xl font-bold text-primary">
                    3
                  </div>
                  <h3 className="mb-2 text-base font-semibold text-foreground">Publish</h3>
                  <p className="text-sm text-muted">
                    One click to publish. Your tool becomes discoverable and installable by AI agents.
                  </p>
                </div>
              </div>
            </div>
          </section>

          {/* SOCIAL PROOF */}
          <section className="border-b border-border bg-card/30">
            <div className="mx-auto max-w-5xl px-4 sm:px-6 py-14">
              <div className="grid gap-6 sm:grid-cols-3">
                <div className="rounded-xl border border-border bg-card p-6 text-center">
                  <div className="text-3xl font-bold text-foreground">77+</div>
                  <div className="mt-1 text-sm text-muted">Published packages</div>
                </div>
                <div className="rounded-xl border border-border bg-card p-6 text-center">
                  <div className="text-3xl font-bold text-foreground">80+</div>
                  <div className="mt-1 text-sm text-muted">Capability IDs</div>
                </div>
                <div className="rounded-xl border border-border bg-card p-6 text-center">
                  <div className="text-3xl font-bold text-foreground">All</div>
                  <div className="mt-1 text-sm text-muted">Frameworks supported</div>
                </div>
              </div>
              <div className="mt-8 flex flex-wrap items-center justify-center gap-4 text-sm text-muted">
                <span>Works with:</span>
                {PLATFORMS.map((p) => (
                  <span key={p.id} className="rounded-full border border-border bg-card px-3 py-1 text-xs font-medium text-foreground">
                    {p.icon} {p.name}
                  </span>
                ))}
              </div>
            </div>
          </section>

          {/* BOTTOM CTA */}
          <section>
            <div className="mx-auto max-w-4xl px-4 sm:px-6 py-20 text-center">
              <h2 className="text-2xl font-bold text-foreground sm:text-3xl">
                Your tool deserves to be discovered
              </h2>
              <p className="mx-auto mt-4 max-w-xl text-muted">
                AI agents are already searching for capabilities on AgentNode.
                Import your tool and make it available to every agent, on every framework.
              </p>
              <button
                onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
                className="mt-8 rounded-xl bg-primary px-8 py-3.5 text-sm font-bold text-white transition-colors hover:bg-primary/90"
              >
                Import your tool now
              </button>
              <p className="mt-4 text-sm text-muted">
                Free to convert — no account needed until you publish.
              </p>
            </div>
          </section>
        </>
      )}
    </div>
  );
}
