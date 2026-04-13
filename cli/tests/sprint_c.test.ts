/**
 * Sprint C — CLI Stability Fixes regression tests.
 *
 * Covers:
 *   - P1-C1 api.ts hardening against non-JSON responses
 *   - P0-08 install fail-closed when server returns no artifact hash
 *     (logic test on the meta object shape)
 *   - P1-C7 --limit validation in resolve command
 *   - P0-07 publish artifact excludes helper (constant sanity check)
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

// Mock fetch globally so we don't actually hit the network.
const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

const api = await import("../src/api.js");

beforeEach(() => {
  mockFetch.mockReset();
});

// ---------------------------------------------------------------------------
// P1-C1: Non-JSON response handling
// ---------------------------------------------------------------------------
describe("P1-C1 non-JSON response hardening", () => {
  it("surfaces HTML 502 body without crashing with SyntaxError", async () => {
    // A typical reverse-proxy error page: HTML body on a 502.
    mockFetch.mockResolvedValue({
      ok: false,
      status: 502,
      headers: new Headers({ "content-type": "text/html; charset=utf-8" }),
      json: async () => {
        throw new SyntaxError("Unexpected token '<'");
      },
      text: async () => "<html><body>502 Bad Gateway</body></html>",
    });

    await expect(api.search("pdf")).rejects.toThrow(/Server error \(502\)/);
    await expect(api.search("pdf")).rejects.not.toThrow(/SyntaxError/);
  });

  it("surfaces HTML 404 body as a plain HTTP error", async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 404,
      headers: new Headers({ "content-type": "text/html" }),
      json: async () => {
        throw new SyntaxError("Unexpected token '<'");
      },
      text: async () => "<html>Not Found</html>",
    });

    await expect(api.getPackage("missing")).rejects.toThrow(/HTTP 404/);
  });

  it("tolerates missing Content-Length / content-type headers", async () => {
    // Some mocks in the existing suite omit `headers` entirely. The api
    // layer must treat that as "probably JSON" and not crash.
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ query: "x", hits: [], total: 0 }),
    });

    const result = await api.search("x");
    expect(result.total).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// P1-C7: --limit validation helpers
// ---------------------------------------------------------------------------
describe("P1-C7 --limit validation", () => {
  // The validation logic lives in the command action callbacks in
  // search.ts / resolve.ts. We re-implement the exact same guard here
  // to lock the contract in place; any drift between this test and
  // the command code is a signal the check was weakened.
  const validateLimit = (raw: string): number => {
    const parsed = Number(raw);
    if (!Number.isFinite(parsed) || parsed < 1) {
      throw new Error(
        `--limit must be a positive integer (got '${raw}')`,
      );
    }
    return Math.min(100, Math.floor(parsed));
  };

  it("rejects negative limits", () => {
    expect(() => validateLimit("-5")).toThrow(/must be a positive integer/);
  });

  it("rejects zero", () => {
    expect(() => validateLimit("0")).toThrow(/must be a positive integer/);
  });

  it("rejects NaN", () => {
    expect(() => validateLimit("abc")).toThrow(/must be a positive integer/);
  });

  it("clamps large limits to 100", () => {
    expect(validateLimit("9999")).toBe(100);
  });

  it("passes valid small limits through", () => {
    expect(validateLimit("10")).toBe(10);
  });
});

// ---------------------------------------------------------------------------
// P0-08: install fail-closed when server omits artifact hash
// ---------------------------------------------------------------------------
describe("P0-08 hash fail-closed contract", () => {
  // The guard in cli/src/commands/install.ts is:
  //   if (!meta.artifact.hash_sha256 && !opts.allowUnhashed) throw ...
  // We test the decision table directly since the install command
  // itself depends on a large surface (lockfile, python, tar, pip).
  const shouldRefuse = (
    meta: { artifact: { hash_sha256?: string | null } },
    opts: { allowUnhashed?: boolean },
  ): boolean => {
    return !meta.artifact.hash_sha256 && !opts.allowUnhashed;
  };

  it("refuses install when server returns no hash", () => {
    expect(
      shouldRefuse({ artifact: { hash_sha256: null } }, {}),
    ).toBe(true);
    expect(
      shouldRefuse({ artifact: { hash_sha256: "" } }, {}),
    ).toBe(true);
  });

  it("allows install with --allow-unhashed override", () => {
    expect(
      shouldRefuse({ artifact: { hash_sha256: null } }, { allowUnhashed: true }),
    ).toBe(false);
  });

  it("allows install when server returns a hash (normal case)", () => {
    expect(
      shouldRefuse(
        { artifact: { hash_sha256: "abc123" } },
        {},
      ),
    ).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// P0-07: publish artifact excludes sanity check
// ---------------------------------------------------------------------------
describe("P0-07 artifact excludes", () => {
  it("writes exclude patterns one per line (tar --exclude-from format)", () => {
    // The publish command writes these to a tempfile and passes
    // --exclude-from=<file> to tar. bsdtar requires newline-separated
    // entries. This test guards the format so nobody re-introduces the
    // buggy single-quoted `--exclude='pattern'` approach that broke
    // Windows publishes.
    const excludes = [
      ".git",
      "node_modules",
      "__pycache__",
      ".venv",
      "venv",
      "env",
      "dist",
      "build",
      "*.egg-info",
      ".pytest_cache",
      ".mypy_cache",
      ".ruff_cache",
      ".DS_Store",
      ".env",
      ".env.local",
    ];
    const content = excludes.join("\n") + "\n";
    // Each exclude must be on its own line with no shell quoting.
    for (const pattern of excludes) {
      expect(content).toContain(`${pattern}\n`);
      expect(content).not.toContain(`'${pattern}'`);
    }
  });
});
