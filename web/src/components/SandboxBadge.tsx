import { deriveSandbox, type SandboxStatus } from "@/lib/sandbox";

interface SandboxBadgeProps {
  package_type?: string | null;
  trust_level?: string | null;
  runtime?: string | null;
  size?: "sm" | "md";
  /** Show the reason inline (detail pages) instead of only as a tooltip. */
  showReason?: boolean;
}

const STYLES: Record<SandboxStatus, { icon: string; className: string }> = {
  required: {
    icon: "◆", // ◆
    className: "bg-amber-500/10 text-amber-400 border-amber-500/20",
  },
  optional: {
    icon: "◇", // ◇
    className: "bg-sky-500/10 text-sky-400 border-sky-500/20",
  },
  none: {
    icon: "✓", // ✓
    className: "bg-green-500/10 text-green-400 border-green-500/20",
  },
};

export default function SandboxBadge({
  package_type,
  trust_level,
  runtime,
  size = "sm",
  showReason = false,
}: SandboxBadgeProps) {
  const info = deriveSandbox({ package_type, trust_level, runtime });
  const s = STYLES[info.status];
  const sizeClasses = size === "sm" ? "px-2 py-0.5 text-xs" : "px-3 py-1 text-sm";

  if (showReason) {
    return (
      <span className="inline-flex flex-col gap-1">
        <span
          className={`inline-flex w-fit items-center gap-1 rounded-full border font-medium ${s.className} ${sizeClasses}`}
        >
          <span>{s.icon}</span>
          {info.label}
        </span>
        <span className="text-xs text-muted">{info.reason}</span>
      </span>
    );
  }

  return (
    <span
      title={info.reason}
      className={`inline-flex items-center gap-1 rounded-full border font-medium ${s.className} ${sizeClasses}`}
    >
      <span>{s.icon}</span>
      {info.label}
    </span>
  );
}
