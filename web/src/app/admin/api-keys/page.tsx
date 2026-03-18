"use client";

import { useState, useEffect } from "react";
import { fetchWithAuth } from "@/lib/api";

interface ApiKeyInfo {
  masked: string;
  is_set: boolean;
}

interface ApiKeysData {
  anthropic_api_key: ApiKeyInfo;
  source: "database" | "environment";
  updated_at: string | null;
}

export default function AdminApiKeysPage() {
  const [data, setData] = useState<ApiKeysData | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  const [anthropicKey, setAnthropicKey] = useState("");

  useEffect(() => {
    loadKeys();
  }, []);

  async function loadKeys() {
    try {
      const res = await fetchWithAuth("/admin/settings/api-keys");
      if (res.ok) {
        const d: ApiKeysData = await res.json();
        setData(d);
      }
    } catch {
      setError("Failed to load API keys");
    } finally {
      setLoading(false);
    }
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError("");
    setSuccess("");

    try {
      const res = await fetchWithAuth("/admin/settings/api-keys", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          anthropic_api_key: anthropicKey,
        }),
      });

      if (res.ok) {
        setSuccess("API keys saved successfully");
        setAnthropicKey("");
        await loadKeys();
      } else {
        const d = await res.json();
        setError(d.error?.message || "Failed to save API keys");
      }
    } catch {
      setError("Network error");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <div className="py-12 text-center text-muted">Loading...</div>;
  }

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">API Keys</h1>
          <p className="mt-1 text-sm text-muted">
            Manage external API keys used by platform features
          </p>
        </div>
        {data?.source && (
          <span className="rounded-full bg-card px-3 py-1 text-xs text-muted">
            Source: {data.source}
          </span>
        )}
      </div>

      {error && (
        <div className="mb-4 rounded-md border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">
          {error}
        </div>
      )}
      {success && (
        <div className="mb-4 rounded-md border border-success/30 bg-success/10 px-4 py-3 text-sm text-success">
          {success}
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <form onSubmit={handleSave} className="rounded-lg border border-border bg-card p-6">
            <h2 className="mb-4 text-sm font-semibold uppercase tracking-wider text-muted">
              Anthropic (Claude AI)
            </h2>

            <div>
              <label className="mb-1 block text-sm text-muted">
                API Key
                {data?.anthropic_api_key.is_set && (
                  <span className="ml-2 text-xs text-success">
                    (set: {data.anthropic_api_key.masked})
                  </span>
                )}
              </label>
              <input
                type="password"
                value={anthropicKey}
                onChange={(e) => setAnthropicKey(e.target.value)}
                className="w-full rounded-md border border-border bg-background px-3 py-2.5 font-mono text-sm text-foreground focus:border-primary focus:outline-none"
                placeholder={
                  data?.anthropic_api_key.is_set
                    ? "Leave empty to keep current key"
                    : "sk-ant-..."
                }
              />
              <p className="mt-1.5 text-xs text-muted">
                Used by the Capabilities Builder for AI-powered package generation
              </p>
            </div>

            <div className="mt-6 flex items-center gap-3">
              <button
                type="submit"
                disabled={saving}
                className="rounded-md bg-primary px-5 py-2.5 text-sm font-medium text-white hover:bg-primary/90 disabled:opacity-50"
              >
                {saving ? "Saving..." : "Save API Keys"}
              </button>
            </div>
          </form>
        </div>

        {/* Sidebar */}
        <div className="space-y-4">
          <div className="rounded-lg border border-border bg-card p-5">
            <h3 className="mb-3 text-sm font-semibold text-foreground">How it works</h3>
            <div className="space-y-2 text-xs text-muted">
              <p>
                API keys stored in the database override environment variables.
              </p>
              <p>
                Changes take effect immediately without restarting the server.
              </p>
              <p>
                Keys are encrypted at rest in the database and masked in the UI.
              </p>
            </div>
          </div>

          <div className="rounded-lg border border-border bg-card p-5">
            <h3 className="mb-3 text-sm font-semibold text-foreground">Features using API keys</h3>
            <div className="space-y-3 text-xs">
              <div>
                <div className="flex items-center justify-between">
                  <span className="font-medium text-foreground">Capabilities Builder</span>
                  {data?.anthropic_api_key.is_set ? (
                    <span className="rounded-full bg-success/20 px-2 py-0.5 text-[10px] font-medium text-success">
                      Active
                    </span>
                  ) : (
                    <span className="rounded-full bg-warning/20 px-2 py-0.5 text-[10px] font-medium text-warning">
                      Fallback
                    </span>
                  )}
                </div>
                <p className="mt-1 text-muted">
                  {data?.anthropic_api_key.is_set
                    ? "AI-powered generation with Claude Sonnet"
                    : "Using heuristic scaffold generator (no AI)"}
                </p>
              </div>
            </div>
          </div>

          <div className="rounded-lg border border-border bg-card p-5">
            <h3 className="mb-3 text-sm font-semibold text-foreground">Get an API key</h3>
            <div className="space-y-2 text-xs text-muted">
              <p>
                Visit{" "}
                <a
                  href="https://console.anthropic.com/settings/keys"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-primary hover:underline"
                >
                  console.anthropic.com
                </a>{" "}
                to create an Anthropic API key.
              </p>
            </div>
          </div>

          {data?.updated_at && (
            <p className="text-xs text-muted">
              Last updated: {new Date(data.updated_at).toLocaleString()}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
