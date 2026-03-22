"use client";

import { useState, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";

interface VersionSelectorProps {
  slug: string;
  currentVersion: string;
  versions: Array<{
    version_number: string;
    verification_status?: string | null;
  }>;
}

function StatusDot({ status }: { status: string | null | undefined }) {
  if (!status) return null;
  switch (status) {
    case "passed":
      return <span className="inline-block h-1.5 w-1.5 rounded-full bg-green-400 shrink-0" title="Verified" />;
    case "failed":
      return <span className="inline-block h-1.5 w-1.5 rounded-full bg-red-400 shrink-0" title="Failed" />;
    case "error":
      return <span className="inline-block h-1.5 w-1.5 rounded-full bg-red-400 shrink-0" title="Error" />;
    case "running":
    case "pending":
      return <span className="inline-block h-1.5 w-1.5 rounded-full bg-yellow-400 animate-pulse shrink-0" title="Verifying" />;
    default:
      return null;
  }
}

export default function VersionSelector({
  slug,
  currentVersion,
  versions,
}: VersionSelectorProps) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close on click outside
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    if (open) {
      document.addEventListener("mousedown", handleClick);
      return () => document.removeEventListener("mousedown", handleClick);
    }
  }, [open]);

  // Close on Escape
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    if (open) {
      document.addEventListener("keydown", handleKey);
      return () => document.removeEventListener("keydown", handleKey);
    }
  }, [open]);

  if (versions.length <= 1) return null;

  const currentStatus = versions.find((v) => v.version_number === currentVersion)?.verification_status;
  const isLatest = currentVersion === versions[0]?.version_number;

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="inline-flex items-center gap-2 rounded-md bg-card border border-border px-2.5 py-1 text-xs font-mono text-foreground hover:border-primary/40 transition-colors"
      >
        <StatusDot status={currentStatus} />
        <span className="font-medium">v{currentVersion}</span>
        {isLatest && (
          <span className="text-[9px] text-primary font-sans font-medium">latest</span>
        )}
        <svg
          className={`h-3 w-3 text-muted transition-transform ${open ? "rotate-180" : ""}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {open && (
        <div className="absolute top-full left-0 mt-1 z-50 min-w-[200px] rounded-lg border border-border bg-card shadow-xl overflow-hidden">
          <div className="max-h-64 overflow-y-auto py-1">
            {versions.map((v, i) => {
              const isCurrent = v.version_number === currentVersion;
              return (
                <button
                  key={v.version_number}
                  onClick={() => {
                    setOpen(false);
                    if (i === 0) {
                      router.push(`/packages/${slug}`);
                    } else {
                      router.push(`/packages/${slug}?v=${v.version_number}`);
                    }
                  }}
                  className={`w-full flex items-center gap-2.5 px-3 py-2 text-left transition-colors ${
                    isCurrent
                      ? "bg-primary/10 text-primary"
                      : "text-foreground hover:bg-card/80"
                  }`}
                >
                  <StatusDot status={v.verification_status} />
                  <span className={`text-xs font-mono ${isCurrent ? "font-bold" : ""}`}>
                    v{v.version_number}
                  </span>
                  {i === 0 && (
                    <span className="rounded-full bg-primary/10 px-1.5 py-0.5 text-[9px] text-primary font-medium">
                      latest
                    </span>
                  )}
                  {v.verification_status === "passed" && (
                    <span className="text-[10px] text-green-400 ml-auto">verified</span>
                  )}
                  {v.verification_status === "failed" && (
                    <span className="text-[10px] text-red-400 ml-auto">failed</span>
                  )}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
