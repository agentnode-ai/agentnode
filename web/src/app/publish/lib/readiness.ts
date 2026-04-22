import type { GuidedState, CodeFile } from "./types";
import type { PanelStatus } from "../components/CollapsiblePanel";
import { SLUG_PATTERN } from "./constants";
import { isValidSemver } from "./manifest";

export interface ReadinessItem {
  label: string;
  ok: boolean;
  required: boolean;
  target?: "name" | "artifact" | "tools";
}

export function computeReadiness(
  g: GuidedState,
  hasArtifact: boolean,
  source: string | null,
  codeFiles?: CodeFile[],
): { canPublish: boolean; items: ReadinessItem[] } {
  // Check actual code file content rather than trusting source alone.
  // Import-sourced packages only count as having content if code_files
  // actually contain non-empty content, or if a real artifact was uploaded.
  const hasCodeContent = codeFiles ? codeFiles.some(f => f.content.trim()) : false;
  const hasContent = hasArtifact || hasCodeContent || source === "builder";

  const items: ReadinessItem[] = [
    { label: "Package name (min 3 characters)", ok: !!g.name && g.name.trim().length >= 3, required: true, target: "name" },
    { label: "Package ID", ok: SLUG_PATTERN.test(g.package_id), required: true, target: "name" },
    { label: "Version", ok: isValidSemver(g.version), required: true },
    { label: "At least one tool with capability", ok: g.tools.some(t => t.name && t.capability_id), required: g.package_type === "toolpack", target: "tools" },
    { label: "Code or artifact", ok: hasContent, required: g.package_type !== "upgrade", target: "artifact" },
    { label: "Summary (20-200 characters)", ok: !!g.summary && g.summary.trim().length >= 20 && g.summary.trim().length <= 200, required: true, target: "name" },
    { label: "Description (min 50 characters)", ok: !!g.description && g.description.trim().length >= 50 && g.description !== g.summary, required: false },
    { label: "Tool descriptions", ok: g.tools.every(t => !!t.description?.trim()), required: false },
    { label: "Tags", ok: !!g.tags.trim(), required: false },
  ];

  // Agent-specific required fields
  if (g.package_type === "agent") {
    items.push(
      { label: "Agent entrypoint (module:function)", ok: !!g.agent_entrypoint.trim() && g.agent_entrypoint.includes(":"), required: true },
      { label: "Agent goal", ok: !!g.agent_goal.trim(), required: true },
    );
  }

  // Upgrade-specific required fields
  if (g.package_type === "upgrade") {
    items.push({ label: "Upgrade target (recommended_for)", ok: !!g.upgrade_recommended_for.trim(), required: true });
  }

  const canPublish = items.filter(i => i.required).every(i => i.ok);
  return { canPublish, items };
}

export function computePanelStatuses(
  g: GuidedState,
  codeFiles: CodeFile[],
  artifactFile: File | null,
  builderArtifactName: string,
  tarGzFile: File | null,
  uploadedFiles: File[],
  permissionsTouched: boolean,
): Record<string, PanelStatus> {
  const hasCode = codeFiles.some((f) => f.content.trim());
  const hasArtifact = !!(builderArtifactName || artifactFile || tarGzFile || uploadedFiles.length > 0 || hasCode);
  const basicsOk = !!g.name && g.name.trim().length >= 3 && SLUG_PATTERN.test(g.package_id) && isValidSemver(g.version) && !!g.summary && g.summary.trim().length >= 20 && g.summary.trim().length <= 200;
  const toolsOk = g.tools.some((t) => t.name && t.capability_id);

  return {
    basics: basicsOk ? "complete" : "incomplete",
    artifact: hasArtifact ? "complete" : "incomplete",
    tools: toolsOk ? "complete" : "incomplete",
    permissions: permissionsTouched ? "complete" : "warning",
  };
}
