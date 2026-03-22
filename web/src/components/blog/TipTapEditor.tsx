"use client";

import { useEditor, EditorContent } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Image from "@tiptap/extension-image";
import Link from "@tiptap/extension-link";
import Underline from "@tiptap/extension-underline";
import Placeholder from "@tiptap/extension-placeholder";
import TextAlign from "@tiptap/extension-text-align";
import { useCallback, useState } from "react";
import MediaLibraryModal from "./MediaLibraryModal";

// Extend Image to support data-align attribute for float/alignment
const AlignableImage = Image.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      "data-align": {
        default: null,
        parseHTML: (element) => element.getAttribute("data-align"),
        renderHTML: (attributes) => {
          if (!attributes["data-align"]) return {};
          return { "data-align": attributes["data-align"] };
        },
      },
    };
  },
});

interface TipTapEditorProps {
  content?: object | null;
  contentHtml?: string | null;
  onChange?: (json: object, html: string) => void;
}

function MenuBar({ editor, onOpenMedia }: { editor: ReturnType<typeof useEditor> | null; onOpenMedia: () => void }) {
  if (!editor) return null;

  const isImageSelected = editor.isActive("image");

  const addLink = useCallback(() => {
    const url = prompt("URL:");
    if (!url) return;
    editor?.chain().focus().extendMarkRange("link").setLink({ href: url }).run();
  }, [editor]);

  const setImageAlign = useCallback((align: string | null) => {
    editor?.chain().focus().updateAttributes("image", { "data-align": align }).run();
  }, [editor]);

  const btn = (active: boolean, onClick: () => void, label: string) => (
    <button
      type="button"
      onClick={onClick}
      className={`rounded px-2 py-1 text-xs font-medium transition-colors ${
        active ? "bg-primary text-background" : "bg-card text-muted hover:text-foreground"
      }`}
    >
      {label}
    </button>
  );

  return (
    <div className="flex flex-wrap gap-1 p-2">
      {btn(editor.isActive("bold"), () => editor.chain().focus().toggleBold().run(), "B")}
      {btn(editor.isActive("italic"), () => editor.chain().focus().toggleItalic().run(), "I")}
      {btn(editor.isActive("underline"), () => editor.chain().focus().toggleUnderline().run(), "U")}
      {btn(editor.isActive("strike"), () => editor.chain().focus().toggleStrike().run(), "S")}
      <span className="mx-1 border-l border-border" />
      {btn(editor.isActive("heading", { level: 2 }), () => editor.chain().focus().toggleHeading({ level: 2 }).run(), "H2")}
      {btn(editor.isActive("heading", { level: 3 }), () => editor.chain().focus().toggleHeading({ level: 3 }).run(), "H3")}
      {btn(editor.isActive("heading", { level: 4 }), () => editor.chain().focus().toggleHeading({ level: 4 }).run(), "H4")}
      <span className="mx-1 border-l border-border" />
      {btn(editor.isActive("bulletList"), () => editor.chain().focus().toggleBulletList().run(), "• List")}
      {btn(editor.isActive("orderedList"), () => editor.chain().focus().toggleOrderedList().run(), "1. List")}
      {btn(editor.isActive("blockquote"), () => editor.chain().focus().toggleBlockquote().run(), "Quote")}
      {btn(editor.isActive("codeBlock"), () => editor.chain().focus().toggleCodeBlock().run(), "Code")}
      <span className="mx-1 border-l border-border" />
      {btn(editor.isActive({ textAlign: "left" }), () => editor.chain().focus().setTextAlign("left").run(), "Left")}
      {btn(editor.isActive({ textAlign: "center" }), () => editor.chain().focus().setTextAlign("center").run(), "Center")}
      {btn(editor.isActive({ textAlign: "right" }), () => editor.chain().focus().setTextAlign("right").run(), "Right")}
      <span className="mx-1 border-l border-border" />
      <button type="button" onClick={() => editor.chain().focus().setHorizontalRule().run()} className="rounded px-2 py-1 text-xs text-muted hover:text-foreground">
        —
      </button>
      <button type="button" onClick={addLink} className={`rounded px-2 py-1 text-xs font-medium ${editor.isActive("link") ? "text-primary" : "text-muted hover:text-foreground"}`}>
        Link
      </button>
      <button type="button" onClick={onOpenMedia} className="rounded px-2 py-1 text-xs text-muted hover:text-foreground">
        Media
      </button>
      <span className="mx-1 border-l border-border" />
      <button type="button" onClick={() => editor.chain().focus().undo().run()} disabled={!editor.can().undo()} className="rounded px-2 py-1 text-xs text-muted hover:text-foreground disabled:opacity-30">
        Undo
      </button>
      <button type="button" onClick={() => editor.chain().focus().redo().run()} disabled={!editor.can().redo()} className="rounded px-2 py-1 text-xs text-muted hover:text-foreground disabled:opacity-30">
        Redo
      </button>

      {/* Image alignment bar - shows when image is selected */}
      {isImageSelected && (
        <>
          <span className="mx-1 border-l border-border" />
          <span className="text-xs text-muted leading-6">Img:</span>
          {btn(editor.isActive("image", { "data-align": "left" }), () => setImageAlign("left"), "Float L")}
          {btn(editor.isActive("image", { "data-align": "center" }), () => setImageAlign("center"), "Center")}
          {btn(editor.isActive("image", { "data-align": "right" }), () => setImageAlign("right"), "Float R")}
          {btn(!editor.getAttributes("image")["data-align"], () => setImageAlign(null), "Full")}
        </>
      )}
    </div>
  );
}

