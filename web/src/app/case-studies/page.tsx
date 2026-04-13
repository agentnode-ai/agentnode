export const revalidate = 3600; // P1-SEO3: ISR (1h) instead of force-dynamic

import type { Metadata } from "next";
import PostTypeArchive, { generateArchiveMetadata } from "@/components/blog/PostTypeArchive";

export async function generateMetadata(): Promise<Metadata> {
  return generateArchiveMetadata("case-study");
}

export default function CaseStudiesPage() {
  return <PostTypeArchive postTypeSlug="case-study" />;
}
