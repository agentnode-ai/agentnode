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

  const data = await resp.json();

  if (!resp.ok) {
    if (resp.status >= 500) {
      throw new Error(`Server error (${resp.status}). Please try again later.`);
    }
    const err = data.error || {};
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

export async function publishPackage(manifest: string, token: string): Promise<any> {
  const url = `${getBaseUrl()}/v1/packages/publish`;
  const formData = new FormData();
  formData.append("manifest", manifest);

  const resp = await fetch(url, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: formData,
  });
  const data = await resp.json();
  if (!resp.ok) {
    const err = data.error || {};
    throw new Error(`[${err.code || resp.status}] ${err.message || "Publish failed"}`);
  }
  return data;
}
