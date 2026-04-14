import Link from "next/link";
import TrustBadge from "./TrustBadge";
import VerificationBadge from "./VerificationBadge";

interface PackageCardProps {
  slug: string;
  name?: string;
  summary: string;
  trust_level: "curated" | "trusted" | "verified" | "unverified";
  frameworks: string[];
  version?: string;
  download_count?: number;
  install_count?: number;
  verification_status?: string | null;
  verification_tier?: string | null;
  verification_score?: number | null;
  package_type?: string | null;
  tags?: string[];
  publisher_name?: string | null;
}

function formatDownloads(count: number): string {
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`;
  if (count >= 1_000) return `${(count / 1_000).toFixed(1)}k`;
  return String(count);
}

export default function PackageCard({
  slug,
  name,
  summary,
  trust_level,
  frameworks,
  version,
  download_count,
  install_count,
  verification_status,
  verification_tier,
  verification_score,
  package_type,
  tags,
  publisher_name,
}: PackageCardProps) {
  // Derive UI category from package_type + tags
  const category = package_type === "agent"
    ? "agent"
    : (tags ?? []).some((t) => t === "character" || t === "persona")
      ? "character"
      : (tags ?? []).some((t) => t === "connector")
        ? "connector"
        : null;

  const CATEGORY_STYLES: Record<string, { bg: string; text: string; label: string }> = {
    agent: { bg: "bg-blue-500/10", text: "text-blue-400", label: "agent" },
    character: { bg: "bg-purple-500/10", text: "text-purple-400", label: "character" },
    connector: { bg: "bg-orange-500/10", text: "text-orange-400", label: "connector" },
  };
  return (
    <Link
      href={`/packages/${slug}`}
      className="group flex flex-col gap-3 rounded-xl border border-border bg-card p-5 transition-all hover:border-primary/30 hover:bg-card/80"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="truncate font-mono text-sm font-semibold text-foreground group-hover:text-primary transition-colors">
            {name ?? slug}
          </h3>
          <div className="flex items-center gap-1.5">
            {version && (
              <span className="text-xs text-muted">v{version}</span>
            )}
            {category && CATEGORY_STYLES[category] && (
              <span className={`rounded ${CATEGORY_STYLES[category].bg} px-1.5 py-0.5 text-[10px] font-medium ${CATEGORY_STYLES[category].text}`}>
                {CATEGORY_STYLES[category].label}
              </span>
            )}
            {!category && package_type && package_type !== "toolpack" && (
              <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">
                {package_type}
              </span>
            )}
          </div>
          {publisher_name && (
            <span className="text-xs text-muted truncate">by {publisher_name}</span>
          )}
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <VerificationBadge
            tier={verification_tier}
            score={verification_score}
            status={!verification_tier ? verification_status : undefined}
          />
          <TrustBadge level={trust_level} />
        </div>
      </div>

      <p className="text-sm leading-relaxed text-muted line-clamp-2">
        {summary}
      </p>

      <div className="mt-auto flex items-center justify-between gap-2">
        <div className="flex flex-wrap gap-1.5">
          {frameworks.map((fw) => (
            <span
              key={fw}
              className="rounded-md bg-background px-2 py-0.5 text-xs text-muted"
            >
              {fw}
            </span>
          ))}
        </div>
        <div className="flex items-center gap-2">
          {download_count != null && (
            <span className="whitespace-nowrap text-xs text-muted">
              {formatDownloads(download_count)} downloads
            </span>
          )}
          {install_count != null && install_count > 0 && (
            <span className="whitespace-nowrap text-xs text-muted">
              {formatDownloads(install_count)} installs
            </span>
          )}
        </div>
      </div>
    </Link>
  );
}
