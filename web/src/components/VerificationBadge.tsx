/**
 * Inline verification status badge — server-renderable.
 * Shows a compact status indicator in the package header.
 */

const statusConfig: Record<string, { label: string; className: string; icon: string }> = {
  passed: {
    label: "Verified",
    className: "bg-green-500/10 text-green-400 border-green-500/20",
    icon: "\u2714", // checkmark
  },
  failed: {
    label: "Failed",
    className: "bg-red-500/10 text-red-400 border-red-500/20",
    icon: "\u2716", // cross
  },
  error: {
    label: "Error",
    className: "bg-red-500/10 text-red-400 border-red-500/20",
    icon: "!",
  },
  running: {
    label: "Verifying\u2026",
    className: "bg-yellow-500/10 text-yellow-400 border-yellow-500/20",
    icon: "\u25F7", // clock
  },
  pending: {
    label: "Pending",
    className: "bg-zinc-500/10 text-zinc-400 border-zinc-500/20",
    icon: "\u25CB", // circle
  },
  skipped: {
    label: "Skipped",
    className: "bg-zinc-500/10 text-zinc-400 border-zinc-500/20",
    icon: "\u2014", // dash
  },
};

export default function VerificationBadge({
  status,
}: {
  status: string | null | undefined;
}) {
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
