"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { fetchWithAuth } from "@/lib/api";

interface PostType {
  id: string;
  name: string;
  slug: string;
  url_prefix: string;
}

export default function NewPostPage() {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [slug, setSlug] = useState("");
  const [postTypeId, setPostTypeId] = useState("");
  const [postTypes, setPostTypes] = useState<PostType[]>([]);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    fetchWithAuth("/admin/blog/post-types")
      .then((res) => res.json())
      .then((data) => {
        setPostTypes(data);
        // Default to "Post" type
        const defaultType = data.find((pt: PostType) => pt.slug === "post");
        if (defaultType) setPostTypeId(defaultType.id);
      })
      .catch(() => {});
  }, []);

  const currentType = postTypes.find((pt) => pt.id === postTypeId);
  const urlPrefix = currentType?.url_prefix || "blog";

  async function handleCreate() {
    if (!title || !slug) return;
    setCreating(true);
    try {
      const body: Record<string, unknown> = { title, slug };
      if (postTypeId) body.post_type_id = postTypeId;

      const res = await fetchWithAuth("/admin/blog/posts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (res.ok) {
        const data = await res.json();
        router.push(`/admin/blog/${data.id}`);
      } else {
        const err = await res.json();
        alert(err.error?.message || "Failed to create post");
        setCreating(false);
      }
    } catch {
      alert("Failed to create post");
      setCreating(false);
    }
  }

  return (
    <div>
      <div className="mb-6 flex items-center gap-3">
        <button onClick={() => router.push("/admin/blog")} className="text-muted hover:text-foreground">
          ← Back
        </button>
        <h1 className="text-2xl font-bold">New Post</h1>
      </div>

      <div className="mx-auto max-w-lg space-y-4">
        <div>
          <label className="mb-1 block text-sm font-medium text-muted">Post Type</label>
          <select
            value={postTypeId}
            onChange={(e) => setPostTypeId(e.target.value)}
            className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          >
            {postTypes.map((pt) => (
              <option key={pt.id} value={pt.id}>{pt.name}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-sm font-medium text-muted">Title</label>
          <input
            value={title}
            onChange={(e) => {
              setTitle(e.target.value);
              setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, ""));
            }}
            placeholder="My awesome post"
            className="w-full rounded-md border border-border bg-background px-4 py-3 text-lg"
            autoFocus
          />
        </div>
        <div>
          <label className="mb-1 block text-sm font-medium text-muted">Slug</label>
          <div className="flex items-center gap-2 text-sm text-muted">
            <span>agentnode.net/{urlPrefix}/</span>
            <input
              value={slug}
              onChange={(e) => setSlug(e.target.value)}
              className="flex-1 rounded-md border border-border bg-background px-3 py-2 font-mono text-sm"
            />
          </div>
        </div>
        <button
          onClick={handleCreate}
          disabled={creating || !title || !slug}
          className="w-full rounded-md bg-primary py-3 text-sm font-medium text-background hover:bg-primary/90 disabled:opacity-50"
        >
          {creating ? "Creating..." : "Create Post & Open Editor"}
        </button>
      </div>
    </div>
  );
}
