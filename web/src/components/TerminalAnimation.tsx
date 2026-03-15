"use client";

import { useState, useEffect, useRef } from "react";

const TERMINAL_LINES = [
  { type: "input" as const, text: "$ agentnode search pdf" },
  { type: "output" as const, text: "Found 3 packages:" },
  {
    type: "result" as const,
    text: "  pdf-reader-pack     \u2605 trusted    langchain, crewai    v1.2.0",
  },
  {
    type: "result" as const,
    text: "  pdf-extractor       \u2713 verified   generic              v0.8.1",
  },
  {
    type: "result" as const,
    text: "  doc-parser-pro      \u25CB unverified langchain            v2.0.0",
  },
  { type: "blank" as const, text: "" },
  { type: "input" as const, text: "$ agentnode install pdf-reader-pack" },
  {
    type: "success" as const,
    text: "\u2713 Installed pdf-reader-pack@1.2.0",
  },
  {
    type: "hint" as const,
    text: "\u2192 from pdf_reader_pack import tool",
  },
];

const TYPE_SPEED = 30;
const LINE_DELAY = 300;
const OUTPUT_DELAY = 80;

export default function TerminalAnimation() {
  const [visibleLines, setVisibleLines] = useState<
    { text: string; type: string; complete: boolean }[]
  >([]);
  const [currentLineIndex, setCurrentLineIndex] = useState(0);
  const [currentCharIndex, setCurrentCharIndex] = useState(0);
  const [isTyping, setIsTyping] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const startedRef = useRef(false);

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;

    const timeout = setTimeout(() => {
      setIsTyping(true);
    }, 800);

    return () => clearTimeout(timeout);
  }, []);

  useEffect(() => {
    if (!isTyping || currentLineIndex >= TERMINAL_LINES.length) {
      return;
    }

    const line = TERMINAL_LINES[currentLineIndex];

    // For input lines, type character by character
    if (line.type === "input") {
      if (currentCharIndex === 0) {
        setVisibleLines((prev) => [
          ...prev,
          { text: "", type: line.type, complete: false },
        ]);
      }

      if (currentCharIndex < line.text.length) {
        const timer = setTimeout(() => {
          setVisibleLines((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            updated[updated.length - 1] = {
              ...last,
              text: line.text.slice(0, currentCharIndex + 1),
            };
            return updated;
          });
          setCurrentCharIndex((c) => c + 1);
        }, TYPE_SPEED);
        return () => clearTimeout(timer);
      } else {
        // Line complete
        const timer = setTimeout(() => {
          setVisibleLines((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            updated[updated.length - 1] = { ...last, complete: true };
            return updated;
          });
          setCurrentCharIndex(0);
          setCurrentLineIndex((i) => i + 1);
        }, LINE_DELAY);
        return () => clearTimeout(timer);
      }
    } else {
      // For output lines, appear instantly with a small delay
      const timer = setTimeout(() => {
        setVisibleLines((prev) => [
          ...prev,
          { text: line.text, type: line.type, complete: true },
        ]);
        setCurrentLineIndex((i) => i + 1);
      }, OUTPUT_DELAY);
      return () => clearTimeout(timer);
    }
  }, [isTyping, currentLineIndex, currentCharIndex]);

  // Auto-scroll
  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [visibleLines]);

  const showCursor =
    isTyping && currentLineIndex < TERMINAL_LINES.length;

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
        className="h-[280px] overflow-y-auto p-4 font-mono text-sm leading-6"
      >
        {visibleLines.map((line, i) => {
          const isLastLine = i === visibleLines.length - 1;
          return (
            <div key={i} className="min-h-[1.5rem]">
              {line.type === "input" && (
                <span className="text-foreground">
                  {line.text}
                  {isLastLine && showCursor && !line.complete && (
                    <span className="terminal-cursor ml-0.5 inline-block h-4 w-2 bg-primary align-middle" />
                  )}
                </span>
              )}
              {line.type === "output" && (
                <span className="text-muted">{line.text}</span>
              )}
              {line.type === "result" && (
                <span className="text-foreground/80">{line.text}</span>
              )}
              {line.type === "success" && (
                <span className="text-success">{line.text}</span>
              )}
              {line.type === "hint" && (
                <span className="text-primary">{line.text}</span>
              )}
              {line.type === "blank" && <span>&nbsp;</span>}
            </div>
          );
        })}
        {visibleLines.length === 0 && showCursor && (
          <div>
            <span className="terminal-cursor inline-block h-4 w-2 bg-primary" />
          </div>
        )}
      </div>
    </div>
  );
}
