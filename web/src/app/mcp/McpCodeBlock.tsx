"use client";

import { useState, useCallback } from "react";

interface McpCodeBlockProps {
  code: string;
  language?: string;
  altCode?: string;
  altLanguage?: string;
}

export default function McpCodeBlock({
  code,
  language = "bash",
  altCode,
  altLanguage,
}: McpCodeBlockProps) {
  const [copied, setCopied] = useState(false);
  const [activeTab, setActiveTab] = useState(0);

  const currentCode = activeTab === 0 ? code : (altCode ?? code);
  const currentLang = activeTab === 0 ? language : (altLanguage ?? language);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(currentCode);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // silently fail
    }
  }, [currentCode]);

  const hasTabs = altCode && altLanguage;

  return (
    <div className="rounded-lg border border-border bg-card overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-4 py-2">
        {hasTabs ? (
          <div className="flex gap-3">
            <button
              onClick={() => setActiveTab(0)}
              className={`text-xs font-mono transition-colors ${
                activeTab === 0
                  ? "text-foreground"
                  : "text-muted hover:text-foreground"
              }`}
            >
              {language}
            </button>
            <button
              onClick={() => setActiveTab(1)}
              className={`text-xs font-mono transition-colors ${
                activeTab === 1
                  ? "text-foreground"
                  : "text-muted hover:text-foreground"
              }`}
            >
              {altLanguage}
            </button>
          </div>
        ) : (
          <span className="text-xs text-muted font-mono">{currentLang}</span>
        )}
        <button
          onClick={handleCopy}
          className="text-xs text-muted transition-colors hover:text-foreground"
        >
          {copied ? "Copied!" : "Copy"}
        </button>
      </div>
      <pre className="overflow-x-auto p-4">
        <code className="text-sm font-mono text-foreground">{currentCode}</code>
      </pre>
    </div>
  );
}
