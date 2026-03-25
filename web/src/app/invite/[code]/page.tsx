"use client";

import { useState, useEffect, useCallback } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { fetchWithAuth } from "@/lib/api";

interface InviteInfo {
  code: string;
  valid: boolean;
  display_name: string | null;
  description: string | null;
  source_url: string | null;
}

interface AuthUser {
  id: string;
  email: string;
  username: string;
}

/* ------------------------------------------------------------------ */
/*  Value Prop Components                                              */
/* ------------------------------------------------------------------ */

function ValueProp({ icon, title, desc }: { icon: string; title: string; desc: string }) {
  return (
    <div className="flex gap-3">
      <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-sm text-primary">
        {icon}
      </div>
      <div>
        <div className="text-sm font-semibold text-foreground">{title}</div>
        <div className="text-xs text-muted leading-relaxed">{desc}</div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main Page                                                          */
/* ------------------------------------------------------------------ */

export default function InviteLandingPage() {
  const params = useParams();
  const router = useRouter();
  const searchParams = useSearchParams();
  const code = params.code as string;

  const [invite, setInvite] = useState<InviteInfo | null>(null);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [claiming, setClaiming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Check if we just came back from auth flow
  const justAuthed = searchParams.get("authed") === "1";

  // Load invite info
  useEffect(() => {
    async function load() {
      try {
        const res = await fetch(`/api/v1/invites/${encodeURIComponent(code)}`);
        if (!res.ok) {
          const data = await res.json().catch(() => null);
          setError(data?.error?.message || "This invite is no longer valid.");
          setLoading(false);
          return;
        }
        const data = await res.json();
        setInvite(data);
      } catch {
        setError("Failed to load invite. Please try again.");
      }

      // Check auth
      try {
        const authRes = await fetchWithAuth("/auth/me");
        if (authRes.ok) {
          const userData = await authRes.json();
          setUser(userData);
        }
      } catch {
        // Not logged in — that's fine
      }

      setLoading(false);
    }
    load();
  }, [code]);

  // Auto-claim if just came back from auth
  useEffect(() => {
    if (justAuthed && user && invite && !claiming) {
      handleClaim();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [justAuthed, user, invite]);

  const handleClaim = useCallback(async () => {
    if (claiming) return;
    setClaiming(true);
    setError(null);

    try {
      const res = await fetchWithAuth(`/invites/${encodeURIComponent(code)}/claim`, {
        method: "POST",
      });

      if (!res.ok) {
        const data = await res.json().catch(() => null);
        setError(data?.error?.message || "Failed to claim invite.");
        setClaiming(false);
        return;
      }

      const { prefill_data } = await res.json();

      // Store invite code for post-publish callback
      sessionStorage.setItem("invite_code", code);

      // Store prefill for publish page (same format as /import)
      sessionStorage.setItem("publish_prefill", JSON.stringify(prefill_data));

      // Navigate to publish
      router.push("/publish?from=import");
    } catch {
      setError("Something went wrong. Please try again.");
      setClaiming(false);
    }
  }, [claiming, code, router]);

  if (loading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center text-muted">
        Loading invite...
      </div>
    );
  }

  if (error && !invite) {
    return (
      <div className="mx-auto max-w-lg px-4 py-20 text-center">
        <div className="rounded-xl border border-border bg-card p-8">
          <div className="mb-4 text-4xl">:(</div>
          <h1 className="text-xl font-bold text-foreground">Invite not found</h1>
          <p className="mt-2 text-sm text-muted">{error}</p>
          <Link
            href="/"
            className="mt-6 inline-block rounded-lg bg-primary px-6 py-2.5 text-sm font-medium text-white transition-colors hover:bg-primary/90"
          >
            Go to AgentNode
          </Link>
        </div>
      </div>
    );
  }

  const returnTo = `/invite/${encodeURIComponent(code)}?authed=1`;
  const toolName = invite?.display_name || "your tool";

  return (
    <div className="mx-auto max-w-xl px-4 py-12">
      <div className="rounded-xl border border-border bg-card p-8">
        {/* Header */}
        <div className="mb-6 text-center">
          <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-primary/30 bg-primary/10 px-3 py-1 text-xs font-medium text-primary">
            Invitation
          </div>
          <h1 className="text-2xl font-bold text-foreground">
            Get {toolName} auto-installed by AI agents
          </h1>
          <p className="mt-2 text-sm text-muted">
            Publish on AgentNode and agents will discover, install, and use your tool automatically.
          </p>
        </div>

        {/* Tool info card */}
        {invite && (
          <div className="mb-6 rounded-lg border border-border bg-background p-4">
            {invite.display_name && (
              <h2 className="text-lg font-semibold text-foreground">{invite.display_name}</h2>
            )}
            {invite.description && (
              <p className="mt-1 text-sm text-muted">{invite.description}</p>
            )}
            {invite.source_url && (
              <a
                href={invite.source_url}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-2 inline-flex items-center gap-1 text-xs text-primary hover:underline"
              >
                {invite.source_url.replace("https://github.com/", "")} &rarr;
              </a>
            )}
          </div>
        )}

        {/* Value propositions */}
        <div className="mb-6 space-y-4">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">Why publish on AgentNode?</h3>
          <ValueProp
            icon="&#x1F50D;"
            title="Agents find you automatically"
            desc="AI agents search by capability, not package name. When they need what your tool does, they find it and install it at runtime — no marketing required."
          />
          <ValueProp
            icon="&#x1F517;"
            title="Works across every framework"
            desc="One listing works with LangChain, CrewAI, MCP, AutoGPT, and plain Python. No separate integrations to maintain."
          />
          <ValueProp
            icon="&#x2713;"
            title="Verified trust badge"
            desc="Every package is sandbox-tested on publish. Import checks, smoke tests, security scan. Agents trust verified tools by default."
          />
          <ValueProp
            icon="&#x1F4CA;"
            title="Real usage analytics"
            desc="See exactly how many agents install and use your tool, across which frameworks and use cases."
          />
        </div>

        {/* How it works */}
        <div className="mb-6 rounded-lg border border-border bg-background p-4">
          <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted">How it works</h3>
          <div className="space-y-2 text-sm text-muted">
            <div className="flex gap-2">
              <span className="shrink-0 font-mono text-xs text-primary">1.</span>
              <span>Review the pre-filled metadata we generated from your repo</span>
            </div>
            <div className="flex gap-2">
              <span className="shrink-0 font-mono text-xs text-primary">2.</span>
              <span>Adjust anything you like &mdash; name, description, capabilities</span>
            </div>
            <div className="flex gap-2">
              <span className="shrink-0 font-mono text-xs text-primary">3.</span>
              <span>Hit publish &mdash; takes about 2 minutes</span>
            </div>
          </div>
          <p className="mt-3 text-xs font-medium text-foreground">
            You publish under your own name. Nothing is published automatically.
          </p>
        </div>

        {/* Error */}
        {error && (
          <div className="mb-4 rounded-lg border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">
            {error}
          </div>
        )}

        {/* Actions */}
        <div className="space-y-3">
          {user ? (
            <button
              onClick={handleClaim}
              disabled={claiming}
              className="w-full rounded-xl bg-primary px-6 py-3 text-sm font-bold text-white transition-colors hover:bg-primary/90 disabled:opacity-50"
            >
              {claiming ? "Preparing your draft..." : `Publish ${toolName} \u2192`}
            </button>
          ) : (
            <>
              <Link
                href={`/auth/register?returnTo=${encodeURIComponent(returnTo)}&invite=${encodeURIComponent(code)}`}
                className="block w-full rounded-xl bg-primary px-6 py-3 text-center text-sm font-bold text-white transition-colors hover:bg-primary/90"
              >
                Create account &amp; publish
              </Link>
              <Link
                href={`/auth/login?returnTo=${encodeURIComponent(returnTo)}`}
                className="block w-full rounded-xl border border-border px-6 py-3 text-center text-sm font-medium text-foreground transition-colors hover:bg-card"
              >
                Already have an account? Sign in
              </Link>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
