"use client";

import { useState, Suspense } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";

function RegisterContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const returnTo = searchParams.get("returnTo") || "/dashboard";
  const inviteCode = searchParams.get("invite") || null;

  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleRegister(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      const res = await fetch("/api/v1/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, username, password, ...(inviteCode ? { invite_code: inviteCode } : {}) }),
      });

      const data = await res.json();

      if (!res.ok) {
        const err = data.error || {};
        throw new Error(err.message || "Registration failed");
      }

      // Auto-login after registration
      await fetch("/api/v1/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ email, password }),
      });

      router.push(returnTo);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Registration failed");
    } finally {
      setLoading(false);
    }
  }

  const loginHref = returnTo !== "/dashboard"
    ? `/auth/login?returnTo=${encodeURIComponent(returnTo)}`
    : "/auth/login";

  return (
    <div className="mx-auto max-w-md px-6 py-24">
      <h1 className="mb-2 text-2xl font-bold text-foreground">Create account</h1>
      <p className="mb-8 text-sm text-muted">
        Already have an account?{" "}
        <Link href={loginHref} className="text-primary hover:underline">
          Sign in
        </Link>
      </p>

      {returnTo !== "/dashboard" && (
        <div className="mb-6 rounded-md border border-primary/30 bg-primary/5 px-4 py-3 text-sm text-muted">
          Create an account to publish your capability on AgentNode.
        </div>
      )}

      {error && (
        <div className="mb-4 rounded-md border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">
          {error}
        </div>
      )}

      <form onSubmit={handleRegister} className="space-y-4">
        <div>
          <label className="mb-1 block text-sm text-muted">Email</label>
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded-md border border-border bg-card px-4 py-2.5 text-foreground placeholder:text-muted/50 focus:border-primary focus:outline-none"
            placeholder="you@example.com"
          />
        </div>

        <div>
          <label className="mb-1 block text-sm text-muted">Username</label>
          <input
            type="text"
            required
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="w-full rounded-md border border-border bg-card px-4 py-2.5 text-foreground placeholder:text-muted/50 focus:border-primary focus:outline-none"
            placeholder="your-username"
            pattern="[a-z0-9_-]+"
            title="Lowercase letters, numbers, hyphens, underscores"
          />
        </div>

        <div>
          <label className="mb-1 block text-sm text-muted">Password</label>
          <input
            type="password"
            required
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-md border border-border bg-card px-4 py-2.5 text-foreground placeholder:text-muted/50 focus:border-primary focus:outline-none"
            placeholder="Min. 8 characters"
          />
        </div>

        <button
          type="submit"
          disabled={loading}
          className="w-full rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-primary/90 disabled:opacity-50"
        >
          {loading ? "Creating account..." : "Create account"}
        </button>
      </form>
    </div>
  );
}

export default function RegisterPage() {
  return (
    <Suspense fallback={<div className="py-24 text-center text-muted">Loading...</div>}>
      <RegisterContent />
    </Suspense>
  );
}
