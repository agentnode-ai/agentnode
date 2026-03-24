"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { fetchWithAuth } from "@/lib/api";

interface PostType {
  id: string;
  name: string;
  slug: string;
  url_prefix: string;
  description: string | null;
  icon: string;
  has_archive: boolean;
  is_system: boolean;
  archive_title: string | null;
  archive_description: string | null;
  sitemap_priority: number;
  sitemap_changefreq: string;
  sort_order: number;
  post_count: number;
}

interface Post {
  id: string;
  title: string;
  slug: string;
  status: string;
  category: { id: string; name: string; slug: string } | null;
  post_type: { id: string; name: string; slug: string; url_prefix: string } | null;
  author: { id: string; username: string };
  published_at: string | null;
  is_featured: boolean;
  updated_at: string;
}

interface Category {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  sort_order: number;
  post_count: number;
}

export default function AdminBlogPage() {
  const [posts, setPosts] = useState<Post[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [postTypes, setPostTypes] = useState<PostType[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<"posts" | "categories" | "post-types">("posts");

  // Category form
  const [catName, setCatName] = useState("");
  const [catSlug, setCatSlug] = useState("");
  const [catDesc, setCatDesc] = useState("");
  const [catSaving, setCatSaving] = useState(false);

  // Post Type form
  const [ptName, setPtName] = useState("");
  const [ptSlug, setPtSlug] = useState("");
  const [ptPrefix, setPtPrefix] = useState("");
  const [ptDesc, setPtDesc] = useState("");
  const [ptSaving, setPtSaving] = useState(false);

  useEffect(() => {
    loadPosts();
    loadCategories();
    loadPostTypes();
  }, [page, statusFilter]);

  async function loadPosts() {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(page), per_page: "20" });
      if (statusFilter) params.set("status", statusFilter);
      const res = await fetchWithAuth(`/admin/blog/posts?${params}`);
      const data = await res.json();
      setPosts(data.posts);
      setTotal(data.total);
    } catch {
      /* ignore */
    }
    setLoading(false);
  }

  async function loadCategories() {
    try {
      const res = await fetchWithAuth("/admin/blog/categories");
      setCategories(await res.json());
    } catch {
      /* ignore */
    }
  }

  async function loadPostTypes() {
    try {
      const res = await fetchWithAuth("/admin/blog/post-types");
      setPostTypes(await res.json());
    } catch {
      /* ignore */
    }
  }

  async function revalidateCache(paths: string[]) {
    try {
      await fetch("/api/revalidate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ paths }),
      });
    } catch { /* best effort */ }
  }

  async function deletePost(id: string, title: string) {
    if (!confirm(`Delete "${title}"?`)) return;
    const post = posts.find((p) => p.id === id);
    await fetchWithAuth(`/admin/blog/posts/${id}`, { method: "DELETE" });
    loadPosts();
    const prefix = post?.post_type?.url_prefix || "blog";
    revalidateCache(["/blog", `/${prefix}/${post?.slug}`]);
  }

  async function createCategory() {
    if (!catName || !catSlug) return;
    setCatSaving(true);
    await fetchWithAuth("/admin/blog/categories", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: catName, slug: catSlug, description: catDesc || null }),
    });
    setCatName("");
    setCatSlug("");
    setCatDesc("");
    setCatSaving(false);
    loadCategories();
  }

  async function deleteCategory(id: string, name: string) {
    if (!confirm(`Delete category "${name}"? Posts in this category will become uncategorized.`)) return;
    await fetchWithAuth(`/admin/blog/categories/${id}`, { method: "DELETE" });
    loadCategories();
  }

  async function createPostType() {
    if (!ptName || !ptSlug || !ptPrefix) return;
    setPtSaving(true);
    const res = await fetchWithAuth("/admin/blog/post-types", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: ptName, slug: ptSlug, url_prefix: ptPrefix,
        description: ptDesc || null,
      }),
    });
    if (!res.ok) {
      const err = await res.json();
      alert(err.error?.message || "Failed to create post type");
    } else {
      setPtName("");
      setPtSlug("");
      setPtPrefix("");
      setPtDesc("");
      loadPostTypes();
    }
    setPtSaving(false);
  }

  async function deletePostType(id: string, name: string) {
    if (!confirm(`Delete post type "${name}"?`)) return;
    const res = await fetchWithAuth(`/admin/blog/post-types/${id}`, { method: "DELETE" });
    if (!res.ok) {
      const err = await res.json();
      alert(err.error?.message || "Cannot delete post type");
    } else {
      loadPostTypes();
    }
  }

  const totalPages = Math.ceil(total / 20);

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold">Blog</h1>
        <Link
          href="/admin/blog/new"
          className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-background hover:bg-primary/90 transition-colors"
        >
          + New Post
        </Link>
      </div>

      {/* Tabs */}
      <div className="mb-6 flex gap-4 border-b border-border">
        <button
          onClick={() => setTab("posts")}
          className={`pb-2 text-sm font-medium transition-colors ${tab === "posts" ? "border-b-2 border-primary text-primary" : "text-muted hover:text-foreground"}`}
        >
          Posts ({total})
        </button>
        <button
          onClick={() => setTab("categories")}
          className={`pb-2 text-sm font-medium transition-colors ${tab === "categories" ? "border-b-2 border-primary text-primary" : "text-muted hover:text-foreground"}`}
        >
          Categories ({categories.length})
        </button>
        <button
          onClick={() => setTab("post-types")}
          className={`pb-2 text-sm font-medium transition-colors ${tab === "post-types" ? "border-b-2 border-primary text-primary" : "text-muted hover:text-foreground"}`}
        >
          Post Types ({postTypes.length})
        </button>
      </div>

      {tab === "posts" && (
        <>
          {/* Filters */}
          <div className="mb-4 flex gap-2">
            {["", "draft", "published", "archived"].map((s) => (
              <button
                key={s}
                onClick={() => { setStatusFilter(s); setPage(1); }}
                className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${statusFilter === s ? "bg-primary text-background" : "bg-card text-muted hover:text-foreground"}`}
              >
                {s || "All"}
              </button>
            ))}
          </div>

          {loading ? (
            <div className="py-12 text-center text-muted">Loading...</div>
          ) : posts.length === 0 ? (
            <div className="py-12 text-center text-muted">
              No posts yet.{" "}
              <Link href="/admin/blog/new" className="text-primary hover:underline">
                Create your first post
              </Link>
            </div>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-border">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-card/50">
                    <th className="px-4 py-3 text-left font-medium text-muted">Title</th>
                    <th className="px-4 py-3 text-left font-medium text-muted">Status</th>
                    <th className="px-4 py-3 text-left font-medium text-muted">Type</th>
                    <th className="px-4 py-3 text-left font-medium text-muted">Category</th>
                    <th className="px-4 py-3 text-left font-medium text-muted">Updated</th>
                    <th className="px-4 py-3 text-right font-medium text-muted">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {posts.map((post) => (
                    <tr key={post.id} className="border-b border-border last:border-0 hover:bg-card/30">
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          {post.is_featured && <span className="text-yellow-500" title="Featured">★</span>}
                          <Link href={`/admin/blog/${post.id}`} className="font-medium hover:text-primary">
                            {post.title}
                          </Link>
                        </div>
                        <div className="mt-0.5 text-xs text-muted">
                          /{post.post_type?.url_prefix || "blog"}/{post.slug}
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${
                          post.status === "published" ? "bg-green-500/10 text-green-400" :
                          post.status === "draft" ? "bg-yellow-500/10 text-yellow-400" :
                          "bg-red-500/10 text-red-400"
                        }`}>
                          {post.status}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-muted">{post.post_type?.name || "Post"}</td>
                      <td className="px-4 py-3 text-muted">{post.category?.name || "—"}</td>
                      <td className="px-4 py-3 text-muted">{new Date(post.updated_at).toLocaleDateString()}</td>
                      <td className="px-4 py-3 text-right">
                        <Link href={`/admin/blog/${post.id}`} className="mr-3 text-primary hover:underline">
                          Edit
                        </Link>
                        <button onClick={() => deletePost(post.id, post.title)} className="text-red-400 hover:underline">
                          Delete
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="mt-4 flex items-center justify-center gap-2">
              <button
                onClick={() => setPage(Math.max(1, page - 1))}
                disabled={page === 1}
                className="rounded-md bg-card px-3 py-1.5 text-sm disabled:opacity-50"
              >
                Prev
              </button>
              <span className="text-sm text-muted">
                {page} / {totalPages}
              </span>
              <button
                onClick={() => setPage(Math.min(totalPages, page + 1))}
                disabled={page === totalPages}
                className="rounded-md bg-card px-3 py-1.5 text-sm disabled:opacity-50"
              >
                Next
              </button>
            </div>
          )}
        </>
      )}

      {tab === "categories" && (
        <div className="space-y-6">
          {/* Create category form */}
          <div className="rounded-lg border border-border p-4">
            <h3 className="mb-3 text-sm font-semibold">New Category</h3>
            <div className="flex flex-wrap gap-3">
              <input
                value={catName}
                onChange={(e) => {
                  setCatName(e.target.value);
                  setCatSlug(e.target.value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, ""));
                }}
                placeholder="Name"
                className="rounded-md border border-border bg-background px-3 py-2 text-sm"
              />
              <input
                value={catSlug}
                onChange={(e) => setCatSlug(e.target.value)}
                placeholder="slug"
                className="rounded-md border border-border bg-background px-3 py-2 text-sm font-mono"
              />
              <input
                value={catDesc}
                onChange={(e) => setCatDesc(e.target.value)}
                placeholder="Description (optional)"
                className="flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm"
              />
              <button
                onClick={createCategory}
                disabled={catSaving || !catName || !catSlug}
                className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-background disabled:opacity-50"
              >
                {catSaving ? "..." : "Add"}
              </button>
            </div>
          </div>

          {/* Categories list */}
          {categories.length === 0 ? (
            <div className="py-8 text-center text-muted">No categories yet.</div>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-border">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-card/50">
                    <th className="px-4 py-3 text-left font-medium text-muted">Name</th>
                    <th className="px-4 py-3 text-left font-medium text-muted">Slug</th>
                    <th className="px-4 py-3 text-left font-medium text-muted">Posts</th>
                    <th className="px-4 py-3 text-right font-medium text-muted">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {categories.map((cat) => (
                    <tr key={cat.id} className="border-b border-border last:border-0">
                      <td className="px-4 py-3 font-medium">{cat.name}</td>
                      <td className="px-4 py-3 font-mono text-muted">{cat.slug}</td>
                      <td className="px-4 py-3 text-muted">{cat.post_count}</td>
                      <td className="px-4 py-3 text-right">
                        <button onClick={() => deleteCategory(cat.id, cat.name)} className="text-red-400 hover:underline">
                          Delete
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {tab === "post-types" && (
        <div className="space-y-6">
          {/* Create post type form */}
          <div className="rounded-lg border border-border p-4">
            <h3 className="mb-3 text-sm font-semibold">New Post Type</h3>
            <div className="flex flex-wrap gap-3">
              <input
                value={ptName}
                onChange={(e) => {
                  setPtName(e.target.value);
                  const s = e.target.value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
                  setPtSlug(s);
                  setPtPrefix(s + "s");
                }}
                placeholder="Name"
                className="rounded-md border border-border bg-background px-3 py-2 text-sm"
              />
              <input
                value={ptSlug}
                onChange={(e) => setPtSlug(e.target.value)}
                placeholder="slug"
                className="rounded-md border border-border bg-background px-3 py-2 text-sm font-mono"
              />
              <input
                value={ptPrefix}
                onChange={(e) => setPtPrefix(e.target.value)}
                placeholder="url-prefix"
                className="rounded-md border border-border bg-background px-3 py-2 text-sm font-mono"
              />
              <input
                value={ptDesc}
                onChange={(e) => setPtDesc(e.target.value)}
                placeholder="Description (optional)"
                className="flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm"
              />
              <button
                onClick={createPostType}
                disabled={ptSaving || !ptName || !ptSlug || !ptPrefix}
                className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-background disabled:opacity-50"
              >
                {ptSaving ? "..." : "Add"}
              </button>
            </div>
          </div>

          {/* Post types list */}
          {postTypes.length === 0 ? (
            <div className="py-8 text-center text-muted">No post types yet.</div>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-border">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-card/50">
                    <th className="px-4 py-3 text-left font-medium text-muted">Name</th>
                    <th className="px-4 py-3 text-left font-medium text-muted">Slug</th>
                    <th className="px-4 py-3 text-left font-medium text-muted">URL Prefix</th>
                    <th className="px-4 py-3 text-left font-medium text-muted">Posts</th>
                    <th className="px-4 py-3 text-left font-medium text-muted">System</th>
                    <th className="px-4 py-3 text-left font-medium text-muted">Priority</th>
                    <th className="px-4 py-3 text-right font-medium text-muted">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {postTypes.map((pt) => (
                    <tr key={pt.id} className="border-b border-border last:border-0">
                      <td className="px-4 py-3 font-medium">{pt.name}</td>
                      <td className="px-4 py-3 font-mono text-muted">{pt.slug}</td>
                      <td className="px-4 py-3 font-mono text-muted">/{pt.url_prefix}</td>
                      <td className="px-4 py-3 text-muted">{pt.post_count}</td>
                      <td className="px-4 py-3 text-muted">{pt.is_system ? "Yes" : "No"}</td>
                      <td className="px-4 py-3 text-muted">{pt.sitemap_priority}</td>
                      <td className="px-4 py-3 text-right">
                        {!pt.is_system && pt.post_count === 0 ? (
                          <button onClick={() => deletePostType(pt.id, pt.name)} className="text-red-400 hover:underline">
                            Delete
                          </button>
                        ) : (
                          <span className="text-xs text-muted">
                            {pt.is_system ? "System" : "Has posts"}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
