"use client";

import { useState, useEffect, Suspense } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { fetchWithAuth } from "@/lib/api";

interface UserInfo {
  id: string;
  username: string;
  publisher?: { slug: string; display_name: string } | null;
}

function PublishContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const prefill = searchParams.get("manifest") || "";

  const [user, setUser] = useState<UserInfo | null>(null);
  const [authChecked, setAuthChecked] = useState(false);

  const [manifestText, setManifestText] = useState(prefill);
  const [artifact, setArtifact] = useState<File | null>(null);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [loading, setLoading] = useState(false);

  // Inline publisher creation
  const [pubSlug, setPubSlug] = useState("");
  const [pubDisplayName, setPubDisplayName] = useState("");
  const [creatingPublisher, setCreatingPublisher] = useState(false);

  useEffect(() => {
    fetchWithAuth("/auth/me")
      .then((res) => {
        if (!res.ok) throw new Error("unauth");
        return res.json();
      })
      .then((data) => setUser(data))
      .catch(() => setUser(null))
      .finally(() => setAuthChecked(true));
  }, []);

  async function createPublisher() {
    if (!pubSlug || !pubDisplayName) return;
    setCreatingPublisher(true);
    setError("");
    try {
      const res = await fetchWithAuth("/publishers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ display_name: pubDisplayName, slug: pubSlug }),
      });
      if (res.ok) {
        // Reload user to get publisher
        const meRes = await fetchWithAuth("/auth/me");
        if (meRes.ok) setUser(await meRes.json());
      } else {
        const data = await res.json();
        setError(data.error?.message || "Failed to create publisher");
      }
    } finally {
      setCreatingPublisher(false);
    }
  }

  async function handlePublish(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setSuccess("");
    setLoading(true);

    try {
      try { JSON.parse(manifestText); } catch {
        throw new Error("Invalid JSON in manifest");
      }

      const formData = new FormData();
      formData.append("manifest", manifestText);
      if (artifact) formData.append("artifact", artifact);

      const res = await fetchWithAuth("/packages/publish", {
        method: "POST",
        body: formData,
      });

      const data = await res.json();

      if (!res.ok) {
        const err = data.error || {};
        throw new Error(err.message || "Publish failed");
      }

      setSuccess(`Published ${data.slug}@${data.version}`);
      setTimeout(() => router.push(`/packages/${data.slug}`), 1500);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  if (!authChecked) {
    return <div className="mx-auto max-w-2xl px-4 py-24 text-center text-muted">Loading...</div>;
  }

  // --- NOT LOGGED IN: Show login gate ---
  if (!user) {
    const returnTo = prefill
      ? `/publish?manifest=${encodeURIComponent(prefill)}`
      : "/publish";

    return (
      <div className="mx-auto max-w-lg px-4 sm:px-6 py-24 text-center">
        <div className="mb-6 text-4xl">&#128640;</div>
        <h1 className="mb-3 text-2xl font-bold text-foreground">Publish on AgentNode</h1>
        <p className="mb-8 text-muted">
          Make your AI capability discoverable, installable, and usable across all agent frameworks.
        </p>

        <div className="mb-6 grid gap-3 text-left rounded-lg border border-border bg-card p-5 text-sm">
          <div className="flex items-start gap-3">
            <span className="mt-0.5 text-success">&#10003;</span>
            <span className="text-muted">Works across LangChain, CrewAI, MCP, and more</span>
          </div>
          <div className="flex items-start gap-3">
            <span className="mt-0.5 text-success">&#10003;</span>
            <span className="text-muted">Download counters, trust badges, and discovery</span>
          </div>
          <div className="flex items-start gap-3">
            <span className="mt-0.5 text-success">&#10003;</span>
            <span className="text-muted">Version management and artifact hosting</span>
          </div>
          <div className="flex items-start gap-3">
            <span className="mt-0.5 text-success">&#10003;</span>
            <span className="text-muted">CLI publishing: <code className="text-primary">agentnode publish</code></span>
          </div>
        </div>

        <div className="flex flex-col gap-3">
          <Link
            href={`/auth/register?returnTo=${encodeURIComponent(returnTo)}`}
            className="rounded-md bg-primary px-6 py-3 text-sm font-semibold text-white hover:bg-primary/90 transition-colors"
          >
            Create account to publish
          </Link>
          <Link
            href={`/auth/login?returnTo=${encodeURIComponent(returnTo)}`}
            className="rounded-md border border-border px-6 py-3 text-sm text-muted hover:text-foreground hover:border-primary/30 transition-colors"
          >
            Already have an account? Sign in
          </Link>
        </div>

        <p className="mt-8 text-xs text-muted">
          Don&apos;t have a manifest yet?{" "}
          <Link href="/import" className="text-primary hover:underline">Import your existing tools</Link>
        </p>
      </div>
    );
  }

  // --- LOGGED IN BUT NO PUBLISHER: Inline publisher creation ---
  if (!user.publisher) {
    return (
      <div className="mx-auto max-w-lg px-4 sm:px-6 py-16">
        <h1 className="mb-2 text-2xl font-bold text-foreground">Almost there!</h1>
        <p className="mb-6 text-muted text-sm">
          Create a publisher profile to start publishing packages. This takes 10 seconds.
        </p>

        {error && (
          <div className="mb-4 rounded-md border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">{error}</div>
        )}

        <div className="rounded-lg border border-border bg-card p-6">
          <div className="mb-4 flex items-center gap-3">
            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-primary/20 text-sm font-bold text-primary">1</div>
            <span className="text-sm font-medium text-foreground">Set up your publisher identity</span>
          </div>

          <div className="space-y-4">
            <div>
              <label className="mb-1 block text-sm text-muted">Display name</label>
              <input
                type="text"
                value={pubDisplayName}
                onChange={(e) => {
                  setPubDisplayName(e.target.value);
                  if (!pubSlug || pubSlug === pubDisplayName.toLowerCase().replace(/[^a-z0-9-]/g, "-").replace(/-+/g, "-")) {
                    setPubSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-").replace(/-+/g, "-"));
                  }
                }}
                className="w-full rounded-md border border-border bg-background px-3 py-2.5 text-foreground focus:border-primary focus:outline-none"
                placeholder="My Company"
              />
            </div>
            <div>
              <label className="mb-1 block text-sm text-muted">
                Slug <span className="text-muted/50">(used in package URLs: @your-slug)</span>
              </label>
              <input
                type="text"
                value={pubSlug}
                onChange={(e) => setPubSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ""))}
                className="w-full rounded-md border border-border bg-background px-3 py-2.5 font-mono text-foreground focus:border-primary focus:outline-none"
                placeholder="my-company"
                pattern="[a-z0-9-]+"
              />
            </div>
            <button
              onClick={createPublisher}
              disabled={creatingPublisher || !pubSlug || !pubDisplayName}
              className="w-full rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-white hover:bg-primary/90 disabled:opacity-50"
            >
              {creatingPublisher ? "Creating..." : "Create publisher & continue"}
            </button>
          </div>
        </div>
      </div>
    );
  }

  // --- FULLY AUTHENTICATED + PUBLISHER: Show publish form ---
  return (
    <div className="mx-auto max-w-2xl px-4 sm:px-6 py-12 overflow-hidden">
      <div className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="mb-1 text-2xl font-bold text-foreground">Publish a package</h1>
          <p className="text-sm text-muted">
            Publishing as <span className="text-primary font-medium">@{user.publisher.slug}</span>
          </p>
        </div>
      </div>

      {error && (
        <div className="mb-4 rounded-md border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">{error}</div>
      )}
      {success && (
        <div className="mb-4 rounded-md border border-success/30 bg-success/10 px-4 py-3 text-sm text-success">{success}</div>
      )}

      <form onSubmit={handlePublish} className="space-y-6">
        <div>
          <label className="mb-2 block text-sm font-medium text-foreground">Manifest (JSON)</label>
          <textarea
            required
            rows={16}
            value={manifestText}
            onChange={(e) => setManifestText(e.target.value)}
            className="w-full rounded-md border border-border bg-card px-4 py-3 font-mono text-sm text-foreground placeholder:text-muted/50 focus:border-primary focus:outline-none"
            placeholder='{"package_id": "my-pack", "version": "1.0.0", ...}'
            spellCheck={false}
          />
        </div>

        <div>
          <label className="mb-2 block text-sm font-medium text-foreground">
            Artifact <span className="text-muted">(optional .tar.gz)</span>
          </label>
          <input
            type="file"
            accept=".tar.gz,.tgz"
            onChange={(e) => setArtifact(e.target.files?.[0] || null)}
            className="block w-full text-sm text-muted file:mr-4 file:rounded file:border-0 file:bg-primary file:px-4 file:py-2 file:text-sm file:font-medium file:text-white hover:file:bg-primary/90"
          />
        </div>

        <button
          type="submit"
          disabled={loading || !manifestText}
          className="rounded-md bg-primary px-6 py-2.5 text-sm font-medium text-white transition-colors hover:bg-primary/90 disabled:opacity-50"
        >
          {loading ? "Publishing..." : "Publish"}
        </button>
      </form>

      <p className="mt-6 text-xs text-muted">
        Or publish via CLI: <code className="rounded bg-card px-1.5 py-0.5 text-primary">agentnode publish</code>
        {" "}&middot;{" "}
        <Link href="/import" className="text-primary hover:underline">Import from another platform</Link>
      </p>
    </div>
  );
}

export default function PublishPage() {
  return (
    <Suspense fallback={<div className="py-24 text-center text-muted">Loading...</div>}>
      <PublishContent />
    </Suspense>
  );
}
