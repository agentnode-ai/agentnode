"use client";

import type { ReactNode } from "react";

export type PanelStatus = "complete" | "incomplete" | "warning";

export function CollapsiblePanel({
  title,
  subtitle,
  open,
  onToggle,
  status,
  children,
}: {
  title: string;
  subtitle?: string;
  open: boolean;
  onToggle: () => void;
  status?: PanelStatus;
  children: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-border overflow-hidden">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center justify-between px-5 py-4 text-left hover:bg-card/50 transition-colors"
      >
        <div className="flex items-center gap-3 min-w-0 pr-4">
          {status && <StatusDot status={status} />}
          <div className="min-w-0">
            <div className="text-sm font-medium text-foreground">{title}</div>
            {subtitle && !open && (
              <div className="mt-0.5 text-xs text-muted truncate">{subtitle}</div>
            )}
          </div>
        </div>
        <svg
          className={`h-4 w-4 shrink-0 text-muted transition-transform ${open ? "rotate-180" : ""}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {open && (
        <div className="border-t border-border px-5 py-5 space-y-4">{children}</div>
      )}
    </div>
  );
}

function StatusDot({ status }: { status: PanelStatus }) {
  const colors = {
    complete: "bg-success",
    incomplete: "bg-muted/40",
    warning: "bg-yellow-500",
  };
  return (
    <span
      className={`inline-block h-2 w-2 shrink-0 rounded-full ${colors[status]}`}
      title={status === "complete" ? "Complete" : status === "warning" ? "Needs review" : "Incomplete"}
    />
  );
}
