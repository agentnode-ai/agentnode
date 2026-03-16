import { describe, it, expect, vi, beforeEach } from "vitest";

// Mock fetch globally
const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

// Import after mocking
const api = await import("../src/api.js");

beforeEach(() => {
  mockFetch.mockReset();
});

// ---------------------------------------------------------------------------
// search
// ---------------------------------------------------------------------------
describe("search", () => {
  it("should send POST to /v1/search with query", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ query: "pdf", hits: [], total: 0, limit: 20, offset: 0 }),
    });

    const result = await api.search("pdf");
    expect(result.query).toBe("pdf");
    expect(result.hits).toEqual([]);
    expect(mockFetch).toHaveBeenCalledOnce();

    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toContain("/v1/search");
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body);
    expect(body.q).toBe("pdf");
  });

  it("should pass capability_id option", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ hits: [{ slug: "pdf-tool" }], total: 1 }),
    });

    await api.search("pdf", { capability_id: "pdf_extraction" });

    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.capability_id).toBe("pdf_extraction");
  });

  it("should pass framework filter", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ hits: [], total: 0 }),
    });

    await api.search("test", { framework: "langchain" });

    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.framework).toBe("langchain");
  });

  it("should pass runtime filter", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ hits: [], total: 0 }),
    });

    await api.search("test", { runtime: "python" });

    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.runtime).toBe("python");
  });

  it("should pass pagination options", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ hits: [], total: 50 }),
    });

    await api.search("test", { page: "2", per_page: "10" });

    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.page).toBe(2);
    expect(body.per_page).toBe(10);
  });

  it("should pass trust_level and sort_by options", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ hits: [], total: 0 }),
    });

    await api.search("test", { trust_level: "verified", sort_by: "downloads" });

    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.trust_level).toBe("verified");
    expect(body.sort_by).toBe("downloads");
  });

  it("should throw on server error", async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({ error: { code: "INTERNAL", message: "Server error" } }),
    });

    await expect(api.search("pdf")).rejects.toThrow("Server error");
  });

  it("should throw on network error", async () => {
    mockFetch.mockRejectedValue(new Error("ECONNREFUSED"));

    await expect(api.search("pdf")).rejects.toThrow("Network error");
  });
});

// ---------------------------------------------------------------------------
// resolve
// ---------------------------------------------------------------------------
describe("resolve", () => {
  it("should call resolve endpoint with capabilities list", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        results: [
          { slug: "pdf-tool", version: "1.0.0", capabilities: ["pdf_extraction"] },
        ],
        total: 1,
      }),
    });

    const result = await api.resolve(["pdf_extraction"]);
    expect(result.total).toBe(1);
    expect(result.results).toHaveLength(1);
    expect(result.results[0].slug).toBe("pdf-tool");

    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.capabilities).toEqual(["pdf_extraction"]);
  });

  it("should pass multiple capabilities", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ results: [], total: 0 }),
    });

    await api.resolve(["pdf_extraction", "ocr", "text_summarization"]);

    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.capabilities).toEqual(["pdf_extraction", "ocr", "text_summarization"]);
  });

  it("should pass additional options", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ results: [], total: 0 }),
    });

    await api.resolve(["pdf_extraction"], { framework: "langchain", runtime: "python" });

    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.framework).toBe("langchain");
    expect(body.runtime).toBe("python");
  });

  it("should handle empty capabilities list", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ results: [], total: 0 }),
    });

    const result = await api.resolve([]);
    expect(result.total).toBe(0);

    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.capabilities).toEqual([]);
  });

  it("should throw on API error response", async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ error: { code: "INVALID_REQUEST", message: "Bad request" } }),
    });

    await expect(api.resolve(["invalid"])).rejects.toThrow("INVALID_REQUEST");
  });
});

// ---------------------------------------------------------------------------
// getPackage
// ---------------------------------------------------------------------------
describe("getPackage", () => {
  it("should fetch package details by slug", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        slug: "pdf-parser",
        name: "PDF Parser",
        package_type: "toolpack",
        summary: "Parse PDF documents",
        version: "2.1.0",
      }),
    });

    const result = await api.getPackage("pdf-parser");
    expect(result.slug).toBe("pdf-parser");
    expect(result.name).toBe("PDF Parser");
    expect(result.version).toBe("2.1.0");

    const url = mockFetch.mock.calls[0][0] as string;
    expect(url).toContain("/v1/packages/pdf-parser");
  });

  it("should throw on 404 for missing package", async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({ error: { code: "PACKAGE_NOT_FOUND", message: "Not found" } }),
    });

    await expect(api.getPackage("nonexistent-pkg")).rejects.toThrow("PACKAGE_NOT_FOUND");
  });

  it("should throw on server error", async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 503,
      json: async () => ({ error: {} }),
    });

    await expect(api.getPackage("some-pkg")).rejects.toThrow("Server error");
  });
});

