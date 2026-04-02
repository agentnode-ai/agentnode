import { NextRequest } from "next/server";

import { BACKEND_URL } from "@/lib/constants";
const SITE_URL = "https://agentnode.net";

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
      const res = await fetch(`${BACKEND_URL}/v1/sitemap/pages`, { next: { revalidate: 300 } });
      if (res.ok) {
        const data = await res.json();
        urls = (data.items as SitemapItem[]).map((item) =>
          buildUrlEntry(item.path!, undefined, item.changefreq, item.priority)
        );
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
        urls = (data.items as SitemapItem[]).map((item) =>
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
