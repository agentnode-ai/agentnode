import { revalidatePath } from "next/cache";
import { NextRequest, NextResponse } from "next/server";

const REVALIDATE_SECRET = process.env.REVALIDATE_SECRET || "";

/**
 * On-demand revalidation endpoint.
 * Called by admin pages after mutations (save, publish, delete)
 * to bust the Next.js data cache immediately.
 *
 * POST /api/revalidate { paths: ["/blog", "/blog/my-slug"] }
 *
 * In production, requires X-Revalidate-Secret header matching REVALIDATE_SECRET env var.
 */
export async function POST(req: NextRequest) {
  try {
    // Require secret to prevent cache-busting abuse
    if (!REVALIDATE_SECRET) {
      if (process.env.NODE_ENV === "production") {
        return NextResponse.json({ error: "REVALIDATE_SECRET not configured" }, { status: 500 });
      }
      // In development, allow without secret
    } else {
      const secret = req.headers.get("x-revalidate-secret");
      if (secret !== REVALIDATE_SECRET) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
      }
    }

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
