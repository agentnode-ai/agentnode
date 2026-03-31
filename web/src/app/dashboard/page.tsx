"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import QRCode from "qrcode";
import { fetchWithAuth, search, type SearchHit } from "@/lib/api";
import VerificationBadge from "@/components/VerificationBadge";

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

  // Publisher editing (Fix 4)
  const [editingPublisher, setEditingPublisher] = useState(false);
  const [pubEditFields, setPubEditFields] = useState({ display_name: "", bio: "", website_url: "", github_url: "" });
  const [savingPublisher, setSavingPublisher] = useState(false);

  // My Packages (Fix 6)
  const [myPackages, setMyPackages] = useState<SearchHit[]>([]);
  const [loadingPackages, setLoadingPackages] = useState(false);

  // Manual Reviews
  const [myReviews, setMyReviews] = useState<any[]>([]);
  const [loadingReviews, setLoadingReviews] = useState(false);
  const [reviewForm, setReviewForm] = useState({ package_slug: "", version: "", tier: "security", express: false });
  const [requestingReview, setRequestingReview] = useState(false);
  const [showReviewForm, setShowReviewForm] = useState(false);
  // Package versions for review form
  const [reviewVersions, setReviewVersions] = useState<{ version_number: string; is_yanked?: boolean; quarantine_status?: string }[]>([]);

  // API Key copy (Fix 8)
  const [copiedKey, setCopiedKey] = useState(false);

  // QR code (Fix 9)
  const [qrDataUrl, setQrDataUrl] = useState("");

  useEffect(() => {
    // Handle review redirect params from Stripe
    const params = new URLSearchParams(window.location.search);
    const reviewParam = params.get("review");
    if (reviewParam === "success") {
      setSuccess("Payment successful! Your review request is in the queue.");
      params.delete("review");
      const newQuery = params.toString();
      window.history.replaceState({}, '', '/dashboard' + (newQuery ? '?' + newQuery : ''));
    } else if (reviewParam === "cancelled") {
      setError("Payment was cancelled.");
      params.delete("review");
      const newQuery = params.toString();
      window.history.replaceState({}, '', '/dashboard' + (newQuery ? '?' + newQuery : ''));
    }

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
      if (data.publisher?.slug) {
        loadMyPackages(data.publisher.slug);
        loadReviews();
      }
    } catch {
      router.push("/auth/login");
    } finally {
      setLoading(false);
    }
  }

  async function setup2FA() {
    setShowSetup2FA(true);
    setQrDataUrl("");
    const res = await fetchWithAuth("/auth/2fa/setup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    if (res.ok) {
      const data = await res.json();
      const uri = data.provisioning_uri || data.qr_uri || "";
      setQrUri(uri);
      setSecret(data.secret || "");
      if (uri) {
        try { const url = await QRCode.toDataURL(uri, { width: 200, margin: 2 }); setQrDataUrl(url); } catch { /* fallback to text */ }
      }
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

  async function loadMyPackages(publisherSlug: string) {
    setLoadingPackages(true);
    try {
      const res = await search({ publisher_slug: publisherSlug, per_page: 50 });
      setMyPackages(res.hits || []);
    } catch { /* non-critical */ }
    finally { setLoadingPackages(false); }
  }

  async function savePublisher() {
    if (!user?.publisher) return;
    setSavingPublisher(true);
    setError("");
    try {
      const res = await fetchWithAuth(`/publishers/${user.publisher.slug}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(pubEditFields),
      });
      if (res.ok) {
        setEditingPublisher(false);
        setSuccess("Publisher profile updated.");
        await loadUser();
      } else {
        const data = await res.json().catch(() => ({}));
        setError(data.error?.message || data.detail || "Failed to update publisher");
      }
    } finally {
      setSavingPublisher(false);
    }
  }

  async function loadReviews() {
    setLoadingReviews(true);
    try {
      const res = await fetchWithAuth("/reviews/my");
      if (res.ok) {
        const data = await res.json();
        setMyReviews(data || []);
      }
    } catch { /* non-critical */ }
    finally { setLoadingReviews(false); }
  }

  async function loadVersionsForReview(slug: string) {
    try {
      const res = await fetchWithAuth(`/packages/${encodeURIComponent(slug)}/versions/all`);
      if (res.ok) {
        const data = await res.json();
        // Filter out yanked and rejected versions
        const activeVersions = (data.versions || []).filter(
          (v: any) => !v.is_yanked && v.quarantine_status !== "rejected"
        );
        setReviewVersions(activeVersions);
        if (activeVersions.length > 0) setReviewForm(f => ({ ...f, version: activeVersions[0].version_number }));
      }
    } catch { /* non-critical */ }
  }

  async function requestReview() {
    setRequestingReview(true);
    clearMessages();
    try {
      const res = await fetchWithAuth("/reviews/request", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(reviewForm),
      });
      if (res.ok) {
        const data = await res.json();
        // Redirect to Stripe Checkout
        if (data.checkout_url) {
          window.location.href = data.checkout_url;
          return;
        }
        setShowReviewForm(false);
        await loadReviews();
      } else {
        const data = await res.json().catch(() => ({}));
        setError(data.error?.message || "Failed to request review");
      }
    } finally {
      setRequestingReview(false);
    }
  }

  const tierPrices: Record<string, number> = { security: 49, compatibility: 99, full: 199 };
  const expressSurcharge = 100;
  function reviewPrice() {
    const base = tierPrices[reviewForm.tier] || 0;
    return base + (reviewForm.express ? expressSurcharge : 0);
  }

  const statusColors: Record<string, string> = {
    pending_payment: "bg-yellow-500/10 text-yellow-400",
    paid: "bg-blue-500/10 text-blue-400",
    in_review: "bg-purple-500/10 text-purple-400",
    approved: "bg-green-500/10 text-green-400",
    changes_requested: "bg-orange-500/10 text-orange-400",
    rejected: "bg-red-500/10 text-red-400",
    refunded: "bg-gray-500/10 text-gray-400",
  };

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
                <div className="text-right">
                  <span className="text-success text-xs font-medium">Enabled</span>
                  <p className="text-[10px] text-muted mt-0.5">Cannot be disabled for security. Contact support if needed.</p>
                </div>
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
          {qrDataUrl && (
            <div className="mb-4 flex justify-center">
              <img src={qrDataUrl} alt="2FA QR Code" width={200} height={200} className="rounded-lg" />
            </div>
          )}
          {secret && (
            <div className="mb-4">
              <p className="mb-1 text-xs text-muted">Or enter this secret manually:</p>
              <div className="rounded bg-background p-3 font-mono text-xs text-foreground break-all select-all">
                {secret}
              </div>
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
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-foreground">Publisher</h2>
          {user.publisher && !editingPublisher && (
            <button
              onClick={() => {
                setPubEditFields({
                  display_name: user.publisher!.display_name || "",
                  bio: "",
                  website_url: "",
                  github_url: "",
                });
                setEditingPublisher(true);
                // Also load packages on first view
                if (myPackages.length === 0) loadMyPackages(user.publisher!.slug);
              }}
              className="text-xs text-primary hover:underline"
            >
              Edit
            </button>
          )}
        </div>
        {user.publisher ? (
          editingPublisher ? (
            <div className="space-y-3">
              <div>
                <label className="mb-1 block text-sm text-muted">Slug</label>
                <input
                  type="text"
                  value={user.publisher.slug}
                  disabled
                  className="w-full rounded-md border border-border bg-background/50 px-3 py-2 text-sm text-muted cursor-not-allowed"
                />
              </div>
              <div>
                <label className="mb-1 block text-sm text-muted">Display name</label>
                <input
                  type="text"
                  value={pubEditFields.display_name}
                  onChange={(e) => setPubEditFields(f => ({ ...f, display_name: e.target.value }))}
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
                />
              </div>
              <div>
                <label className="mb-1 block text-sm text-muted">Bio</label>
                <textarea
                  value={pubEditFields.bio}
                  onChange={(e) => setPubEditFields(f => ({ ...f, bio: e.target.value }))}
                  rows={2}
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none resize-none"
                  placeholder="A short bio about you or your organization"
                />
              </div>
              <div>
                <label className="mb-1 block text-sm text-muted">Website URL</label>
                <input
                  type="url"
                  value={pubEditFields.website_url}
                  onChange={(e) => setPubEditFields(f => ({ ...f, website_url: e.target.value }))}
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
                  placeholder="https://example.com"
                />
              </div>
              <div>
                <label className="mb-1 block text-sm text-muted">GitHub URL</label>
                <input
                  type="url"
                  value={pubEditFields.github_url}
                  onChange={(e) => setPubEditFields(f => ({ ...f, github_url: e.target.value }))}
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
                  placeholder="https://github.com/your-org"
                />
              </div>
              <div className="flex gap-2">
                <button
                  onClick={savePublisher}
                  disabled={savingPublisher}
                  className="rounded bg-primary px-4 py-2 text-sm text-white hover:bg-primary/90 disabled:opacity-50"
                >
                  {savingPublisher ? "Saving..." : "Save changes"}
                </button>
                <button
                  onClick={() => setEditingPublisher(false)}
                  className="rounded border border-border px-4 py-2 text-sm text-muted hover:bg-card"
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
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
          )
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

      {/* My Packages (Fix 6) */}
      {user.publisher && (
        <section className="mb-8 rounded-lg border border-border bg-card p-6">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-semibold text-foreground">My Packages</h2>
            <Link href={`/publishers/${user.publisher.slug}`} className="text-xs text-primary hover:underline">
              View all
            </Link>
          </div>
          {loadingPackages ? (
            <p className="text-sm text-muted">Loading...</p>
          ) : myPackages.length > 0 ? (
            <div className="space-y-2">
              {myPackages.map((pkg) => (
                <div
                  key={pkg.slug}
                  className="flex items-center justify-between rounded-lg border border-border bg-background px-4 py-2.5 transition-colors hover:border-primary/30"
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <Link href={`/packages/${pkg.slug}`} className="text-sm font-medium text-foreground truncate hover:text-primary transition-colors">
                      {pkg.name}
                    </Link>
                    {pkg.latest_version && (
                      <span className="font-mono text-xs text-muted">v{pkg.latest_version}</span>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    <VerificationBadge
                      tier={pkg.verification_tier}
                      status={pkg.verification_status}
                      score={pkg.verification_score}
                    />
                    {pkg.verification_score != null && (
                      <span className="font-mono text-[11px] text-muted">{pkg.verification_score}/100</span>
                    )}
                    {pkg.is_deprecated && (
                      <span className="rounded-full bg-red-500/10 px-2 py-0.5 text-[10px] font-medium text-red-400">deprecated</span>
                    )}
                    <div className="flex items-center gap-1 ml-1">
                      <Link
                        href={`/packages/${pkg.slug}`}
                        className="rounded border border-border px-2 py-0.5 text-[10px] text-muted hover:text-foreground hover:bg-card transition-colors"
                      >
                        View
                      </Link>
                      <Link
                        href={`/packages/${pkg.slug}`}
                        className="rounded border border-border px-2 py-0.5 text-[10px] text-muted hover:text-foreground hover:bg-card transition-colors"
                      >
                        Edit
                      </Link>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-sm text-muted">
              No packages yet.{" "}
              <Link href="/publish" className="text-primary hover:underline">Publish your first package</Link>
            </div>
          )}
        </section>
      )}

      {/* Manual Reviews */}
      {user.publisher && (
        <section className="mb-8 rounded-lg border border-border bg-card p-6">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-semibold text-foreground">Manual Reviews</h2>
            <button
              onClick={() => setShowReviewForm(!showReviewForm)}
              className="text-xs text-primary hover:underline"
            >
              {showReviewForm ? "Cancel" : "Request a review"}
            </button>
          </div>

          <p className="mb-4 text-sm text-muted">
            Reviewed tools build more trust with users. Request a manual code review for any package version.
          </p>

          {showReviewForm && (
            <div className="mb-6 rounded-lg border border-primary/20 bg-background p-4 space-y-3">
              <div>
                <label className="mb-1 block text-sm text-muted">Package</label>
                <select
                  value={reviewForm.package_slug}
                  onChange={(e) => {
                    const slug = e.target.value;
                    setReviewForm(f => ({ ...f, package_slug: slug, version: "" }));
                    if (slug) loadVersionsForReview(slug);
                  }}
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
                >
                  <option value="">Select a package</option>
                  {myPackages.map(p => (
                    <option key={p.slug} value={p.slug}>{p.name} ({p.slug})</option>
                  ))}
                </select>
              </div>
              {reviewForm.package_slug && (
                <div>
                  <label className="mb-1 block text-sm text-muted">Version</label>
                  <select
                    value={reviewForm.version}
                    onChange={(e) => setReviewForm(f => ({ ...f, version: e.target.value }))}
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
                  >
                    <option value="">Select a version</option>
                    {reviewVersions.map(v => (
                      <option key={v.version_number} value={v.version_number}>
                        v{v.version_number}{v.quarantine_status === "quarantined" ? " (quarantined)" : ""}
                      </option>
                    ))}
                  </select>
                </div>
              )}
              <div>
                <label className="mb-1 block text-sm text-muted">Review Tier</label>
                <div className="space-y-2">
                  {[
                    { value: "security", label: "Security Review", price: "$49", desc: "Dependency audit, permission check, sandbox-escape analysis" },
                    { value: "compatibility", label: "Compatibility Review", price: "$99", desc: "Security + provider compatibility, edge-cases, error handling" },
                    { value: "full", label: "Full Review", price: "$199", desc: "Everything + code quality, docs, best practices" },
                  ].map(t => (
                    <label key={t.value} className={`flex items-start gap-3 rounded-lg border p-3 cursor-pointer transition-colors ${reviewForm.tier === t.value ? "border-primary bg-primary/5" : "border-border hover:border-border/80"}`}>
                      <input
                        type="radio"
                        name="tier"
                        value={t.value}
                        checked={reviewForm.tier === t.value}
                        onChange={(e) => setReviewForm(f => ({ ...f, tier: e.target.value }))}
                        className="mt-0.5"
                      />
                      <div className="flex-1">
                        <div className="flex items-center justify-between">
                          <span className="text-sm font-medium text-foreground">{t.label}</span>
                          <span className="text-sm font-mono text-primary">{t.price}</span>
                        </div>
                        <p className="text-xs text-muted mt-0.5">{t.desc}</p>
                      </div>
                    </label>
                  ))}
                </div>
              </div>
              <div className="flex items-center gap-3">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={reviewForm.express}
                    onChange={(e) => setReviewForm(f => ({ ...f, express: e.target.checked }))}
                  />
                  <span className="text-sm text-foreground">Express (+$100, 48h instead of 7 days)</span>
                </label>
              </div>
              <div className="flex items-center justify-between pt-2 border-t border-border">
                <span className="text-sm text-muted">Total: <span className="font-mono font-medium text-foreground">${reviewPrice()}</span> USD</span>
                <button
                  onClick={requestReview}
                  disabled={requestingReview || !reviewForm.package_slug || !reviewForm.version}
                  className="rounded bg-primary px-4 py-2 text-sm text-white hover:bg-primary/90 disabled:opacity-50"
                >
                  {requestingReview ? "Processing..." : "Request Review"}
                </button>
              </div>
            </div>
          )}

          {loadingReviews ? (
            <p className="text-sm text-muted">Loading reviews...</p>
          ) : myReviews.length > 0 ? (
            <div className="space-y-2">
              {myReviews.map((r: any) => (
                <div key={r.id} className="rounded-lg border border-border bg-background px-4 py-3">
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-foreground">{r.package_name || r.package_slug}</span>
                      <span className="font-mono text-xs text-muted">v{r.version}</span>
                      <span className="rounded-full px-2 py-0.5 text-[10px] font-medium capitalize border border-border bg-card text-muted">{r.tier}</span>
                      {r.express && <span className="rounded-full bg-yellow-500/10 px-2 py-0.5 text-[10px] font-medium text-yellow-400">Express</span>}
                    </div>
                    <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${statusColors[r.status] || "bg-card text-muted"}`}>
                      {r.status.replace(/_/g, " ")}
                    </span>
                  </div>
                  <div className="text-xs text-muted">
                    ${(r.price_cents / 100).toFixed(0)} USD
                    {r.paid_at && <> &middot; Paid {new Date(r.paid_at).toLocaleDateString()}</>}
                    {r.reviewed_at && <> &middot; Reviewed {new Date(r.reviewed_at).toLocaleDateString()}</>}
                  </div>
                  {/* Show structured feedback for completed reviews */}
                  {r.review_result && (r.status === "changes_requested" || r.status === "approved" || r.status === "rejected") && (
                    <div className="mt-2 rounded border border-border bg-card p-3 text-xs space-y-1">
                      <div className="flex gap-4">
                        <span className={r.review_result.security_passed ? "text-green-400" : "text-red-400"}>
                          Security: {r.review_result.security_passed ? "Passed" : "Failed"}
                        </span>
                        {r.review_result.compatibility_passed !== undefined && (
                          <span className={r.review_result.compatibility_passed ? "text-green-400" : "text-red-400"}>
                            Compatibility: {r.review_result.compatibility_passed ? "Passed" : "Failed"}
                          </span>
                        )}
                        {r.review_result.docs_passed !== undefined && (
                          <span className={r.review_result.docs_passed ? "text-green-400" : "text-red-400"}>
                            Docs: {r.review_result.docs_passed ? "Passed" : "Failed"}
                          </span>
                        )}
                      </div>
                      {r.review_result.required_changes && r.review_result.required_changes.length > 0 && (
                        <div className="mt-1">
                          <span className="text-muted">Required changes:</span>
                          <ul className="mt-0.5 list-disc list-inside text-foreground">
                            {r.review_result.required_changes.map((c: string, i: number) => (
                              <li key={i}>{c}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                      {r.review_result.reviewer_summary && (
                        <p className="text-muted mt-1">{r.review_result.reviewer_summary}</p>
                      )}
                    </div>
                  )}
                  {r.status === "changes_requested" && (
                    <div className="mt-2 rounded border border-orange-500/30 bg-orange-500/10 px-3 py-2 text-xs text-orange-300">
                      <strong>Next steps:</strong> Fix the issues above, publish a new version,
                      then request another review for the new version.
                    </div>
                  )}
                  {r.review_notes && (
                    <p className="mt-1 text-xs text-muted italic">{r.review_notes}</p>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted">No review requests yet.</p>
          )}
        </section>
      )}

      {/* API Keys Section */}
      <section className="mb-8 rounded-lg border border-border bg-card p-6">
        <h2 className="mb-4 text-lg font-semibold text-foreground">API Keys</h2>
        <p className="mb-4 text-sm text-muted">
          API keys allow programmatic access via the SDK or API. Keys are shown only once at creation.
        </p>

        {createdKey && (
          <div className="mb-4 rounded-md border border-success/30 bg-success/10 px-4 py-3 text-sm">
            <p className="mb-1 font-medium text-success">Key created! Copy it now — it won&apos;t be shown again.</p>
            <div className="flex items-center gap-2">
              <code className="flex-1 break-all rounded bg-background px-2 py-1 font-mono text-xs text-foreground">
                {createdKey}
              </code>
              <button
                onClick={() => { navigator.clipboard.writeText(createdKey); setCopiedKey(true); setTimeout(() => setCopiedKey(false), 2000); }}
                className="shrink-0 rounded border border-border px-3 py-1 text-xs text-muted transition-colors hover:text-foreground"
              >
                {copiedKey ? "Copied!" : "Copy"}
              </button>
            </div>
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

      {/* Account Deletion (Fix 14) */}
      <section className="mb-8 rounded-lg border border-border bg-card p-6">
        <h2 className="mb-2 text-lg font-semibold text-foreground">Delete Account</h2>
        <p className="text-sm text-muted">
          Need to delete your account? Contact{" "}
          <a href="mailto:support@agentnode.net" className="text-primary hover:underline">
            support@agentnode.net
          </a>{" "}
          and we&apos;ll process your request.
        </p>
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
