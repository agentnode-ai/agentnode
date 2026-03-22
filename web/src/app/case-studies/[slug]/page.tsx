export const dynamic = "force-dynamic";

import type { Metadata } from "next";
import PostTypeSingle, { generateSingleMetadata } from "@/components/blog/PostTypeSingle";

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const { slug } = await params;
  return generateSingleMetadata("case-study", slug, "case-studies");
}

export default async function CaseStudyPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return <PostTypeSingle postTypeSlug="case-study" slug={slug} urlPrefix="case-studies" />;
}
