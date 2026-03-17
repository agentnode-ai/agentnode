"use client";

import { useState, useEffect, createContext, useContext } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { fetchWithAuth } from "@/lib/api";

interface AdminUser {
  id: string;
  email: string;
  username: string;
  is_admin: boolean;
}

const AdminContext = createContext<AdminUser | null>(null);
export function useAdminUser() {
  return useContext(AdminContext);
}

const navItems = [
  { href: "/admin", label: "Overview", icon: "◆" },
  { href: "/admin/users", label: "Users", icon: "●" },
  { href: "/admin/publishers", label: "Publishers", icon: "▲" },
  { href: "/admin/packages", label: "Packages", icon: "■" },
  { href: "/admin/reports", label: "Reports", icon: "!" },
  { href: "/admin/capabilities", label: "Capabilities", icon: "⚙" },
  { href: "/admin/installations", label: "Installations", icon: "↓" },
  { href: "/admin/email", label: "Email / SMTP", icon: "✉" },
  { href: "/admin/audit", label: "Audit Log", icon: "☰" },
];

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [user, setUser] = useState<AdminUser | null>(null);
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    fetchWithAuth("/auth/me")
      .then((res) => {
        if (!res.ok) throw new Error("unauth");
        return res.json();
      })
      .then((data) => {
        if (!data.is_admin) {
          router.push("/dashboard");
        } else {
          setUser(data);
          setChecked(true);
        }
      })
      .catch(() => router.push("/auth/login"));
  }, [router]);

  if (!checked) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center text-muted">
        Checking access...
      </div>
    );
  }

  return (
    <AdminContext.Provider value={user}>
      <div className="mx-auto flex max-w-7xl gap-6 px-4 sm:px-6 py-8">
        {/* Sidebar */}
        <aside className="hidden md:block w-52 shrink-0">
          <div className="sticky top-24 space-y-1">
            <div className="mb-4 px-3 text-xs font-semibold uppercase tracking-wider text-muted">
              Admin Panel
            </div>
            {navItems.map((item) => {
              const isActive = pathname === item.href;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`flex items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors ${
                    isActive
                      ? "bg-primary/10 text-primary font-medium"
                      : "text-muted hover:text-foreground hover:bg-card"
                  }`}
                >
                  <span className="w-4 text-center text-xs">{item.icon}</span>
                  {item.label}
                </Link>
              );
            })}
          </div>
        </aside>

        {/* Mobile nav */}
        <div className="md:hidden fixed bottom-0 left-0 right-0 z-50 border-t border-border bg-background/95 backdrop-blur-md px-2 py-1.5">
          <div className="flex justify-around">
            {navItems.slice(0, 5).map((item) => {
              const isActive = pathname === item.href;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`flex flex-col items-center gap-0.5 px-2 py-1 text-xs ${
                    isActive ? "text-primary" : "text-muted"
                  }`}
                >
                  <span>{item.icon}</span>
                  <span>{item.label}</span>
                </Link>
              );
            })}
          </div>
        </div>

        {/* Main content */}
        <main className="min-w-0 flex-1">{children}</main>
      </div>
    </AdminContext.Provider>
  );
}
