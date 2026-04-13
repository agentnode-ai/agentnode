/**
 * AgentNode API client for the CLI.
 */

import { getApiUrl, getApiKey } from "./config.js";

function getBaseUrl(): string {
  return getApiUrl();
}

function getHeaders(): Record<string, string> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const apiKey = getApiKey();
  if (apiKey) {
    headers["X-API-Key"] = apiKey;
  }
  return headers;
}

async function request(method: string, path: string, body?: unknown): Promise<any> {
  const url = `${getBaseUrl()}${path}`;
  const options: RequestInit = {
    method,
    headers: getHeaders(),
    signal: AbortSignal.timeout(30_000),
  };
  if (body) {
    options.body = JSON.stringify(body);
  }

  let resp: Response;
  try {
    resp = await fetch(url, options);
  } catch (err: any) {
    if (err.name === "TimeoutError") {
      throw new Error("Request timed out. Please try again.");
    }
    throw new Error(`Network error: ${err.message}. Check your connection.`);
  }

  // P1-C1: Parse the response body defensively. Previously we called
  // `resp.json()` unconditionally, so any HTML/plain-text error page
  // (reverse-proxy 502, maintenance page, Cloudflare challenge) crashed
  // the CLI with a cryptic `SyntaxError: Unexpected token '<'`.
  const ctype = (resp.headers?.get?.("content-type") || "").toLowerCase();
  const looksJson = ctype.includes("json") || ctype === "";
  let data: any = {};
  if (looksJson) {
    try {
      data = await resp.json();
    } catch {
      // Body was advertised as JSON (or no content-type at all) but
      // failed to parse. Treat as empty and fall through to status-based
      // error handling below.
      data = {};
      if (resp.ok) {
        throw new Error(
          `Invalid JSON response from server (HTTP ${resp.status})`,
        );
      }
    }
  } else if (!resp.ok) {
    // Non-JSON error body: keep the first 200 chars of text for the
    // operator to diagnose without drowning the terminal.
    const text = (await resp.text().catch(() => "")).trim().slice(0, 200);
    if (resp.status >= 500) {
      throw new Error(
        `Server error (${resp.status})${text ? `: ${text}` : ""}. Please try again later.`,
      );
    }
    throw new Error(
      `HTTP ${resp.status}${text ? `: ${text}` : ""}`,
    );
  }

  if (!resp.ok) {
    if (resp.status >= 500) {
      const msg = data?.error?.message || "";
      throw new Error(
        `Server error (${resp.status})${msg ? `: ${msg}` : ""}. Please try again later.`,
      );
    }
    const err = (data && typeof data === "object" && data.error) || {};
    throw new Error(`[${err.code || resp.status}] ${err.message || "Request failed"}`);
  }
  return data;
}

export async function search(query: string, options: Record<string, any> = {}): Promise<any> {
  return request("POST", "/v1/search", {
    q: query,
    capability_id: options.capability_id,
    framework: options.framework,
    runtime: options.runtime,
    trust_level: options.trust_level,
    sort_by: options.sort_by,
    page: options.page ? Number(options.page) : undefined,
    per_page: options.per_page ? Number(options.per_page) : undefined,
    ...Object.fromEntries(
      Object.entries(options).filter(([_, v]) => v !== undefined)
    ),
  });
}

export async function resolve(capabilities: string[], options: Record<string, any> = {}): Promise<any> {
  return request("POST", "/v1/resolve", { capabilities, ...options });
}

export async function getPackage(slug: string): Promise<any> {
  return request("GET", `/v1/packages/${slug}`);
}

export async function getInstallMetadata(slug: string, version?: string): Promise<any> {
  const params = version ? `?version=${version}` : "";
  return request("GET", `/v1/packages/${slug}/install-info${params}`);
}

export async function trackInstall(
  slug: string,
  version?: string,
  eventType: "install" | "update" | "rollback" = "install",
): Promise<any> {
  return request("POST", `/v1/packages/${slug}/download`, {
    version,
    event_type: eventType,
    source: "cli",
  });
}

export async function checkUpdates(packages: { slug: string; version: string }[]): Promise<any> {
  return request("POST", "/v1/packages/check-updates", { packages });
}

export async function checkPolicy(
  packageSlug: string,
  policy: { min_trust?: string; allow_shell?: boolean; allow_network?: boolean } = {},
): Promise<any> {
  return request("POST", "/v1/check-policy", { package_slug: packageSlug, policy });
}

export async function reportPackage(
  slug: string,
  reason: string,
  description: string,
): Promise<any> {
  return request("POST", `/v1/packages/${slug}/report`, { reason, description });
}

export async function recommend(
  missingCapabilities: string[],
  options: { framework?: string; runtime?: string } = {},
): Promise<any> {
  return request("POST", "/v1/recommend", {
    missing_capabilities: missingCapabilities,
    ...options,
  });
}

export async function resolveUpgrade(
  currentCapabilities: string[],
  options: {
    framework?: string;
    runtime?: string;
    policy?: { min_trust?: string; allow_shell?: boolean; allow_network?: boolean };
  } = {},
): Promise<any> {
  return request("POST", "/v1/resolve-upgrade", {
    current_capabilities: currentCapabilities,
    ...options,
  });
}

export async function createApiKey(label: string, token: string): Promise<any> {
  const url = `${getBaseUrl()}/v1/auth/api-keys`;
  const resp = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ label }),
    signal: AbortSignal.timeout(30_000),
  });
  const data = await resp.json();
  if (!resp.ok) {
    const err = data.error || {};
    throw new Error(`[${err.code || resp.status}] ${err.message || "Failed to create API key"}`);
  }
  return data;
}

export async function publishPackage(manifest: string, token: string, artifactBytes?: Uint8Array): Promise<any> {
  const url = `${getBaseUrl()}/v1/packages/publish`;
  const formData = new FormData();
  formData.append("manifest", manifest);

  if (artifactBytes) {
    // Copy into a fresh ArrayBuffer so the Blob constructor accepts it
    // under strict `lib.dom.d.ts` typings (Uint8Array backed by
    // SharedArrayBuffer is not a valid BlobPart on newer TS).
    const ab = new ArrayBuffer(artifactBytes.byteLength);
    new Uint8Array(ab).set(artifactBytes);
    const blob = new Blob([ab], { type: "application/gzip" });
    formData.append("artifact", blob, "package.tar.gz");
  }

  const resp = await fetch(url, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: formData,
    signal: AbortSignal.timeout(120_000),
  });
  const data = await resp.json();
  if (!resp.ok) {
    const err = data.error || {};
    throw new Error(`[${err.code || resp.status}] ${err.message || "Publish failed"}`);
  }
  return data;
}
