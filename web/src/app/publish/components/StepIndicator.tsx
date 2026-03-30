"use client";

export function StepIndicator({ current }: { current: 1 | 2 | 3 }) {
  const steps = [
    { num: 1, label: "Start" },
    { num: 2, label: "Review" },
    { num: 3, label: "Finalize" },
  ] as const;

  return (
    <div className="mb-8 flex items-center justify-center gap-2">
      {steps.map((step, idx) => {
        const isActive = step.num === current;
        const isComplete = step.num < current;
        return (
          <div key={step.num} className="flex items-center gap-2">
            {idx > 0 && (
              <div
                className={`h-px w-8 sm:w-12 ${
                  isComplete || isActive ? "bg-primary/50" : "bg-border"
                }`}
              />
            )}
            <div className="flex items-center gap-1.5">
              <div
                className={`flex h-6 w-6 items-center justify-center rounded-full text-xs font-medium transition-colors ${
                  isActive
                    ? "bg-primary text-white"
                    : isComplete
                    ? "bg-primary/20 text-primary"
                    : "bg-card border border-border text-muted"
                }`}
              >
                {isComplete ? (
                  <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                  </svg>
                ) : (
                  step.num
                )}
              </div>
              <span
                className={`text-xs font-medium ${
                  isActive ? "text-foreground" : isComplete ? "text-primary/70" : "text-muted"
                }`}
              >
                {step.label}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
