import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import type { Metadata } from "next";

const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8001";

interface Post {
  id: string;
  title: string;
  slug: string;
  content_html: string | null;
  excerpt: string | null;
  cover_image_url: string | null;
  cover_image_alt: string | null;
  seo_title: string | null;
  seo_description: string | null;
  og_image_url: string | null;
  category: { id: string; name: string; slug: string } | null;
  post_type: { id: string; name: string; slug: string; url_prefix: string } | null;
  author: { id: string; username: string };
  tags: string[];
  published_at: string | null;
  reading_time_min: number | null;
}

async function getPost(slug: string): Promise<Post | null> {
  const res = await fetch(`${BACKEND_URL}/v1/blog/posts/${slug}`, { next: { revalidate: 60 } });
  if (!res.ok) return null;
  return res.json();
}

async function resolveRedirect(urlPrefix: string, slug: string): Promise<string | null> {
  const path = `/${urlPrefix}/${slug}`;
  const res = await fetch(`${BACKEND_URL}/v1/blog/resolve?path=${encodeURIComponent(path)}`, {
    next: { revalidate: 60 },
  });
  if (!res.ok) return null;
  const data = await res.json();
  return data.redirect_to || null;
}

export async function generateSingleMetadata(
  postTypeSlug: string,
  slug: string,
  urlPrefix: string
): Promise<Metadata> {
  const post = await getPost(slug);
  if (!post || post.post_type?.slug !== postTypeSlug) {
    return { title: "Post not found — AgentNode" };
  }

  const title = post.seo_title || post.title;
  const description = post.seo_description || post.excerpt || "";
  const image = post.og_image_url || post.cover_image_url;
  const typeName = post.post_type?.name || "Blog";

  return {
    title: `${title} — AgentNode ${typeName}`,
    description,
    openGraph: {
      title,
      description,
      type: "article",
      url: `https://agentnode.net/${urlPrefix}/${post.slug}`,
      siteName: "AgentNode",
      ...(image ? { images: [{ url: image, width: 1200, height: 630 }] } : {}),
      ...(post.published_at ? { publishedTime: post.published_at } : {}),
      ...(post.category ? { section: post.category.name } : {}),
    },
    twitter: {
      card: image ? "summary_large_image" : "summary",
      site: "@AgentNodenet",
      title,
      description,
      ...(image ? { images: [image] } : {}),
    },
    alternates: {
      canonical: `https://agentnode.net/${urlPrefix}/${post.slug}`,
    },
  };
}

export default async function PostTypeSingle({
  postTypeSlug,
  slug,
  urlPrefix,
}: {
  postTypeSlug: string;
  slug: string;
  urlPrefix: string;
}) {
  const post = await getPost(slug);

  // If post exists but belongs to a different type, or if not found, try redirect
  if (!post || post.post_type?.slug !== postTypeSlug) {
    const redirectTo = await resolveRedirect(urlPrefix, slug);
    if (redirectTo) {
      redirect(redirectTo);
    }
    notFound();
  }

  const typeName = post.post_type?.name || "Blog";
  const typePrefix = post.post_type?.url_prefix || urlPrefix;

  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "BlogPosting",
    headline: post.title,
    description: post.seo_description || post.excerpt || "",
    ...(post.cover_image_url ? { image: post.og_image_url || post.cover_image_url } : {}),
    ...(post.published_at ? { datePublished: post.published_at } : {}),
    author: {
      "@type": "Person",
      name: post.author.username,
    },
    publisher: {
      "@type": "Organization",
      name: "AgentNode",
      url: "https://agentnode.net",
    },
    mainEntityOfPage: {
      "@type": "WebPage",
      "@id": `https://agentnode.net/${typePrefix}/${post.slug}`,
    },
    ...(post.reading_time_min ? { wordCount: post.reading_time_min * 200 } : {}),
    ...(post.category ? { articleSection: post.category.name } : {}),
    ...(post.tags?.length ? { keywords: post.tags.join(", ") } : {}),
  };

  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }} />

      <article className="mx-auto max-w-3xl px-6 py-16">
        {/* Breadcrumb */}
        <nav className="mb-8 flex items-center gap-2 text-sm text-muted">
          <Link href={`/${typePrefix}`} className="hover:text-foreground">{typeName}</Link>
          {post.category && (
            <>
              <span>/</span>
              <span>{post.category.name}</span>
            </>
          )}
          <span>/</span>
          <span className="text-foreground">{post.title}</span>
        </nav>

        {/* Header */}
        <header className="mb-8">
          <div className="mb-4 flex items-center gap-3 text-sm text-muted">
            {post.category && (
              <span className="rounded-full bg-primary/10 px-3 py-1 text-primary">
                {post.category.name}
              </span>
            )}
            {post.published_at && (
              <time dateTime={post.published_at}>
                {new Date(post.published_at).toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric" })}
              </time>
            )}
            {post.reading_time_min && <span>{post.reading_time_min} min read</span>}
          </div>

          <h1 className="mb-4 text-3xl font-bold tracking-tight sm:text-4xl lg:text-5xl">{post.title}</h1>

          {post.cover_image_url && (
            <figure className="my-6">
              <img
                src={post.cover_image_url}
                alt={post.cover_image_alt || post.title}
                className="w-full rounded-xl"
              />
            </figure>
          )}

          {post.excerpt && (
            <p className="text-lg text-muted">{post.excerpt}</p>
          )}

          <div className="mt-4 text-sm text-muted">
            By <span className="text-foreground">{post.author.username}</span>
          </div>
        </header>

        {/* Content */}
        {post.content_html && (
          <div
            className="prose prose-invert max-w-none prose-headings:font-bold prose-h2:text-2xl prose-h3:text-xl prose-a:text-primary prose-code:text-primary prose-pre:bg-card prose-pre:border prose-pre:border-border prose-img:rounded-lg"
            dangerouslySetInnerHTML={{ __html: post.content_html }}
          />
        )}

        {/* Tags */}
        {post.tags?.length > 0 && (
          <div className="mt-12 flex flex-wrap gap-2 border-t border-border pt-6">
            {post.tags.map((tag) => (
              <span
                key={tag}
                className="rounded-full bg-card px-3 py-1 text-sm text-muted"
              >
                #{tag}
              </span>
            ))}
          </div>
        )}

        {/* Back link */}
        <div className="mt-12 border-t border-border pt-6 text-center">
          <Link href={`/${typePrefix}`} className="text-primary hover:underline">
            ← Back to {typeName}
          </Link>
        </div>
      </article>
    </>
  );
}
