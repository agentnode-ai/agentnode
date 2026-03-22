"use client";

import { useState, useEffect, useCallback } from "react";
import { fetchWithAuth } from "@/lib/api";

interface MediaImage {
  id: string;
  url: string;
  alt_text: string | null;
  file_size: number | null;
  width: number | null;
  height: number | null;
  post_id: string | null;
  created_at: string | null;
}

function formatBytes(bytes: number | null): string {
  if (!bytes) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function MediaPage() {
  const [images, setImages] = useState<MediaImage[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [selected, setSelected] = useState<MediaImage | null>(null);
  const [editAlt, setEditAlt] = useState("");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const perPage = 40;

  const loadImages = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(page), per_page: String(perPage) });
      if (search) params.set("search", search);
      const res = await fetchWithAuth(`/admin/blog/images?${params}`);
      const data = await res.json();
      setImages(data.images || []);
      setTotal(data.total || 0);
    } catch { /* ignore */ }
    setLoading(false);
  }, [page, search]);

  useEffect(() => { loadImages(); }, [loadImages]);

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const files = e.target.files;
    if (!files?.length) return;
    setUploading(true);
    for (const file of Array.from(files)) {
      const formData = new FormData();
      formData.append("file", file);
      try {
        await fetchWithAuth("/admin/blog/images/upload", { method: "POST", body: formData });
      } catch { /* ignore */ }
    }
    setUploading(false);
    e.target.value = "";
    loadImages();
  }

  function selectImage(img: MediaImage) {
    setSelected(img);
    setEditAlt(img.alt_text || "");
    setMessage("");
  }

  async function handleSaveAlt() {
    if (!selected) return;
    setSaving(true);
    try {
      const res = await fetchWithAuth(`/admin/blog/images/${selected.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ alt_text: editAlt }),
      });
      if (res.ok) {
        const updated = await res.json();
        setImages((prev) => prev.map((img) => (img.id === updated.id ? { ...img, alt_text: updated.alt_text } : img)));
        setSelected((prev) => (prev ? { ...prev, alt_text: updated.alt_text } : null));
        setMessage("Saved");
        setTimeout(() => setMessage(""), 2000);
      }
    } catch { /* ignore */ }
    setSaving(false);
  }

  async function handleDelete() {
    if (!selected || !confirm("Delete this image permanently?")) return;
    try {
      const res = await fetchWithAuth(`/admin/blog/images/${selected.id}`, { method: "DELETE" });
      if (res.ok) {
        setImages((prev) => prev.filter((img) => img.id !== selected.id));
        setTotal((prev) => prev - 1);
        setSelected(null);
      }
    } catch { /* ignore */ }
  }

  function copyUrl() {
    if (!selected) return;
    navigator.clipboard.writeText(selected.url);
    setMessage("URL copied");
    setTimeout(() => setMessage(""), 2000);
  }

  const totalPages = Math.ceil(total / perPage);

  return (
    <div>
      {/* Header */}
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Media Library</h1>
          <p className="mt-1 text-sm text-muted">{total} image{total !== 1 ? "s" : ""}</p>
        </div>
        <label className={`cursor-pointer rounded-md bg-primary px-4 py-2 text-sm font-medium text-background hover:bg-primary/90 ${uploading ? "opacity-50 pointer-events-none" : ""}`}>
          {uploading ? "Uploading..." : "Upload Images"}
          <input type="file" accept="image/*" multiple onChange={handleUpload} className="hidden" />
        </label>
      </div>

      {/* Search */}
      <div className="mb-4">
        <input
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(1); }}
          placeholder="Search by alt text..."
          className="w-full max-w-sm rounded-md border border-border bg-background px-3 py-2 text-sm placeholder:text-muted/50"
        />
      </div>

      <div className="flex gap-6">
        {/* Grid */}
        <div className="min-w-0 flex-1">
          {loading ? (
            <div className="py-16 text-center text-muted">Loading...</div>
          ) : images.length === 0 ? (
            <div className="py-16 text-center text-muted">
              {search ? "No images match your search." : "No images uploaded yet."}
            </div>
          ) : (
            <>
              <div className="grid grid-cols-3 gap-3 sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6">
                {images.map((img) => (
                  <button
                    key={img.id}
                    onClick={() => selectImage(img)}
                    className={`group relative aspect-square overflow-hidden rounded-lg border-2 transition-colors ${
                      selected?.id === img.id ? "border-primary" : "border-border hover:border-primary/40"
                    }`}
                  >
                    <img src={img.url} alt={img.alt_text || ""} className="h-full w-full object-cover" />
                    {!img.alt_text && (
                      <div className="absolute bottom-0 left-0 right-0 bg-yellow-500/80 px-1 py-0.5 text-center text-[10px] font-medium text-black">
                        No alt text
                      </div>
                    )}
                  </button>
                ))}
              </div>

              {/* Pagination */}
              {totalPages > 1 && (
                <div className="mt-6 flex items-center justify-center gap-2">
                  <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1} className="rounded border border-border px-3 py-1 text-sm disabled:opacity-30">
                    Prev
                  </button>
                  <span className="text-sm text-muted">{page} / {totalPages}</span>
                  <button onClick={() => setPage((p) => Math.min(totalPages, p + 1))} disabled={page === totalPages} className="rounded border border-border px-3 py-1 text-sm disabled:opacity-30">
                    Next
                  </button>
                </div>
              )}
            </>
          )}
        </div>

        {/* Detail sidebar */}
        {selected && (
          <div className="hidden w-72 shrink-0 space-y-4 lg:block">
            <div className="overflow-hidden rounded-lg border border-border">
              <img src={selected.url} alt={selected.alt_text || ""} className="w-full" />
            </div>

            <div className="space-y-3">
              <div>
                <label className="mb-1 block text-xs font-medium text-muted">Alt Text</label>
                <input
                  value={editAlt}
                  onChange={(e) => setEditAlt(e.target.value)}
                  placeholder="Describe the image"
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                />
              </div>

              <div className="flex gap-2">
                <button onClick={handleSaveAlt} disabled={saving} className="flex-1 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-background hover:bg-primary/90 disabled:opacity-50">
                  {saving ? "..." : "Save"}
                </button>
                <button onClick={copyUrl} className="rounded-md border border-border px-3 py-1.5 text-sm hover:bg-card">
                  Copy URL
                </button>
              </div>

              {message && <div className="text-center text-sm text-green-400">{message}</div>}

              <div className="space-y-1 text-xs text-muted">
                <div>Size: {formatBytes(selected.file_size)}</div>
                {selected.width && selected.height && (
                  <div>Dimensions: {selected.width} x {selected.height}</div>
                )}
                {selected.created_at && (
                  <div>Uploaded: {new Date(selected.created_at).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" })}</div>
                )}
              </div>

              <div className="border-t border-border pt-3">
                <button onClick={handleDelete} className="w-full rounded-md border border-danger/30 px-3 py-1.5 text-sm text-danger hover:bg-danger/10">
                  Delete Image
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
