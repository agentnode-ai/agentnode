"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

export default function PublishPage() {
  const router = useRouter();
  const [manifestText, setManifestText] = useState("");
  const [artifact, setArtifact] = useState<File | null>(null);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [loading, setLoading] = useState(false);

  function getToken(): string {
    return localStorage.getItem("access_token") || "";
  }

  async function handlePublish(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setSuccess("");
    setLoading(true);

    try {
      // Validate JSON first
      try {
        JSON.parse(manifestText);
      } catch {
        throw new Error("Invalid JSON in manifest");
      }

      const formData = new FormData();
      formData.append("manifest", manifestText);
      if (artifact) {
        formData.append("artifact", artifact);
      }

      const token = getToken();
      if (!token) {
        router.push("/auth/login");
        return;
      }

      const res = await fetch("/api/v1/packages/publish", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
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

  return (
    <div className="mx-auto max-w-2xl px-6 py-12">
      <h1 className="mb-2 text-2xl font-bold text-foreground">Publish a package</h1>
      <p className="mb-8 text-sm text-muted">
        Paste your ANP manifest (agentnode.yaml as JSON) and optionally upload an artifact.
      </p>

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

      <form onSubmit={handlePublish} className="space-y-6">
        <div>
          <label className="mb-2 block text-sm font-medium text-foreground">
            Manifest (JSON)
          </label>
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
    </div>
  );
}
