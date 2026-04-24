import Link from "next/link";
import { notFound } from "next/navigation";
import type { Metadata } from "next";
import SafeImage from "@/components/blog/SafeImage";

import { BACKEND_URL } from "@/lib/constants";

interface PostType {
  id: string;
  name: string;
  slug: string;
  url_prefix: string;
  has_archive: boolean;
  archive_title: string | null;
  archive_description: string | null;
}

interface Post {
  id: string;
  title: string;
  slug: string;
  excerpt: string | null;
  cover_image_url: string | null;
  category: { id: string; name: string; slug: string } | null;
  post_type: { id: string; name: string; slug: string; url_prefix: string } | null;
  author: { id: string; username: string };
  published_at: string | null;
  reading_time_min: number | null;
  is_featured: boolean;
}

async function getPostType(slug: string): Promise<PostType | null> {
  try {
    const res = await fetch(`${BACKEND_URL}/v1/blog/post-types`, { next: { revalidate: 60 } });
    if (!res.ok) return null;
    const types: PostType[] = await res.json();
    return types.find((t) => t.slug === slug) || null;
  } catch {
    return null;
  }
}

async function getPosts(postTypeSlug: string, page = 1) {
  try {
    const url = `${BACKEND_URL}/v1/blog/posts?post_type=${postTypeSlug}&page=${page}&per_page=50`;
    const res = await fetch(url, { next: { revalidate: 60 } });
    if (!res.ok) return { posts: [], total: 0, page: 1, per_page: 50 };
    return res.json();
  } catch {
    return { posts: [], total: 0, page: 1, per_page: 50 };
  }
}

export async function generateArchiveMetadata(postTypeSlug: string): Promise<Metadata> {
  const pt = await getPostType(postTypeSlug);
  if (!pt || !pt.has_archive) return { title: "Not Found — AgentNode" };

  const title = pt.archive_title || pt.name;
  const description = pt.archive_description || `${pt.name} from the AgentNode team.`;

  return {
    title: `${title} — AgentNode`,
    description,
    openGraph: {
      title: `${title} — AgentNode`,
      description,
      type: "website",
      url: `https://agentnode.net/${pt.url_prefix}`,
      siteName: "AgentNode",
    },
    twitter: {
      card: "summary",
      site: "@AgentNodenet",
    },
    alternates: {
      canonical: `https://agentnode.net/${pt.url_prefix}`,
    },
  };
}

export default async function PostTypeArchive({ postTypeSlug }: { postTypeSlug: string }) {
  const pt = await getPostType(postTypeSlug);
  if (!pt || !pt.has_archive) notFound();

  const postsData = await getPosts(postTypeSlug);
  const posts: Post[] = postsData.posts || [];

  const featured = posts.filter((p) => p.is_featured);
  const regular = posts.filter((p) => !p.is_featured);

  return (
    <div className="mx-auto max-w-5xl px-6 py-16">
      <div className="mb-12 text-center">
        <h1 className="mb-3 text-4xl font-bold tracking-tight sm:text-5xl">
          {pt.archive_title || pt.name}
        </h1>
        {pt.archive_description && (
          <p className="text-lg text-muted">{pt.archive_description}</p>
        )}
      </div>

      {posts.length === 0 ? (
        <div className="py-20 text-center text-muted">
          <p className="text-lg">No {pt.name.toLowerCase()} posts yet. Check back soon!</p>
        </div>
      ) : (
        <>
          {featured.length > 0 && (
            <div className="mb-12 space-y-6">
              {featured.map((post) => (
                <Link key={post.id} href={`/${pt.url_prefix}/${post.slug}`} className="group block">
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

          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {regular.map((post) => (
              <Link key={post.id} href={`/${pt.url_prefix}/${post.slug}`} className="group block">
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
