"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { fetchWithAuth } from "@/lib/api";

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
  const [success, setSuccess] = useState("");

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

  // Profile editing
  const [showEditProfile, setShowEditProfile] = useState(false);
  const [editUsername, setEditUsername] = useState("");
  const [editEmail, setEditEmail] = useState("");
  const [savingProfile, setSavingProfile] = useState(false);

  // Password change
  const [showChangePassword, setShowChangePassword] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [changingPassword, setChangingPassword] = useState(false);

  // Email preferences
  const [emailPrefs, setEmailPrefs] = useState<Record<string, boolean>>({});
  const [loadingPrefs, setLoadingPrefs] = useState(false);
  const [savingPrefs, setSavingPrefs] = useState(false);

  useEffect(() => {
    loadUser();
    loadApiKeys();
    loadEmailPrefs();
  }, []);

  function clearMessages() {
    setError("");
    setSuccess("");
  }

  async function loadUser() {
    try {
      const res = await fetchWithAuth("/auth/me");
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
    const res = await fetchWithAuth("/auth/2fa/setup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    if (res.ok) {
      const data = await res.json();
      setQrUri(data.provisioning_uri || data.qr_uri || "");
      setSecret(data.secret || "");
    }
  }

  async function verify2FA() {
    setVerifying2FA(true);
    try {
      const res = await fetchWithAuth("/auth/2fa/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
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
    clearMessages();
    try {
      const res = await fetchWithAuth("/publishers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
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
      const res = await fetchWithAuth("/auth/api-keys");
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
      const res = await fetchWithAuth("/auth/api-keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
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
      await fetchWithAuth(`/auth/api-keys/${keyId}`, {
        method: "DELETE",
      });
      await loadApiKeys();
    } catch {
      setError("Failed to revoke API key");
    }
  }

  async function saveProfile() {
    setSavingProfile(true);
    clearMessages();
    try {
      const body: Record<string, string> = {};
      if (editUsername && editUsername !== user?.username) body.username = editUsername;
      if (editEmail && editEmail !== user?.email) body.email = editEmail;

      if (Object.keys(body).length === 0) {
        setShowEditProfile(false);
        return;
      }

      const res = await fetchWithAuth("/auth/profile", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (res.ok) {
        setShowEditProfile(false);
        setSuccess("Profile updated.");
        await loadUser();
      } else {
        const data = await res.json();
        setError(data.error?.message || "Failed to update profile");
      }
    } finally {
      setSavingProfile(false);
    }
  }

  async function changePassword() {
    if (newPassword !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }
    setChangingPassword(true);
    clearMessages();
    try {
      const res = await fetchWithAuth("/auth/change-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      });
      if (res.ok) {
        setShowChangePassword(false);
        setCurrentPassword("");
        setNewPassword("");
        setConfirmPassword("");
        setSuccess("Password changed successfully.");
      } else {
        const data = await res.json();
        setError(data.error?.message || "Failed to change password");
      }
    } finally {
      setChangingPassword(false);
    }
  }

  async function loadEmailPrefs() {
    setLoadingPrefs(true);
    try {
      const res = await fetchWithAuth("/auth/email-preferences");
      if (res.ok) {
        const data = await res.json();
        setEmailPrefs(data.preferences || {});
      }
    } catch {
      // non-critical
    } finally {
      setLoadingPrefs(false);
    }
  }

  async function toggleEmailPref(key: string, value: boolean) {
    setSavingPrefs(true);
    try {
      const res = await fetchWithAuth("/auth/email-preferences", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [key]: value }),
      });
      if (res.ok) {
        const data = await res.json();
        setEmailPrefs(data.preferences || {});
      }
    } catch {
      setError("Failed to update email preferences");
    } finally {
      setSavingPrefs(false);
    }
  }

  async function handleLogout() {
    await fetch("/api/v1/auth/logout", { method: "POST", credentials: "include" });
    router.push("/auth/login");
  }

  if (loading) {
    return (
      <div className="mx-auto max-w-6xl px-6 py-24 text-center text-muted">
        Loading...
      </div>
    );
  }

  if (!user) return null;

  return (
    <div className="mx-auto max-w-6xl px-4 sm:px-6 py-12">
      <div className="mb-8 flex items-center justify-between">
        <h1 className="text-2xl font-bold text-foreground">Dashboard</h1>
        <button
          onClick={handleLogout}
          className="rounded border border-border px-4 py-2 text-sm text-muted transition-colors hover:bg-card hover:text-foreground"
        >
          Log out
        </button>
      </div>

      {error && (
        <div className="mb-4 rounded-md border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">
          {error}
          <button onClick={() => setError("")} className="ml-2 underline">dismiss</button>
        </div>
      )}

      {success && (
        <div className="mb-4 rounded-md border border-success/30 bg-success/10 px-4 py-3 text-sm text-success">
          {success}
          <button onClick={() => setSuccess("")} className="ml-2 underline">dismiss</button>
        </div>
      )}

      {/* Account Info */}
      <section className="mb-8 rounded-lg border border-border bg-card p-6">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-foreground">Account</h2>
          <button
            onClick={() => {
              setEditUsername(user.username);
              setEditEmail(user.email);
              setShowEditProfile(!showEditProfile);
              setShowChangePassword(false);
            }}
            className="text-xs text-primary hover:underline"
          >
            {showEditProfile ? "Cancel" : "Edit profile"}
          </button>
        </div>

        {!showEditProfile ? (
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
            <div className="flex justify-between items-center">
              <span className="text-muted">Password</span>
              <button
                onClick={() => {
                  setShowChangePassword(!showChangePassword);
                  setShowEditProfile(false);
                }}
                className="text-xs text-primary hover:underline"
              >
                Change password
              </button>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <div>
              <label className="mb-1 block text-sm text-muted">Username</label>
              <input
                type="text"
                value={editUsername}
                onChange={(e) => setEditUsername(e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, ""))}
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
              />
            </div>
            <div>
              <label className="mb-1 block text-sm text-muted">Email</label>
              <input
                type="email"
                value={editEmail}
                onChange={(e) => setEditEmail(e.target.value)}
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
              />
            </div>
            <button
              onClick={saveProfile}
              disabled={savingProfile}
              className="rounded bg-primary px-4 py-2 text-sm text-white hover:bg-primary/90 disabled:opacity-50"
            >
              {savingProfile ? "Saving..." : "Save changes"}
            </button>
          </div>
        )}
      </section>

      {/* Change Password */}
      {showChangePassword && (
        <section className="mb-8 rounded-lg border border-border bg-card p-6">
          <h2 className="mb-4 text-lg font-semibold text-foreground">Change password</h2>
          <div className="space-y-3">
            <div>
              <label className="mb-1 block text-sm text-muted">Current password</label>
              <input
                type="password"
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
              />
            </div>
            <div>
              <label className="mb-1 block text-sm text-muted">New password</label>
              <input
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
                placeholder="Min. 8 characters"
              />
            </div>
            <div>
              <label className="mb-1 block text-sm text-muted">Confirm new password</label>
              <input
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
              />
            </div>
            <div className="flex gap-2">
              <button
                onClick={changePassword}
                disabled={changingPassword || newPassword.length < 8 || !currentPassword}
                className="rounded bg-primary px-4 py-2 text-sm text-white hover:bg-primary/90 disabled:opacity-50"
              >
                {changingPassword ? "Changing..." : "Change password"}
              </button>
              <button
                onClick={() => {
                  setShowChangePassword(false);
                  setCurrentPassword("");
                  setNewPassword("");
                  setConfirmPassword("");
                }}
                className="rounded border border-border px-4 py-2 text-sm text-muted hover:bg-card"
              >
                Cancel
              </button>
            </div>
          </div>
        </section>
      )}

      {/* 2FA Setup */}
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
        {apiKeys.length === 0 && !createdKey && (
          <p className="mt-2 text-xs text-muted">No API keys yet.</p>
        )}
      </section>

      {/* Email Notification Preferences */}
      <section className="mb-8 rounded-lg border border-border bg-card p-6">
        <h2 className="mb-4 text-lg font-semibold text-foreground">Email Notifications</h2>
        <p className="mb-4 text-sm text-muted">
          Choose which email notifications you want to receive.
        </p>

        {loadingPrefs ? (
          <p className="text-sm text-muted">Loading preferences...</p>
        ) : (
          <div className="space-y-6">
            {/* Recurring / potentially noisy notifications */}
            <div>
              <h3 className="mb-2 text-sm font-medium text-foreground">Recurring</h3>
              <div className="space-y-2">
                <PrefToggle k="security_login" label="Login alerts" desc="Email on every new sign-in to your account" prefs={emailPrefs} saving={savingPrefs} toggle={toggleEmailPref} />
                <PrefToggle k="package_published" label="Publish confirmations" desc="Confirmation email every time you publish a version" prefs={emailPrefs} saving={savingPrefs} toggle={toggleEmailPref} />
                <PrefToggle k="milestone" label="Download milestones" desc="When your packages reach download milestones" prefs={emailPrefs} saving={savingPrefs} toggle={toggleEmailPref} />
                <PrefToggle k="deprecated" label="Deprecation notices" desc="When packages you use are deprecated" prefs={emailPrefs} saving={savingPrefs} toggle={toggleEmailPref} />
                <PrefToggle k="weekly_digest" label="Weekly publisher digest" desc="Weekly summary of your package stats" prefs={emailPrefs} saving={savingPrefs} toggle={toggleEmailPref} />
              </div>
            </div>

            {/* Admin-only */}
            {user.is_admin && (
              <div>
                <h3 className="mb-2 text-sm font-medium text-foreground">Admin</h3>
                <div className="space-y-2">
                  <PrefToggle k="admin_report_notify" label="Report notifications" desc="Email on every new package report from users" prefs={emailPrefs} saving={savingPrefs} toggle={toggleEmailPref} />
                  <PrefToggle k="admin_daily_digest" label="Daily admin digest" desc="Daily platform stats summary" prefs={emailPrefs} saving={savingPrefs} toggle={toggleEmailPref} />
                </div>
              </div>
            )}

            <p className="text-xs text-muted">
              Security alerts (password changes, 2FA, API keys) and important account notifications are always sent and cannot be disabled.
            </p>
          </div>
        )}
      </section>
    </div>
  );
}


function PrefToggle({
  k, label, desc, prefs, saving, toggle
}: {
  k: string;
  label: string;
  desc: string;
  prefs: Record<string, boolean>;
  saving: boolean;
  toggle: (key: string, value: boolean) => void;
}) {
  const enabled = prefs[k] !== false; // default true
  return (
    <div className="flex items-center justify-between rounded bg-background px-3 py-2">
      <div>
        <div className="text-sm text-foreground">{label}</div>
        <div className="text-xs text-muted">{desc}</div>
      </div>
      <button
        onClick={() => toggle(k, !enabled)}
        disabled={saving}
        className={`relative inline-flex h-5 w-9 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ${
          enabled ? "bg-primary" : "bg-border"
        } ${saving ? "opacity-50" : ""}`}
      >
        <span
          className={`pointer-events-none inline-block h-4 w-4 rounded-full bg-white shadow transition-transform duration-200 ${
            enabled ? "translate-x-4" : "translate-x-0"
          }`}
        />
      </button>
    </div>
  );
}
