"use client";

import { useState } from "react";
import MarkdownRenderer from "@/components/MarkdownRenderer";

interface ReadmeSectionProps {
  content: string;
}

export default function ReadmeSection({ content }: ReadmeSectionProps) {
  const [expanded, setExpanded] = useState(false);

  // Estimate if content is long enough to need collapsing (~800px worth of content)
  // Rough heuristic: 40 lines or 3000 chars
  const isLong = content.split("\n").length > 40 || content.length > 3000;

  return (
    <section className="rounded-xl border border-border bg-card p-4 sm:p-6">
      <h2 className="mb-4 text-lg font-semibold text-foreground">README</h2>
      <div
        className={`relative ${
          !expanded && isLong ? "max-h-[500px] overflow-hidden" : ""
        }`}
      >
        <MarkdownRenderer content={content} />

        {/* Gradient fade overlay when collapsed */}
        {!expanded && isLong && (
          <div className="absolute bottom-0 left-0 right-0 h-32 bg-gradient-to-t from-card to-transparent pointer-events-none" />
        )}
      </div>

      {isLong && (
        <button
          onClick={() => setExpanded(!expanded)}
          className="mt-3 text-sm text-primary hover:text-primary-hover transition-colors"
        >
          {expanded ? "Show less" : "Show more"}
        </button>
      )}
    </section>
  );
}
