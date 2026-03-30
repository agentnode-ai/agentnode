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
  source: string | null
): { canPublish: boolean; items: ReadinessItem[] } {
  const hasContent = hasArtifact || source === "builder" || (source != null && source.startsWith("import"));

  const items: ReadinessItem[] = [
    { label: "Package name", ok: !!g.name, required: true, target: "name" },
    { label: "Package ID", ok: SLUG_PATTERN.test(g.package_id), required: true, target: "name" },
    { label: "Version", ok: isValidSemver(g.version), required: true },
    { label: "At least one tool with capability", ok: g.tools.some(t => t.name && t.capability_id), required: true, target: "tools" },
    { label: "Code or artifact", ok: hasContent, required: true, target: "artifact" },
    { label: "Summary", ok: !!g.summary, required: false, target: "name" },
    { label: "Description", ok: !!g.description, required: false },
    { label: "Tags", ok: !!g.tags.trim(), required: false },
  ];

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
  const basicsOk = !!g.name && SLUG_PATTERN.test(g.package_id) && isValidSemver(g.version) && !!g.summary;
  const toolsOk = g.tools.some((t) => t.name && t.capability_id);

  return {
    basics: basicsOk ? "complete" : "incomplete",
    artifact: hasArtifact ? "complete" : "incomplete",
    tools: toolsOk ? "complete" : "incomplete",
    permissions: permissionsTouched ? "complete" : "warning",
  };
}