export default function TipTapEditor({ content, contentHtml, onChange }: TipTapEditorProps) {
  const [mode, setMode] = useState<"visual" | "html">("visual");
  const [htmlSource, setHtmlSource] = useState("");
  const [mediaOpen, setMediaOpen] = useState(false);

  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        heading: { levels: [2, 3, 4] },
      }),
      AlignableImage.configure({ inline: false, allowBase64: false }),
      Link.configure({ openOnClick: false, autolink: true }),
      Underline,
      Placeholder.configure({ placeholder: "Start writing..." }),
      TextAlign.configure({ types: ["heading", "paragraph"] }),
    ],
    content: content || contentHtml || undefined,
    onUpdate: ({ editor }) => {
      onChange?.(editor.getJSON(), editor.getHTML());
    },
    editorProps: {
      attributes: {
        class: "prose prose-invert max-w-none min-h-[400px] p-4 focus:outline-none",
      },
    },
    immediatelyRender: false,
  });

  function switchToHtml() {
    if (editor) {
      setHtmlSource(editor.getHTML());
    }
    setMode("html");
  }

  function switchToVisual() {
    setMode("visual");
    if (editor && htmlSource) {
      setTimeout(() => {
        editor.commands.setContent(htmlSource);
        onChange?.(editor.getJSON(), editor.getHTML());
      }, 0);
    }
  }

  function handleHtmlChange(value: string) {
    setHtmlSource(value);
  }

  function handleMediaSelect(url: string, alt: string) {
    if (editor) {
      editor.chain().focus().setImage({ src: url, alt }).run();
    }
  }

  return (
    <>
      <div className="overflow-hidden rounded-lg border border-border bg-background">
        {/* Tab bar */}
        <div className="flex items-center justify-between border-b border-border">
          {mode === "visual" ? (
            <MenuBar editor={editor} onOpenMedia={() => setMediaOpen(true)} />
          ) : (
            <div className="p-2 text-xs text-muted">Edit raw HTML</div>
          )}
          <div className="flex shrink-0 border-l border-border">
            <button
              type="button"
              onClick={() => mode === "html" ? switchToVisual() : null}
              className={`px-3 py-2 text-xs font-medium transition-colors ${
                mode === "visual" ? "bg-card text-foreground" : "text-muted hover:text-foreground"
              }`}
            >
              Visual
            </button>
            <button
              type="button"
              onClick={() => mode === "visual" ? switchToHtml() : null}
              className={`px-3 py-2 text-xs font-medium transition-colors ${
                mode === "html" ? "bg-card text-foreground" : "text-muted hover:text-foreground"
              }`}
            >
              HTML
            </button>
          </div>
        </div>

        {/* Editor area */}
        {mode === "visual" ? (
          <EditorContent editor={editor} />
        ) : (
          <textarea
            value={htmlSource}
            onChange={(e) => handleHtmlChange(e.target.value)}
            spellCheck={false}
            className="min-h-[400px] w-full resize-y bg-background p-4 font-mono text-sm text-foreground focus:outline-none"
          />
        )}
      </div>

      <MediaLibraryModal
        open={mediaOpen}
        onClose={() => setMediaOpen(false)}
        onSelect={handleMediaSelect}
      />
    </>
  );
}
