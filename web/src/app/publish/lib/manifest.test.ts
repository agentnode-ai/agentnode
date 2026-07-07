import { describe, it, expect } from "vitest";
import { buildManifestFromGuided, parseManifestToGuided } from "./manifest";
import { DEFAULT_GUIDED } from "./constants";
import type { GuidedState } from "./types";

function skillGuided(overrides: Partial<GuidedState> = {}): GuidedState {
  return {
    ...DEFAULT_GUIDED,
    package_type: "skill",
    name: "My Skill",
    package_id: "my-skill",
    version: "1.0.0",
    summary: "A prompt-only skill for testing purposes.",
    skill_content: "# My Skill\nDo the thing.",
    ...overrides,
  };
}

describe("buildManifestFromGuided — skill", () => {
  it("emits a prompt-only skill manifest", () => {
    const m = buildManifestFromGuided(skillGuided(), "pub");
    expect(m.package_type).toBe("skill");
    expect(m.runtime).toBe("none");
    expect(m.install_mode).toBe("prompt_only");
    expect(m.entrypoint).toBeUndefined();

    const caps = m.capabilities as Record<string, unknown>;
    expect(caps.tools).toEqual([]);
    const prompts = caps.prompts as Array<Record<string, unknown>>;
    expect(prompts).toHaveLength(1);
    expect(prompts[0].template).toBe("SKILL.md");
    expect(prompts[0].capability_id).toBeTruthy();

    const perms = m.permissions as Record<string, { level?: string; required?: string }>;
    expect(perms.network.level).toBe("none");
    expect(perms.filesystem.level).toBe("none");
    expect(perms.code_execution.level).toBe("none");
  });

  it("never carries a package-level entrypoint or tools, even if fields are set", () => {
    const m = buildManifestFromGuided(skillGuided({ entrypoint: "mod:fn" }), "pub");
    expect(m.entrypoint).toBeUndefined();
    expect((m.capabilities as { tools: unknown[] }).tools).toEqual([]);
  });
});

describe("buildManifestFromGuided — toolpack unchanged", () => {
  it("still emits python runtime + package install for a toolpack", () => {
    const g: GuidedState = {
      ...DEFAULT_GUIDED,
      package_type: "toolpack",
      name: "T",
      package_id: "t-pack",
      summary: "A toolpack summary long enough.",
    };
    const m = buildManifestFromGuided(g, "pub");
    expect(m.package_type).toBe("toolpack");
    expect(m.runtime).toBe("python");
    expect(m.install_mode).toBe("package");
  });
});

describe("parseManifestToGuided — skill", () => {
  it("recognizes a pasted skill manifest instead of coercing to toolpack", () => {
    const g = parseManifestToGuided({
      package_type: "skill",
      package_id: "my-skill",
      name: "My Skill",
    });
    expect(g.package_type).toBe("skill");
  });
});
