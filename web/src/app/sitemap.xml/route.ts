const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8001";
const SITE_URL = "https://agentnode.net";

interface PostType {
  slug: string;
}

export async function GET() {
  let postTypes: PostType[] = [];

  try {
    const res = await fetch(`${BACKEND_URL}/v1/blog/post-types`, {
      next: { revalidate: 300 },
    });
    if (res.ok) {
      postTypes = await res.json();
    }
  } catch {
    // Graceful degradation — only pages.xml
  }

  const sitemaps = [
    `<sitemap><loc>${SITE_URL}/sitemap/pages.xml</loc></sitemap>`,
    ...postTypes.map(
      (pt) => `<sitemap><loc>${SITE_URL}/sitemap/${pt.slug}.xml</loc></sitemap>`
    ),
    `<sitemap><loc>${SITE_URL}/sitemap/packages.xml</loc></sitemap>`,
    `<sitemap><loc>${SITE_URL}/sitemap/publishers.xml</loc></sitemap>`,
  ];

  const xml = `<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${sitemaps.join("\n")}
</sitemapindex>`;

  return new Response(xml, {
    headers: {
      "Content-Type": "application/xml",
      "Cache-Control": "public, max-age=300",
    },
  });
}
