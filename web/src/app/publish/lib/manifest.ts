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

  if (g.description) manifest.description = g.description;
  if (g.tags.trim()) {
    manifest.tags = g.tags.split(",").map((t) => t.trim()).filter(Boolean);
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
  if (json.package_type === "toolpack" || json.package_type === "upgrade") {
    g.package_type = json.package_type;
  } else if (json.package_type === "agent") {
    g.package_type = "toolpack"; // Legacy: "agent" maps to "toolpack"
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

  if (typeof json.entrypoint === "string" && json.entrypoint && g.tools.length === 1 && !g.tools[0].entrypoint) {
    g.tools[0].entrypoint = json.entrypoint;
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
