export async function GET() {
  const body = `User-agent: *
Allow: /
Disallow: /admin/
Disallow: /auth/
Disallow: /dashboard/
Disallow: /api/
Sitemap: https://agentnode.net/sitemap.xml
`;

  return new Response(body, {
    headers: {
      "Content-Type": "text/plain",
      "Cache-Control": "public, max-age=86400",
    },
  });
}
