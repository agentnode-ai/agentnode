import { describe, it, expect } from "vitest";
import { parseSkillMd } from "./skill-import";

const CLAUDE_SKILL = `---
name: Release Notes
description: Turns commit history into concise release notes.
license: MIT
allowed-tools: [bash]
---

# Release Notes

You are an expert at writing release notes.

## Instructions

1. Read {{input}}.
`;

describe("parseSkillMd", () => {
  it("maps Claude frontmatter to ANP metadata and strips it from the content", () => {
    const res = parseSkillMd(CLAUDE_SKILL);
    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.skill.name).toBe("Release Notes");
    expect(res.skill.summary).toBe("Turns commit history into concise release notes.");
    expect(res.skill.packageId).toBe("release-notes");
    expect(res.skill.hadFrontmatter).toBe(true);
    expect(res.skill.skillContent.startsWith("# Release Notes")).toBe(true);
    expect(res.skill.skillContent).not.toContain("allowed-tools");
    expect(res.skill.ignoredFrontmatterKeys.sort()).toEqual(["allowed-tools", "license"]);
  });

  it("handles a plain SKILL.md without frontmatter", () => {
    const res = parseSkillMd("# Email Tone\n\nRewrites emails to a target tone.\n");
    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.skill.name).toBe("Email Tone");
    expect(res.skill.summary).toBe("Rewrites emails to a target tone.");
    expect(res.skill.packageId).toBe("email-tone");
    expect(res.skill.hadFrontmatter).toBe(false);
    expect(res.skill.ignoredFrontmatterKeys).toEqual([]);
  });

  it("falls back to a default name when nothing is derivable", () => {
    const res = parseSkillMd("just some instructions without a heading");
    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.skill.name).toBe("Imported Skill");
    expect(res.skill.packageId).toBe("imported-skill");
  });

  it("supports CRLF frontmatter fences", () => {
    const res = parseSkillMd("---\r\nname: CRLF Skill\r\n---\r\n\r\n# Body\r\nText.\r\n");
    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.skill.name).toBe("CRLF Skill");
    expect(res.skill.hadFrontmatter).toBe(true);
  });

  it("rejects empty input and frontmatter-only files", () => {
    expect(parseSkillMd("   ").ok).toBe(false);
    expect(parseSkillMd("---\nname: X\n---\n").ok).toBe(false);
  });

  it("rejects invalid frontmatter YAML", () => {
    const res = parseSkillMd("---\nname: [unclosed\n---\n\n# Body\n");
    expect(res.ok).toBe(false);
    if (res.ok) return;
    expect(res.error).toMatch(/frontmatter/);
  });

  it("caps the package id at 40 characters on a word boundary", () => {
    const res = parseSkillMd(
      "# A Very Long Skill Name That Goes On And On Beyond Forty Characters\n\nBody.\n"
    );
    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.skill.packageId.length).toBeLessThanOrEqual(40);
    expect(res.skill.packageId.endsWith("-")).toBe(false);
  });
});
