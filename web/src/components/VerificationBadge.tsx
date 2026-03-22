/**
 * Verification badge — supports both legacy status-based and new tier-based display.
 *
 * Phase 4B: When tier is provided, shows tier-based badge with score tooltip.
 * Fallback: status-based badge for backward compatibility.
 */

const tierConfig: Record<string, { label: string; className: string; icon: string }> = {
  gold: {
    label: "Gold Verified",
    className: "bg-yellow-500/10 text-yellow-400 border-yellow-500/20",
    icon: "\u2605", // star
  },
  verified: {
    label: "Verified",
    className: "bg-green-500/10 text-green-400 border-green-500/20",
    icon: "\u2714", // checkmark
  },
  partial: {
    label: "Partially Verified",
    className: "bg-yellow-500/10 text-yellow-400 border-yellow-500/20",
    icon: "\u25CB", // circle
  },
  unverified: {
    label: "Unverified",
    className: "bg-zinc-500/10 text-zinc-500 border-zinc-500/20",
    icon: "",
  },
};

const statusConfig: Record<string, { label: string; className: string; icon: string }> = {
  passed: {
    label: "Verified",
    className: "bg-green-500/10 text-green-400 border-green-500/20",
    icon: "\u2714",
  },
  failed: {
    label: "Failed",
    className: "bg-red-500/10 text-red-400 border-red-500/20",
    icon: "\u2716",
  },
  error: {
    label: "Error",
    className: "bg-red-500/10 text-red-400 border-red-500/20",
    icon: "!",
  },
  running: {
    label: "Verifying\u2026",
    className: "bg-yellow-500/10 text-yellow-400 border-yellow-500/20",
    icon: "\u25F7",
  },
  pending: {
    label: "Pending",
    className: "bg-zinc-500/10 text-zinc-400 border-zinc-500/20",
    icon: "\u25CB",
  },
  skipped: {
    label: "Skipped",
    className: "bg-zinc-500/10 text-zinc-400 border-zinc-500/20",
    icon: "\u2014",
  },
};

const PARTIAL_TOOLTIP: Record<string, string> = {
  needs_credentials: "Requires API credentials for full verification",
  missing_system_dependency: "Requires system dependencies not available in sandbox",
  needs_binary_input: "Requires binary files for full verification",
  external_network_blocked: "Requires network access blocked during verification",
};

interface VerificationBadgeProps {
  status?: string | null;
  tier?: string | null;
  score?: number | null;
  smoke_reason?: string | null;
  size?: "sm" | "md";
}

export default function VerificationBadge({
  status,
  tier,
  score,
  smoke_reason,
  size = "sm",
}: VerificationBadgeProps) {
  // Phase 4B: Use tier-based display when available
  if (tier) {
    const config = tierConfig[tier] ?? tierConfig.unverified;

    // Show a subtle badge even for unverified so the state is visible
    if (tier === "unverified") {
      const sizeClasses = size === "md"
        ? "px-2.5 py-1 text-xs gap-1.5"
        : "px-2 py-0.5 text-[11px] gap-1";
      return (
        <span
          className={`inline-flex items-center rounded-md border font-medium ${config.className} ${sizeClasses}`}
          title={score != null ? `Verification score: ${score}/100` : "Not yet fully verified"}
        >
          {config.label}
        </span>
      );
    }

    const tooltip = tier === "partial" && smoke_reason
      ? PARTIAL_TOOLTIP[smoke_reason] ?? `Score: ${score ?? "?"}/100`
      : score != null
        ? `Verification score: ${score}/100`
        : config.label;

    const sizeClasses = size === "md"
      ? "px-2.5 py-1 text-xs gap-1.5"
      : "px-2 py-0.5 text-[11px] gap-1";

    return (
      <span
        className={`inline-flex items-center rounded-md border font-medium ${config.className} ${sizeClasses}`}
        title={tooltip}
      >
        {config.icon && <span className="text-[10px]">{config.icon}</span>}
        {config.label}
        {score != null && size === "md" && (
          <span className="opacity-70 font-mono text-[10px]">{score}</span>
        )}
      </span>
    );
  }

  // Legacy: status-based display
  if (!status) return null;

  const config = statusConfig[status] ?? statusConfig.pending;

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-medium ${config.className}`}
      title={`Verification: ${status}`}
    >
      <span className="text-[10px]">{config.icon}</span>
      {config.label}
    </span>
  );
}
