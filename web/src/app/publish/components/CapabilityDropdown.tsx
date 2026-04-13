"use client";

import { useState, useEffect, useRef } from "react";
import type { CapabilityOption } from "../lib/types";
import { CAPABILITY_FALLBACK } from "../lib/constants";

export function CapabilityDropdown({
  value,
  onChange,
  capabilities,
}: {
  value: string;
  onChange: (v: string) => void;
  capabilities: CapabilityOption[];
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [activeIndex, setActiveIndex] = useState(-1);
  const ref = useRef<HTMLDivElement>(null);
  const listboxId = "capability-listbox";

  const capList = capabilities.length > 0 ? capabilities : CAPABILITY_FALLBACK;

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const q = search.toLowerCase();
  const filtered = q
    ? capList.filter(
        (c) =>
          c.id.toLowerCase().includes(q) ||
          c.name.toLowerCase().includes(q) ||
          c.category.toLowerCase().includes(q)
      )
    : capList;

  // Flat visible list matches the render order (grouped, capped at 60)
  const visible = filtered.slice(0, 60);

  const grouped: Record<string, CapabilityOption[]> = {};
  for (const c of visible) {
    if (!grouped[c.category]) grouped[c.category] = [];
    grouped[c.category].push(c);
  }
  // Flatten in grouped render order so activeIndex aligns with rendered options
  const orderedVisible: CapabilityOption[] = Object.values(grouped).flat();

  const selectedCap = capList.find((c) => c.id === value);

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Escape") {
      if (open) {
        e.preventDefault();
        setOpen(false);
        setActiveIndex(-1);
      }
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (!open) setOpen(true);
      setActiveIndex((i) => Math.min(orderedVisible.length - 1, i + 1));
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => Math.max(0, i - 1));
      return;
    }
    if (e.key === "Enter") {
      if (open && activeIndex >= 0 && activeIndex < orderedVisible.length) {
        e.preventDefault();
        const pick = orderedVisible[activeIndex];
        onChange(pick.id);
        setOpen(false);
        setSearch("");
        setActiveIndex(-1);
      }
      return;
    }
  }

  return (
    <div ref={ref} className="relative">
      <input
        type="text"
        role="combobox"
        aria-expanded={open}
        aria-controls={listboxId}
        aria-autocomplete="list"
        aria-activedescendant={
          open && activeIndex >= 0 && activeIndex < orderedVisible.length
            ? `capability-option-${orderedVisible[activeIndex].id}`
            : undefined
        }
        value={open ? search : value}
        onChange={(e) => {
          setSearch(e.target.value);
          setActiveIndex(-1);
          if (!open) setOpen(true);
        }}
        onFocus={() => {
          setOpen(true);
          setSearch(value);
        }}
        onBlur={() => {
          setTimeout(() => {
            if (open && search) {
              const exactMatch = capList.find(c => c.id === search.toLowerCase().trim());
              if (exactMatch) {
                onChange(exactMatch.id);
              }
            }
            setOpen(false);
            setActiveIndex(-1);
          }, 200);
        }}
        onKeyDown={handleKeyDown}
        placeholder="What does your tool do? e.g. pdf, json, search..."
        className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm font-mono text-foreground focus:border-primary focus:outline-none"
      />
      {value && !open && selectedCap && (
        <p className="mt-1 text-xs text-primary/80">{selectedCap.name}</p>
      )}
      {open && (
        <div
          id={listboxId}
          role="listbox"
          className="absolute z-50 mt-1 max-h-64 w-full overflow-auto rounded-md border border-border bg-card shadow-lg"
        >
          {Object.keys(grouped).length === 0 ? (
            <div className="px-3 py-2 text-xs text-muted">
              No matches found. You can type a custom ID below.
            </div>
          ) : (
            Object.entries(grouped).map(([category, items]) => (
              <div key={category} role="group" aria-label={category}>
                <div className="sticky top-0 bg-card/95 backdrop-blur-sm px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted/60 border-b border-border/50">
                  {category}
                </div>
                {items.map((c) => {
                  const idx = orderedVisible.indexOf(c);
                  const isActive = idx === activeIndex;
                  return (
                    <button
                      key={c.id}
                      id={`capability-option-${c.id}`}
                      role="option"
                      aria-selected={c.id === value}
                      type="button"
                      onClick={() => { onChange(c.id); setOpen(false); setSearch(""); setActiveIndex(-1); }}
                      className={`block w-full px-3 py-2 text-left hover:bg-primary/10 ${
                        isActive ? "bg-primary/15" : c.id === value ? "bg-primary/5" : ""
                      }`}
                    >
                      <span className="text-sm text-foreground">{c.name}</span>
                      <span className="ml-2 font-mono text-[10px] text-muted/60">{c.id}</span>
                    </button>
                  );
                })}
              </div>
            ))
          )}
          {search && !filtered.some((c) => c.id === search) && (
            <button
              type="button"
              onClick={() => { onChange(search); setOpen(false); setSearch(""); }}
              className="block w-full border-t border-border px-3 py-2 text-left text-xs text-primary hover:bg-primary/10"
            >
              Use &quot;{search}&quot; as custom ID
            </button>
          )}
        </div>
      )}
      {!value && !open && (
        <p className="mt-1 text-xs text-muted">Describes what your tool does. Pick from the list or type a custom ID.</p>
      )}
    </div>
  );
}
