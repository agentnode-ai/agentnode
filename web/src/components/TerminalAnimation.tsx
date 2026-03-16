"use client";

import { useState, useEffect, useRef, useCallback } from "react";

// Each line has a type that determines rendering style, and the raw text content.
// "command" lines are typed character by character; all others appear instantly.
interface TerminalLine {
  type:
    | "command"
    | "blank"
    | "output"
    | "result"
    | "success"
    | "hint"
    | "label";
  text: string;
}

const TERMINAL_LINES: TerminalLine[] = [
  { type: "command", text: '$ agentnode search "pdf extraction"' },
  { type: "blank", text: "" },
  { type: "output", text: "Found 1 package(s):" },
  { type: "result", text: "  pdf-reader-pack [trusted]" },
  {
    type: "output",
    text: "    Extract text, tables, and metadata from PDF files.",
  },
  { type: "blank", text: "" },
  { type: "command", text: "$ agentnode install pdf-reader-pack" },
  { type: "output", text: "Resolving pdf-reader-pack..." },
  { type: "output", text: "Installing pdf-reader-pack@1.0.0..." },
  { type: "blank", text: "" },
  { type: "success", text: "\u2713 Installed pdf-reader-pack@1.0.0" },
  { type: "blank", text: "" },
  { type: "label", text: "Next step:" },
  { type: "hint", text: "  from pdf_reader_pack import tool" },
];

const TYPE_SPEED = 32; // ms per character for commands
const LINE_DELAY = 400; // pause after finishing a command before showing output
const OUTPUT_DELAY = 90; // ms between output lines appearing
const RESTART_DELAY = 4000; // pause at end before looping

interface VisibleLine {
  text: string;
  type: TerminalLine["type"];
  complete: boolean;
}

