"use client";

import { useState, useEffect, useRef } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { refreshSession } from "@/lib/api";

interface NavItem {
  href: string;
  label: string;
}
interface NavGroup {
  label: string;
  items: NavItem[];
}

// Single source of truth for the audience-oriented IA. Drives both the desktop
// dropdowns and the mobile accordion. ("Docs" is a direct top-level link, not a
// group, and is rendered separately between Publish and Resources.)
export const NAV_GROUPS: NavGroup[] = [
  {
    label: "Product",
    items: [
      { href: "/why-agentnode", label: "Why AgentNode" },
      { href: "/getting-started", label: "Getting Started" },
      { href: "/compatibility", label: "Model Compatibility" },
      { href: "/security", label: "Security" },
      { href: "/changelog", label: "Changelog" },
    ],
  },
  {
    label: "Browse",
    items: [
      { href: "/search", label: "Search" },
      { href: "/agents", label: "Agents" },
      { href: "/mcp", label: "MCP Servers" },
      { href: "/capabilities", label: "Capabilities" },
      { href: "/discover", label: "Trending" },
      { href: "/compare", label: "Compare" },
    ],
  },
  {
    label: "Publish",
    items: [
      { href: "/for-developers", label: "Why publish" },
      { href: "/publish", label: "Publish a package" },
      { href: "/builder", label: "Builder" },
      { href: "/import", label: "Import existing tools" },
      { href: "/docs/verification", label: "Verification & tiers" },
    ],
  },
  {
    label: "Resources",
    items: [
      { href: "/blog", label: "Blog" },
      { href: "/tutorials", label: "Tutorials" },
      { href: "/case-studies", label: "Case Studies" },
      { href: "/faq", label: "FAQ" },
    ],
  },
];

