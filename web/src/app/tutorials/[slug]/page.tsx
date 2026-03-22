export const dynamic = "force-dynamic";

import type { Metadata } from "next";
import PostTypeSingle, { generateSingleMetadata } from "@/components/blog/PostTypeSingle";

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const { slug } = await params;
  return generateSingleMetadata("tutorial", slug, "tutorials");
}

export default async function TutorialPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return <PostTypeSingle postTypeSlug="tutorial" slug={slug} urlPrefix="tutorials" />;
}
