interface TrustBadgeProps {
  level: "curated" | "trusted" | "verified" | "unverified";
  size?: "sm" | "md";
}

const config: Record<
  TrustBadgeProps["level"],
  { label: string; icon: string; className: string }
> = {
  curated: {
    label: "Curated",
    icon: "\u2605",
    className: "bg-indigo-500/10 text-indigo-400 border-indigo-500/20",
  },
  trusted: {
    label: "Trusted",
    icon: "\u2605",
    className: "bg-green-500/10 text-green-400 border-green-500/20",
  },
  verified: {
    label: "Verified",
    icon: "\u2713",
    className: "bg-yellow-500/10 text-yellow-400 border-yellow-500/20",
  },
  unverified: {
    label: "Unverified",
    icon: "\u25CB",
    className: "bg-zinc-500/10 text-zinc-400 border-zinc-500/20",
  },
};

export default function TrustBadge({ level, size = "sm" }: TrustBadgeProps) {
  const c = config[level];
  const sizeClasses = size === "sm" ? "px-2 py-0.5 text-xs" : "px-3 py-1 text-sm";

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border font-medium ${c.className} ${sizeClasses}`}
    >
      <span>{c.icon}</span>
      {c.label}
    </span>
  );
}
