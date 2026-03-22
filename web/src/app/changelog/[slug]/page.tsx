export const dynamic = "force-dynamic";

import type { Metadata } from "next";
import PostTypeSingle, { generateSingleMetadata } from "@/components/blog/PostTypeSingle";

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const { slug } = await params;
  return generateSingleMetadata("changelog", slug, "changelog");
}

export default async function ChangelogEntryPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return <PostTypeSingle postTypeSlug="changelog" slug={slug} urlPrefix="changelog" />;
}