// ---------------------------------------------------------------------------
// checkUpdates
// ---------------------------------------------------------------------------
describe("checkUpdates", () => {
  it("should post package list and return update info", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        updates: [
          { slug: "pdf-tool", current: "1.0.0", latest: "1.2.0", has_update: true },
          { slug: "ocr-tool", current: "2.0.0", latest: "2.0.0", has_update: false },
        ],
      }),
    });

    const packages = [
      { slug: "pdf-tool", version: "1.0.0" },
      { slug: "ocr-tool", version: "2.0.0" },
    ];

    const result = await api.checkUpdates(packages);
    expect(result.updates).toHaveLength(2);
    expect(result.updates[0].has_update).toBe(true);
    expect(result.updates[1].has_update).toBe(false);

    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toContain("/v1/packages/check-updates");
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body);
    expect(body.packages).toEqual(packages);
  });

  it("should handle empty package list", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ updates: [] }),
    });

    const result = await api.checkUpdates([]);
    expect(result.updates).toEqual([]);
  });

  it("should throw on error response", async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ error: { code: "INVALID_REQUEST", message: "packages is required" } }),
    });

    await expect(api.checkUpdates([])).rejects.toThrow("INVALID_REQUEST");
  });
});

// ---------------------------------------------------------------------------
// checkPolicy
// ---------------------------------------------------------------------------
describe("checkPolicy", () => {
  it("should check policy with all constraints", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        compliant: true,
        violations: [],
        package_slug: "pdf-tool",
      }),
    });

    const policy = { min_trust: "verified", allow_shell: false, allow_network: true };
    const result = await api.checkPolicy("pdf-tool", policy);
    expect(result.compliant).toBe(true);
    expect(result.violations).toEqual([]);

    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toContain("/v1/check-policy");
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body);
    expect(body.package_slug).toBe("pdf-tool");
    expect(body.policy).toEqual(policy);
  });

  it("should return violations for non-compliant package", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        compliant: false,
        violations: [
          { rule: "min_trust", message: "Package trust level is below minimum" },
          { rule: "allow_shell", message: "Package uses shell commands" },
        ],
      }),
    });

    const result = await api.checkPolicy("untrusted-tool", { min_trust: "verified", allow_shell: false });
    expect(result.compliant).toBe(false);
    expect(result.violations).toHaveLength(2);
  });

  it("should work with empty policy (defaults)", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ compliant: true, violations: [] }),
    });

    const result = await api.checkPolicy("safe-tool");
    expect(result.compliant).toBe(true);

    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.policy).toEqual({});
  });

  it("should throw on server error", async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({ error: {} }),
    });

    await expect(api.checkPolicy("pkg")).rejects.toThrow("Server error");
  });
});

// ---------------------------------------------------------------------------
// reportPackage
// ---------------------------------------------------------------------------
describe("reportPackage", () => {
  it("should submit a report with reason and description", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        report_id: "rpt_123abc",
        status: "pending",
      }),
    });

    const result = await api.reportPackage("bad-pkg", "malware", "Contains crypto miner");
    expect(result.report_id).toBe("rpt_123abc");
    expect(result.status).toBe("pending");

    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toContain("/v1/packages/bad-pkg/report");
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body);
    expect(body.reason).toBe("malware");
    expect(body.description).toBe("Contains crypto miner");
  });

  it("should handle typosquatting report", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ report_id: "rpt_456", status: "pending" }),
    });

    const result = await api.reportPackage("nmp-tool", "typosquatting", "Mimics npm-tool");
    expect(result.report_id).toBe("rpt_456");
  });

  it("should throw on unauthorized report", async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 401,
      json: async () => ({ error: { code: "UNAUTHORIZED", message: "API key required" } }),
    });

    await expect(api.reportPackage("pkg", "spam", "test")).rejects.toThrow("UNAUTHORIZED");
  });
});

// ---------------------------------------------------------------------------
// recommend
// ---------------------------------------------------------------------------
describe("recommend", () => {
  it("should recommend packages for missing capabilities", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        recommendations: [
          { slug: "pdf-extractor", capabilities: ["pdf_extraction"], score: 0.95 },
          { slug: "pdf-reader", capabilities: ["pdf_extraction"], score: 0.82 },
        ],
      }),
    });

    const result = await api.recommend(["pdf_extraction"]);
    expect(result.recommendations).toHaveLength(2);
    expect(result.recommendations[0].slug).toBe("pdf-extractor");
    expect(result.recommendations[0].score).toBe(0.95);

    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.missing_capabilities).toEqual(["pdf_extraction"]);
  });

  it("should pass framework and runtime options", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ recommendations: [] }),
    });

    await api.recommend(["ocr"], { framework: "langchain", runtime: "python" });

    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.missing_capabilities).toEqual(["ocr"]);
    expect(body.framework).toBe("langchain");
    expect(body.runtime).toBe("python");
  });

  it("should handle multiple missing capabilities", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        recommendations: [
          { slug: "multi-tool", capabilities: ["pdf_extraction", "ocr", "translation"], score: 0.9 },
        ],
      }),
    });

    const result = await api.recommend(["pdf_extraction", "ocr", "translation"]);
    expect(result.recommendations[0].capabilities).toHaveLength(3);
  });

  it("should throw on error", async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ error: { code: "INVALID_REQUEST", message: "capabilities required" } }),
    });

    await expect(api.recommend([])).rejects.toThrow("INVALID_REQUEST");
  });
});

