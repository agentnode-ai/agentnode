"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { fetchWithAuth, fetchAllVersions } from "@/lib/api";

interface VersionItem {
  version_number: string;
  channel: string;
  changelog?: string | null;
  published_at: string;
  verification_status?: string | null;
  is_yanked?: boolean;
  quarantine_status?: string;
}

function VerificationStatusBadge({ status }: { status: string | null | undefined }) {
  if (!status) return null;
  switch (status) {
    case "passed":
      return (
        <span className="rounded-full bg-green-500/10 px-2 py-0.5 text-[10px] font-medium text-green-400">
          verified
        </span>
      );
    case "failed":
      return (
        <span className="rounded-full bg-red-500/10 px-2 py-0.5 text-[10px] font-medium text-red-400">
          failed
        </span>
      );
    case "running":
    case "pending":
      return (
        <span className="rounded-full bg-yellow-500/10 px-2 py-0.5 text-[10px] font-medium text-yellow-400">
          verifying
        </span>
      );
    case "error":
      return (
        <span className="rounded-full bg-red-500/10 px-2 py-0.5 text-[10px] font-medium text-red-400">
          error
        </span>
      );
    default:
      return null;
  }
}

export default function VersionHistory({
  versions: initialVersions,
  currentVersion,
  slug,
  installableVersion,
}: {
  versions: VersionItem[];
  currentVersion: string;
  slug: string;
  installableVersion?: string | null;
}) {
  const [versions, setVersions] = useState<VersionItem[]>(initialVersions);
  const [isOwner, setIsOwner] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function enrichForOwner() {
      try {
        const meRes = await fetchWithAuth("/auth/me");
        if (!meRes.ok) return;
        const user = await meRes.json();
        if (!user.publisher) return;

        // Check if we're the owner by comparing publisher slug
        // We need to know the package's publisher — infer from the page or fetch
        const vRes = await fetchAllVersions(slug);
        if (vRes.ok) {
          const data = await vRes.json();
          if (!cancelled && data.versions) {
            setVersions(data.versions);
            setIsOwner(true);
          }
        }
      } catch {
        // Not owner or not logged in — keep public versions
      }
    }
    enrichForOwner();
    return () => { cancelled = true; };
  }, [slug]);

  return (
    <div className="space-y-0">
      {versions.map((v) => {
        const isCurrent = v.version_number === currentVersion;
        const isYanked = v.is_yanked;
        const isQuarantined = v.quarantine_status === "quarantined";

        const isInstallTarget = installableVersion === v.version_number;

        return (
          <Link
            key={v.version_number}
            href={`/packages/${slug}?v=${v.version_number}`}
            className={`flex items-center justify-between px-3 py-2.5 rounded-lg transition-colors ${
              isCurrent
                ? "bg-primary/5 border border-primary/20"
                : "hover:bg-card/80 border border-transparent"
            }`}
          >
            <div className="flex items-center gap-3 min-w-0">
              <span className={`font-mono text-sm ${
                isYanked ? "line-through text-muted" :
                isCurrent ? "text-primary font-medium" : "text-foreground"
              }`}>
                v{v.version_number}
              </span>
              {isCurrent && !isYanked && (
                <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
                  latest
                </span>
              )}
              {isInstallTarget && !isCurrent && (
                <span className="rounded-full bg-blue-500/10 px-2 py-0.5 text-[10px] font-medium text-blue-400">
                  default install
                </span>
              )}
              <VerificationStatusBadge status={v.verification_status} />
              {isOwner && isYanked && (
                <span className="rounded-full bg-red-500/10 px-2 py-0.5 text-[10px] font-medium text-red-400">
                  yanked
                </span>
              )}
              {isOwner && isQuarantined && !isYanked && (
                <span className="rounded-full bg-yellow-500/10 px-2 py-0.5 text-[10px] font-medium text-yellow-400">
                  quarantined
                </span>
              )}
              {v.channel !== "stable" && (
                <span className="rounded bg-card px-1.5 py-0.5 text-[10px] text-muted border border-border">
                  {v.channel}
                </span>
              )}
            </div>
            <span className="text-xs text-muted shrink-0">
              {new Date(v.published_at).toLocaleDateString()}
            </span>
          </Link>
        );
      })}
    </div>
  );
}
