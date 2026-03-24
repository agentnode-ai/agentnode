export const dynamic = "force-dynamic";

import Link from "next/link";
import type { Metadata } from "next";
import SafeImage from "@/components/blog/SafeImage";

export const metadata: Metadata = {
  title: "Blog — AI Agent Skills, MCP Tools & Agentic AI Insights",
  description: "Expert articles on AI agent skills, MCP servers, agent tools, and agentic AI. Tutorials, guides, and insights for developers building with AI agents.",
  openGraph: {
    title: "AgentNode Blog — AI Agent Skills & Tools Insights",
    description: "Expert articles on AI agent skills, MCP servers, agent tools, and agentic AI for developers.",
    type: "website",
    url: "https://agentnode.net/blog",
    siteName: "AgentNode",
  },
  twitter: {
    card: "summary",
    site: "@AgentNodenet",
  },
};

interface Post {
  id: string;
  title: string;
  slug: string;
  excerpt: string | null;
  cover_image_url: string | null;
  category: { id: string; name: string; slug: string } | null;
  author: { id: string; username: string };
  published_at: string | null;
  reading_time_min: number | null;
  is_featured: boolean;
}

interface Category {
  id: string;
  name: string;
  slug: string;
  post_count: number;
}

async function getPosts(page = 1) {
  const url = `${process.env.BACKEND_URL || "http://localhost:8001"}/v1/blog/posts?page=${page}&per_page=50&post_type=post`;
  const res = await fetch(url, { next: { revalidate: 60 } });
  if (!res.ok) return { posts: [], total: 0, page: 1, per_page: 20 };
  return res.json();
}

async function getCategories() {
  const url = `${process.env.BACKEND_URL || "http://localhost:8001"}/v1/blog/categories`;
  const res = await fetch(url, { next: { revalidate: 60 } });
  if (!res.ok) return [];
  return res.json();
}

export default async function BlogPage() {
  const [postsData, categories] = await Promise.all([getPosts(), getCategories()]);
  const posts: Post[] = postsData.posts || [];
  const cats: Category[] = categories || [];

  const featured = posts.filter((p) => p.is_featured);
  const regular = posts.filter((p) => !p.is_featured);

  return (
    <div className="mx-auto max-w-5xl px-6 py-16">
      {/* Hero */}
      <div className="mb-12 text-center">
        <h1 className="mb-3 text-4xl font-bold tracking-tight sm:text-5xl">Blog</h1>
        <p className="text-lg text-muted">
          Tutorials, product updates, and engineering insights from the AgentNode team.
        </p>
      </div>

      {/* Categories */}
      {cats.length > 0 && (
        <div className="mb-10 flex flex-wrap justify-center gap-2">
          <Link href="/blog" className="rounded-full bg-primary/10 px-4 py-1.5 text-sm font-medium text-primary">
            All
          </Link>
          {cats.filter((c) => c.post_count > 0).map((cat) => (
            <Link
              key={cat.id}
              href={`/blog/category/${cat.slug}`}
              className="rounded-full bg-card px-4 py-1.5 text-sm text-muted transition-colors hover:bg-primary/10 hover:text-primary"
            >
              {cat.name} ({cat.post_count})
            </Link>
          ))}
        </div>
      )}

      {posts.length === 0 ? (
        <div className="py-20 text-center text-muted">
          <p className="text-lg">No posts yet. Check back soon!</p>
        </div>
      ) : (
        <>
          {/* Featured posts */}
          {featured.length > 0 && (
            <div className="mb-12 space-y-6">
              {featured.map((post) => (
                <Link key={post.id} href={`/blog/${post.slug}`} className="group block">
                  <article className="overflow-hidden rounded-xl border border-border bg-card transition-colors hover:border-primary/30">
                    {post.cover_image_url && (
                      <SafeImage src={post.cover_image_url} alt={post.title} className="h-64 w-full object-cover" />
                    )}
                    <div className="p-6">
                      <div className="mb-2 flex items-center gap-3 text-xs text-muted">
                        {post.category && (
                          <span className="rounded-full bg-primary/10 px-2 py-0.5 text-primary">{post.category.name}</span>
                        )}
                        {post.published_at && <span>{new Date(post.published_at).toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric" })}</span>}
                        {post.reading_time_min && <span>{post.reading_time_min} min read</span>}
                      </div>
                      <h2 className="mb-2 text-2xl font-bold group-hover:text-primary">{post.title}</h2>
                      {post.excerpt && <p className="text-muted">{post.excerpt}</p>}
                    </div>
                  </article>
                </Link>
              ))}
            </div>
          )}

          {/* Regular posts grid */}
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {regular.map((post) => (
              <Link key={post.id} href={`/blog/${post.slug}`} className="group block">
                <article className="h-full overflow-hidden rounded-xl border border-border bg-card transition-colors hover:border-primary/30">
                  {post.cover_image_url && (
                    <SafeImage src={post.cover_image_url} alt={post.title} className="h-40 w-full object-cover" />
                  )}
                  <div className="p-5">
                    <div className="mb-2 flex items-center gap-2 text-xs text-muted">
                      {post.category && (
                        <span className="rounded-full bg-primary/10 px-2 py-0.5 text-primary">{post.category.name}</span>
                      )}
                      {post.reading_time_min && <span>{post.reading_time_min} min read</span>}
                    </div>
                    <h3 className="mb-2 text-lg font-semibold group-hover:text-primary">{post.title}</h3>
                    {post.excerpt && <p className="line-clamp-2 text-sm text-muted">{post.excerpt}</p>}
                    <div className="mt-3 text-xs text-muted">
                      {post.published_at && new Date(post.published_at).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" })}
                    </div>
                  </div>
                </article>
              </Link>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
