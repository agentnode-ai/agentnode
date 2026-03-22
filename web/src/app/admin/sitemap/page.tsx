"use client";

import { useState, useEffect } from "react";
import { fetchWithAuth } from "@/lib/api";

interface SitemapPage {
  id: string;
  path: string;
  priority: number;
  changefreq: string;
  indexable: boolean;
  sort_order: number;
}

const CHANGEFREQ_OPTIONS = ["always", "hourly", "daily", "weekly", "monthly", "yearly", "never"];

export default function AdminSitemapPage() {
  const [pages, setPages] = useState<SitemapPage[]>([]);
  const [loading, setLoading] = useState(true);

  // Create form
  const [newPath, setNewPath] = useState("");
  const [newPriority, setNewPriority] = useState("0.5");
  const [newChangefreq, setNewChangefreq] = useState("monthly");
  const [newIndexable, setNewIndexable] = useState(true);
  const [saving, setSaving] = useState(false);

  // Inline edit
  const [editId, setEditId] = useState<string | null>(null);
  const [editPriority, setEditPriority] = useState("");
  const [editChangefreq, setEditChangefreq] = useState("");
  const [editIndexable, setEditIndexable] = useState(true);
  const [editSaving, setEditSaving] = useState(false);

  useEffect(() => {
    loadPages();
  }, []);

  async function loadPages() {
    setLoading(true);
    try {
      const res = await fetchWithAuth("/admin/sitemap/pages");
      if (res.ok) setPages(await res.json());
    } catch { /* ignore */ }
    setLoading(false);
  }

  async function createPage() {
    if (!newPath) return;
    setSaving(true);
    const res = await fetchWithAuth("/admin/sitemap/pages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        path: newPath,
        priority: parseFloat(newPriority),
        changefreq: newChangefreq,
        indexable: newIndexable,
        sort_order: pages.length,
      }),
    });
    if (!res.ok) {
      const err = await res.json();
      alert(err.error?.message || "Failed to create page");
    } else {
      setNewPath("");
      setNewPriority("0.5");
      setNewChangefreq("monthly");
      setNewIndexable(true);
      loadPages();
    }
    setSaving(false);
  }

  function startEdit(page: SitemapPage) {
    setEditId(page.id);
    setEditPriority(String(page.priority));
    setEditChangefreq(page.changefreq);
    setEditIndexable(page.indexable);
  }

  async function saveEdit() {
    if (!editId) return;
    setEditSaving(true);
    const res = await fetchWithAuth(`/admin/sitemap/pages/${editId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        priority: parseFloat(editPriority),
        changefreq: editChangefreq,
        indexable: editIndexable,
      }),
    });
    if (res.ok) {
      setEditId(null);
      loadPages();
    }
    setEditSaving(false);
  }

  async function deletePage(id: string, path: string) {
    if (!confirm(`Remove "${path}" from sitemap?`)) return;
    await fetchWithAuth(`/admin/sitemap/pages/${id}`, { method: "DELETE" });
    loadPages();
  }

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-bold">Sitemap Pages</h1>
        <p className="mt-1 text-sm text-muted">
          Manage which pages appear in the sitemap. Archive pages (blog, tutorials, etc.) are managed under Post Types.
        </p>
      </div>

      {/* Create form */}
      <div className="mb-6 rounded-lg border border-border p-4">
        <h3 className="mb-3 text-sm font-semibold">Add Page</h3>
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-[200px] flex-1">
            <label className="mb-1 block text-xs text-muted">Path</label>
            <input
              value={newPath}
              onChange={(e) => setNewPath(e.target.value)}
              placeholder="/my-page"
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm font-mono"
            />
          </div>
          <div className="w-24">
            <label className="mb-1 block text-xs text-muted">Priority</label>
            <input
              type="number"
              min="0"
              max="1"
              step="0.1"
              value={newPriority}
              onChange={(e) => setNewPriority(e.target.value)}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
            />
          </div>
          <div className="w-32">
            <label className="mb-1 block text-xs text-muted">Change Freq</label>
            <select
              value={newChangefreq}
              onChange={(e) => setNewChangefreq(e.target.value)}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
            >
              {CHANGEFREQ_OPTIONS.map((o) => (
                <option key={o} value={o}>{o}</option>
              ))}
            </select>
          </div>
          <div className="flex items-center gap-2">
            <label className="flex items-center gap-1.5 text-sm">
              <input
                type="checkbox"
                checked={newIndexable}
                onChange={(e) => setNewIndexable(e.target.checked)}
                className="rounded"
              />
              Indexable
            </label>
          </div>
          <button
            onClick={createPage}
            disabled={saving || !newPath}
            className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-background disabled:opacity-50"
          >
            {saving ? "..." : "Add"}
          </button>
        </div>
      </div>

      {/* Pages table */}
      {loading ? (
        <div className="py-12 text-center text-muted">Loading...</div>
      ) : pages.length === 0 ? (
        <div className="py-12 text-center text-muted">No sitemap pages configured.</div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-card/50">
                <th className="px-4 py-3 text-left font-medium text-muted">Path</th>
                <th className="px-4 py-3 text-left font-medium text-muted">Priority</th>
                <th className="px-4 py-3 text-left font-medium text-muted">Change Freq</th>
                <th className="px-4 py-3 text-left font-medium text-muted">Indexable</th>
                <th className="px-4 py-3 text-right font-medium text-muted">Actions</th>
              </tr>
            </thead>
            <tbody>
              {pages.map((page) => (
                <tr key={page.id} className="border-b border-border last:border-0 hover:bg-card/30">
                  <td className="px-4 py-3 font-mono">{page.path}</td>
                  {editId === page.id ? (
                    <>
                      <td className="px-4 py-2">
                        <input
                          type="number"
                          min="0"
                          max="1"
                          step="0.1"
                          value={editPriority}
                          onChange={(e) => setEditPriority(e.target.value)}
                          className="w-20 rounded border border-border bg-background px-2 py-1 text-sm"
                        />
                      </td>
                      <td className="px-4 py-2">
                        <select
                          value={editChangefreq}
                          onChange={(e) => setEditChangefreq(e.target.value)}
                          className="rounded border border-border bg-background px-2 py-1 text-sm"
                        >
                          {CHANGEFREQ_OPTIONS.map((o) => (
                            <option key={o} value={o}>{o}</option>
                          ))}
                        </select>
                      </td>
                      <td className="px-4 py-2">
                        <input
                          type="checkbox"
                          checked={editIndexable}
                          onChange={(e) => setEditIndexable(e.target.checked)}
                          className="rounded"
                        />
                      </td>
                      <td className="px-4 py-2 text-right">
                        <button
                          onClick={saveEdit}
                          disabled={editSaving}
                          className="mr-2 text-green-400 hover:underline"
                        >
                          {editSaving ? "..." : "Save"}
                        </button>
                        <button
                          onClick={() => setEditId(null)}
                          className="text-muted hover:underline"
                        >
                          Cancel
                        </button>
                      </td>
                    </>
                  ) : (
                    <>
                      <td className="px-4 py-3 text-muted">{page.priority}</td>
                      <td className="px-4 py-3 text-muted">{page.changefreq}</td>
                      <td className="px-4 py-3">
                        {page.indexable ? (
                          <span className="text-green-400">Yes</span>
                        ) : (
                          <span className="text-red-400">No</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <button
                          onClick={() => startEdit(page)}
                          className="mr-3 text-primary hover:underline"
                        >
                          Edit
                        </button>
                        <button
                          onClick={() => deletePage(page.id, page.path)}
                          className="text-red-400 hover:underline"
                        >
                          Delete
                        </button>
                      </td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
