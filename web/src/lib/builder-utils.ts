/* ------------------------------------------------------------------ */
/*  Shared builder utilities                                           */
/*  Used by /builder (SEO landing page) and /publish (unified flow)    */
/* ------------------------------------------------------------------ */

import { fetchWithAuth } from "@/lib/api";

export interface CodeFile {
  path: string;
  content: string;
}

export interface BuilderMetadata {
  package_id: string;
  package_name: string;
  tool_count: number;
  detected_capability_ids: string[];
  detected_framework: string;
  publish_ready: boolean;
  warnings: string[];
}

export interface BuilderResult {
  manifest_yaml: string;
  manifest_json: Record<string, unknown>;
  code_files: CodeFile[];
  metadata: BuilderMetadata;
}

export const BUILDER_EXAMPLES = [
  "A skill that turns commit history into concise release notes",
  "A skill that summarizes webpages into three key bullet points",
  "A skill that rewrites emails to match a requested tone",
  "A skill that turns meeting notes into action items with owners",
  "A skill that explains code changes in reviewer-friendly language",
  "A skill that drafts social media posts from a product update",
  "A skill that structures brainstorming output into a project plan",
  "A skill that reviews text for clarity and suggests improvements",
];

/* ------------------------------------------------------------------ */
/*  Generate a skill from a description via the backend API            */
/*  The builder is skills-only: prompt-only packages (SKILL.md).       */
/* ------------------------------------------------------------------ */

export async function generateSkill(
  description: string,
): Promise<{ ok: boolean; status: number; data?: BuilderResult; error?: string }> {
  try {
    const res = await fetchWithAuth("/builder/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ description: description.trim(), package_type: "skill" }),
    });

    if (res.ok) {
      const data: BuilderResult = await res.json();
      return { ok: true, status: res.status, data };
    }

    if (res.status === 401) {
      return { ok: false, status: 401, error: "Please log in to use the Skill Generator." };
    }

    if (res.status === 429) {
      return { ok: false, status: 429, error: "You're generating too quickly. Please wait a minute and try again." };
    }

    const data = await res.json().catch(() => ({}));
    return {
      ok: false,
      status: res.status,
      error: data?.error?.message || data?.detail || "Generation failed. Please try again.",
    };
  } catch {
    return { ok: false, status: 0, error: "Network error. Please check your connection." };
  }
}

/* ------------------------------------------------------------------ */
/*  Build a .tar.gz artifact from manifest + code files                */
/* ------------------------------------------------------------------ */

export async function buildArtifact(
  packageId: string,
  manifestJson: Record<string, unknown>,
  codeFiles: CodeFile[]
): Promise<Blob> {
  const res = await fetchWithAuth("/builder/artifact", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ package_id: packageId, manifest_json: manifestJson, code_files: codeFiles }),
  });
  if (!res.ok) throw new Error("Failed to build artifact");
  return res.blob();
}
