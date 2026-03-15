"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

interface ApiKeyInfo {
  id: string;
  key_prefix: string;
  label: string | null;
  created_at: string;
  last_used_at: string | null;
}

interface UserInfo {
  id: string;
  email: string;
  username: string;
  two_factor_enabled: boolean;
  is_admin?: boolean;
  publisher?: {
    slug: string;
    display_name: string;
    trust_level: string;
    packages_published_count: number;
  };
}

export default function DashboardPage() {
  const router = useRouter();
  const [user, setUser] = useState<UserInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // 2FA setup
  const [showSetup2FA, setShowSetup2FA] = useState(false);
  const [qrUri, setQrUri] = useState("");
  const [secret, setSecret] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [verifying2FA, setVerifying2FA] = useState(false);

  // Publisher creation
  const [showCreatePublisher, setShowCreatePublisher] = useState(false);
  const [pubSlug, setPubSlug] = useState("");
  const [pubDisplayName, setPubDisplayName] = useState("");
  const [creatingPublisher, setCreatingPublisher] = useState(false);

  // API Keys
  const [apiKeys, setApiKeys] = useState<ApiKeyInfo[]>([]);
  const [newKeyLabel, setNewKeyLabel] = useState("");
  const [createdKey, setCreatedKey] = useState("");
  const [creatingKey, setCreatingKey] = useState(false);

  useEffect(() => {
    loadUser();
  }, []);

  function getAuthHeaders(): Record<string, string> {
    const token = localStorage.getItem("access_token");
    if (!token) return {};
    return { Authorization: `Bearer ${token}` };
  }

  async function loadUser() {
    try {
      const res = await fetch("/api/v1/auth/me", {
        headers: getAuthHeaders(),
      });
      if (!res.ok) {
        router.push("/auth/login");
        return;
      }
      const data = await res.json();
      setUser(data);
    } catch {
      router.push("/auth/login");
    } finally {
      setLoading(false);
    }
  }

  async function setup2FA() {
    setShowSetup2FA(true);
    const res = await fetch("/api/v1/auth/2fa/setup", {
      method: "POST",
      headers: { ...getAuthHeaders(), "Content-Type": "application/json" },
    });
    if (res.ok) {
      const data = await res.json();
      setQrUri(data.provisioning_uri || "");
      setSecret(data.secret || "");
    }
  }

  async function verify2FA() {
    setVerifying2FA(true);
    try {
      const res = await fetch("/api/v1/auth/2fa/verify", {
        method: "POST",
        headers: { ...getAuthHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({ code: totpCode }),
      });
      if (res.ok) {
        setShowSetup2FA(false);
        await loadUser();
      } else {
        const data = await res.json();
        setError(data.error?.message || "Invalid code");
      }
    } finally {
      setVerifying2FA(false);
    }
  }

  async function createPublisher() {
    setCreatingPublisher(true);
    setError("");
    try {
      const res = await fetch("/api/v1/publishers", {
        method: "POST",
        headers: { ...getAuthHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({
          display_name: pubDisplayName,
          slug: pubSlug,
        }),
      });
      if (res.ok) {
        setShowCreatePublisher(false);
        await loadUser();
      } else {
        const data = await res.json();
        setError(data.error?.message || "Failed to create publisher");
      }
    } finally {
      setCreatingPublisher(false);
    }
  }

  async function loadApiKeys() {
    try {
      const res = await fetch("/api/v1/auth/api-keys", {
        headers: getAuthHeaders(),
      });
      if (res.ok) {
        const data = await res.json();
        setApiKeys(data.keys || []);
      }
    } catch {
      // non-critical
    }
  }

  async function createApiKey() {
    setCreatingKey(true);
    setCreatedKey("");
    try {
      const res = await fetch("/api/v1/auth/api-keys", {
        method: "POST",
        headers: { ...getAuthHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({ label: newKeyLabel || undefined }),
      });
      if (res.ok) {
        const data = await res.json();
        setCreatedKey(data.key);
        setNewKeyLabel("");
        await loadApiKeys();
      } else {
        const data = await res.json();
        setError(data.error?.message || "Failed to create API key");
      }
    } finally {
      setCreatingKey(false);
    }
  }

  async function revokeApiKey(keyId: string) {
    try {
      await fetch(`/api/v1/auth/api-keys/${keyId}`, {
        method: "DELETE",
        headers: getAuthHeaders(),
      });
      await loadApiKeys();
    } catch {
      setError("Failed to revoke API key");
    }
  }

  if (loading) {
    return (
      <div className="mx-auto max-w-4xl px-6 py-24 text-center text-muted">
        Loading...
      </div>
    );
  }

  if (!user) return null;

  return (
    <div className="mx-auto max-w-4xl px-6 py-12">
      <h1 className="mb-8 text-2xl font-bold text-foreground">Dashboard</h1>

      {error && (
        <div className="mb-4 rounded-md border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">
          {error}
        </div>
      )}

      {/* Account Info */}
      <section className="mb-8 rounded-lg border border-border bg-card p-6">
        <h2 className="mb-4 text-lg font-semibold text-foreground">Account</h2>
        <div className="space-y-2 text-sm">
          <div className="flex justify-between">
            <span className="text-muted">Username</span>
            <span className="text-foreground">{user.username}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted">Email</span>
            <span className="text-foreground">{user.email}</span>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-muted">2FA</span>
            {user.two_factor_enabled ? (
              <span className="text-success text-xs font-medium">Enabled</span>
            ) : (
              <button
                onClick={setup2FA}
                className="rounded bg-primary px-3 py-1 text-xs text-white hover:bg-primary/90"
              >
                Enable 2FA
              </button>
            )}
          </div>
        </div>
      </section>

      {/* 2FA Setup Modal */}
      {showSetup2FA && (
        <section className="mb-8 rounded-lg border border-primary/30 bg-card p-6">
          <h2 className="mb-4 text-lg font-semibold text-foreground">Set up 2FA</h2>
          <p className="mb-4 text-sm text-muted">
            Scan the QR code with your authenticator app, or enter the secret manually.
          </p>
          {secret && (
            <div className="mb-4 rounded bg-background p-3 font-mono text-xs text-foreground break-all">
              {secret}
            </div>
          )}
          {qrUri && (
            <div className="mb-4 text-xs text-muted break-all">
              URI: {qrUri}
            </div>
          )}
          <div className="flex gap-2">
            <input
              type="text"
              inputMode="numeric"
              maxLength={6}
              value={totpCode}
              onChange={(e) => setTotpCode(e.target.value)}
              className="w-32 rounded-md border border-border bg-background px-3 py-2 text-foreground focus:border-primary focus:outline-none"
              placeholder="123456"
            />
            <button
              onClick={verify2FA}
              disabled={verifying2FA || totpCode.length < 6}
              className="rounded bg-primary px-4 py-2 text-sm text-white hover:bg-primary/90 disabled:opacity-50"
            >
              {verifying2FA ? "Verifying..." : "Verify"}
            </button>
          </div>
        </section>
      )}

      {/* Publisher Section */}
      <section className="mb-8 rounded-lg border border-border bg-card p-6">
        <h2 className="mb-4 text-lg font-semibold text-foreground">Publisher</h2>
        {user.publisher ? (
          <div className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-muted">Name</span>
              <Link
                href={`/publishers/${user.publisher.slug}`}
                className="text-primary hover:underline"
              >
                {user.publisher.display_name}
              </Link>
            </div>
            <div className="flex justify-between">
              <span className="text-muted">Trust level</span>
              <span className="text-foreground">{user.publisher.trust_level}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted">Packages</span>
              <span className="text-foreground">{user.publisher.packages_published_count}</span>
            </div>
            <div className="mt-4">
              <Link
                href="/publish"
                className="inline-block rounded bg-primary px-4 py-2 text-sm text-white hover:bg-primary/90"
              >
                Publish a package
              </Link>
            </div>
          </div>
        ) : (
          <div>
            {!showCreatePublisher ? (
              <div>
                <p className="mb-3 text-sm text-muted">
                  Create a publisher profile to start publishing packages.
                </p>
                <button
                  onClick={() => setShowCreatePublisher(true)}
                  className="rounded bg-primary px-4 py-2 text-sm text-white hover:bg-primary/90"
                >
                  Create publisher profile
                </button>
              </div>
            ) : (
              <div className="space-y-3">
                <div>
                  <label className="mb-1 block text-sm text-muted">Display name</label>
                  <input
                    type="text"
                    value={pubDisplayName}
                    onChange={(e) => setPubDisplayName(e.target.value)}
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-foreground focus:border-primary focus:outline-none"
                    placeholder="My Publisher"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-sm text-muted">
                    Slug <span className="text-muted/50">(a-z, 0-9, hyphens)</span>
                  </label>
                  <input
                    type="text"
                    value={pubSlug}
                    onChange={(e) => setPubSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ""))}
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-foreground focus:border-primary focus:outline-none"
                    placeholder="my-publisher"
                    pattern="[a-z0-9-]+"
                  />
                </div>
                <button
                  onClick={createPublisher}
                  disabled={creatingPublisher || !pubSlug || !pubDisplayName}
                  className="rounded bg-primary px-4 py-2 text-sm text-white hover:bg-primary/90 disabled:opacity-50"
                >
                  {creatingPublisher ? "Creating..." : "Create"}
                </button>
              </div>
            )}
          </div>
        )}
      </section>

      {/* API Keys Section */}
      <section className="mb-8 rounded-lg border border-border bg-card p-6">
        <h2 className="mb-4 text-lg font-semibold text-foreground">API Keys</h2>
        <p className="mb-4 text-sm text-muted">
          API keys allow programmatic access via the SDK or API. Keys are shown only once at creation.
        </p>

        {createdKey && (
          <div className="mb-4 rounded-md border border-success/30 bg-success/10 px-4 py-3 text-sm">
            <p className="mb-1 font-medium text-success">Key created! Copy it now — it won&apos;t be shown again.</p>
            <code className="block break-all rounded bg-background px-2 py-1 font-mono text-xs text-foreground">
              {createdKey}
            </code>
          </div>
        )}

        {apiKeys.length > 0 && (
          <div className="mb-4 space-y-2">
            {apiKeys.map((k) => (
              <div key={k.id} className="flex items-center justify-between rounded bg-background px-3 py-2 text-sm">
                <div>
                  <code className="font-mono text-xs text-foreground">{k.key_prefix}...</code>
                  {k.label && <span className="ml-2 text-muted">({k.label})</span>}
                </div>
                <button
                  onClick={() => revokeApiKey(k.id)}
                  className="text-xs text-danger hover:underline"
                >
                  Revoke
                </button>
              </div>
            ))}
          </div>
        )}

        <div className="flex gap-2">
          <input
            type="text"
            value={newKeyLabel}
            onChange={(e) => setNewKeyLabel(e.target.value)}
            className="w-48 rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
            placeholder="Label (optional)"
          />
          <button
            onClick={createApiKey}
            disabled={creatingKey}
            className="rounded bg-primary px-4 py-2 text-sm text-white hover:bg-primary/90 disabled:opacity-50"
          >
            {creatingKey ? "Creating..." : "Create API key"}
          </button>
        </div>
        {apiKeys.length === 0 && (
          <button
            onClick={loadApiKeys}
            className="mt-2 text-xs text-primary hover:underline"
          >
            Load existing keys
          </button>
        )}
      </section>
    </div>
  );
}
