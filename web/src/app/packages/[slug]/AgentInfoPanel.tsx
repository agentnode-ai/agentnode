/* eslint-disable @typescript-eslint/no-explicit-any */

interface AgentConfig {
  goal?: string;
  entrypoint?: string;
  allowed_packages?: string[];
  max_iterations?: number;
  max_tool_calls?: number;
  max_runtime_seconds?: number;
  isolation?: string;
}

export default function AgentInfoPanel({ agentConfig }: { agentConfig: AgentConfig }) {
  const limits = [
    { label: "Max Iterations", value: agentConfig.max_iterations },
    { label: "Max Tool Calls", value: agentConfig.max_tool_calls },
    { label: "Max Runtime", value: agentConfig.max_runtime_seconds ? `${agentConfig.max_runtime_seconds}s` : undefined },
  ].filter((l) => l.value != null);

  const allowedPkgs = agentConfig.allowed_packages;
  const fullRegistry = !allowedPkgs || allowedPkgs.length === 0;

  return (
    <section className="rounded-xl border border-blue-500/20 bg-blue-500/5 p-4 sm:p-6">
      <h2 className="mb-4 text-lg font-semibold text-foreground flex items-center gap-2">
        <span className="inline-flex h-6 w-6 items-center justify-center rounded-md bg-blue-500/10 text-blue-400 text-xs">
          A
        </span>
        Agent Configuration
      </h2>

      {/* Goal */}
      {agentConfig.goal && (
        <div className="mb-4">
          <p className="text-xs font-medium uppercase tracking-wider text-muted mb-1.5">Goal</p>
          <p className="text-sm text-foreground leading-relaxed">{agentConfig.goal}</p>
        </div>
      )}

      {/* Tool Access */}
      <div className="mb-4">
        <p className="text-xs font-medium uppercase tracking-wider text-muted mb-1.5">Tool Access</p>
        {fullRegistry ? (
          <span className="inline-flex items-center gap-1.5 rounded-full bg-green-500/10 border border-green-500/20 px-3 py-1 text-xs font-medium text-green-400">
            Full Registry
          </span>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {allowedPkgs!.map((pkg) => (
              <span
                key={pkg}
                className="rounded-md bg-card px-2 py-0.5 text-xs text-muted border border-border font-mono"
              >
                {pkg}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Limits */}
      {limits.length > 0 && (
        <div className="mb-4">
          <p className="text-xs font-medium uppercase tracking-wider text-muted mb-1.5">Limits</p>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
            {limits.map((l) => (
              <div
                key={l.label}
                className="rounded-lg border border-border bg-background px-3 py-2 text-center"
              >
                <p className="text-xs text-muted">{l.label}</p>
                <p className="text-sm font-mono font-medium text-foreground">{l.value}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Isolation */}
      {agentConfig.isolation && (
        <div>
          <p className="text-xs font-medium uppercase tracking-wider text-muted mb-1.5">Isolation</p>
          <span className="rounded-md bg-card px-2.5 py-0.5 text-xs font-mono text-muted border border-border">
            {agentConfig.isolation}
          </span>
        </div>
      )}
    </section>
  );
}
