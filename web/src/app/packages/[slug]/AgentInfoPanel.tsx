/* eslint-disable @typescript-eslint/no-explicit-any */

interface AgentConfig {
  goal?: string;
  entrypoint?: string;
  allowed_packages?: string[];
  max_iterations?: number;
  max_tool_calls?: number;
  max_runtime_seconds?: number;
  isolation?: string;
  system_prompt?: string;
  tier?: string;
}

const TIER_LABELS: Record<string, { label: string; color: string }> = {
  llm_only: { label: "LLM Only", color: "text-purple-400 bg-purple-500/10 border-purple-500/20" },
  llm_plus_tools: { label: "LLM + Tools", color: "text-blue-400 bg-blue-500/10 border-blue-500/20" },
  llm_plus_credentials: { label: "LLM + Credentials", color: "text-amber-400 bg-amber-500/10 border-amber-500/20" },
};

export default function AgentInfoPanel({ agentConfig }: { agentConfig: AgentConfig }) {
  const limits = [
    { label: "Max Iterations", value: agentConfig.max_iterations },
    { label: "Max Tool Calls", value: agentConfig.max_tool_calls },
    { label: "Max Runtime", value: agentConfig.max_runtime_seconds ? `${agentConfig.max_runtime_seconds}s` : undefined },
  ].filter((l) => l.value != null);

  const allowedPkgs = agentConfig.allowed_packages;
  const fullRegistry = !allowedPkgs || allowedPkgs.length === 0;
  const tierInfo = agentConfig.tier ? TIER_LABELS[agentConfig.tier] : null;

  return (
    <section className="rounded-xl border border-blue-500/20 bg-blue-500/5 p-4 sm:p-6">
      <h2 className="mb-4 text-lg font-semibold text-foreground flex items-center gap-2">
        <span className="inline-flex h-6 w-6 items-center justify-center rounded-md bg-blue-500/10 text-blue-400 text-xs">
          A
        </span>
        Agent Configuration
      </h2>

      {/* Tier */}
      {tierInfo && (
        <div className="mb-4">
          <p className="text-xs font-medium uppercase tracking-wider text-muted mb-1.5">Tier</p>
          <span className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-medium ${tierInfo.color}`}>
            {tierInfo.label}
          </span>
        </div>
      )}

      {/* Goal */}
      {agentConfig.goal && (
        <div className="mb-4">
          <p className="text-xs font-medium uppercase tracking-wider text-muted mb-1.5">Goal</p>
          <p className="text-sm text-foreground leading-relaxed">{agentConfig.goal}</p>
        </div>
      )}

      {/* Agent Behavior */}
      {agentConfig.system_prompt && (
        <div className="mb-4">
          <div className="flex items-center gap-2 mb-1.5">
            <p className="text-xs font-medium uppercase tracking-wider text-muted">Agent Behavior</p>
            <span className="rounded bg-zinc-500/10 px-1.5 py-0.5 text-[10px] text-zinc-500 border border-zinc-500/20">
              description only
            </span>
          </div>
          <pre className="rounded-lg border border-border bg-background p-3 text-xs text-muted leading-relaxed whitespace-pre-wrap max-h-48 overflow-y-auto font-mono">
            {agentConfig.system_prompt}
          </pre>
        </div>
      )}

      {/* Tool Access */}
      <div className="mb-4">
        <p className="text-xs font-medium uppercase tracking-wider text-muted mb-1.5">Tool Access</p>
        {agentConfig.tier === "llm_only" ? (
          <span className="inline-flex items-center gap-1.5 rounded-full bg-zinc-500/10 border border-zinc-500/20 px-3 py-1 text-xs font-medium text-zinc-400">
            None (LLM reasoning only)
          </span>
        ) : fullRegistry ? (
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
