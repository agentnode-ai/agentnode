"use client";

import { useState, useCallback } from "react";

interface Example {
  title: string;
  language: string;
  code: string;
}

interface EnvRequirement {
  name: string;
  required: boolean;
  description?: string | null;
}

interface QuickStartProps {
  slug: string;
  entrypoint?: string | null;
  examples?: Example[] | null;
  envRequirements?: EnvRequirement[] | null;
  readmeMd?: string | null;
  installResolution?: string | null;
  installableVersion?: string | null;
  latestVersion?: string | null;
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // ignore
    }
  }, [text]);

  return (
    <button
      onClick={handleCopy}
      className="text-xs text-muted hover:text-foreground transition-colors shrink-0"
    >
      {copied ? (
        <span className="text-green-400">Copied!</span>
      ) : (
        "Copy"
      )}
    </button>
  );
}

function CodeWithCopy({
  code,
  language,
}: {
  code: string;
  language?: string;
}) {
  return (
    <div className="rounded-lg border border-border bg-card overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-4 py-2">
        <span className="text-xs text-muted font-mono">{language || "text"}</span>
        <CopyButton text={code} />
      </div>
      <pre className="overflow-x-auto p-4">
        <code className="text-sm font-mono text-foreground">{code}</code>
      </pre>
    </div>
  );
}

function extractQuickStartFromReadme(readme: string): string | null {
  const lines = readme.split("\n");
  let capturing = false;
  const content: string[] = [];

  for (const line of lines) {
    if (/^##\s+(quick\s*start|getting\s*started)/i.test(line)) {
      capturing = true;
      continue;
    }
    if (capturing && /^##\s/.test(line)) {
      break;
    }
    if (capturing) {
      content.push(line);
    }
  }

  if (content.length === 0) return null;

  // Extract first code block
  const joined = content.join("\n");
  const match = joined.match(/```[\w]*\n([\s\S]*?)```/);
  return match ? match[1].trim() : null;
}

export default function QuickStart({
  slug,
  entrypoint,
  examples,
  envRequirements,
  readmeMd,
  installResolution,
  installableVersion,
  latestVersion,
}: QuickStartProps) {
  const [activeTab, setActiveTab] = useState<"cli" | "python">("cli");

  const cliCommand = `agentnode install ${slug}`;
  const pythonImport = entrypoint
    ? `from ${entrypoint.split(":")[0]} import ${entrypoint.split(":")[1] || "run"}`
    : `from agentnode_sdk import run_tool\nresult = run_tool("${slug}", mode="auto")`;

  // Determine usage example with fallback logic + source tracking
  let usageCode: string | null = null;
  let usageLanguage = "python";
  let usageSource: "example" | "readme" | "template" | null = null;

  if (examples && examples.length > 0) {
    usageCode = examples[0].code;
    usageLanguage = examples[0].language || "python";
    usageSource = "example";
  } else if (readmeMd) {
    usageCode = extractQuickStartFromReadme(readmeMd);
    if (usageCode) usageSource = "readme";
  }

  if (!usageCode && entrypoint) {
    const [mod, fn] = entrypoint.split(":");
    usageCode = `from ${mod} import ${fn || "run"}\n\nresult = ${fn || "run"}()\nprint(result)`;
    usageSource = "template";
  }

  return (
    <section className="rounded-xl border border-primary/20 bg-primary/5 p-4 sm:p-6">
      <h2 className="mb-4 text-lg font-semibold text-foreground">Quick Start</h2>

      {/* Install tabs */}
      <div className="mb-4">
        <div className="flex gap-0 border-b border-border mb-3">
          <button
            onClick={() => setActiveTab("cli")}
            className={`px-4 py-2 text-xs font-medium border-b-2 transition-colors ${
              activeTab === "cli"
                ? "border-primary text-primary"
                : "border-transparent text-muted hover:text-foreground"
            }`}
          >
            CLI
          </button>
          <button
            onClick={() => setActiveTab("python")}
            className={`px-4 py-2 text-xs font-medium border-b-2 transition-colors ${
              activeTab === "python"
                ? "border-primary text-primary"
                : "border-transparent text-muted hover:text-foreground"
            }`}
          >
            Python
          </button>
        </div>

        {activeTab === "cli" && (
          <CodeWithCopy code={cliCommand} language="bash" />
        )}
        {activeTab === "python" && (
          <CodeWithCopy code={pythonImport} language="python" />
        )}

        {/* Install resolution note */}
        {installableVersion && latestVersion && installableVersion !== latestVersion && (
          installResolution === "fallback" ? (
            <p className="mt-2 text-xs text-yellow-400">
              No verified version available. Installing latest published version.
            </p>
          ) : (
            <p className="mt-2 text-xs text-muted">
              Installs v{installableVersion} ({installResolution}). Latest published is v{latestVersion}.
            </p>
          )
        )}
      </div>

      {/* Usage example */}
      {usageCode && (
        <div className="mt-4">
          <div className="flex items-center gap-2 mb-2">
            <p className="text-xs font-medium uppercase tracking-wider text-muted">
              Usage
            </p>
            {usageSource === "example" && (
              <span
                className="rounded bg-green-500/10 px-2 py-0.5 text-[10px] text-green-400 cursor-help"
                title="Defined by the package author in manifest examples[]"
              >
                From package
              </span>
            )}
            {usageSource === "readme" && (
              <span
                className="rounded bg-blue-500/10 px-2 py-0.5 text-[10px] text-blue-400 cursor-help"
                title="Extracted from the Quick Start section of the README"
              >
                From documentation
              </span>
            )}
            {usageSource === "template" && (
              <span
                className="rounded bg-yellow-500/10 px-2 py-0.5 text-[10px] text-yellow-400 cursor-help"
                title="Auto-generated fallback based on the package entrypoint"
              >
                Generated template
              </span>
            )}
          </div>
          <CodeWithCopy code={usageCode} language={usageLanguage} />
        </div>
      )}

      {/* Env requirements */}
      {envRequirements && envRequirements.length > 0 && (
        <div className="mt-4">
          <p className="text-xs font-medium uppercase tracking-wider text-muted mb-2">
            Environment Variables
          </p>
          <div className="space-y-1.5">
            {envRequirements.map((env) => (
              <div
                key={env.name}
                className="flex items-start gap-3 rounded-lg bg-card border border-border px-3 py-2"
              >
                <code className="text-xs font-mono text-primary shrink-0">{env.name}</code>
                <div className="min-w-0">
                  {env.description && (
                    <p className="text-xs text-muted truncate">{env.description}</p>
                  )}
                </div>
                {env.required && (
                  <span className="text-[10px] text-red-400 shrink-0">required</span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