function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      className={`ml-1 h-3 w-3 transition-transform ${open ? "rotate-180" : ""}`}
      viewBox="0 0 12 12"
      fill="none"
      aria-hidden="true"
    >
      <path d="M2.5 4.5 6 8l3.5-3.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export default function Navbar() {
  const pathname = usePathname();
  const router = useRouter();
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [isAdmin, setIsAdmin] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [openDropdown, setOpenDropdown] = useState<string | null>(null);
  const [mobileExpanded, setMobileExpanded] = useState<string | null>(null);
  const desktopNavRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Check the non-httpOnly cookies set by the server (synchronous — no API call needed)
    const cookies = document.cookie.split("; ");
    const loggedIn = cookies.some((c) => c.startsWith("logged_in="));
    const admin = cookies.some((c) => c === "is_admin=1");
    // eslint-disable-next-line react-hooks/set-state-in-effect -- Sync login state from cookie on route change
    setIsLoggedIn(loggedIn);
    // eslint-disable-next-line react-hooks/set-state-in-effect -- Sync admin state from cookie
    setIsAdmin(loggedIn && admin);

    // Proactively refresh access token on every navigation so the user
    // never hits a stale-token 401 on the next API call.
    if (loggedIn) {
      refreshSession().then((valid) => {
        if (!valid) {
          setIsLoggedIn(false);
          setIsAdmin(false);
        }
      });
    }
  }, [pathname]);

  // Close all menus on navigation.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- Close menus on navigation
    setMobileOpen(false);
    // eslint-disable-next-line react-hooks/set-state-in-effect -- Close dropdown on navigation
    setOpenDropdown(null);
    // eslint-disable-next-line react-hooks/set-state-in-effect -- Reset mobile accordion on navigation
    setMobileExpanded(null);
  }, [pathname]);

  // Desktop dropdown: close on Escape and on click outside the nav row.
  useEffect(() => {
    if (!openDropdown) return;
    function onDocMouseDown(e: MouseEvent) {
      if (desktopNavRef.current && !desktopNavRef.current.contains(e.target as Node)) {
        setOpenDropdown(null);
      }
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setOpenDropdown(null);
    }
    document.addEventListener("mousedown", onDocMouseDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onDocMouseDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [openDropdown]);

  async function handleLogout() {
    await fetch("/api/v1/auth/logout", { method: "POST", credentials: "include" });
    setIsLoggedIn(false);
    router.push("/");
  }

  const groupIsActive = (group: NavGroup) =>
    group.items.some((it) => pathname === it.href);
  const docsIsActive = pathname === "/docs" || pathname.startsWith("/docs/");

  function DesktopDropdown({ group }: { group: NavGroup }) {
    const isOpen = openDropdown === group.label;
    const active = groupIsActive(group);
    return (
      <div className="relative">
        <button
          type="button"
          aria-haspopup="true"
          aria-expanded={isOpen}
          onClick={() => setOpenDropdown(isOpen ? null : group.label)}
          className={`flex items-center rounded-md px-3 py-2 text-sm transition-colors ${
            isOpen || active ? "text-foreground bg-card" : "text-muted hover:text-foreground"
          }`}
        >
          {group.label}
          <Chevron open={isOpen} />
        </button>
        {isOpen && (
          <div
            role="menu"
            aria-label={group.label}
            className="absolute left-0 mt-1 min-w-[13rem] rounded-lg border border-border bg-background/95 p-1 shadow-lg backdrop-blur-md"
          >
            {group.items.map((it) => (
              <Link
                key={it.href}
                href={it.href}
                role="menuitem"
                onClick={() => setOpenDropdown(null)}
                className={`block rounded-md px-3 py-2 text-sm transition-colors ${
                  pathname === it.href
                    ? "text-foreground bg-card"
                    : "text-muted hover:text-foreground hover:bg-card/60"
                }`}
              >
                {it.label}
              </Link>
            ))}
          </div>
        )}
      </div>
    );
  }

  function MobileAccordion({ group }: { group: NavGroup }) {
    const isOpen = mobileExpanded === group.label;
    return (
      <div className="border-b border-border/50 last:border-0">
        <button
          type="button"
          aria-expanded={isOpen}
          onClick={() => setMobileExpanded(isOpen ? null : group.label)}
          className="flex w-full items-center justify-between rounded-md px-3 py-2 text-sm text-foreground"
        >
          {group.label}
          <Chevron open={isOpen} />
        </button>
        {isOpen && (
          <div className="pb-2 pl-3">
            {group.items.map((it) => (
              <Link
                key={it.href}
                href={it.href}
                className={`block rounded-md px-3 py-2 text-sm transition-colors ${
                  pathname === it.href
                    ? "text-foreground bg-card"
                    : "text-muted hover:text-foreground"
                }`}
              >
                {it.label}
              </Link>
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    <nav className="sticky top-0 z-50 border-b border-border bg-background/80 backdrop-blur-md">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-6">
        <Link
          href="/"
          className="flex items-center gap-2 text-lg font-semibold tracking-tight text-foreground transition-colors hover:text-primary"
        >
          <span className="text-primary font-mono font-bold">&gt;_</span>
          <span>AgentNode</span>
        </Link>

        {/* Desktop nav — grouped, audience-oriented IA. Breakpoint stays at lg
            (1024); with 5 top-level entries instead of 13 flat links there is
            ample room. */}
        <div ref={desktopNavRef} className="hidden lg:flex items-center gap-1">
          <DesktopDropdown group={NAV_GROUPS[0]} />
          <DesktopDropdown group={NAV_GROUPS[1]} />
          <DesktopDropdown group={NAV_GROUPS[2]} />
          <Link
            href="/docs"
            className={`rounded-md px-3 py-2 text-sm transition-colors ${
              docsIsActive ? "text-foreground bg-card" : "text-muted hover:text-foreground"
            }`}
          >
            Docs
          </Link>
          <DesktopDropdown group={NAV_GROUPS[3]} />

          <Link
            href="/getting-started"
            className="ml-2 rounded-md border border-border px-3 py-2 text-sm font-medium text-foreground transition-colors hover:bg-card"
          >
            Get Started
          </Link>

          {isLoggedIn ? (
            <>
              <Link
                href="/dashboard"
                className={`rounded-md px-3 py-2 text-sm transition-colors ${
                  pathname === "/dashboard"
                    ? "text-foreground bg-card"
                    : "text-muted hover:text-foreground"
                }`}
              >
                Dashboard
              </Link>
              {isAdmin && (
                <Link
                  href="/admin"
                  className={`rounded-md px-3 py-2 text-sm transition-colors ${
                    pathname.startsWith("/admin")
                      ? "text-foreground bg-card"
                      : "text-muted hover:text-foreground"
                  }`}
                >
                  Admin
                </Link>
              )}
              <button
                onClick={handleLogout}
                className="rounded-md px-3 py-2 text-sm text-muted transition-colors hover:text-foreground"
              >
                Logout
              </button>
            </>
          ) : (
            <Link
              href="/auth/login"
              className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-primary/90"
            >
              Sign in
            </Link>
          )}
        </div>

        {/* Mobile hamburger */}
        <button
          type="button"
          onClick={() => setMobileOpen(!mobileOpen)}
          className="lg:hidden flex flex-col gap-1.5 p-2"
          aria-label={mobileOpen ? "Close menu" : "Open menu"}
          aria-expanded={mobileOpen}
          aria-controls="primary-mobile-menu"
        >
          <span className={`block h-0.5 w-5 bg-foreground transition-transform ${mobileOpen ? "translate-y-2 rotate-45" : ""}`} />
          <span className={`block h-0.5 w-5 bg-foreground transition-opacity ${mobileOpen ? "opacity-0" : ""}`} />
          <span className={`block h-0.5 w-5 bg-foreground transition-transform ${mobileOpen ? "-translate-y-2 -rotate-45" : ""}`} />
        </button>
      </div>

      {/* Mobile menu — grouped accordion */}
      {mobileOpen && (
        <div
          id="primary-mobile-menu"
          className="lg:hidden border-t border-border bg-background/95 backdrop-blur-md px-6 pb-4 pt-2"
        >
          <div className="flex flex-col">
            <MobileAccordion group={NAV_GROUPS[0]} />
            <MobileAccordion group={NAV_GROUPS[1]} />
            <MobileAccordion group={NAV_GROUPS[2]} />
            <Link
              href="/docs"
              className={`border-b border-border/50 rounded-md px-3 py-2 text-sm transition-colors ${
                docsIsActive ? "text-foreground bg-card" : "text-foreground hover:text-foreground"
              }`}
            >
              Docs
            </Link>
            <MobileAccordion group={NAV_GROUPS[3]} />

            <Link
              href="/getting-started"
              className="mt-3 rounded-md border border-border px-4 py-2 text-center text-sm font-medium text-foreground transition-colors hover:bg-card"
            >
              Get Started
            </Link>

            {isLoggedIn ? (
              <>
                <Link
                  href="/dashboard"
                  className={`mt-1 rounded-md px-3 py-2 text-sm transition-colors ${
                    pathname === "/dashboard"
                      ? "text-foreground bg-card"
                      : "text-muted hover:text-foreground"
                  }`}
                >
                  Dashboard
                </Link>
                {isAdmin && (
                  <Link
                    href="/admin"
                    className={`rounded-md px-3 py-2 text-sm transition-colors ${
                      pathname.startsWith("/admin")
                        ? "text-foreground bg-card"
                        : "text-muted hover:text-foreground"
                    }`}
                  >
                    Admin
                  </Link>
                )}
                <button
                  onClick={handleLogout}
                  className="rounded-md px-3 py-2 text-left text-sm text-muted transition-colors hover:text-foreground"
                >
                  Logout
                </button>
              </>
            ) : (
              <Link
                href="/auth/login"
                className="mt-2 rounded-md bg-primary px-4 py-2 text-center text-sm font-medium text-white transition-colors hover:bg-primary/90"
              >
                Sign in
              </Link>
            )}
          </div>
        </div>
      )}
    </nav>
  );
}
