/* ------------------------------------------------------------------ */
/*  Claude SKILL.md import: frontmatter parsing + ANP metadata mapping */
/*  Used by /import to turn a pasted SKILL.md into an ANP skill.       */
/*                                                                     */
/*  Anthropic-style skills carry YAML frontmatter (name, description,  */
/*  ...) inside SKILL.md; ANP keeps metadata in the manifest instead.  */
/*  This module extracts the frontmatter into ANP fields and returns   */
/*  the remaining Markdown as the skill content.                       */
/* ------------------------------------------------------------------ */

import yaml from "js-yaml";

export interface ParsedSkillMd {
  name: string;
  summary: string;
  description: string;
  packageId: string;
  /** SKILL.md body without the frontmatter block */
  skillContent: string;
  hadFrontmatter: boolean;
  /** Frontmatter keys that have no ANP mapping (reported, not dropped silently) */
  ignoredFrontmatterKeys: string[];
}

export type SkillMdResult =
  | { ok: true; skill: ParsedSkillMd }
  | { ok: false; error: string };

const MAPPED_KEYS = new Set(["name", "description"]);

function slugifySkillName(text: string): string {
  let slug = text
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9\s-]/g, "")
    .replace(/[\s-]+/g, "-")
    .replace(/^-|-$/g, "");
  if (slug.length > 40) slug = slug.slice(0, 40).replace(/-[^-]*$/, "");
  return slug;
}

function firstHeading(body: string): string {
  const m = body.match(/^#\s+(.+)$/m);
  return m ? m[1].trim() : "";
}

function firstParagraph(body: string): string {
  for (const line of body.split("\n")) {
    const t = line.trim();
    if (t && !t.startsWith("#") && !t.startsWith("---")) return t;
  }
  return "";
}

export function parseSkillMd(text: string): SkillMdResult {
  const raw = text.replace(/^﻿/, "");
  if (!raw.trim()) {
    return { ok: false, error: "Paste your SKILL.md above." };
  }

  let frontmatter: Record<string, unknown> = {};
  let body = raw;
  let hadFrontmatter = false;

  const fmMatch = raw.match(/^---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)/);
  if (fmMatch) {
    hadFrontmatter = true;
    try {
      const parsed = yaml.load(fmMatch[1]);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        frontmatter = parsed as Record<string, unknown>;
      } else if (parsed != null) {
        return { ok: false, error: "The SKILL.md frontmatter is not a YAML mapping." };
      }
    } catch {
      return { ok: false, error: "Could not parse the SKILL.md frontmatter as YAML." };
    }
    body = raw.slice(fmMatch[0].length);
  }

  const skillContent = body.replace(/^\s*\n/, "").trimEnd() + "\n";
  if (!skillContent.trim()) {
    return {
      ok: false,
      error: "The SKILL.md has no content besides the frontmatter.",
    };
  }

  const fmName = typeof frontmatter.name === "string" ? frontmatter.name.trim() : "";
  const fmDescription =
    typeof frontmatter.description === "string" ? frontmatter.description.trim() : "";

  const name = fmName || firstHeading(skillContent) || "Imported Skill";
  const summary = (fmDescription || firstParagraph(skillContent) || name).slice(0, 200);
  const description = fmDescription || firstParagraph(skillContent) || summary;
  const packageId = slugifySkillName(name) || "imported-skill";

  const ignoredFrontmatterKeys = Object.keys(frontmatter).filter(
    (k) => !MAPPED_KEYS.has(k)
  );

  return {
    ok: true,
    skill: {
      name,
      summary,
      description,
      packageId,
      skillContent,
      hadFrontmatter,
      ignoredFrontmatterKeys,
    },
  };
}
