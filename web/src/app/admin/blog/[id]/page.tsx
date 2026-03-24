"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useRouter, useParams } from "next/navigation";
import dynamic from "next/dynamic";
import { fetchWithAuth } from "@/lib/api";
import MediaLibraryModal from "@/components/blog/MediaLibraryModal";

const TipTapEditor = dynamic(() => import("@/components/blog/TipTapEditor"), { ssr: false });

interface Category {
  id: string;
  name: string;
  slug: string;
}

interface PostType {
  id: string;
  name: string;
  slug: string;
  url_prefix: string;
}

export default function EditPostPage() {
  const router = useRouter();
  const params = useParams();
  const postId = params.id as string;

  const [title, setTitle] = useState("");
  const [slug, setSlug] = useState("");
  const [contentJson, setContentJson] = useState<object | null>(null);
  const [contentHtml, setContentHtml] = useState("");
  const [excerpt, setExcerpt] = useState("");
  const [coverImageUrl, setCoverImageUrl] = useState("");
  const [coverImageAlt, setCoverImageAlt] = useState("");
  const [seoTitle, setSeoTitle] = useState("");
  const [seoDescription, setSeoDescription] = useState("");
  const [ogImageUrl, setOgImageUrl] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [tags, setTags] = useState("");
  const [isFeatured, setIsFeatured] = useState(false);
  const [status, setStatus] = useState("draft");
  const [postTypeId, setPostTypeId] = useState("");
  const [originalPostTypeId, setOriginalPostTypeId] = useState("");

  const [categories, setCategories] = useState<Category[]>([]);
  const [postTypes, setPostTypes] = useState<PostType[]>([]);
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");
  const [editorReady, setEditorReady] = useState(false);
  const [mediaOpen, setMediaOpen] = useState(false);
  const [dragging, setDragging] = useState(false);
  const dragCounter = useRef(0);

  const loadCategories = useCallback(async () => {
    try {
      const res = await fetchWithAuth("/admin/blog/categories");
      setCategories(await res.json());
    } catch { /* ignore */ }
  }, []);

  const loadPostTypes = useCallback(async () => {
    try {
      const res = await fetchWithAuth("/admin/blog/post-types");
      setPostTypes(await res.json());
    } catch { /* ignore */ }
  }, []);

  const loadPost = useCallback(async () => {
    try {
      const res = await fetchWithAuth(`/admin/blog/posts/${postId}`);
      if (!res.ok) { router.push("/admin/blog"); return; }
      const data = await res.json();
      setTitle(data.title || "");
      setSlug(data.slug || "");
      setContentJson(data.content_json);
      setContentHtml(data.content_html || "");
      setExcerpt(data.excerpt || "");
      setCoverImageUrl(data.cover_image_url || "");
      setCoverImageAlt(data.cover_image_alt || "");
      setSeoTitle(data.seo_title || "");
      setSeoDescription(data.seo_description || "");
      setOgImageUrl(data.og_image_url || "");
      setCategoryId(data.category?.id || "");
      setTags((data.tags || []).join(", "));
      setIsFeatured(data.is_featured || false);
      setStatus(data.status || "draft");
      setPostTypeId(data.post_type?.id || "");
      setOriginalPostTypeId(data.post_type?.id || "");
    } catch { /* ignore */ }
    setLoading(false);
    setEditorReady(true);
  }, [postId, router]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- Load post data on mount
    loadCategories();
    loadPostTypes();
    loadPost();
  }, [loadCategories, loadPostTypes, loadPost]);

  const handleEditorChange = useCallback((json: object, html: string) => {
    setContentJson(json);
    setContentHtml(html);
  }, []);

  // Get current post type for URL preview
  const currentPostType = postTypes.find((pt) => pt.id === postTypeId);
  const urlPrefix = currentPostType?.url_prefix || "blog";

  /** Bust Next.js data cache for blog pages after mutations */
  async function revalidateCache() {
    const paths = [`/blog`, `/${urlPrefix}`, `/${urlPrefix}/${slug}`];
    if (urlPrefix !== "blog") paths.push(`/blog/${slug}`);
    try {
      await fetch("/api/revalidate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ paths }),
      });
    } catch { /* best effort */ }
  }

  async function handleSave() {
    setSaving(true);
    setMessage("");
    const body: Record<string, unknown> = {
      title,
      slug,
      content_json: contentJson,
      content_html: contentHtml,
      excerpt: excerpt || null,
      cover_image_url: coverImageUrl || null,
      cover_image_alt: coverImageAlt || null,
      seo_title: seoTitle || null,
      seo_description: seoDescription || null,
      og_image_url: ogImageUrl || null,
      category_id: categoryId || null,
      tags: tags ? tags.split(",").map((t) => t.trim()).filter(Boolean) : [],
      is_featured: isFeatured,
    };

    // Only send post_type_id if it changed
    if (postTypeId && postTypeId !== originalPostTypeId) {
      body.post_type_id = postTypeId;
    }

    try {
      const res = await fetchWithAuth(`/admin/blog/posts/${postId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = await res.json();
        setMessage(err.error?.message || "Save failed");
      } else {
        const data = await res.json();
        setOriginalPostTypeId(data.post_type?.id || "");
        setMessage("Saved!");
        setTimeout(() => setMessage(""), 2000);
        revalidateCache();
      }
    } catch {
      setMessage("Save failed");
    }
    setSaving(false);
  }

  async function handlePublish() {
    setSaving(true);
    await handleSave();
    const res = await fetchWithAuth(`/admin/blog/posts/${postId}/publish`, { method: "POST" });
    if (res.ok) {
      setStatus("published");
      setMessage("Published!");
      setTimeout(() => setMessage(""), 2000);
    }
    setSaving(false);
  }

  async function handleUnpublish() {
    const res = await fetchWithAuth(`/admin/blog/posts/${postId}/unpublish`, { method: "POST" });
    if (res.ok) {
      setStatus("draft");
      setMessage("Unpublished");
      setTimeout(() => setMessage(""), 2000);
      revalidateCache();
    }
  }

  async function uploadCoverFile(file: File) {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("post_id", postId);
    try {
      const res = await fetchWithAuth(`/admin/blog/images/upload?post_id=${postId}`, {
        method: "POST",
        body: formData,
      });
      const data = await res.json();
      if (data.url) setCoverImageUrl(data.url);
    } catch {
      alert("Upload failed");
    }
  }

  function handleCoverUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) uploadCoverFile(file);
  }

  function handleCoverDrop(e: React.DragEvent) {
    e.preventDefault();
    e.stopPropagation();
    setDragging(false);
    dragCounter.current = 0;
    const file = e.dataTransfer.files?.[0];
    if (file && file.type.startsWith("image/")) uploadCoverFile(file);
  }

  function handleDragEnter(e: React.DragEvent) {
    e.preventDefault();
    e.stopPropagation();
    dragCounter.current++;
    setDragging(true);
  }

  function handleDragLeave(e: React.DragEvent) {
    e.preventDefault();
    e.stopPropagation();
    dragCounter.current--;
    if (dragCounter.current === 0) setDragging(false);
  }

  function handleDragOver(e: React.DragEvent) {
    e.preventDefault();
    e.stopPropagation();
  }

  function handleMediaSelect(url: string, alt: string) {
    setCoverImageUrl(url);
    if (alt && !coverImageAlt) setCoverImageAlt(alt);
  }

  if (loading) {
    return <div className="flex min-h-[40vh] items-center justify-center text-muted">Loading...</div>;
  }

  const typeChanged = postTypeId !== originalPostTypeId;

  return (
    <div>
      {/* Header */}
      <div className="mb-6 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button onClick={() => router.push("/admin/blog")} className="text-muted hover:text-foreground">
            ← Back
          </button>
          <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${
            status === "published" ? "bg-green-500/10 text-green-400" : "bg-yellow-500/10 text-yellow-400"
          }`}>
            {status}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {message && <span className="text-sm text-green-400">{message}</span>}
          {status === "published" && (
            <button onClick={handleUnpublish} className="rounded-md border border-border px-3 py-1.5 text-sm text-muted hover:text-foreground">
              Unpublish
            </button>
          )}
          <button onClick={handleSave} disabled={saving} className="rounded-md border border-border px-4 py-1.5 text-sm font-medium hover:bg-card disabled:opacity-50">
            {saving ? "..." : "Save Draft"}
          </button>
          <button onClick={handlePublish} disabled={saving} className="rounded-md bg-primary px-4 py-1.5 text-sm font-medium text-background hover:bg-primary/90 disabled:opacity-50">
            {saving ? "..." : "Publish"}
          </button>
        </div>
      </div>

      <div className="flex gap-6">
        {/* Main content */}
        <div className="min-w-0 flex-1 space-y-4">
          {/* Title */}
          <input
            value={title}
            onChange={(e) => {
              setTitle(e.target.value);
              if (!slug || slug === title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "")) {
                setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, ""));
              }
            }}
            placeholder="Post title"
            className="w-full border-0 bg-transparent text-2xl font-bold placeholder:text-muted/50 focus:outline-none"
          />

          {/* Cover image */}
          <div>
            {coverImageUrl ? (
              <div className="relative">
                <img src={coverImageUrl} alt={coverImageAlt} className="max-h-64 w-full rounded-lg object-cover" onError={() => setCoverImageUrl("")} />
                <button
                  onClick={() => setCoverImageUrl("")}
                  className="absolute right-2 top-2 rounded-full bg-background/80 px-2 py-0.5 text-xs text-muted hover:text-foreground"
                >
                  Remove
                </button>
              </div>
            ) : (
              <div
                onDragEnter={handleDragEnter}
                onDragLeave={handleDragLeave}
                onDragOver={handleDragOver}
                onDrop={handleCoverDrop}
                className={`flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed py-8 transition-colors ${
                  dragging ? "border-primary bg-primary/5 text-foreground" : "border-border text-muted hover:border-primary hover:text-foreground"
                }`}
              >
                <label className="cursor-pointer text-sm">
                  {dragging ? "Drop image here" : "Drop or click to upload cover image"}
                  <input type="file" accept="image/*" onChange={handleCoverUpload} className="hidden" />
                </label>
                <button
                  type="button"
                  onClick={() => setMediaOpen(true)}
                  className="text-xs text-primary hover:underline"
                >
                  or choose from media library
                </button>
              </div>
            )}
          </div>

          <MediaLibraryModal open={mediaOpen} onClose={() => setMediaOpen(false)} onSelect={handleMediaSelect} />

          {/* Editor */}
          {editorReady && (
            <TipTapEditor content={contentJson} contentHtml={contentHtml} onChange={handleEditorChange} />
          )}

          {/* Excerpt */}
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Excerpt</label>
            <textarea
              value={excerpt}
              onChange={(e) => setExcerpt(e.target.value)}
              maxLength={500}
              rows={3}
              placeholder="Brief summary for cards and previews..."
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
            />
            <div className="mt-1 text-right text-xs text-muted">{excerpt.length}/500</div>
          </div>
        </div>

        {/* Sidebar */}
        <div className="hidden w-72 shrink-0 space-y-5 lg:block">
          {/* Post Type */}
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Post Type</label>
            <select
              value={postTypeId}
              onChange={(e) => setPostTypeId(e.target.value)}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
            >
              {postTypes.map((pt) => (
                <option key={pt.id} value={pt.id}>{pt.name}</option>
              ))}
            </select>
            {typeChanged && (
              <div className="mt-1.5 rounded-md bg-yellow-500/10 px-2 py-1.5 text-xs text-yellow-400">
                Changing post type will redirect the old URL to the new one
              </div>
            )}
          </div>

          {/* URL Preview */}
          <div className="rounded-md border border-border bg-card/50 px-3 py-2">
            <div className="text-xs font-medium text-muted">Final URL</div>
            <div className="mt-0.5 font-mono text-sm text-foreground truncate">
              /{urlPrefix}/{slug || "post-slug"}
            </div>
          </div>

          {/* Category */}
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Category</label>
            <select
              value={categoryId}
              onChange={(e) => setCategoryId(e.target.value)}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
            >
              <option value="">None</option>
              {categories.map((cat) => (
                <option key={cat.id} value={cat.id}>{cat.name}</option>
              ))}
            </select>
          </div>

          {/* Tags */}
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Tags (comma-separated)</label>
            <input
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              placeholder="ai, agents, tutorial"
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
            />
          </div>

          {/* Slug */}
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Slug</label>
            <input
              value={slug}
              onChange={(e) => setSlug(e.target.value)}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm font-mono"
            />
          </div>

          {/* Featured */}
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={isFeatured} onChange={(e) => setIsFeatured(e.target.checked)} className="rounded" />
            Featured post
          </label>

          {/* SEO Section */}
          <div className="border-t border-border pt-4">
            <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted">SEO</h3>

            <div className="space-y-3">
              <div>
                <label className="mb-1 block text-xs font-medium text-muted">SEO Title</label>
                <input
                  value={seoTitle}
                  onChange={(e) => setSeoTitle(e.target.value)}
                  maxLength={70}
                  placeholder={title || "SEO title"}
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                />
                <div className={`mt-1 text-right text-xs ${seoTitle.length > 60 ? "text-yellow-400" : "text-muted"}`}>
                  {seoTitle.length}/70
                </div>
              </div>

              <div>
                <label className="mb-1 block text-xs font-medium text-muted">SEO Description</label>
                <textarea
                  value={seoDescription}
                  onChange={(e) => setSeoDescription(e.target.value)}
                  maxLength={170}
                  rows={3}
                  placeholder="Meta description for search engines..."
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                />
                <div className={`mt-1 text-right text-xs ${seoDescription.length > 160 ? "text-yellow-400" : "text-muted"}`}>
                  {seoDescription.length}/170
                </div>
              </div>

              <div>
                <label className="mb-1 block text-xs font-medium text-muted">OG Image URL</label>
                <input
                  value={ogImageUrl}
                  onChange={(e) => setOgImageUrl(e.target.value)}
                  placeholder="Defaults to cover image"
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                />
              </div>

              <div>
                <label className="mb-1 block text-xs font-medium text-muted">Cover Image Alt</label>
                <input
                  value={coverImageAlt}
                  onChange={(e) => setCoverImageAlt(e.target.value)}
                  placeholder="Describe the image"
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                />
              </div>
            </div>
          </div>

          {/* Google Preview */}
          <div className="border-t border-border pt-4">
            <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted">Google Preview</h3>
            <div className="rounded-lg border border-border p-3">
              <div className="truncate text-sm font-medium text-blue-400">
                {seoTitle || title || "Post title"}
              </div>
              <div className="truncate text-xs text-green-400">
                agentnode.net/{urlPrefix}/{slug || "post-slug"}
              </div>
              <div className="mt-1 line-clamp-2 text-xs text-muted">
                {seoDescription || excerpt || "Post description will appear here..."}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
