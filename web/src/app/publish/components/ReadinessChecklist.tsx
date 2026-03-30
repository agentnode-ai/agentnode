"use client";

export interface ReadinessItem {
  label: string;
  ok: boolean;
  required: boolean;
  target?: "name" | "artifact" | "tools";
}

export function ReadinessChecklist({
  items,
  onNavigate,
}: {
  items: ReadinessItem[];
  onNavigate?: (target: "name" | "artifact" | "tools") => void;
}) {
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <h3 className="mb-3 text-sm font-medium text-foreground">Readiness checklist</h3>
      <ul className="space-y-2">
        {items.map((item) => (
          <li key={item.label} className="flex items-center gap-2">
            <span
              className={`inline-block h-4 w-4 rounded-full text-center text-[10px] leading-4 font-bold ${
                item.ok
                  ? "bg-success/20 text-success"
                  : item.required
                  ? "bg-danger/20 text-danger"
                  : "bg-muted/20 text-muted"
              }`}
            >
              {item.ok ? "\u2713" : item.required ? "!" : "\u2013"}
            </span>
            {!item.ok && item.target && onNavigate ? (
              <button
                type="button"
                onClick={() => onNavigate(item.target!)}
                className="text-sm text-primary hover:underline transition-colors"
              >
                {item.label}
                {item.required && <span className="text-danger ml-1">*</span>}
              </button>
            ) : (
              <span
                className={`text-sm ${
                  item.ok ? "text-muted" : item.required ? "text-foreground" : "text-muted"
                }`}
              >
                {item.label}
                {!item.ok && item.required && <span className="text-danger ml-1">*</span>}
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