export default function TerminalAnimation() {
  const [visibleLines, setVisibleLines] = useState<VisibleLine[]>([]);
  const [lineIndex, setLineIndex] = useState(0);
  const [charIndex, setCharIndex] = useState(0);
  const [isTyping, setIsTyping] = useState(false);
  const [animationDone, setAnimationDone] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const startedRef = useRef(false);

  // Start animation after a brief pause
  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    const timeout = setTimeout(() => setIsTyping(true), 600);
    return () => clearTimeout(timeout);
  }, []);

  // Restart animation after it completes
  const restart = useCallback(() => {
    setVisibleLines([]);
    setLineIndex(0);
    setCharIndex(0);
    setAnimationDone(false);
    setIsTyping(true);
  }, []);

  useEffect(() => {
    if (!animationDone) return;
    const timer = setTimeout(restart, RESTART_DELAY);
    return () => clearTimeout(timer);
  }, [animationDone, restart]);

  // Main animation loop
  useEffect(() => {
    if (!isTyping || lineIndex >= TERMINAL_LINES.length) {
      if (isTyping && lineIndex >= TERMINAL_LINES.length) {
        setIsTyping(false);
        setAnimationDone(true);
      }
      return;
    }

    const line = TERMINAL_LINES[lineIndex];

    if (line.type === "command") {
      // For command lines, type character by character
      if (charIndex === 0) {
        setVisibleLines((prev) => [
          ...prev,
          { text: "", type: line.type, complete: false },
        ]);
      }

      if (charIndex < line.text.length) {
        const timer = setTimeout(() => {
          setVisibleLines((prev) => {
            const updated = [...prev];
            updated[updated.length - 1] = {
              ...updated[updated.length - 1],
              text: line.text.slice(0, charIndex + 1),
            };
            return updated;
          });
          setCharIndex((c) => c + 1);
        }, TYPE_SPEED);
        return () => clearTimeout(timer);
      } else {
        // Command fully typed, mark complete and move on
        const timer = setTimeout(() => {
          setVisibleLines((prev) => {
            const updated = [...prev];
            updated[updated.length - 1] = {
              ...updated[updated.length - 1],
              complete: true,
            };
            return updated;
          });
          setCharIndex(0);
          setLineIndex((i) => i + 1);
        }, LINE_DELAY);
        return () => clearTimeout(timer);
      }
    } else {
      // Non-command lines appear instantly with a short delay
      const timer = setTimeout(() => {
        setVisibleLines((prev) => [
          ...prev,
          { text: line.text, type: line.type, complete: true },
        ]);
        setLineIndex((i) => i + 1);
      }, OUTPUT_DELAY);
      return () => clearTimeout(timer);
    }
  }, [isTyping, lineIndex, charIndex]);

  // Auto-scroll to bottom as content appears
  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [visibleLines]);

  const showCursor = isTyping && lineIndex < TERMINAL_LINES.length;

  return (
    <div className="w-full max-w-2xl overflow-hidden rounded-xl border border-border bg-card shadow-2xl shadow-primary/5">
      {/* Title bar */}
      <div className="flex items-center gap-2 border-b border-border px-4 py-3">
        <div className="flex gap-1.5">
          <div className="h-3 w-3 rounded-full bg-[#ff5f57]" />
          <div className="h-3 w-3 rounded-full bg-[#febc2e]" />
          <div className="h-3 w-3 rounded-full bg-[#28c840]" />
        </div>
        <span className="ml-2 text-xs text-muted font-mono">terminal</span>
      </div>

      {/* Terminal content */}
      <div
        ref={containerRef}
        className="h-[320px] overflow-y-auto p-4 font-mono text-sm leading-6"
      >
        {visibleLines.map((line, i) => {
          const isLastLine = i === visibleLines.length - 1;
          return (
            <div key={i} className="min-h-[1.5rem] whitespace-pre">
              {line.type === "command" && (
                <span>
                  <CommandText text={line.text} />
                  {isLastLine && showCursor && !line.complete && (
                    <span className="terminal-cursor ml-0.5 inline-block h-4 w-2 bg-primary align-middle" />
                  )}
                </span>
              )}
              {line.type === "output" && (
                <span className="text-muted">{line.text}</span>
              )}
              {line.type === "result" && <ResultText text={line.text} />}
              {line.type === "success" && (
                <span className="text-success font-semibold">{line.text}</span>
              )}
              {line.type === "label" && (
                <span className="text-foreground">{line.text}</span>
              )}
              {line.type === "hint" && (
                <span className="text-primary">{line.text}</span>
              )}
              {line.type === "blank" && <span>&nbsp;</span>}
            </div>
          );
        })}

        {/* Show blinking cursor before any text appears */}
        {visibleLines.length === 0 && showCursor && (
          <div>
            <span className="terminal-cursor inline-block h-4 w-2 bg-primary" />
          </div>
        )}

        {/* Show blinking cursor at a new prompt line when animation is done */}
        {animationDone && (
          <div className="min-h-[1.5rem]">
            <span className="text-success font-bold">$</span>
            <span className="terminal-cursor ml-1 inline-block h-4 w-2 bg-primary align-middle" />
          </div>
        )}
      </div>
    </div>
  );
}

/** Renders a command line with green `$` prompt and white command text */
function CommandText({ text }: { text: string }) {
  if (!text) return null;

  // The text starts with "$ ", so split the green prompt from the white command
  if (text.startsWith("$")) {
    const prompt = "$";
    const rest = text.slice(1); // everything after the $
    return (
      <>
        <span className="text-success font-bold">{prompt}</span>
        <span className="text-foreground">{rest}</span>
      </>
    );
  }

  return <span className="text-foreground">{text}</span>;
}

/** Renders a search result line, highlighting the package name and trust badge */
function ResultText({ text }: { text: string }) {
  const match = text.match(
    /^(\s*)([\w-]+)(\s+)(\[trusted\]|\[verified\]|\[unverified\])(.*)$/,
  );
  if (match) {
    const [, indent, name, space, badge, rest] = match;
    const badgeColor =
      badge === "[trusted]"
        ? "text-success"
        : badge === "[verified]"
          ? "text-primary"
          : "text-warning";
    return (
      <>
        <span className="text-foreground">{indent}</span>
        <span className="text-foreground font-semibold">{name}</span>
        <span>{space}</span>
        <span className={badgeColor}>{badge}</span>
        <span className="text-muted">{rest}</span>
      </>
    );
  }
  return <span className="text-foreground/80">{text}</span>;
}
