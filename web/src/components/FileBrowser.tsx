"use client";

import { useState, useCallback } from "react";

interface FileItem {
  path: string;
  size: number;
}

interface FileBrowserProps {
  files: FileItem[];
  slug: string;
  version: string;
}

const PREVIEW_EXTENSIONS = new Set([
  ".md", ".py", ".ts", ".js", ".json", ".yaml", ".yml", ".toml", ".txt", ".cfg", ".ini",
]);

const FILE_ICONS: Record<string, string> = {
  ".py": "Py",
  ".ts": "TS",
  ".js": "JS",
  ".md": "Md",
  ".json": "{}",
  ".yaml": "Ym",
  ".yml": "Ym",
  ".toml": "Tm",
  ".txt": "Tx",
  ".cfg": "Cf",
  ".ini": "In",
};

const HIGHLIGHT_FILES = new Set(["README.md", "readme.md", "agentnode.yaml", "tool.py"]);

function getExt(path: string): string {
  const dot = path.lastIndexOf(".");
  return dot >= 0 ? path.slice(dot).toLowerCase() : "";
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function FileBrowser({ files, slug, version }: FileBrowserProps) {
  const [expanded, setExpanded] = useState(files.length <= 10);
  const [previewFile, setPreviewFile] = useState<string | null>(null);
  const [previewContent, setPreviewContent] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  const displayFiles = expanded ? files : files.slice(0, 10);

  const basename = (path: string) => {
    const parts = path.split("/");
    return parts[parts.length - 1];
  };

  const loadPreview = useCallback(
    async (filePath: string) => {
      if (previewFile === filePath) {
        setPreviewFile(null);
        setPreviewContent(null);
        return;
      }

      setPreviewFile(filePath);
      setPreviewContent(null);
      setPreviewLoading(true);

      try {
        const baseUrl = "/api/v1";
        const res = await fetch(
          `${baseUrl}/packages/${encodeURIComponent(slug)}/versions/${encodeURIComponent(version)}/files/${filePath}`
        );
        if (res.ok) {
          const text = await res.text();
          setPreviewContent(text);
        } else {
          setPreviewContent("// Preview not available");
        }
      } catch {
        setPreviewContent("// Failed to load preview");
      }
      setPreviewLoading(false);
    },
    [slug, version, previewFile]
  );

  // Sort: highlighted files first, then alphabetically
  const sortedFiles = [...files].sort((a, b) => {
    const aName = basename(a.path);
    const bName = basename(b.path);
    const aHighlight = HIGHLIGHT_FILES.has(aName);
    const bHighlight = HIGHLIGHT_FILES.has(bName);
    if (aHighlight && !bHighlight) return -1;
    if (!aHighlight && bHighlight) return 1;
    return a.path.localeCompare(b.path);
  });

  const visibleFiles = expanded ? sortedFiles : sortedFiles.slice(0, 10);

  return (
    <div>
      <div className="space-y-0.5">
        {visibleFiles.map((file) => {
          const ext = getExt(file.path);
          const icon = FILE_ICONS[ext] || "..";
          const canPreview = PREVIEW_EXTENSIONS.has(ext);
          const isHighlighted = HIGHLIGHT_FILES.has(basename(file.path));
          const isActive = previewFile === file.path;

          return (
            <div key={file.path}>
              <button
                onClick={() => canPreview && loadPreview(file.path)}
                disabled={!canPreview}
                className={`w-full flex items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors ${
                  isActive
                    ? "bg-primary/10 border border-primary/20"
                    : canPreview
                      ? "hover:bg-card/80 border border-transparent"
                      : "opacity-60 cursor-default border border-transparent"
                }`}
              >
                <span
                  className={`shrink-0 w-6 h-6 rounded flex items-center justify-center text-[9px] font-bold ${
                    isHighlighted
                      ? "bg-primary/20 text-primary"
                      : "bg-card text-muted"
                  }`}
                >
                  {icon}
                </span>
                <span
                  className={`text-xs truncate min-w-0 ${
                    isHighlighted ? "text-foreground font-medium" : "text-muted"
                  }`}
                  title={file.path}
                >
                  {file.path}
                </span>
                <span className="text-[10px] text-zinc-600 shrink-0 ml-auto">
                  {formatSize(file.size)}
                </span>
              </button>

              {/* Inline preview */}
              {isActive && (
                <div className="mt-1 mb-2 ml-8 rounded-lg border border-border bg-card overflow-hidden">
                  {previewLoading ? (
                    <div className="p-3 text-xs text-muted animate-pulse">
                      Loading...
                    </div>
                  ) : (
                    <pre className="p-3 overflow-x-auto max-h-60 text-[11px] font-mono text-muted leading-relaxed">
                      {previewContent}
                    </pre>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {!expanded && sortedFiles.length > 10 && (
        <button
          onClick={() => setExpanded(true)}
          className="mt-2 text-xs text-primary hover:text-primary-hover transition-colors"
        >
          Show all {sortedFiles.length} files
        </button>
      )}
    </div>
  );
}
