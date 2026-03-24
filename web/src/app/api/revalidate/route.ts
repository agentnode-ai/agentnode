import { revalidatePath } from "next/cache";
import { NextRequest, NextResponse } from "next/server";

/**
 * On-demand revalidation endpoint.
 * Called by admin pages after mutations (save, publish, delete)
 * to bust the Next.js data cache immediately.
 *
 * POST /api/revalidate { paths: ["/blog", "/blog/my-slug"] }
 */
export async function POST(req: NextRequest) {
  try {
    const { paths } = await req.json();
    if (!Array.isArray(paths) || paths.length === 0) {
      return NextResponse.json({ error: "paths required" }, { status: 400 });
    }

    for (const p of paths) {
      if (typeof p === "string" && p.startsWith("/")) {
        revalidatePath(p);
      }
    }

    return NextResponse.json({ revalidated: paths });
  } catch {
    return NextResponse.json({ error: "Invalid request" }, { status: 400 });
  }
}
