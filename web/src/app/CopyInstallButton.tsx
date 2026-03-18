"use client";

import { useState, useCallback } from "react";

export default function CopyInstallButton() {
  const [copied, setCopied] = useState(false);
  const command = "pip install agentnode-sdk";

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Silently fail
    }
  }, [command]);

  return (
    <button
      onClick={handleCopy}
      className="group inline-flex h-11 items-center gap-3 rounded-lg border border-border bg-card px-5 text-sm transition-colors hover:border-primary/40 hover:bg-card/80"
    >
      <span className="text-muted/60 font-mono">$</span>
      <code className="whitespace-nowrap font-mono text-foreground">{command}</code>
      <span className="ml-1 text-muted/40 transition-colors group-hover:text-muted">
        {copied ? "\u2713 Copied" : "Copy"}
      </span>
    </button>
  );
}
