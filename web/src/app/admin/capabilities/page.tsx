"use client";

import { useState, useEffect } from "react";
import { fetchWithAuth } from "@/lib/api";

interface Capability {
  id: string;
  display_name: string;
  description: string | null;
  category: string | null;
}

export default function AdminCapabilitiesPage() {
  const [capabilities, setCapabilities] = useState<Capability[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  // Create form
  const [newId, setNewId] = useState("");
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newCategory, setNewCategory] = useState("");
  const [creating, setCreating] = useState(false);

  // Edit
  const [editId, setEditId] = useState("");
  const [editName, setEditName] = useState("");
  const [editDesc, setEditDesc] = useState("");
  const [editCategory, setEditCategory] = useState("");

  // Delete confirmation
  const [deleteTarget, setDeleteTarget] = useState<Capability | null>(null);
  const [deleteConfirmText, setDeleteConfirmText] = useState("");

  useEffect(() => { loadCapabilities(); }, []);

  async function loadCapabilities() {
    setLoading(true);
    try {
      const res = await fetchWithAuth("/admin/capabilities");
      if (res.ok) { const d = await res.json(); setCapabilities(d.capabilities); }
    } catch { setError("Failed to load"); }
    finally { setLoading(false); }
  }

  async function createCapability() {
    if (!newId || !newName) return;
    setCreating(true); setError(""); setSuccess("");
    const res = await fetchWithAuth("/admin/capabilities", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: newId, display_name: newName, description: newDesc || null, category: newCategory || null }),
    });
    if (res.ok) { setSuccess("Capability created"); setNewId(""); setNewName(""); setNewDesc(""); setNewCategory(""); await loadCapabilities(); }
    else { const d = await res.json(); setError(d.error?.message || "Failed"); }
    setCreating(false);
  }

  async function updateCapability() {
    if (!editId) return;
    setError(""); setSuccess("");
    const res = await fetchWithAuth(`/admin/capabilities/${editId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ display_name: editName, description: editDesc, category: editCategory }),
    });
    if (res.ok) { setSuccess("Updated"); setEditId(""); await loadCapabilities(); }
    else { const d = await res.json(); setError(d.error?.message || "Failed"); }
  }

  async function confirmDelete() {
    if (!deleteTarget || deleteConfirmText !== deleteTarget.id) return;
    setError(""); setSuccess("");
    const res = await fetchWithAuth(`/admin/capabilities/${deleteTarget.id}`, { method: "DELETE" });
    if (res.ok) { setSuccess(`Deleted '${deleteTarget.id}'`); setDeleteTarget(null); setDeleteConfirmText(""); await loadCapabilities(); }
    else { const d = await res.json(); setError(d.error?.message || "Failed"); }
  }

  // Group by category
  const grouped: Record<string, Capability[]> = {};
  capabilities.forEach((c) => {
    const cat = c.category || "Uncategorized";
    if (!grouped[cat]) grouped[cat] = [];
    grouped[cat].push(c);
  });

  return (
    <div>
      <h1 className="mb-6 text-xl font-bold text-foreground">Capabilities ({capabilities.length})</h1>

      {error && (
        <div className="mb-4 rounded-md border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">
          {error} <button onClick={() => setError("")} className="ml-2 underline">dismiss</button>
        </div>
      )}
      {success && (
        <div className="mb-4 rounded-md border border-success/30 bg-success/10 px-4 py-3 text-sm text-success">
          {success} <button onClick={() => setSuccess("")} className="ml-2 underline">dismiss</button>
        </div>
      )}

      {/* Delete confirmation dialog */}
      {deleteTarget && (
        <div className="mb-4 rounded-lg border border-danger/30 bg-danger/5 p-4">
          <p className="text-sm text-foreground">
            Delete capability <span className="font-mono font-semibold text-danger">{deleteTarget.id}</span> ({deleteTarget.display_name})?
          </p>
          <p className="mt-1 text-xs text-muted">Type the capability ID to confirm:</p>
          <div className="mt-2 flex gap-2">
            <input
              type="text"
              placeholder={deleteTarget.id}
              value={deleteConfirmText}
              onChange={(e) => setDeleteConfirmText(e.target.value)}
              className="w-52 rounded border border-border bg-background px-3 py-1.5 text-sm font-mono focus:outline-none focus:border-danger"
            />
            <button
              onClick={confirmDelete}
              disabled={deleteConfirmText !== deleteTarget.id}
              className="rounded bg-danger px-4 py-1.5 text-sm font-medium text-white hover:bg-danger/90 disabled:opacity-30"
            >Delete</button>
            <button
              onClick={() => { setDeleteTarget(null); setDeleteConfirmText(""); }}
              className="rounded border border-border px-4 py-1.5 text-sm text-muted hover:bg-card"
            >Cancel</button>
          </div>
        </div>
      )}

      {/* Create form */}
      <div className="mb-6 rounded-lg border border-border bg-card p-4">
        <h2 className="mb-3 text-sm font-semibold text-foreground">Add Capability</h2>
        <div className="flex flex-wrap gap-2">
          <input type="text" placeholder="ID (e.g. code.lint)" value={newId} onChange={(e) => setNewId(e.target.value.toLowerCase().replace(/[^a-z0-9_.]/g, ""))}
            className="w-40 rounded border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:border-primary" />
          <input type="text" placeholder="Display Name" value={newName} onChange={(e) => setNewName(e.target.value)}
            className="w-40 rounded border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:border-primary" />
          <input type="text" placeholder="Category" value={newCategory} onChange={(e) => setNewCategory(e.target.value)}
            className="w-32 rounded border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:border-primary" />
          <input type="text" placeholder="Description" value={newDesc} onChange={(e) => setNewDesc(e.target.value)}
            className="flex-1 min-w-[200px] rounded border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:border-primary" />
          <button onClick={createCapability} disabled={creating || !newId || !newName}
            className="rounded bg-primary px-4 py-2 text-sm text-white hover:bg-primary/90 disabled:opacity-50">
            {creating ? "Adding..." : "Add"}
          </button>
        </div>
      </div>

      {loading ? (
        <div className="py-8 text-center text-muted">Loading...</div>
      ) : (
        Object.entries(grouped).sort(([a], [b]) => a.localeCompare(b)).map(([category, caps]) => (
          <div key={category} className="mb-6">
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted">{category}</h3>
            <div className="rounded-lg border border-border bg-card overflow-hidden">
              <table className="w-full text-sm">
                <tbody>
                  {caps.map((c) => (
                    <tr key={c.id} className="border-b border-border/50 last:border-0 hover:bg-card/80">
                      {editId === c.id ? (
                        <>
                          <td className="px-4 py-2 font-mono text-xs text-muted">{c.id}</td>
                          <td className="px-4 py-2"><input type="text" value={editName} onChange={(e) => setEditName(e.target.value)} className="w-full rounded border border-border bg-background px-2 py-1 text-sm focus:outline-none" /></td>
                          <td className="px-4 py-2"><input type="text" value={editCategory} onChange={(e) => setEditCategory(e.target.value)} className="w-full rounded border border-border bg-background px-2 py-1 text-sm focus:outline-none" /></td>
                          <td className="px-4 py-2"><input type="text" value={editDesc} onChange={(e) => setEditDesc(e.target.value)} className="w-full rounded border border-border bg-background px-2 py-1 text-sm focus:outline-none" /></td>
                          <td className="px-4 py-2">
                            <div className="flex gap-1">
                              <button onClick={updateCapability} className="rounded bg-primary/20 px-2 py-1 text-xs text-primary">Save</button>
                              <button onClick={() => setEditId("")} className="rounded px-2 py-1 text-xs text-muted">Cancel</button>
                            </div>
                          </td>
                        </>
                      ) : (
                        <>
                          <td className="px-4 py-2 font-mono text-xs text-muted">{c.id}</td>
                          <td className="px-4 py-2 font-medium text-foreground">{c.display_name}</td>
                          <td className="px-4 py-2 text-muted text-xs">{c.category}</td>
                          <td className="px-4 py-2 text-muted text-xs">{c.description || "-"}</td>
                          <td className="px-4 py-2">
                            <div className="flex gap-1">
                              <button onClick={() => { setEditId(c.id); setEditName(c.display_name); setEditDesc(c.description || ""); setEditCategory(c.category || ""); }}
                                className="rounded bg-primary/20 px-2 py-1 text-xs text-primary hover:bg-primary/30">Edit</button>
                              <button onClick={() => setDeleteTarget(c)}
                                className="rounded bg-danger/20 px-2 py-1 text-xs text-danger hover:bg-danger/30">Del</button>
                            </div>
                          </td>
                        </>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ))
      )}
    </div>
  );
}
