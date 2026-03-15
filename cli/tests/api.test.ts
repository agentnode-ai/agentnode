import { describe, it, expect, vi, beforeEach } from "vitest";

// Mock fetch globally
const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

// Import after mocking
const api = await import("../src/api.js");

beforeEach(() => {
  mockFetch.mockReset();
});

describe("search", () => {
  it("should call search endpoint with query", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ query: "pdf", hits: [], total: 0, limit: 20, offset: 0 }),
    });

    const result = await api.search("pdf");
    expect(result.query).toBe("pdf");
    expect(mockFetch).toHaveBeenCalledOnce();

    const url = mockFetch.mock.calls[0][0] as string;
    expect(url).toContain("/v1/search");
    expect(url).toContain("q=pdf");
  });
});

describe("resolve", () => {
  it("should call resolve endpoint with capabilities", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ results: [], total: 0 }),
    });

    const result = await api.resolve(["pdf_extraction"]);
    expect(result.total).toBe(0);

    const call = mockFetch.mock.calls[0];
    const body = JSON.parse(call[1].body);
    expect(body.capabilities).toEqual(["pdf_extraction"]);
  });
});

describe("getPackage", () => {
  it("should fetch package details", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        slug: "test-pkg",
        name: "Test Package",
        package_type: "toolpack",
        summary: "A test",
      }),
    });

    const result = await api.getPackage("test-pkg");
    expect(result.slug).toBe("test-pkg");
  });

  it("should throw on 404", async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({ error: { code: "PACKAGE_NOT_FOUND", message: "Not found" } }),
    });

    await expect(api.getPackage("nonexistent")).rejects.toThrow("PACKAGE_NOT_FOUND");
  });
});

describe("getInstallMetadata", () => {
  it("should fetch install metadata with version", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        slug: "test-pkg",
        version: "1.0.0",
        runtime: "python",
      }),
    });

    const result = await api.getInstallMetadata("test-pkg", "1.0.0");
    expect(result.version).toBe("1.0.0");

    const url = mockFetch.mock.calls[0][0] as string;
    expect(url).toContain("version=1.0.0");
  });
});
