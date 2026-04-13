export const revalidate = 3600; // P1-SEO3: ISR (1h) instead of force-dynamic

import Link from "next/link";
import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";
import SafeImage from "@/components/blog/SafeImage";
import FaqSection, { extractFaqFromHtml } from "@/components/blog/FaqSection";
import { sanitizeHtml } from "@/lib/sanitize";

import { BACKEND_URL } from "@/lib/constants";

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
  const url = `${BACKEND_URL}/v1/blog/posts/${slug}`;
  const res = await fetch(url, { next: { revalidate: 60 } });
  if (!res.ok) return null;
  return res.json();
}

async function resolveRedirect(slug: string): Promise<string | null> {
  const path = `/blog/${slug}`;
  const res = await fetch(`${BACKEND_URL}/v1/blog/resolve?path=${encodeURIComponent(path)}`, {
    next: { revalidate: 60 },
  });
  if (!res.ok) return null;
  const data = await res.json();
  return data.redirect_to || null;
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const { slug } = await params;
  const post = await getPost(slug);
  if (!post) return { title: "Post not found — AgentNode" };

  // If post belongs to a different type, metadata doesn't matter (will redirect)
  if (post.post_type && post.post_type.url_prefix !== "blog") {
    return { title: "Redirecting..." };
  }

  const title = post.seo_title || post.title;
  const description = post.seo_description || post.excerpt || "";
  const image = post.og_image_url || post.cover_image_url;

  return {
    title: `${title} — AgentNode Blog`,
    description,
    openGraph: {
      title,
      description,
      type: "article",
      url: `https://agentnode.net/blog/${post.slug}`,
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
      canonical: `https://agentnode.net/blog/${post.slug}`,
    },
  };
}

export default async function BlogPostPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const post = await getPost(slug);

  if (!post) {
    // Try redirect resolution
    const redirectTo = await resolveRedirect(slug);
    if (redirectTo) redirect(redirectTo);
    notFound();
  }

  // If post exists but belongs to a different type, redirect to correct URL
  if (post.post_type && post.post_type.url_prefix !== "blog") {
    redirect(`/${post.post_type.url_prefix}/${post.slug}`);
  }

  const { cleanHtml, faqItems } = post.content_html
    ? extractFaqFromHtml(post.content_html)
    : { cleanHtml: null, faqItems: [] };

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
      "@id": `https://agentnode.net/blog/${post.slug}`,
    },
    ...(post.reading_time_min ? { wordCount: post.reading_time_min * 200 } : {}),
    ...(post.category ? { articleSection: post.category.name } : {}),
    ...(post.tags?.length ? { keywords: post.tags.join(", ") } : {}),
  };

  const breadcrumbLd = {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: [
      {
        "@type": "ListItem",
        position: 1,
        name: "Blog",
        item: "https://agentnode.net/blog",
      },
      ...(post.category
        ? [
            {
              "@type": "ListItem",
              position: 2,
              name: post.category.name,
              item: `https://agentnode.net/blog/category/${post.category.slug}`,
            },
          ]
        : []),
      {
        "@type": "ListItem",
        position: post.category ? 3 : 2,
        name: post.title,
        item: `https://agentnode.net/blog/${post.slug}`,
      },
    ],
  };

  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }} />
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(breadcrumbLd) }} />

      <article className="mx-auto max-w-3xl px-6 py-16">
        {/* Breadcrumb */}
        <nav className="mb-8 flex items-center gap-2 text-sm text-muted">
          <Link href="/blog" className="hover:text-foreground">Blog</Link>
          {post.category && (
            <>
              <span>/</span>
              <Link href={`/blog/category/${post.category.slug}`} className="hover:text-foreground">
                {post.category.name}
              </Link>
            </>
          )}
          <span>/</span>
          <span className="text-foreground">{post.title}</span>
        </nav>

        {/* Header */}
        <header className="mb-8">
          <div className="mb-4 flex items-center gap-3 text-sm text-muted">
            {post.category && (
              <Link href={`/blog/category/${post.category.slug}`} className="rounded-full bg-primary/10 px-3 py-1 text-primary hover:bg-primary/20">
                {post.category.name}
              </Link>
            )}
            {post.published_at && (
              <time dateTime={post.published_at}>
                {new Date(post.published_at).toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric" })}
              </time>
            )}
            {post.reading_time_min && <span>{post.reading_time_min} min read</span>}
          </div>

          <h1 className="mb-4 text-3xl font-bold tracking-tight sm:text-4xl lg:text-5xl">{post.title}</h1>

          {/* Cover image — between title and excerpt */}
          {post.cover_image_url && (
            <figure className="my-6">
              <SafeImage
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
        {cleanHtml && (
          <div
            className="prose prose-invert max-w-none prose-headings:font-bold prose-h2:text-2xl prose-h3:text-xl prose-a:text-primary prose-code:text-primary prose-pre:bg-card prose-pre:border prose-pre:border-border prose-img:rounded-lg"
            dangerouslySetInnerHTML={{ __html: sanitizeHtml(cleanHtml) }}
          />
        )}

        <FaqSection items={faqItems} />

        {/* Tags */}
        {post.tags?.length > 0 && (
          <div className="mt-12 flex flex-wrap gap-2 border-t border-border pt-6">
            {post.tags.map((tag) => (
              <Link
                key={tag}
                href={`/blog?tag=${encodeURIComponent(tag)}`}
                className="rounded-full bg-card px-3 py-1 text-sm text-muted hover:text-primary"
              >
                #{tag}
              </Link>
            ))}
          </div>
        )}

        {/* Back to blog */}
        <div className="mt-12 border-t border-border pt-6 text-center">
          <Link href="/blog" className="text-primary hover:underline">
            ← Back to Blog
          </Link>
        </div>
      </article>
    </>
  );
}
