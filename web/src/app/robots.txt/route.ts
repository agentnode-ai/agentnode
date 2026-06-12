// AI crawler policy (deliberate, 2026-06): AgentNode is an SDK — being known
// to AI systems IS distribution. We therefore explicitly ALLOW both classes:
// - search/citation crawlers (OAI-SearchBot, Claude-SearchBot, PerplexityBot,
//   ChatGPT-User, Claude-User, Perplexity-User, Bingbot) so AI answers can
//   cite agentnode.net, and
// - training crawlers (GPTBot, ClaudeBot, Google-Extended) so coding
//   assistants know AgentNode natively.
// The explicit groups make the policy auditable and protect it from a future
// blanket change to the `*` group.
const AI_CRAWLERS = [
  "GPTBot",
  "OAI-SearchBot",
  "ChatGPT-User",
  "ClaudeBot",
  "Claude-SearchBot",
  "Claude-User",
  "PerplexityBot",
  "Perplexity-User",
  "Google-Extended",
  "Bingbot",
];

export async function GET() {
  const aiSections = AI_CRAWLERS.map(
    (bot) => `User-agent: ${bot}\nAllow: /\n`
  ).join("\n");

  const body = `User-agent: *
Allow: /
Disallow: /admin/
Disallow: /auth/
Disallow: /dashboard/
Disallow: /api/

# AI crawlers — explicitly allowed (citations + assistant knowledge are
# distribution channels for an SDK).
${aiSections}
Sitemap: https://agentnode.net/sitemap.xml
`;

  return new Response(body, {
    headers: {
      "Content-Type": "text/plain",
      "Cache-Control": "public, max-age=86400",
    },
  });
}
