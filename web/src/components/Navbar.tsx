"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { refreshSession } from "@/lib/api";

const navLinks = [
  { href: "/agents", label: "Agents" },
  { href: "/import", label: "Import" },
  { href: "/builder", label: "Builder" },
  { href: "/search", label: "Search" },
  { href: "/compatibility", label: "Models" },
  { href: "/for-developers", label: "For Developers" },
  { href: "/publish", label: "Publish" },
  { href: "/blog", label: "Blog" },
  { href: "/tutorials", label: "Tutorials" },
  { href: "/getting-started", label: "Getting Started" },
  { href: "/docs", label: "Docs" },
  { href: "https://github.com/agentnode-ai/agentnode", label: "GitHub", external: true },
];

export default function Navbar() {
  const pathname = usePathname();
  const router = useRouter();
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [isAdmin, setIsAdmin] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

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

  // Close mobile menu on navigation
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- Close mobile menu on navigation
    setMobileOpen(false);
  }, [pathname]);

  async function handleLogout() {
    await fetch("/api/v1/auth/logout", { method: "POST", credentials: "include" });
    setIsLoggedIn(false);
    router.push("/");
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

        {/* Desktop nav — P0-12: raised breakpoint from md (768) to lg
            (1024) because the full link set (10 + auth buttons) does not
            fit cleanly at 768-1023px and caused wrap/horizontal-scroll
            artifacts in that range. */}
        <div className="hidden lg:flex items-center gap-1">
          {navLinks.map((link) => {
            const isActive = !link.external && pathname === link.href;
            if (link.external) {
              return (
                <a
                  key={link.href}
                  href={link.href}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="rounded-md px-3 py-2 text-sm text-muted transition-colors hover:text-foreground"
                >
                  {link.label}
                  <span className="ml-1 text-xs">&#8599;</span>
                </a>
              );
            }
            return (
              <Link
                key={link.href}
                href={link.href}
                className={`rounded-md px-3 py-2 text-sm transition-colors ${
                  (link as { highlight?: boolean }).highlight && !isActive
                    ? "text-primary font-medium hover:text-primary/80"
                    : isActive
                      ? "text-foreground bg-card"
                      : "text-muted hover:text-foreground"
                }`}
              >
                {link.label}
              </Link>
            );
          })}

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
              className="ml-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-primary/90"
            >
              Sign in
            </Link>
          )}
        </div>

        {/* Mobile hamburger — P0-13: aria-expanded + aria-controls so
            assistive tech can report the menu's open/closed state. */}
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

      {/* Mobile menu */}
      {mobileOpen && (
        <div
          id="primary-mobile-menu"
          className="lg:hidden border-t border-border bg-background/95 backdrop-blur-md px-6 pb-4 pt-2"
        >
          <div className="flex flex-col gap-1">
            {navLinks.map((link) => {
              const isActive = !link.external && pathname === link.href;
              if (link.external) {
                return (
                  <a
                    key={link.href}
                    href={link.href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="rounded-md px-3 py-2 text-sm text-muted transition-colors hover:text-foreground"
                  >
                    {link.label}
                    <span className="ml-1 text-xs">&#8599;</span>
                  </a>
                );
              }
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  className={`rounded-md px-3 py-2 text-sm transition-colors ${
                    isActive
                      ? "text-foreground bg-card"
                      : "text-muted hover:text-foreground"
                  }`}
                >
                  {link.label}
                </Link>
              );
            })}

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
                  className="rounded-md px-3 py-2 text-sm text-muted text-left transition-colors hover:text-foreground"
                >
                  Logout
                </button>
              </>
            ) : (
              <Link
                href="/auth/login"
                className="mt-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-white text-center transition-colors hover:bg-primary/90"
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
