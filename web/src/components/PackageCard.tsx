import Link from "next/link";
import TrustBadge from "./TrustBadge";
import VerificationBadge from "./VerificationBadge";
import SandboxBadge from "./SandboxBadge";

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
  runtime?: string | null;
  tags?: string[];
  publisher_name?: string | null;
  is_deprecated?: boolean;
  network_level?: string | null;
  filesystem_level?: string | null;
  code_execution_level?: string | null;
  has_connector?: boolean | null;
}

// One consistent kind chip per card — same wording and colors everywhere.
const KIND_STYLES = {
  toolpack: { label: "Tool Pack", className: "bg-primary/10 text-primary border-primary/20" },
  skill: { label: "Skill", className: "bg-green-500/10 text-green-400 border-green-500/20" },
  agent: { label: "Agent", className: "bg-blue-500/10 text-blue-400 border-blue-500/20" },
  mcp: { label: "MCP Server", className: "bg-violet-500/10 text-violet-400 border-violet-500/20" },
} as const;

const NETWORK_EXTERNAL_VALUES = new Set(["restricted", "unrestricted"]);
const FILESYSTEM_WRITE_VALUES = new Set(["workspace_write", "any"]);
const CODE_EXEC_ACTIVE_VALUES = new Set(["limited_subprocess", "shell"]);

function getRiskLabel(
  networkLevel: string | null | undefined,
  filesystemLevel: string | null | undefined,
  codeExecutionLevel: string | null | undefined,
  hasConnector: boolean | null | undefined,
  isConnectorCategory: boolean,
): string | null {
  const hasExternalNetwork = !!networkLevel && NETWORK_EXTERNAL_VALUES.has(networkLevel);
  const connector = hasConnector ?? isConnectorCategory;

  if (hasExternalNetwork && connector) return "external services + credentials";
  if (codeExecutionLevel && CODE_EXEC_ACTIVE_VALUES.has(codeExecutionLevel)) return "runs code on your system";
  if (filesystemLevel && FILESYSTEM_WRITE_VALUES.has(filesystemLevel)) return "can modify local files";
  if (hasExternalNetwork && networkLevel === "restricted") return "connects to external services";
  if (hasExternalNetwork) return "full network access";
  if (connector) return "uses credentials";
  return null;
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
  runtime,
  tags,
  publisher_name,
  is_deprecated,
  network_level,
  filesystem_level,
  code_execution_level,
  has_connector,
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

  const riskLabel = getRiskLabel(
    network_level,
    filesystem_level,
    code_execution_level,
    has_connector,
    category === "connector",
  );

  // The one canonical package KIND, derived the same way everywhere:
  // MCP servers are identified by runtime, everything else by package_type.
  const kind: keyof typeof KIND_STYLES =
    runtime === "mcp"
      ? "mcp"
      : package_type === "skill"
        ? "skill"
        : package_type === "agent"
          ? "agent"
          : "toolpack";

  return (
    <Link
      href={`/packages/${slug}`}
      className="group flex flex-col gap-3 rounded-xl border border-border bg-card p-5 transition-all hover:border-primary/30 hover:bg-card/80"
    >
      {/* Name gets its own full-width row — badges live on a second row so
          they can never squeeze the package name out of view. */}
      <div className="min-w-0">
        <div className="flex items-baseline gap-2 min-w-0">
          <h3 className="truncate font-mono text-sm font-semibold text-foreground group-hover:text-primary transition-colors">
            {name ?? slug}
          </h3>
          {version && (
            <span className="shrink-0 text-xs text-muted">v{version}</span>
          )}
        </div>
        {publisher_name && (
          <div className="text-xs text-muted truncate">by {publisher_name}</div>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        {/* Package KIND — always first, always shown, one consistent style:
            the reader identifies what kind of package this is at a glance. */}
        <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${KIND_STYLES[kind].className}`}>
          {KIND_STYLES[kind].label}
        </span>
        <VerificationBadge
          tier={verification_tier}
          score={verification_score}
          status={!verification_tier ? verification_status : undefined}
        />
        <TrustBadge level={trust_level} />
        <SandboxBadge
          package_type={package_type}
          trust_level={trust_level}
          runtime={runtime}
        />
        {category && CATEGORY_STYLES[category] && category !== "agent" && (
          <span className={`rounded ${CATEGORY_STYLES[category].bg} px-1.5 py-0.5 text-[10px] font-medium ${CATEGORY_STYLES[category].text}`}>
            {CATEGORY_STYLES[category].label}
          </span>
        )}
        {is_deprecated && (
          <span className="rounded bg-red-500/10 px-1.5 py-0.5 text-[10px] font-medium text-red-400">
            deprecated
          </span>
        )}
      </div>

      <p className="text-sm leading-relaxed text-muted line-clamp-2">
        {summary}
      </p>

      {riskLabel && (
        <span className="inline-block self-start rounded bg-amber-500/10 px-2 py-0.5 text-[10px] text-amber-400">
          {riskLabel}
        </span>
      )}

      <div className="mt-auto flex items-center justify-between gap-2">
        <div className="flex flex-wrap gap-1.5">
          {/* "generic" (works anywhere) and "mcp" (already the kind chip) are
              noise here — show only genuinely distinguishing frameworks. */}
          {frameworks
            .filter((fw) => fw !== "generic" && fw !== "mcp")
            .map((fw) => (
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
