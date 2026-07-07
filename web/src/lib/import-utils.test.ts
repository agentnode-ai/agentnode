import { describe, it, expect } from "vitest";
import { parseManifestInput } from "./import-utils";

describe("parseManifestInput", () => {
  it("accepts a JSON manifest", () => {
    const res = parseManifestInput(
      JSON.stringify({ package_id: "my-pack", name: "My Pack" })
    );
    expect(res.ok).toBe(true);
    if (res.ok) expect(res.manifest.package_id).toBe("my-pack");
  });

  it("accepts a YAML manifest", () => {
    const res = parseManifestInput(
      'package_id: my-skill\npackage_type: skill\nname: "My Skill"\n'
    );
    expect(res.ok).toBe(true);
    if (res.ok) expect(res.manifest.package_type).toBe("skill");
  });

  it("rejects empty input", () => {
    const res = parseManifestInput("   ");
    expect(res.ok).toBe(false);
  });

  it("rejects unparseable input", () => {
    const res = parseManifestInput("{not: valid: json: or: yaml::}");
    expect(res.ok).toBe(false);
    if (!res.ok) expect(res.error).toMatch(/Could not parse/);
  });

  it("rejects parseable input that is not a manifest", () => {
    const res = parseManifestInput('{"foo": 1}');
    expect(res.ok).toBe(false);
    if (!res.ok) expect(res.error).toMatch(/package_id/);
  });

  it("rejects arrays and scalars", () => {
    expect(parseManifestInput("[1, 2]").ok).toBe(false);
    expect(parseManifestInput('"just a string"').ok).toBe(false);
  });
});
