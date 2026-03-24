"use client";

import { useState, useEffect, useCallback } from "react";
import { fetchWithAuth } from "@/lib/api";

interface MediaImage {
  id: string;
  url: string;
  alt_text: string | null;
  file_size: number | null;
  title: string | null;
  original_filename: string | null;
  caption: string | null;
  created_at: string | null;
}

interface MediaLibraryModalProps {
  open: boolean;
  onClose: () => void;
  onSelect: (url: string, alt: string) => void;
}

export default function MediaLibraryModal({ open, onClose, onSelect }: MediaLibraryModalProps) {
  const [images, setImages] = useState<MediaImage[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [selected, setSelected] = useState<MediaImage | null>(null);
  const [tab, setTab] = useState<"library" | "upload">("library");
  const perPage = 30;

  const loadImages = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetchWithAuth(`/admin/blog/images?page=${page}&per_page=${perPage}`);
      const data = await res.json();
      setImages(data.images || []);
      setTotal(data.total || 0);
    } catch { /* ignore */ }
    setLoading(false);
  }, [page]);

  useEffect(() => {
    if (open) {
      loadImages();
      setSelected(null);
    }
  }, [open, loadImages]);

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const files = e.target.files;
    if (!files?.length) return;
    setUploading(true);
    for (const file of Array.from(files)) {
      const formData = new FormData();
      formData.append("file", file);
      try {
        const res = await fetchWithAuth("/admin/blog/images/upload", { method: "POST", body: formData });
        if (res.ok) {
          const img = await res.json();
          setImages((prev) => [img, ...prev]);
          setTotal((prev) => prev + 1);
        }
      } catch { /* ignore */ }
    }
    setUploading(false);
    e.target.value = "";
    setTab("library");
  }

  function handleInsert() {
    if (!selected) return;
    onSelect(selected.url, selected.alt_text || "");
    onClose();
  }

  if (!open) return null;

  const totalPages = Math.ceil(total / perPage);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="mx-4 flex h-[80vh] w-full max-w-4xl flex-col overflow-hidden rounded-xl border border-border bg-background shadow-2xl" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <div className="flex items-center gap-4">
            <h2 className="text-lg font-semibold">Media Library</h2>
            <div className="flex rounded-md border border-border">
              <button
                onClick={() => setTab("library")}
                className={`px-3 py-1 text-xs font-medium transition-colors ${tab === "library" ? "bg-card text-foreground" : "text-muted hover:text-foreground"}`}
              >
                Library
              </button>
              <button
                onClick={() => setTab("upload")}
                className={`px-3 py-1 text-xs font-medium transition-colors ${tab === "upload" ? "bg-card text-foreground" : "text-muted hover:text-foreground"}`}
              >
                Upload
              </button>
            </div>
          </div>
          <button onClick={onClose} className="text-muted hover:text-foreground">&times;</button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-5">
          {tab === "upload" ? (
            <div className="flex h-full items-center justify-center">
              <label className={`flex cursor-pointer flex-col items-center gap-3 rounded-xl border-2 border-dashed border-border px-16 py-16 text-center transition-colors hover:border-primary ${uploading ? "opacity-50 pointer-events-none" : ""}`}>
                <div className="text-4xl text-muted">+</div>
                <div className="text-sm font-medium">{uploading ? "Uploading..." : "Drop files or click to upload"}</div>
                <div className="text-xs text-muted">PNG, JPG, GIF, WebP — Max 10 MB</div>
                <input type="file" accept="image/*" multiple onChange={handleUpload} className="hidden" />
              </label>
            </div>
          ) : loading ? (
            <div className="flex h-full items-center justify-center text-muted">Loading...</div>
          ) : images.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center gap-2 text-muted">
              <p>No images yet.</p>
              <button onClick={() => setTab("upload")} className="text-sm text-primary hover:underline">Upload your first image</button>
            </div>
          ) : (
            <>
              <div className="grid grid-cols-4 gap-3 sm:grid-cols-5 md:grid-cols-6">
                {images.map((img) => (
                  <button
                    key={img.id}
                    onClick={() => setSelected(img)}
                    className={`group relative aspect-square overflow-hidden rounded-lg border-2 transition-all ${
                      selected?.id === img.id ? "border-primary ring-2 ring-primary/30" : "border-border hover:border-primary/40"
                    }`}
                  >
                    <img src={img.url} alt={img.alt_text || ""} className="h-full w-full object-cover" />
                    {selected?.id === img.id && (
                      <div className="absolute inset-0 bg-primary/10" />
                    )}
                  </button>
                ))}
              </div>

              {totalPages > 1 && (
                <div className="mt-4 flex items-center justify-center gap-2">
                  <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1} className="rounded border border-border px-3 py-1 text-xs disabled:opacity-30">
                    Prev
                  </button>
                  <span className="text-xs text-muted">{page} / {totalPages}</span>
                  <button onClick={() => setPage((p) => Math.min(totalPages, p + 1))} disabled={page === totalPages} className="rounded border border-border px-3 py-1 text-xs disabled:opacity-30">
                    Next
                  </button>
                </div>
              )}
            </>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between border-t border-border px-5 py-3">
          <div className="text-sm text-muted">
            {selected ? (
              <span>Selected: <span className="text-foreground">{selected.title || selected.alt_text || selected.original_filename || "Untitled"}</span></span>
            ) : (
              "Select an image to insert"
            )}
          </div>
          <div className="flex gap-2">
            <button onClick={onClose} className="rounded-md border border-border px-4 py-1.5 text-sm hover:bg-card">
              Cancel
            </button>
            <button
              onClick={handleInsert}
              disabled={!selected}
              className="rounded-md bg-primary px-4 py-1.5 text-sm font-medium text-background hover:bg-primary/90 disabled:opacity-50"
            >
              Insert Image
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
