import type { GuidedState } from "./types";
import { DEFAULT_GUIDED } from "./constants";

export function slugify(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, "")
    .replace(/\s+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 60);
}

export function isValidSemver(v: string): boolean {
  return /^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?(\+[a-zA-Z0-9.]+)?$/.test(v);
}

export function buildManifestFromGuided(g: GuidedState, publisherSlug: string): Record<string, unknown> {
  const tools = g.tools.map((t) => {
    const tool: Record<string, unknown> = {
      name: t.name,
      description: t.description,
      capability_id: t.capability_id,
    };
    if (t.entrypoint) tool.entrypoint = t.entrypoint;
    if (t.input_schema.trim()) {
      try { tool.input_schema = JSON.parse(t.input_schema); } catch { /* skip */ }
    }
    if (t.output_schema.trim()) {
      try { tool.output_schema = JSON.parse(t.output_schema); } catch { /* skip */ }
    }
    return tool;
  });

  const manifest: Record<string, unknown> = {
    manifest_version: "0.2",
    package_id: g.package_id,
    package_type: g.package_type,
    name: g.name,
    publisher: publisherSlug,
    version: g.version,
    summary: g.summary,
    runtime: "python",
    install_mode: "package",
    hosting_type: "agentnode_hosted",
    capabilities: { tools },
    compatibility: { frameworks: g.frameworks },
    permissions: {
      network: { level: g.network },
      filesystem: { level: g.filesystem },
      code_execution: { level: g.code_execution },
      data_access: { level: g.data_access },
      user_approval: { required: g.user_approval },
    },
  };

  // Package-level entrypoint (explicit or auto-derived from first tool / agent entrypoint)
  if (g.entrypoint) {
    manifest.entrypoint = g.entrypoint;
  } else if (g.package_type === "agent" && g.agent_entrypoint) {
    // Derive module-level entrypoint from agent entrypoint (strip :function)
    const parts = g.agent_entrypoint.split(":");
    manifest.entrypoint = parts[0];
  } else if (g.tools.length > 0 && g.tools[0].entrypoint) {
    const parts = g.tools[0].entrypoint.split(":");
    if (parts.length === 2) manifest.entrypoint = parts[0];
  }

  if (g.description) manifest.description = g.description;
  if (g.tags.trim()) {
    manifest.tags = g.tags.split(",").map((t) => t.trim()).filter(Boolean);
  }

  // AI Builder enrichment fields
  if (g.use_cases?.length) manifest.use_cases = g.use_cases;
  if (g.examples?.length) manifest.examples = g.examples;
  if (g.env_requirements?.length) manifest.env_requirements = g.env_requirements;
  if (g.readme_md?.trim()) manifest.readme_md = g.readme_md;

  // Links & license
  if (g.license) manifest.license = g.license;
  if (g.homepage_url?.trim()) manifest.homepage_url = g.homepage_url;
  if (g.docs_url?.trim()) manifest.docs_url = g.docs_url;
  if (g.source_url?.trim()) manifest.source_url = g.source_url;

  // Agent metadata
  if (g.package_type === "agent") {
    const agentBlock: Record<string, unknown> = {
      entrypoint: g.agent_entrypoint,
      goal: g.agent_goal,
      llm: { required: g.agent_llm_required },
      tool_access: {
        allowed_packages: g.agent_allowed_packages
          .split(",").map((s) => s.trim()).filter(Boolean),
      },
      limits: {
        max_iterations: g.agent_max_iterations,
        max_tool_calls: g.agent_max_tool_calls,
        max_runtime_seconds: g.agent_max_runtime_seconds,
      },
      termination: {
        stop_on_final_answer: g.agent_stop_on_final_answer,
        stop_on_consecutive_errors: g.agent_stop_on_consecutive_errors,
      },
      isolation: g.agent_isolation,
      state: { persistence: g.agent_persistence },
    };
    if (g.agent_tier) agentBlock.tier = g.agent_tier;
    if (g.agent_system_prompt.trim()) agentBlock.system_prompt = g.agent_system_prompt;
    manifest.agent = agentBlock;
  }

  // Upgrade metadata
  if (g.package_type === "upgrade") {
    const upgrade: Record<string, unknown> = {};
    if (g.upgrade_recommended_for.trim()) {
      upgrade.recommended_for = g.upgrade_recommended_for.split(",").map((s) => s.trim()).filter(Boolean);
    }
    if (g.upgrade_replaces.trim()) {
      upgrade.replaces = g.upgrade_replaces.split(",").map((s) => s.trim()).filter(Boolean);
    }
    if (g.upgrade_roles.trim()) {
      upgrade.roles = g.upgrade_roles.split(",").map((s) => s.trim()).filter(Boolean);
    }
    if (g.upgrade_install_strategy) {
      upgrade.install_strategy = g.upgrade_install_strategy;
    }
    manifest.upgrade_metadata = upgrade;
  }

  return manifest;
}

