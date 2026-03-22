export const dynamic = "force-dynamic";

import type { Metadata } from "next";
import PostTypeArchive, { generateArchiveMetadata } from "@/components/blog/PostTypeArchive";

export async function generateMetadata(): Promise<Metadata> {
  return generateArchiveMetadata("tutorial");
}

export default function TutorialsPage() {
  return <PostTypeArchive postTypeSlug="tutorial" />;
}
