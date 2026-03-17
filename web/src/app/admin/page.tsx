"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { fetchWithAuth } from "@/lib/api";

interface Stats {
  users: { total: number; admins: number; email_verified: number };
  packages: { total: number; total_versions: number; quarantined: number };
  downloads: { total: number; top_packages: { slug: string; downloads: number }[] };
  installations: { total: number; active: number; failed: number };
  publishers: { total: number; suspended: number };
  moderation: { open_reports: number };
}

function StatCard({ label, value, sub, href }: { label: string; value: number; sub?: string; href?: string }) {
  const inner = (
    <div className="rounded-lg border border-border bg-card p-5 transition-colors hover:border-primary/30">
      <div className="text-sm text-muted">{label}</div>
      <div className="mt-1 text-2xl font-bold text-foreground">{value.toLocaleString()}</div>
      {sub && <div className="mt-1 text-xs text-muted">{sub}</div>}
    </div>
  );
  return href ? <Link href={href}>{inner}</Link> : inner;
}

export default function AdminOverviewPage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchWithAuth("/admin/stats")
      .then((r) => r.json())
      .then(setStats)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return <div className="py-12 text-center text-muted">Loading stats...</div>;
  }

  if (!stats) {
    return <div className="py-12 text-center text-danger">Failed to load stats.</div>;
  }

  return (
    <div>
      <h1 className="mb-6 text-xl font-bold text-foreground">Dashboard Overview</h1>

      {/* Metric cards */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
        <StatCard label="Users" value={stats.users.total} sub={`${stats.users.admins} admins`} href="/admin/users" />
        <StatCard label="Publishers" value={stats.publishers.total} sub={`${stats.publishers.suspended} suspended`} href="/admin/publishers" />
        <StatCard label="Packages" value={stats.packages.total} sub={`${stats.packages.total_versions} versions`} href="/admin/packages" />
        <StatCard label="Downloads" value={stats.downloads.total} />
        <StatCard label="Installations" value={stats.installations.total} sub={`${stats.installations.active} active · ${stats.installations.failed} failed`} href="/admin/installations" />
        <StatCard label="Quarantined" value={stats.packages.quarantined} href="/admin/packages" />
        <StatCard label="Open Reports" value={stats.moderation.open_reports} href="/admin/reports" />
        <StatCard label="Email Verified" value={stats.users.email_verified} sub={`of ${stats.users.total} users`} />
      </div>

      {/* Top packages */}
      {stats.downloads.top_packages.length > 0 && (
        <section className="mt-8">
          <h2 className="mb-3 text-sm font-semibold text-foreground">Top Packages by Downloads</h2>
          <div className="rounded-lg border border-border bg-card overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs text-muted">
                  <th className="px-4 py-2">#</th>
                  <th className="px-4 py-2">Package</th>
                  <th className="px-4 py-2 text-right">Downloads</th>
                </tr>
              </thead>
              <tbody>
                {stats.downloads.top_packages.map((pkg, i) => (
                  <tr key={pkg.slug} className="border-b border-border/50 last:border-0">
                    <td className="px-4 py-2 text-muted">{i + 1}</td>
                    <td className="px-4 py-2">
                      <Link href={`/packages/${pkg.slug}`} className="text-primary hover:underline">
                        {pkg.slug}
                      </Link>
                    </td>
                    <td className="px-4 py-2 text-right font-mono text-foreground">
                      {pkg.downloads.toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* Quick actions */}
      <section className="mt-8">
        <h2 className="mb-3 text-sm font-semibold text-foreground">Quick Actions</h2>
        <div className="flex flex-wrap gap-3">
          <Link href="/admin/packages" className="rounded-md border border-border bg-card px-4 py-2 text-sm text-muted hover:text-foreground hover:border-primary/30 transition-colors">
            Quarantine Queue
          </Link>
          <Link href="/admin/reports" className="rounded-md border border-border bg-card px-4 py-2 text-sm text-muted hover:text-foreground hover:border-primary/30 transition-colors">
            Review Reports
          </Link>
          <Link href="/admin/users" className="rounded-md border border-border bg-card px-4 py-2 text-sm text-muted hover:text-foreground hover:border-primary/30 transition-colors">
            Manage Users
          </Link>
          <Link href="/admin/capabilities" className="rounded-md border border-border bg-card px-4 py-2 text-sm text-muted hover:text-foreground hover:border-primary/30 transition-colors">
            Edit Capabilities
          </Link>
          <Link href="/admin/email" className="rounded-md border border-border bg-card px-4 py-2 text-sm text-muted hover:text-foreground hover:border-primary/30 transition-colors">
            Email / SMTP
          </Link>
          <Link href="/admin/audit" className="rounded-md border border-border bg-card px-4 py-2 text-sm text-muted hover:text-foreground hover:border-primary/30 transition-colors">
            Audit Log
          </Link>
        </div>
      </section>
    </div>
  );
}
