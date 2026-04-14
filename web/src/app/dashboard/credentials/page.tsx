"use client";

import { Suspense, useState, useEffect } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { fetchWithAuth } from "@/lib/api";

interface Credential {
  id: string;
  connector_provider: string;
  connector_package_slug: string;
  auth_type: string;
  scopes: string[];
  allowed_domains: string[];
  status: string;
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
}

interface TestResult {
  reachable: boolean;
  status_code?: number | null;
  latency_ms?: number | null;
  message: string;
}

export default function CredentialsPage() {
  return (
    <Suspense fallback={<div className="flex items-center justify-center py-12"><div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" /></div>}>
      <CredentialsPageInner />
    </Suspense>
  );
}

function CredentialsPageInner() {
  const searchParams = useSearchParams();
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [testingId, setTestingId] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, TestResult>>({});
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  // OAuth callback banner
  const oauthStatus = searchParams.get("oauth");
  const oauthProvider = searchParams.get("provider");
  const oauthError = searchParams.get("message");

  useEffect(() => {
    loadCredentials();
  }, []);

  async function loadCredentials() {
    setLoading(true);
    setError("");
    try {
      const res = await fetchWithAuth("/credentials/");
      if (!res.ok) {
        if (res.status === 401) {
          setError("Not authenticated. Please log in.");
          return;
        }
        throw new Error(`HTTP ${res.status}`);
      }
      const data = await res.json();
      setCredentials(data.credentials || []);
    } catch (err: any) {
      setError(err.message || "Failed to load credentials");
    } finally {
      setLoading(false);
    }
  }

  async function handleTest(id: string) {
    setTestingId(id);
    try {
      const res = await fetchWithAuth(`/credentials/${id}/test`, { method: "POST" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: TestResult = await res.json();
      setTestResults((prev) => ({ ...prev, [id]: data }));
    } catch (err: any) {
      setTestResults((prev) => ({
        ...prev,
        [id]: { reachable: false, message: err.message || "Test failed" },
      }));
    } finally {
      setTestingId(null);
    }
  }

  async function handleDelete(id: string) {
    setDeletingId(id);
    try {
      const res = await fetchWithAuth(`/credentials/${id}`, { method: "DELETE" });
      if (!res.ok && res.status !== 204) throw new Error(`HTTP ${res.status}`);
      setCredentials((prev) => prev.filter((c) => c.id !== id));
      setConfirmDeleteId(null);
    } catch (err: any) {
      setError(err.message || "Failed to delete credential");
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <div className="mx-auto max-w-4xl px-4 py-10">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Credentials</h1>
          <p className="text-sm text-muted">
            Manage stored credentials for connector packages.
          </p>
        </div>
        <Link
          href="/dashboard"
          className="rounded-lg border border-border px-3 py-1.5 text-sm font-medium text-foreground transition-colors hover:bg-background"
        >
          Back to Dashboard
        </Link>
      </div>

      {/* OAuth callback banners */}
      {oauthStatus === "success" && (
        <div className="mb-4 rounded-lg border border-green-300 bg-green-50 p-4 text-sm text-green-800 dark:border-green-800 dark:bg-green-950 dark:text-green-200">
          OAuth credential for <strong>{oauthProvider || "provider"}</strong> stored successfully.
        </div>
      )}
      {oauthStatus === "error" && (
        <div className="mb-4 rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
          OAuth flow failed{oauthError ? `: ${oauthError}` : ""}. Please try again.
        </div>
      )}

      {error && (
        <div className="mb-4 rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-12">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
        </div>
      ) : credentials.length === 0 ? (
        <div className="rounded-lg border border-border bg-card p-8 text-center">
          <p className="text-muted">No credentials stored yet.</p>
          <p className="mt-2 text-sm text-muted">
            Credentials are created when you connect a connector package via OAuth or API key.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {credentials.map((cred) => (
            <div
              key={cred.id}
              className="rounded-lg border border-border bg-card p-4"
            >
              <div className="flex items-start justify-between">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-foreground">
                      {cred.connector_provider}
                    </span>
                    <span
                      className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                        cred.status === "active"
                          ? "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200"
                          : "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200"
                      }`}
                    >
                      {cred.status}
                    </span>
                    <span className="rounded-full bg-muted/20 px-2 py-0.5 text-xs text-muted">
                      {cred.auth_type}
                    </span>
                  </div>

                  <p className="mt-1 text-xs text-muted">
                    Package: {cred.connector_package_slug}
                  </p>

                  {cred.allowed_domains.length > 0 && (
                    <p className="mt-0.5 text-xs text-muted">
                      Domains: {cred.allowed_domains.join(", ")}
                    </p>
                  )}

                  {cred.scopes.length > 0 && (
                    <p className="mt-0.5 text-xs text-muted">
                      Scopes: {cred.scopes.join(", ")}
                    </p>
                  )}

                  <p className="mt-0.5 text-xs text-muted">
                    Created: {new Date(cred.created_at).toLocaleDateString()}
                    {cred.last_used_at && (
                      <> &middot; Last used: {new Date(cred.last_used_at).toLocaleDateString()}</>
                    )}
                  </p>

                  {/* Test result */}
                  {testResults[cred.id] && (
                    <div
                      className={`mt-2 rounded border p-2 text-xs ${
                        testResults[cred.id].reachable
                          ? "border-green-300 bg-green-50 text-green-800 dark:border-green-800 dark:bg-green-950 dark:text-green-200"
                          : "border-red-300 bg-red-50 text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200"
                      }`}
                    >
                      {testResults[cred.id].reachable ? "Reachable" : "Not reachable"}
                      {testResults[cred.id].latency_ms != null && (
                        <> &middot; {testResults[cred.id].latency_ms}ms</>
                      )}
                      {testResults[cred.id].status_code != null && (
                        <> &middot; HTTP {testResults[cred.id].status_code}</>
                      )}
                      <> &middot; {testResults[cred.id].message}</>
                    </div>
                  )}
                </div>

                {/* Actions */}
                <div className="ml-4 flex gap-2">
                  <button
                    onClick={() => handleTest(cred.id)}
                    disabled={testingId === cred.id}
                    className="rounded border border-border px-3 py-1 text-xs font-medium text-foreground transition-colors hover:bg-background disabled:opacity-50"
                  >
                    {testingId === cred.id ? "Testing..." : "Test Connection"}
                  </button>

                  {confirmDeleteId === cred.id ? (
                    <div className="flex gap-1">
                      <button
                        onClick={() => handleDelete(cred.id)}
                        disabled={deletingId === cred.id}
                        className="rounded bg-red-600 px-3 py-1 text-xs font-medium text-white transition-colors hover:bg-red-700 disabled:opacity-50"
                      >
                        {deletingId === cred.id ? "..." : "Confirm"}
                      </button>
                      <button
                        onClick={() => setConfirmDeleteId(null)}
                        className="rounded border border-border px-2 py-1 text-xs text-foreground hover:bg-background"
                      >
                        Cancel
                      </button>
                    </div>
                  ) : (
                    <button
                      onClick={() => setConfirmDeleteId(cred.id)}
                      className="rounded border border-red-300 px-3 py-1 text-xs font-medium text-red-600 transition-colors hover:bg-red-50 dark:border-red-800 dark:text-red-400 dark:hover:bg-red-950"
                    >
                      Delete
                    </button>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
