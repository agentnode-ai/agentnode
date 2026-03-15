"use client";

import { useState, useCallback } from "react";

export default function CopyInstallButton() {
  const [copied, setCopied] = useState(false);
  const command = "npm install -g agentnode";

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
      className="group inline-flex h-11 items-center gap-3 rounded-lg bg-primary px-6 text-sm font-medium text-white transition-colors hover:bg-primary-hover"
    >
      <code className="font-mono">{command}</code>
      <span className="text-white/60 transition-colors group-hover:text-white/80">
        {copied ? "\u2713" : "\u2398"}
      </span>
    </button>
  );
}
