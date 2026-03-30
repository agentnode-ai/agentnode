"use client";

import type { ReactNode } from "react";

export function StickyPublishBar({
  onBack,
  children,
}: {
  onBack: () => void;
  children: ReactNode;
}) {
  return (
    <div className="sticky bottom-0 z-10 bg-background/95 backdrop-blur-sm border-t border-border -mx-4 sm:-mx-6 px-4 sm:px-6 py-4 mt-6">
      <div className="flex items-center justify-between">
        <button
          type="button"
          onClick={onBack}
          className="rounded-md border border-border px-5 py-2.5 text-sm text-muted hover:text-foreground transition-colors"
        >
          &#8592; Back to review
        </button>
        {children}
      </div>
    </div>
  );
}
