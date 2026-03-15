"use client";

import { useState, useCallback } from "react";

interface CodeBlockProps {
  code: string;
  language?: string;
  showCopy?: boolean;
}

export default function CodeBlock({
  code,
  language = "bash",
  showCopy = true,
}: CodeBlockProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Fallback: silently fail
    }
  }, [code]);

  return (
    <div className="group relative rounded-lg border border-border bg-card overflow-hidden">
      {language && (
        <div className="flex items-center justify-between border-b border-border px-4 py-2">
          <span className="text-xs text-muted font-mono">{language}</span>
          {showCopy && (
            <button
              onClick={handleCopy}
              className="text-xs text-muted transition-colors hover:text-foreground"
            >
              {copied ? "Copied!" : "Copy"}
            </button>
          )}
        </div>
      )}
      <pre className="overflow-x-auto p-4">
        <code className="text-sm font-mono text-foreground">{code}</code>
      </pre>
    </div>
  );
}