export function parseManifestToGuided(json: Record<string, unknown>): GuidedState {
  const g = { ...DEFAULT_GUIDED };

  if (typeof json.name === "string" && json.name) g.name = json.name;
  else if (typeof json.display_name === "string" && json.display_name) g.name = json.display_name;

  if (typeof json.package_id === "string") g.package_id = json.package_id;
  if (json.package_type === "toolpack" || json.package_type === "agent") {
    g.package_type = json.package_type;
  } else if (json.package_type === "upgrade") {
    g.package_type = "upgrade";
  }
  if (typeof json.version === "string") g.version = json.version;

  if (typeof json.summary === "string" && json.summary) g.summary = json.summary;
  else if (typeof json.description === "string" && json.description) g.summary = json.description;

  if (typeof json.description === "string") g.description = json.description;

  const caps = json.capabilities as Record<string, unknown> | undefined;
  const capTools = caps && Array.isArray(caps.tools) ? caps.tools : null;
  const topTools = Array.isArray(json.tools) ? json.tools : null;
  const rawTools = (capTools && capTools.length > 0) ? capTools : topTools;

  if (rawTools && rawTools.length > 0) {
    g.tools = rawTools.map((t: Record<string, unknown>) => {
      let capId = (t.capability_id as string) || "";
      if (!capId && Array.isArray(t.capability_ids) && t.capability_ids.length > 0) {
        capId = t.capability_ids[0] as string;
      }
      const inputSchema = t.input_schema || t.parameters;
      const outputSchema = t.output_schema || t.returns;
      return {
        name: (t.name as string) || (t.id as string) || (t.display_name as string) || "",
        description: (t.description as string) || "",
        capability_id: capId,
        entrypoint: (t.entrypoint as string) || "",
        input_schema: inputSchema ? JSON.stringify(inputSchema, null, 2) : "",
        output_schema: outputSchema ? JSON.stringify(outputSchema, null, 2) : "",
      };
    });
  }

  if (typeof json.entrypoint === "string" && json.entrypoint) {
    g.entrypoint = json.entrypoint;
    // Also push down to single tool if it has no entrypoint
    if (g.tools.length === 1 && !g.tools[0].entrypoint) {
      g.tools[0].entrypoint = json.entrypoint;
    }
  }

  const compat = json.compatibility as Record<string, unknown> | undefined;
  if (compat && Array.isArray(compat.frameworks) && compat.frameworks.length > 0) {
    g.frameworks = compat.frameworks as string[];
  }

  const perms = json.permissions as Record<string, unknown> | undefined;
  if (perms) {
    const net = perms.network as Record<string, string> | undefined;
    if (net?.level) g.network = net.level;
    const fs = perms.filesystem as Record<string, string> | undefined;
    if (fs?.level) g.filesystem = fs.level;
    const exec = perms.code_execution as Record<string, string> | undefined;
    if (exec?.level) g.code_execution = exec.level;
    const data = perms.data_access as Record<string, string> | undefined;
    if (data?.level) g.data_access = data.level;
    const approval = perms.user_approval as Record<string, string> | undefined;
    if (approval?.required) g.user_approval = approval.required;
  }

  if (Array.isArray(json.tags)) g.tags = json.tags.join(", ");
  if (!g.tags && Array.isArray(json.dependencies)) {
    g.tags = json.dependencies.join(", ");
  }

  // AI Builder enrichment fields
  if (Array.isArray(json.use_cases)) g.use_cases = json.use_cases as string[];
  if (Array.isArray(json.examples)) g.examples = json.examples as { title: string; language: string; code: string }[];
  if (Array.isArray(json.env_requirements)) g.env_requirements = json.env_requirements as { name: string; required: boolean; description: string }[];
  if (typeof json.readme_md === "string") g.readme_md = json.readme_md;

  // Links & license
  if (typeof json.license === "string") g.license = json.license;
  if (typeof json.homepage_url === "string") g.homepage_url = json.homepage_url;
  if (typeof json.docs_url === "string") g.docs_url = json.docs_url;
  if (typeof json.source_url === "string") g.source_url = json.source_url;

  // Agent metadata
  const agentSection = json.agent as Record<string, unknown> | undefined;
  if (agentSection) {
    if (typeof agentSection.entrypoint === "string") g.agent_entrypoint = agentSection.entrypoint;
    if (typeof agentSection.goal === "string") g.agent_goal = agentSection.goal;
    if (typeof agentSection.system_prompt === "string") g.agent_system_prompt = agentSection.system_prompt;
    if (typeof agentSection.tier === "string") g.agent_tier = agentSection.tier;
    const llmBlock = agentSection.llm as Record<string, unknown> | undefined;
    if (llmBlock && typeof llmBlock.required === "boolean") g.agent_llm_required = llmBlock.required;
    const toolAccess = agentSection.tool_access as Record<string, unknown> | undefined;
    if (toolAccess && Array.isArray(toolAccess.allowed_packages)) {
      g.agent_allowed_packages = toolAccess.allowed_packages.join(", ");
    }
    const limits = agentSection.limits as Record<string, unknown> | undefined;
    if (limits) {
      if (typeof limits.max_iterations === "number") g.agent_max_iterations = limits.max_iterations;
      if (typeof limits.max_tool_calls === "number") g.agent_max_tool_calls = limits.max_tool_calls;
      if (typeof limits.max_runtime_seconds === "number") g.agent_max_runtime_seconds = limits.max_runtime_seconds;
    }
    const termination = agentSection.termination as Record<string, unknown> | undefined;
    if (termination) {
      if (typeof termination.stop_on_final_answer === "boolean") g.agent_stop_on_final_answer = termination.stop_on_final_answer;
      if (typeof termination.stop_on_consecutive_errors === "number") g.agent_stop_on_consecutive_errors = termination.stop_on_consecutive_errors;
    }
    if (typeof agentSection.isolation === "string") g.agent_isolation = agentSection.isolation;
    const state = agentSection.state as Record<string, unknown> | undefined;
    if (state && typeof state.persistence === "string") g.agent_persistence = state.persistence;
  }

  // Upgrade metadata
  const upgrade = json.upgrade_metadata as Record<string, unknown> | undefined;
  if (upgrade) {
    if (Array.isArray(upgrade.recommended_for)) g.upgrade_recommended_for = upgrade.recommended_for.join(", ");
    if (Array.isArray(upgrade.replaces)) g.upgrade_replaces = upgrade.replaces.join(", ");
    if (Array.isArray(upgrade.roles)) g.upgrade_roles = upgrade.roles.join(", ");
    if (typeof upgrade.install_strategy === "string") g.upgrade_install_strategy = upgrade.install_strategy;
  }

  return g;
}
