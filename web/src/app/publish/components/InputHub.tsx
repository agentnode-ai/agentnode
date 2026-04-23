"use client";

import Link from "next/link";
import { PLATFORMS } from "@/lib/import-utils";
import { BUILDER_EXAMPLES } from "@/lib/builder-utils";

import type { InputTab } from "../lib/types";
import { StepIndicator } from "./StepIndicator";
import type { PublishFormState } from "../hooks/usePublishForm";

export function InputHub({ form }: { form: PublishFormState }) {
  const {
    activeTab,
    setActiveTab,
    guided,
    descriptionText,
    setDescriptionText,
    generating,
    importPlatform,
    setImportPlatform,
    importCode,
    setImportCode,
    manifestInput,
    setManifestInput,
    importConverting,
    draftExpired,
    setDraftExpired,
    error,
    setError,
    handleGenerate,
    handleConvert,
    handleContinueWithManifest,
    handleStartFresh,
  } = form;

  const typeLabel = guided.package_type === "agent" ? "agent" : "skill";

  const loginReturnTo = `/publish?tab=${activeTab}`;

  return (
    <div className="mx-auto max-w-3xl px-4 sm:px-6 py-12">
      <StepIndicator current={1} />
      {/* Header */}
      <div className="text-center mb-10">
        <h1 className="text-3xl font-bold tracking-tight text-foreground">
          Publish your {typeLabel}
        </h1>
        <p className="mt-3 text-muted">
          Describe, import, or paste &mdash; we&apos;ll handle the rest.
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
              Describe what your {typeLabel} does in plain English
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
              Publishing on AgentNode is free for all developers. Skills are discoverable via search, API, and the <Link href="/docs#cli-reference" className="text-primary hover:underline">CLI</Link>. The registry is built on the open <Link href="/docs#anp-manifest" className="text-primary hover:underline">Agent Node Package (ANP)</Link> specification.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
