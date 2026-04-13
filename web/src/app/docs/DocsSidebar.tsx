"use client";

import { useState, useEffect } from "react";
import Link from "next/link";

export interface DocSection {
  id: string;
  label: string;
}

/**
 * Client-side sidebar for the docs page. Hosts the scroll-spy state so
 * the surrounding page can stay a pure server component (P1-SEO4).
 */
export default function DocsSidebar({ sections }: { sections: DocSection[] }) {
  const [activeSection, setActiveSection] = useState(sections[0]?.id ?? "");

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setActiveSection(entry.target.id);
          }
        }
      },
      { rootMargin: "-80px 0px -70% 0px", threshold: 0 }
    );

    for (const section of sections) {
      const el = document.getElementById(section.id);
      if (el) observer.observe(el);
    }

    return () => observer.disconnect();
  }, [sections]);

  return (
    <nav className="hidden w-56 shrink-0 lg:block">
      <div className="sticky top-24">
        <p className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted">
          On this page
        </p>
        <ul className="space-y-1">
          {sections.map((s) => (
            <li key={s.id}>
              <a
                href={`#${s.id}`}
                className={`block rounded px-3 py-1.5 text-sm transition-colors ${
                  activeSection === s.id
                    ? "bg-primary/10 font-medium text-primary"
                    : "text-muted hover:text-foreground"
                }`}
              >
                {s.label}
              </a>
            </li>
          ))}
        </ul>
        <div className="mt-6 border-t border-border pt-4">
          <Link
            href="/search"
            className="block text-xs text-muted transition-colors hover:text-primary"
          >
            Browse packages
          </Link>
          <Link
            href="/capabilities"
            className="mt-2 block text-xs text-muted transition-colors hover:text-primary"
          >
            Capability taxonomy
          </Link>
          <a
            href="https://github.com/agentnode-ai/agentnode"
            target="_blank"
            rel="noopener noreferrer"
            className="mt-2 block text-xs text-muted transition-colors hover:text-primary"
          >
            GitHub repository
          </a>
        </div>
      </div>
    </nav>
  );
}