// ---------------------------------------------------------------------------
// resolveUpgrade
// ---------------------------------------------------------------------------
describe("resolveUpgrade", () => {
  it("should resolve upgrades for current capabilities", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        upgrades: [
          { slug: "pdf-tool", from: "1.0.0", to: "2.0.0", breaking: false },
        ],
        new_packages: [],
      }),
    });

    const result = await api.resolveUpgrade(["pdf_extraction"]);
    expect(result.upgrades).toHaveLength(1);
    expect(result.upgrades[0].from).toBe("1.0.0");
    expect(result.upgrades[0].to).toBe("2.0.0");

    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toContain("/v1/resolve-upgrade");
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body);
    expect(body.current_capabilities).toEqual(["pdf_extraction"]);
  });

  it("should pass framework and runtime options", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ upgrades: [], new_packages: [] }),
    });

    await api.resolveUpgrade(["ocr"], { framework: "autogen", runtime: "node" });

    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.current_capabilities).toEqual(["ocr"]);
    expect(body.framework).toBe("autogen");
    expect(body.runtime).toBe("node");
  });

  it("should pass policy constraints", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ upgrades: [], new_packages: [] }),
    });

    const policy = { min_trust: "verified", allow_shell: false, allow_network: true };
    await api.resolveUpgrade(["web_scraping"], { policy });

    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.policy).toEqual(policy);
  });

  it("should handle multiple current capabilities", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        upgrades: [
          { slug: "pdf-tool", from: "1.0.0", to: "1.1.0", breaking: false },
          { slug: "ocr-tool", from: "2.0.0", to: "3.0.0", breaking: true },
        ],
        new_packages: [{ slug: "nlp-helper", version: "1.0.0" }],
      }),
    });

    const result = await api.resolveUpgrade(["pdf_extraction", "ocr", "nlp"]);
    expect(result.upgrades).toHaveLength(2);
    expect(result.upgrades[1].breaking).toBe(true);
    expect(result.new_packages).toHaveLength(1);
  });

  it("should throw on error", async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({ error: {} }),
    });

    await expect(api.resolveUpgrade(["x"])).rejects.toThrow("Server error");
  });
});

// ---------------------------------------------------------------------------
// publishPackage
// ---------------------------------------------------------------------------
describe("publishPackage", () => {
  it("should publish with manifest and token", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        slug: "my-tool",
        version: "1.0.0",
        package_type: "toolpack",
        message: "Published successfully",
      }),
    });

    const manifest = JSON.stringify({ name: "my-tool", version: "1.0.0" });
    const result = await api.publishPackage(manifest, "tok_abc123");

    expect(result.slug).toBe("my-tool");
    expect(result.version).toBe("1.0.0");
    expect(result.message).toBe("Published successfully");

    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toContain("/v1/packages/publish");
    expect(init.method).toBe("POST");
    expect(init.headers.Authorization).toBe("Bearer tok_abc123");
  });

  it("should throw on publish failure", async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ error: { code: "INVALID_MANIFEST", message: "Missing required field" } }),
    });

    const manifest = JSON.stringify({ name: "bad" });
    await expect(api.publishPackage(manifest, "tok_abc")).rejects.toThrow("INVALID_MANIFEST");
  });

  it("should throw on unauthorized publish", async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 401,
      json: async () => ({ error: { code: "UNAUTHORIZED", message: "Invalid token" } }),
    });

    await expect(api.publishPackage("{}", "bad_token")).rejects.toThrow("UNAUTHORIZED");
  });
});

// ---------------------------------------------------------------------------
// createApiKey
// ---------------------------------------------------------------------------
describe("createApiKey", () => {
  it("should create an API key with label and token", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        label: "my-key",
        api_key: "ak_test_123456789",
        created_at: "2026-03-16T00:00:00Z",
      }),
    });

    const result = await api.createApiKey("my-key", "bearer_token_abc");
    expect(result.label).toBe("my-key");
    expect(result.api_key).toBe("ak_test_123456789");

    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toContain("/v1/auth/api-keys");
    expect(init.method).toBe("POST");
    expect(init.headers.Authorization).toBe("Bearer bearer_token_abc");
    expect(init.headers["Content-Type"]).toBe("application/json");
    const body = JSON.parse(init.body);
    expect(body.label).toBe("my-key");
  });

  it("should throw on unauthorized create", async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 401,
      json: async () => ({ error: { code: "UNAUTHORIZED", message: "Invalid token" } }),
    });

    await expect(api.createApiKey("test", "bad_token")).rejects.toThrow("UNAUTHORIZED");
  });

  it("should throw on duplicate label", async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({ error: { code: "DUPLICATE_LABEL", message: "Label already exists" } }),
    });

    await expect(api.createApiKey("existing-key", "tok")).rejects.toThrow("DUPLICATE_LABEL");
  });

  it("should throw on server error", async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({ error: {} }),
    });

    await expect(api.createApiKey("key", "tok")).rejects.toThrow("Failed to create API key");
  });
});
