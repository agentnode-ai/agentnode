"use client";

import { useState, useEffect } from "react";
import { fetchWithAuth } from "@/lib/api";

interface SmtpSettings {
  host: string;
  port: number;
  user: string;
  password_masked: string;
  has_password: boolean;
  use_tls: boolean;
  from_email: string;
  from_name: string;
  source: "database" | "environment";
  updated_at: string | null;
}

export default function AdminEmailPage() {
  const [settings, setSettings] = useState<SmtpSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  // Form fields
  const [host, setHost] = useState("");
  const [port, setPort] = useState(587);
  const [user, setUser] = useState("");
  const [password, setPassword] = useState("");
  const [useTls, setUseTls] = useState(true);
  const [fromEmail, setFromEmail] = useState("noreply@agentnode.net");
  const [fromName, setFromName] = useState("AgentNode");

  useEffect(() => {
    loadSettings();
  }, []);

  async function loadSettings() {
    try {
      const res = await fetchWithAuth("/admin/settings/smtp");
      if (res.ok) {
        const data: SmtpSettings = await res.json();
        setSettings(data);
        setHost(data.host || "");
        setPort(data.port || 587);
        setUser(data.user || "");
        setUseTls(data.use_tls ?? true);
        setFromEmail(data.from_email || "noreply@agentnode.net");
        setFromName(data.from_name || "AgentNode");
      }
    } catch {
      setError("Failed to load SMTP settings");
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
      const res = await fetchWithAuth("/admin/settings/smtp", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          host,
          port,
          user,
          password,
          use_tls: useTls,
          from_email: fromEmail,
          from_name: fromName,
        }),
      });

      if (res.ok) {
        setSuccess("SMTP settings saved successfully");
        setPassword("");
        await loadSettings();
      } else {
        const data = await res.json();
        setError(data.error?.message || "Failed to save settings");
      }
    } catch {
      setError("Network error");
    } finally {
      setSaving(false);
    }
  }

  async function handleTest() {
    setTesting(true);
    setError("");
    setSuccess("");

    try {
      const res = await fetchWithAuth("/admin/settings/smtp/test", {
        method: "POST",
      });
      const data = await res.json();

      if (res.ok) {
        setSuccess(data.message || "Test email sent!");
      } else {
        setError(data.error?.message || "Test failed");
      }
    } catch {
      setError("Network error");
    } finally {
      setTesting(false);
    }
  }

  if (loading) {
    return <div className="py-12 text-center text-muted">Loading...</div>;
  }

  const isConfigured = settings?.has_password && settings?.host && settings?.user;

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Email Settings</h1>
          <p className="mt-1 text-sm text-muted">Configure SMTP for transactional emails</p>
        </div>
        <div className="flex items-center gap-2">
          {isConfigured ? (
            <span className="rounded-full bg-success/20 px-3 py-1 text-xs font-medium text-success">
              Configured
            </span>
          ) : (
            <span className="rounded-full bg-warning/20 px-3 py-1 text-xs font-medium text-warning">
              Not configured
            </span>
          )}
          {settings?.source && (
            <span className="rounded-full bg-card px-3 py-1 text-xs text-muted">
              Source: {settings.source}
            </span>
          )}
        </div>
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
        {/* Settings form */}
        <div className="lg:col-span-2">
          <form onSubmit={handleSave} className="rounded-lg border border-border bg-card p-6">
            <h2 className="mb-4 text-sm font-semibold uppercase tracking-wider text-muted">
              SMTP Server
            </h2>

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="sm:col-span-1">
                <label className="mb-1 block text-sm text-muted">SMTP Host</label>
                <input
                  type="text"
                  value={host}
                  onChange={(e) => setHost(e.target.value)}
                  className="w-full rounded-md border border-border bg-background px-3 py-2.5 font-mono text-sm text-foreground focus:border-primary focus:outline-none"
                  placeholder="smtp.mailgun.org"
                />
              </div>
              <div className="sm:col-span-1">
                <label className="mb-1 block text-sm text-muted">Port</label>
                <input
                  type="number"
                  value={port}
                  onChange={(e) => setPort(parseInt(e.target.value) || 587)}
                  className="w-full rounded-md border border-border bg-background px-3 py-2.5 font-mono text-sm text-foreground focus:border-primary focus:outline-none"
                  placeholder="587"
                />
              </div>
            </div>

            <div className="mt-4 grid gap-4 sm:grid-cols-2">
              <div>
                <label className="mb-1 block text-sm text-muted">Username</label>
                <input
                  type="text"
                  value={user}
                  onChange={(e) => setUser(e.target.value)}
                  className="w-full rounded-md border border-border bg-background px-3 py-2.5 font-mono text-sm text-foreground focus:border-primary focus:outline-none"
                  placeholder="postmaster@agentnode.net"
                />
              </div>
              <div>
                <label className="mb-1 block text-sm text-muted">
                  Password
                  {settings?.has_password && (
                    <span className="ml-2 text-xs text-success">
                      (set: {settings.password_masked})
                    </span>
                  )}
                </label>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full rounded-md border border-border bg-background px-3 py-2.5 font-mono text-sm text-foreground focus:border-primary focus:outline-none"
                  placeholder={settings?.has_password ? "Leave empty to keep current" : "SMTP password"}
                />
              </div>
            </div>

            <div className="mt-4">
              <label className="flex items-center gap-2 text-sm text-muted">
                <input
                  type="checkbox"
                  checked={useTls}
                  onChange={(e) => setUseTls(e.target.checked)}
                  className="rounded border-border"
                />
                Use TLS (recommended)
              </label>
            </div>

            <h2 className="mb-4 mt-8 text-sm font-semibold uppercase tracking-wider text-muted">
              Sender Identity
            </h2>

            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label className="mb-1 block text-sm text-muted">From Name</label>
                <input
                  type="text"
                  value={fromName}
                  onChange={(e) => setFromName(e.target.value)}
                  className="w-full rounded-md border border-border bg-background px-3 py-2.5 text-sm text-foreground focus:border-primary focus:outline-none"
                  placeholder="AgentNode"
                />
              </div>
              <div>
                <label className="mb-1 block text-sm text-muted">From Email</label>
                <input
                  type="email"
                  value={fromEmail}
                  onChange={(e) => setFromEmail(e.target.value)}
                  className="w-full rounded-md border border-border bg-background px-3 py-2.5 font-mono text-sm text-foreground focus:border-primary focus:outline-none"
                  placeholder="noreply@agentnode.net"
                />
              </div>
            </div>

            <div className="mt-6 flex items-center gap-3">
              <button
                type="submit"
                disabled={saving}
                className="rounded-md bg-primary px-5 py-2.5 text-sm font-medium text-white hover:bg-primary/90 disabled:opacity-50"
              >
                {saving ? "Saving..." : "Save Settings"}
              </button>
              <button
                type="button"
                onClick={handleTest}
                disabled={testing || !isConfigured}
                className="rounded-md border border-border px-5 py-2.5 text-sm text-muted hover:text-foreground hover:border-primary/30 disabled:opacity-50"
              >
                {testing ? "Sending..." : "Send Test Email"}
              </button>
            </div>
          </form>
        </div>

        {/* Sidebar info */}
        <div className="space-y-4">
          <div className="rounded-lg border border-border bg-card p-5">
            <h3 className="mb-3 text-sm font-semibold text-foreground">How it works</h3>
            <div className="space-y-2 text-xs text-muted">
              <p>SMTP settings stored in the database take priority over environment variables.</p>
              <p>Changes take effect immediately — no server restart needed.</p>
              <p>Use the test button to verify your configuration before relying on it.</p>
            </div>
          </div>

          <div className="rounded-lg border border-border bg-card p-5">
            <h3 className="mb-3 text-sm font-semibold text-foreground">Recommended Providers</h3>
            <div className="space-y-2 text-xs text-muted">
              <div className="flex items-center justify-between">
                <span className="font-medium text-foreground">Resend</span>
                <span>Free tier: 3k/month</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="font-medium text-foreground">Mailgun</span>
                <span>Free tier: 5k/month</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="font-medium text-foreground">Amazon SES</span>
                <span>$0.10/1k emails</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="font-medium text-foreground">Brevo</span>
                <span>Free tier: 300/day</span>
              </div>
            </div>
          </div>

          <div className="rounded-lg border border-border bg-card p-5">
            <h3 className="mb-3 text-sm font-semibold text-foreground">Emails sent by the platform</h3>
            <ul className="space-y-1.5 text-xs text-muted">
              <li>Email verification</li>
              <li>Password reset</li>
              <li>Publisher suspension notice</li>
              <li>Package quarantine notice</li>
            </ul>
          </div>

          {settings?.updated_at && (
            <p className="text-xs text-muted">
              Last updated: {new Date(settings.updated_at).toLocaleString()}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
