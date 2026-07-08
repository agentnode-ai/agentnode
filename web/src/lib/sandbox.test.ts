import { describe, it, expect } from "vitest";
import { deriveSandbox } from "./sandbox";

describe("deriveSandbox", () => {
  it("skills never need a sandbox (no code runs)", () => {
    const r = deriveSandbox({ package_type: "skill", trust_level: "unverified" });
    expect(r.status).toBe("none");
    expect(r.label).toBe("No sandbox needed");
    // even an untrusted skill is safe — trust is irrelevant here
    expect(deriveSandbox({ package_type: "skill", trust_level: "curated" }).status).toBe("none");
  });

  it("curated + trusted execution packages are sandbox-optional", () => {
    for (const trust of ["curated", "trusted"]) {
      const r = deriveSandbox({ package_type: "toolpack", trust_level: trust });
      expect(r.status).toBe("optional");
      expect(r.label).toBe("Sandbox optional");
    }
  });

  it("curated reason names AgentNode, trusted names the publisher", () => {
    expect(deriveSandbox({ package_type: "toolpack", trust_level: "curated" }).reason)
      .toMatch(/AgentNode/);
    expect(deriveSandbox({ package_type: "toolpack", trust_level: "trusted" }).reason)
      .toMatch(/trusted publisher/);
  });

  it("verified + community + unknown execution packages require a sandbox", () => {
    for (const trust of ["verified", "unverified", "community", "", undefined]) {
      const r = deriveSandbox({ package_type: "toolpack", trust_level: trust });
      expect(r.status).toBe("required");
      expect(r.label).toBe("Sandbox required");
    }
  });

  it("MCP reason mentions a third-party server process", () => {
    const r = deriveSandbox({ package_type: "toolpack", runtime: "mcp", trust_level: "unverified" });
    expect(r.status).toBe("required");
    expect(r.reason).toMatch(/MCP server process/);
  });

  it("non-MCP execution reason mentions third-party code", () => {
    const r = deriveSandbox({ package_type: "agent", runtime: "python", trust_level: "verified" });
    expect(r.reason).toMatch(/third-party code/);
  });

  it("is case-insensitive on inputs", () => {
    expect(deriveSandbox({ package_type: "SKILL", trust_level: "CURATED" }).status).toBe("none");
    expect(deriveSandbox({ package_type: "ToolPack", trust_level: "Curated" }).status).toBe("optional");
  });
});
