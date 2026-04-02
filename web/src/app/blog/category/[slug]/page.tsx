export const dynamic = "force-dynamic";

import Link from "next/link";
import type { Metadata } from "next";
import SafeImage from "@/components/blog/SafeImage";
import { BACKEND_URL } from "@/lib/constants";

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

async function getPosts(categorySlug: string) {
  const url = `${BACKEND_URL}/v1/blog/posts?category=${categorySlug}&per_page=50`;
  const res = await fetch(url, { next: { revalidate: 60 } });
  if (!res.ok) return { posts: [], total: 0 };
  return res.json();
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const { slug } = await params;
  const name = slug.replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  return {
    title: `${name} — AgentNode Blog`,
    description: `Browse ${name} articles on the AgentNode blog.`,
  };
}

export default async function CategoryPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const data = await getPosts(slug);
  const posts: Post[] = data.posts || [];
  const name = slug.replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

  return (
    <div className="mx-auto max-w-5xl px-6 py-16">
      <div className="mb-10">
        <Link href="/blog" className="text-sm text-muted hover:text-foreground">← All posts</Link>
        <h1 className="mt-3 text-3xl font-bold">{name}</h1>
        <p className="mt-1 text-muted">{posts.length} article{posts.length !== 1 ? "s" : ""}</p>
      </div>

      {posts.length === 0 ? (
        <div className="py-16 text-center text-muted">No posts in this category yet.</div>
      ) : (
        <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {posts.map((post) => (
            <Link key={post.id} href={`/blog/${post.slug}`} className="group block">
              <article className="h-full overflow-hidden rounded-xl border border-border bg-card transition-colors hover:border-primary/30">
                {post.cover_image_url && (
                  <SafeImage src={post.cover_image_url} alt={post.title} className="h-40 w-full object-cover" />
                )}
                <div className="p-5">
                  <div className="mb-2 flex items-center gap-2 text-xs text-muted">
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
      )}
    </div>
  );
}
