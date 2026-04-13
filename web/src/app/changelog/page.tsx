export const revalidate = 3600; // P1-SEO3: ISR (1h) instead of force-dynamic

import type { Metadata } from "next";
import PostTypeArchive, { generateArchiveMetadata } from "@/components/blog/PostTypeArchive";

export async function generateMetadata(): Promise<Metadata> {
  return generateArchiveMetadata("changelog");
}

export default function ChangelogPage() {
  return <PostTypeArchive postTypeSlug="changelog" />;
}
