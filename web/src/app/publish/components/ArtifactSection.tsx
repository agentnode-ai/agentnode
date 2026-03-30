"use client";

import { useState, useRef } from "react";
import type { CodeFile } from "../lib/types";
import { MAX_UPLOAD_SIZE_MB } from "../lib/constants";

export function ArtifactSection({
  artifactMode,
  onModeChange,
  codeFiles,
  onCodeFilesChange,
  uploadedFiles,
  onUploadedFilesChange,
  tarGzFile,
  onTarGzChange,
  builderArtifactName,
  onBuilderArtifactClear,
  packageId,
}: {
  artifactMode: "code" | "upload";
  onModeChange: (mode: "code" | "upload") => void;
  codeFiles: CodeFile[];
  onCodeFilesChange: (files: CodeFile[]) => void;
  uploadedFiles: File[];
  onUploadedFilesChange: (files: File[]) => void;
  tarGzFile: File | null;
  onTarGzChange: (f: File | null) => void;
  builderArtifactName?: string;
  onBuilderArtifactClear?: () => void;
  packageId?: string;
}) {
  const dropRef = useRef<HTMLDivElement>(null);
  const [dragOver, setDragOver] = useState(false);

  function updateCodeFile(index: number, field: "path" | "content", value: string) {
    const next = [...codeFiles];
    next[index] = { ...next[index], [field]: value };
    onCodeFilesChange(next);
  }

  function addCodeFile() {
    const moduleName = packageId ? packageId.replace(/-/g, "_") : "my_tool";
    const existingCount = codeFiles.length;
    const defaultPath = existingCount === 0
      ? `${moduleName}/tool.py`
      : `${moduleName}/helper_${existingCount}.py`;
    onCodeFilesChange([...codeFiles, { path: defaultPath, content: "" }]);
  }

  function removeCodeFile(index: number) {
    if (codeFiles.length <= 1) return;
    onCodeFilesChange(codeFiles.filter((_, i) => i !== index));
  }

  function handleFileDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragOver(false);
    processUploadedFiles(Array.from(e.dataTransfer.files));
  }

  function handleFileSelect(e: React.ChangeEvent<HTMLInputElement>) {
    processUploadedFiles(Array.from(e.target.files || []));
  }

  function processUploadedFiles(files: File[]) {
    if (files.length === 1 && (files[0].name.endsWith(".tar.gz") || files[0].name.endsWith(".tgz"))) {
      onTarGzChange(files[0]);
      onUploadedFilesChange([]);
      return;
    }
    onTarGzChange(null);
    onUploadedFilesChange([...uploadedFiles, ...files]);
  }

  function removeUploadedFile(index: number) {
    onUploadedFilesChange(uploadedFiles.filter((_, i) => i !== index));
  }

  if (builderArtifactName && onBuilderArtifactClear) {
    return (
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2 rounded-md border border-primary/30 bg-primary/5 px-4 py-2.5 text-sm">
          <span className="text-primary">&#10003;</span>
          <span className="font-mono text-foreground">{builderArtifactName}</span>
          <span className="text-muted">(from Builder)</span>
        </div>
        <button type="button" onClick={onBuilderArtifactClear} className="text-xs text-muted hover:text-foreground">
          Remove
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => onModeChange("code")}
          className={`rounded-full border px-4 py-1.5 text-xs font-medium transition-colors ${
            artifactMode === "code"
              ? "border-primary bg-primary/10 text-primary"
              : "border-border text-muted hover:border-primary/30"
          }`}
        >
          Write code
        </button>
        <button
          type="button"
          onClick={() => onModeChange("upload")}
          className={`rounded-full border px-4 py-1.5 text-xs font-medium transition-colors ${
            artifactMode === "upload"
              ? "border-primary bg-primary/10 text-primary"
              : "border-border text-muted hover:border-primary/30"
          }`}
        >
          Upload files
        </button>
      </div>

      {artifactMode === "code" && (
        <div className="space-y-3">
          {codeFiles.some((f) => f.content.trim()) && codeFiles[0]?.path.includes("src/") && (
            <div className="rounded-lg border border-green-500/30 bg-green-500/5 px-3 py-2 text-xs text-green-400">
              Code imported from your original tool. Review and edit before publishing.
            </div>
          )}
          {codeFiles.map((file, i) => (
            <div key={i} className="rounded-lg border border-border bg-card overflow-hidden">
              <div className="flex items-center gap-2 border-b border-border px-3 py-2 bg-card">
                <input
                  type="text"
                  value={file.path}
                  onChange={(e) => updateCodeFile(i, "path", e.target.value)}
                  className="flex-1 bg-transparent text-xs font-mono text-foreground focus:outline-none placeholder:text-muted/50"
                  placeholder="my_tool/tool.py"
                />
                {codeFiles.length > 1 && (
                  <button type="button" onClick={() => removeCodeFile(i)} className="text-xs text-muted hover:text-danger transition-colors">
                    Remove
                  </button>
                )}
              </div>
              <textarea
                rows={10}
                value={file.content}
                onChange={(e) => updateCodeFile(i, "content", e.target.value)}
                className="w-full bg-[#0d1117] px-4 py-3 font-mono text-xs text-gray-300 focus:outline-none resize-none"
                placeholder="# Paste or write your Python code here..."
                spellCheck={false}
              />
            </div>
          ))}
          <button
            type="button"
            onClick={addCodeFile}
            className="w-full rounded-md border border-dashed border-border py-2.5 text-xs text-muted hover:border-primary/30 hover:text-foreground transition-colors"
          >
            + Add file
          </button>
          <p className="text-xs text-muted">
            Your code will be automatically packaged. Include at least one .py file.
            <span className="text-muted/50"> Max upload size: {MAX_UPLOAD_SIZE_MB} MB</span>
          </p>
        </div>
      )}

      {artifactMode === "upload" && (
        <div className="space-y-3">
          <div
            ref={dropRef}
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={handleFileDrop}
            className={`rounded-lg border-2 border-dashed px-6 py-8 text-center transition-colors ${
              dragOver ? "border-primary bg-primary/5" : "border-border hover:border-primary/30"
            }`}
          >
            <div className="text-sm text-muted">
              Drag & drop files here, or{" "}
              <label className="cursor-pointer text-primary hover:underline">
                browse
                <input type="file" multiple accept=".py,.toml,.yaml,.yml,.cfg,.txt,.md,.tar.gz,.tgz" onChange={handleFileSelect} className="hidden" />
              </label>
            </div>
            <p className="mt-2 text-xs text-muted/60">
              Individual files (.py, .toml, .yaml) or a single .tar.gz archive
            </p>
            <p className="mt-1 text-xs text-muted/50">
              Max upload size: {MAX_UPLOAD_SIZE_MB} MB
            </p>
          </div>

          {tarGzFile && (
            <div className="flex items-center gap-3 rounded-md border border-primary/30 bg-primary/5 px-4 py-2.5 text-sm">
              <span className="text-primary">&#10003;</span>
              <span className="font-mono text-foreground">{tarGzFile.name}</span>
              <span className="text-xs text-muted">({(tarGzFile.size / 1024).toFixed(1)} KB)</span>
              <button type="button" onClick={() => onTarGzChange(null)} className="ml-auto text-xs text-muted hover:text-foreground">
                Remove
              </button>
            </div>
          )}

          {uploadedFiles.length > 0 && (
            <div className="rounded-lg border border-border divide-y divide-border">
              {uploadedFiles.map((file, i) => (
                <div key={i} className="flex items-center gap-3 px-4 py-2 text-sm">
                  <span className="font-mono text-xs text-foreground">{file.name}</span>
                  <span className="text-xs text-muted">({(file.size / 1024).toFixed(1)} KB)</span>
                  <button type="button" onClick={() => removeUploadedFile(i)} className="ml-auto text-xs text-muted hover:text-danger transition-colors">
                    Remove
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
