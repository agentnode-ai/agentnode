"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

interface Entry {
  href: string;
  label: string;
}

interface Group {
  title: string;
  entries: Entry[];
}

/** Persistent docs sidebar with active-page highlighting. */
export default function DocsNav({ groups }: { groups: Group[] }) {
  const pathname = usePathname();

  return (
    <nav className="hidden w-56 shrink-0 lg:block">
      <div className="sticky top-24 max-h-[calc(100vh-7rem)] overflow-y-auto pr-1">
        <Link
          href="/docs"
          className={`mb-4 block text-sm font-semibold ${
            pathname === "/docs" ? "text-primary" : "text-foreground hover:text-primary"
          }`}
        >
          Documentation
        </Link>
        {groups.map((group) => (
          <div key={group.title} className="mb-5">
            <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted">
              {group.title}
            </p>
            <ul className="space-y-0.5">
              {group.entries.map((entry) => (
                <li key={entry.href}>
                  <Link
                    href={entry.href}
                    className={`block rounded px-3 py-1 text-sm transition-colors ${
                      pathname === entry.href
                        ? "bg-primary/10 font-medium text-primary"
                        : "text-muted hover:text-foreground"
                    }`}
                  >
                    {entry.label}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </nav>
  );
}
