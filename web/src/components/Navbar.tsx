"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

const navLinks = [
  { href: "/import", label: "Import" },
  { href: "/builder", label: "Builder" },
  { href: "/search", label: "Search" },
  { href: "/for-developers", label: "For Developers" },
  { href: "/publish", label: "Publish" },
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
    // Check the non-httpOnly `logged_in` cookie set by the server
    const cookies = document.cookie.split("; ");
    setIsLoggedIn(cookies.some((c) => c.startsWith("logged_in=")));
    setIsAdmin(cookies.some((c) => c.startsWith("is_admin=")));
  }, [pathname]);

  // Close mobile menu on route change
  useEffect(() => {
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

        {/* Desktop nav */}
        <div className="hidden md:flex items-center gap-1">
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
                  (link as any).highlight && !isActive
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

        {/* Mobile hamburger */}
        <button
          onClick={() => setMobileOpen(!mobileOpen)}
          className="md:hidden flex flex-col gap-1.5 p-2"
          aria-label="Toggle menu"
        >
          <span className={`block h-0.5 w-5 bg-foreground transition-transform ${mobileOpen ? "translate-y-2 rotate-45" : ""}`} />
          <span className={`block h-0.5 w-5 bg-foreground transition-opacity ${mobileOpen ? "opacity-0" : ""}`} />
          <span className={`block h-0.5 w-5 bg-foreground transition-transform ${mobileOpen ? "-translate-y-2 -rotate-45" : ""}`} />
        </button>
      </div>

      {/* Mobile menu */}
      {mobileOpen && (
        <div className="md:hidden border-t border-border bg-background/95 backdrop-blur-md px-6 pb-4 pt-2">
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
