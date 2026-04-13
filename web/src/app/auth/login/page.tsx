"use client";

import { useState, Suspense } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";

function LoginContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const returnTo = searchParams.get("returnTo") || "/dashboard";

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [needs2FA, setNeeds2FA] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      const body: Record<string, string> = { email, password };
      if (totpCode) body.totp_code = totpCode;

      const res = await fetch("/api/v1/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(body),
      });

      const data = await res.json();

      if (!res.ok) {
        const err = data.error || {};
        if (err.code === "AUTH_2FA_REQUIRED") {
          setNeeds2FA(true);
          setLoading(false);
          return;
        }
        throw new Error(err.message || "Login failed");
      }

      // Redirect to returnTo or dashboard
      router.push(returnTo);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  const registerHref = returnTo !== "/dashboard"
    ? `/auth/register?returnTo=${encodeURIComponent(returnTo)}`
    : "/auth/register";

  return (
    <div className="mx-auto max-w-md px-6 py-24">
      <h1 className="mb-2 text-2xl font-bold text-foreground">Sign in</h1>
      <p className="mb-8 text-sm text-muted">
        Don&apos;t have an account?{" "}
        <Link href={registerHref} className="text-primary hover:underline">
          Create one
        </Link>
      </p>

      {returnTo !== "/dashboard" && (
        <div className="mb-6 rounded-md border border-primary/30 bg-primary/5 px-4 py-3 text-sm text-muted">
          Sign in to continue to publishing.
        </div>
      )}

      {error && (
        <div className="mb-4 rounded-md border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">
          {error}
        </div>
      )}

      <form onSubmit={handleLogin} className="space-y-4">
        <div>
          <label htmlFor="login-email" className="mb-1 block text-sm text-muted">Email</label>
          <input
            id="login-email"
            name="email"
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded-md border border-border bg-card px-4 py-2.5 text-foreground placeholder:text-muted/50 focus:border-primary focus:outline-none"
            placeholder="you@example.com"
          />
        </div>

        <div>
          <label htmlFor="login-password" className="mb-1 block text-sm text-muted">Password</label>
          <input
            id="login-password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-md border border-border bg-card px-4 py-2.5 text-foreground placeholder:text-muted/50 focus:border-primary focus:outline-none"
            placeholder="••••••••"
          />
        </div>

        {needs2FA && (
          <div>
            <label htmlFor="login-totp" className="mb-1 block text-sm text-muted">2FA Code</label>
            <input
              id="login-totp"
              name="totp_code"
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              value={totpCode}
              onChange={(e) => setTotpCode(e.target.value)}
              className="w-full rounded-md border border-border bg-card px-4 py-2.5 text-foreground placeholder:text-muted/50 focus:border-primary focus:outline-none"
              placeholder="123456"
              autoFocus
            />
          </div>
        )}

        <button
          type="submit"
          disabled={loading}
          className="w-full rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-primary/90 disabled:opacity-50"
        >
          {loading ? "Signing in..." : "Sign in"}
        </button>
      </form>

      <p className="mt-4 text-center text-sm text-muted">
        <Link href="/auth/forgot-password" className="text-primary hover:underline">
          Forgot your password?
        </Link>
      </p>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={<div className="py-24 text-center text-muted">Loading...</div>}>
      <LoginContent />
    </Suspense>
  );
}
