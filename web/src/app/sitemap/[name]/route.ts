import { NextRequest } from "next/server";

import { BACKEND_URL } from "@/lib/constants";
const SITE_URL = "https://agentnode.net";

// Static pages that live in the Next.js app (not in the backend page table) —
// merged into pages.xml so they are not invisible to crawlers.
const STATIC_PAGES: { path: string; changefreq: string; priority: number }[] = [
  { path: "/agents", changefreq: "weekly", priority: 0.8 },
  { path: "/mcp", changefreq: "weekly", priority: 0.8 },
  { path: "/compatibility", changefreq: "weekly", priority: 0.8 },
  { path: "/security", changefreq: "weekly", priority: 0.8 },
  { path: "/for-companies", changefreq: "weekly", priority: 0.7 },
  { path: "/changelog", changefreq: "weekly", priority: 0.7 },
  { path: "/docs/quickstart", changefreq: "weekly", priority: 0.7 },
  { path: "/docs/sandbox", changefreq: "weekly", priority: 0.7 },
  { path: "/docs/credentials", changefreq: "weekly", priority: 0.7 },
  { path: "/docs/llm-providers", changefreq: "weekly", priority: 0.7 },
  { path: "/docs/cli", changefreq: "weekly", priority: 0.7 },
  { path: "/docs/installation", changefreq: "monthly", priority: 0.6 },
  { path: "/docs/discovery", changefreq: "monthly", priority: 0.6 },
  { path: "/docs/packages", changefreq: "monthly", priority: 0.6 },
  { path: "/docs/llm-runtime", changefreq: "monthly", priority: 0.6 },
  { path: "/docs/agents", changefreq: "monthly", priority: 0.6 },
  { path: "/docs/python-sdk", changefreq: "monthly", priority: 0.6 },
  { path: "/docs/publishing", changefreq: "monthly", priority: 0.6 },
  { path: "/docs/github-action", changefreq: "monthly", priority: 0.6 },
  { path: "/docs/manifest", changefreq: "monthly", priority: 0.6 },
  { path: "/docs/rest-api", changefreq: "monthly", priority: 0.6 },
  { path: "/docs/mcp", changefreq: "monthly", priority: 0.6 },
  { path: "/docs/verification", changefreq: "monthly", priority: 0.6 },
  { path: "/docs/trust", changefreq: "monthly", priority: 0.6 },
  { path: "/docs/guard", changefreq: "monthly", priority: 0.6 },
  { path: "/docs/data-sovereignty", changefreq: "monthly", priority: 0.6 },
  { path: "/docs/import", changefreq: "monthly", priority: 0.6 },
  { path: "/docs/security", changefreq: "monthly", priority: 0.6 },
  { path: "/faq", changefreq: "monthly", priority: 0.6 },
  { path: "/getting-started", changefreq: "monthly", priority: 0.9 },
];

// Internal/test accounts that must never appear in the public sitemap.
const PUBLISHER_BLOCKLIST = new Set(["e2e-test-pub-0320", "verify-publisher"]);

interface SitemapItem {
  slug?: string;
  updated_at?: string;
  url_prefix?: string;
  path?: string;
  priority?: number;
  changefreq?: string;
}

function buildUrlEntry(loc: string, lastmod?: string, changefreq?: string, priority?: number): string {
  let entry = `<url><loc>${SITE_URL}${loc}</loc>`;
  if (lastmod) entry += `<lastmod>${lastmod.split("T")[0]}</lastmod>`;
  if (changefreq) entry += `<changefreq>${changefreq}</changefreq>`;
  if (priority !== undefined) entry += `<priority>${priority}</priority>`;
  entry += `</url>`;
  return entry;
}

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ name: string }> }
) {
  const { name } = await params;
  const sitemapName = name.replace(/\.xml$/, "");

  let urls: string[] = [];

  try {
    if (sitemapName === "pages") {
      const backendPaths = new Set<string>();
      try {
        const res = await fetch(`${BACKEND_URL}/v1/sitemap/pages`, { next: { revalidate: 300 } });
        if (res.ok) {
          const data = await res.json();
          urls = (data.items as SitemapItem[]).map((item) =>
            buildUrlEntry(item.path!, undefined, item.changefreq, item.priority)
          );
          for (const item of data.items as SitemapItem[]) {
            if (item.path) backendPaths.add(item.path);
          }
        }
      } catch {
        // backend unreachable — still emit the static pages below
      }
      for (const page of STATIC_PAGES) {
        if (!backendPaths.has(page.path)) {
          urls.push(buildUrlEntry(page.path, undefined, page.changefreq, page.priority));
        }
      }
    } else if (sitemapName === "packages") {
      const res = await fetch(`${BACKEND_URL}/v1/sitemap/packages`, { next: { revalidate: 300 } });
      if (res.ok) {
        const data = await res.json();
        urls = (data.items as SitemapItem[]).map((item) =>
          buildUrlEntry(`/packages/${item.slug}`, item.updated_at, "weekly", 0.5)
        );
      }
    } else if (sitemapName === "publishers") {
      const res = await fetch(`${BACKEND_URL}/v1/sitemap/publishers`, { next: { revalidate: 300 } });
      if (res.ok) {
        const data = await res.json();
        urls = (data.items as SitemapItem[])
          .filter((item) => !PUBLISHER_BLOCKLIST.has(item.slug ?? ""))
          .map((item) =>
            buildUrlEntry(`/publishers/${item.slug}`, item.updated_at, "weekly", 0.5)
          );
      }
    } else {
      // Post type slug (e.g., "post", "tutorial", "changelog", "case-study")
      const res = await fetch(`${BACKEND_URL}/v1/sitemap/posts/${sitemapName}`, { next: { revalidate: 300 } });
      if (res.ok) {
        const data = await res.json();
        urls = (data.items as SitemapItem[]).map((item) =>
          buildUrlEntry(`/${item.url_prefix}/${item.slug}`, item.updated_at, "weekly", 0.6)
        );
      }
    }
  } catch {
    // Return empty sitemap on error
  }

  const xml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${urls.join("\n")}
</urlset>`;

  return new Response(xml, {
    headers: {
      "Content-Type": "application/xml",
      "Cache-Control": "public, max-age=300",
    },
  });
}
