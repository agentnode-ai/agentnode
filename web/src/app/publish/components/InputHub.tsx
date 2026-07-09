"use client";

import Link from "next/link";
import { PLATFORMS } from "@/lib/import-utils";
import { BUILDER_EXAMPLES } from "@/lib/builder-utils";

import type { InputTab } from "../lib/types";
import { typeLabel } from "../lib/manifest";
import { StepIndicator } from "./StepIndicator";
import type { PublishFormState } from "../hooks/usePublishForm";

export function InputHub({ form }: { form: PublishFormState }) {
  const {
    user,
    authChecked,
    activeTab,
    setActiveTab,
    typeChosen,
    setTypeChosen,
    chooseType,
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

  const label = typeLabel(guided.package_type);
  const loginReturnTo = `/publish?tab=${activeTab}`;

  /* ---- Stage 1: what do you want to publish? ---- */
  if (!typeChosen) {
    return (
      <div className="mx-auto max-w-4xl px-4 sm:px-6 py-12">
        <StepIndicator current={1} />
        <div className="text-center mb-10">
          <h1 className="text-3xl font-bold tracking-tight text-foreground">
            What do you want to publish?
          </h1>
          <p className="mt-3 text-muted">
            Pick a package type &mdash; the form only shows what that type needs.
          </p>
        </div>

        {draftExpired && (
          <div className="mb-6 rounded-lg border border-yellow-500/20 bg-yellow-500/5 px-4 py-3 text-sm text-yellow-500">
            Your temporary draft expired. Start again to continue.
          </div>
        )}

        <div className="grid gap-4 sm:grid-cols-2">
          {([
            {
              type: "toolpack" as const,
              title: "Tool Pack",
              desc: "Executable tools (Python) that agents install and run — sandboxed by trust level.",
            },
            {
              type: "skill" as const,
              title: "Skill",
              desc: "Prompt-only instructions (SKILL.md) an agent loads — no code, safe by construction.",
            },
            {
              type: "agent" as const,
              title: "Agent",
              desc: "An orchestrator that uses tools from the registry to accomplish goals.",
            },
          ]).map((c) => (
            <button
              key={c.type}
              onClick={() => { chooseType(c.type); setError(""); setDraftExpired(false); }}
              className="rounded-xl border border-border bg-card p-6 text-left transition-all hover:border-primary/50 hover:bg-primary/5"
            >
              <h2 className="text-lg font-bold text-foreground">{c.title}</h2>
              <p className="mt-1.5 text-sm text-muted">{c.desc}</p>
            </button>
          ))}
          <Link
            href="/mcp/submit"
            className="rounded-xl border border-border bg-card p-6 text-left transition-all hover:border-primary/50 hover:bg-primary/5"
          >
            <h2 className="text-lg font-bold text-foreground">MCP Server</h2>
            <p className="mt-1.5 text-sm text-muted">
              List an existing npm/PyPI MCP server — not a from-scratch build like
              the others. AgentNode verifies the package and ownership automatically;
              today, listings still get a review before going live.
            </p>
          </Link>
        </div>
      </div>
    );
  }

  /* ---- Stage 2: entry methods for the chosen type ---- */
  const availableTabs: { id: InputTab; label: string }[] =
    guided.package_type === "skill"
      ? [
          { id: "describe", label: "Describe it" },
          { id: "manifest", label: "Paste manifest" },
        ]
      : guided.package_type === "agent"
        ? [{ id: "manifest", label: "Paste manifest" }]
        : [
            { id: "import", label: "Import code" },
            { id: "manifest", label: "Paste manifest" },
          ];
  const effectiveTab: InputTab = availableTabs.some((t) => t.id === activeTab)
    ? activeTab
    : availableTabs[0].id;

  return (
    <div className="mx-auto max-w-4xl px-4 sm:px-6 py-12">
      <StepIndicator current={1} />
      {/* Header */}
      <div className="text-center mb-10">
        <h1 className="text-3xl font-bold tracking-tight text-foreground">
          Publish your {label}
        </h1>
        <p className="mt-3 text-muted">
          {guided.package_type === "skill"
            ? "Describe it, paste a manifest, or start from a blank SKILL.md."
            : guided.package_type === "agent"
              ? "Paste a manifest or configure your agent from scratch."
              : "Import existing code, paste a manifest, or start from scratch."}{" "}
          <button
            type="button"
            onClick={() => { setTypeChosen(false); setError(""); }}
            className="text-primary hover:underline"
          >
            Change type
          </button>
        </p>
      </div>

      {/* Draft expired notice */}
      {draftExpired && (
        <div className="mb-6 rounded-lg border border-yellow-500/20 bg-yellow-500/5 px-4 py-3 text-sm text-yellow-500">
          Your temporary draft expired. Start again to continue.
        </div>
      )}

      {/* Tab selector (only when the type has more than one entry method) */}
      {availableTabs.length > 1 && (
        <div className="mb-8 flex justify-center">
          <div className="inline-flex rounded-full border border-border bg-card p-1">
            {availableTabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => { setActiveTab(tab.id); setError(""); setDraftExpired(false); }}
                className={`rounded-full px-5 py-2 text-sm font-medium transition-all ${
                  effectiveTab === tab.id
                    ? "bg-primary text-white shadow-sm"
                    : "text-muted hover:text-foreground"
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>
      )}

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
      {effectiveTab === "describe" && (
        <div className="space-y-6">
          {/* Say the auth requirement BEFORE the user writes a description,
              not as a surprise on Generate. */}
          {authChecked && !user && (
            <div className="rounded-lg border border-yellow-500/20 bg-yellow-500/5 px-4 py-2.5 text-xs text-yellow-400">
              Generating requires an account &mdash; your description is kept
              while you{" "}
              <Link href={`/auth/login?returnTo=${encodeURIComponent(loginReturnTo)}`} className="underline font-medium hover:text-yellow-300">
                sign in
              </Link>
              .
            </div>
          )}
          <div>
            <label className="mb-2 block text-sm font-medium text-foreground">
              Describe the skill you want to create in plain English
            </label>
            <p className="mb-2 text-xs text-muted">
              Generates a prompt-only skill (SKILL.md + manifest). For tool packs
              and agents, use Import code, Paste manifest, or start fresh.
            </p>
            <textarea
              rows={4}
              value={descriptionText}
              onChange={(e) => { setDescriptionText(e.target.value); setError(""); }}
              className="w-full rounded-xl border border-border bg-card px-4 py-3 text-sm text-foreground placeholder:text-muted/60 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary/30 resize-none"
              placeholder='e.g. "A skill that turns commit history into concise release notes"'
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
      {effectiveTab === "import" && (
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
            <span className="text-xs text-muted">
              Free &mdash; quick preview without an account, full conversion when signed in
            </span>
          </div>
        </div>
      )}

      {/* ---- TAB: Paste Manifest ---- */}
      {effectiveTab === "manifest" && (
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
          </div>
        </div>
      )}

      {/* Start from scratch — first-class for every type, not hidden in a tab */}
      <div className="mt-8 border-t border-border pt-6 text-center">
        <p className="text-sm text-muted">
          Prefer a blank form?{" "}
          <button
            onClick={handleStartFresh}
            className="font-medium text-primary hover:underline"
          >
            Start your {label} from scratch
          </button>
        </p>
      </div>

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
              <strong className="text-foreground">Describe it</strong> &mdash; tell us what your skill does in plain English and our AI writes the SKILL.md and manifest (skills are prompt-only).{" "}
              <strong className="text-foreground">Import code</strong> &mdash; paste an existing LangChain tool, CrewAI tool, or MCP server and we convert it into a tool pack automatically.{" "}
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
              Publishing on AgentNode is free for all developers. Skills are discoverable via search, API, and the <Link href="/docs/cli" className="text-primary hover:underline">CLI</Link>. The registry is built on the open <Link href="/docs/manifest" className="text-primary hover:underline">Agent Node Package (ANP)</Link> specification.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
