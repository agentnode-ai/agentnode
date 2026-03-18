// ---- Types matching actual backend API responses ----

export interface SearchHit {
  slug: string;
  name: string;
  package_type: string;
  summary: string;
  publisher_name: string;
  publisher_slug: string;
  trust_level: "curated" | "trusted" | "verified" | "unverified";
  latest_version: string | null;
  runtime: string | null;
  capability_ids: string[];
  tags: string[];
  frameworks: string[];
  download_count: number;
  is_deprecated: boolean;
  verification_status: string | null;
}

export interface SearchResponse {
  query: string;
  hits: SearchHit[];
  total: number;
  page: number;
  per_page: number;
}

export interface SearchParams {
  q?: string;
  package_type?: string;
  framework?: string;
  runtime?: string;
  trust_level?: string;
  capability_id?: string;
  sort_by?: string;
  page?: number;
  per_page?: number;
}

// ---- API Client ----

const API_BASE = "/api/v1";

async function fetchAPI<T>(
  endpoint: string,
  options?: RequestInit
): Promise<T> {
  const url = `${API_BASE}${endpoint}`;
  const res = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });

  if (!res.ok) {
    const errorBody = await res.text().catch(() => "Unknown error");
    throw new Error(`API error ${res.status}: ${errorBody}`);
  }

  return res.json() as Promise<T>;
}

/**
 * Fetch with auto token refresh. If a 401 is returned, attempts to refresh
 * the access token via the refresh endpoint, then retries once.
 */
export async function fetchWithAuth(endpoint: string, options?: RequestInit): Promise<Response> {
  const url = `${API_BASE}${endpoint}`;
  const opts: RequestInit = { ...options, credentials: "include" };

  let res = await fetch(url, opts);

  if (res.status === 401) {
    // Try refreshing the access token
    const refreshRes = await fetch(`${API_BASE}/auth/refresh`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });

    if (refreshRes.ok) {
      // Retry original request with new cookie
      res = await fetch(url, opts);
    }
  }

  return res;
}

export async function search(params: SearchParams): Promise<SearchResponse> {
  const body: Record<string, unknown> = {};
  if (params.q) body.q = params.q;
  if (params.package_type) body.package_type = params.package_type;
  if (params.framework) body.framework = params.framework;
  if (params.runtime) body.runtime = params.runtime;
  if (params.trust_level) body.trust_level = params.trust_level;
  if (params.capability_id) body.capability_id = params.capability_id;
  if (params.sort_by) body.sort_by = params.sort_by;
  if (params.page) body.page = params.page;
  if (params.per_page) body.per_page = params.per_page;

  return fetchAPI<SearchResponse>("/search", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
