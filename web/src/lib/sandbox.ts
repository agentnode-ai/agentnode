/* ------------------------------------------------------------------ */
/*  Per-package sandbox display — derived from server facts only.       */
/*                                                                      */
/*  Mirrors the SDK's trust-tier policy (sdk/agentnode_sdk/sandbox/     */
/*  policy.py): curated + trusted may run on the host by default;       */
/*  everything else that executes code is isolated-or-refused. Skills   */
/*  are prompt-only, so no code ever runs. This is the DEFAULT posture  */
/*  for the tier — a consumer can tighten it with                       */
/*  sandbox.host_trust_policy — not a hard guarantee.                   */
/* ------------------------------------------------------------------ */

export type SandboxStatus = "required" | "optional" | "none";

export interface SandboxInfo {
  status: SandboxStatus;
  label: string;
  reason: string;
}

// Tiers allowed to run directly on the host under the default host-trust
// policy (host_allowed_tiers("default") in policy.py).
const HOST_TRUSTED = new Set(["curated", "trusted"]);

export function deriveSandbox(opts: {
  package_type?: string | null;
  trust_level?: string | null;
  runtime?: string | null;
}): SandboxInfo {
  const type = (opts.package_type || "").toLowerCase();
  const trust = (opts.trust_level || "").toLowerCase();
  const runtime = (opts.runtime || "").toLowerCase();

  // Skills are prompt-only: they ship instructions, not code — nothing runs.
  if (type === "skill") {
    return {
      status: "none",
      label: "No sandbox needed",
      reason:
        "Prompt-only skill — it ships instructions, not code, so nothing is executed.",
    };
  }

  const runsWhat =
    runtime === "mcp"
      ? "a third-party MCP server process on your machine"
      : "third-party code";

  // curated = AgentNode-maintained; trusted = a trusted publisher. Host-allowed
  // by default, but the user can still require isolation via host_trust_policy.
  if (HOST_TRUSTED.has(trust)) {
    const who =
      trust === "curated" ? "Maintained by AgentNode" : "From a trusted publisher";
    return {
      status: "optional",
      label: "Sandbox optional",
      reason: `${who} — runs on the host by default. You can require isolation with sandbox.host_trust_policy.`,
    };
  }

  // verified / community / unknown: not host-trusted → isolated, or refused
  // when no container runtime is available (fail-closed).
  return {
    status: "required",
    label: "Sandbox required",
    reason: `Runs ${runsWhat} that AgentNode hasn't vouched for at host level — it runs isolated in a container, or not at all.`,
  };
}
