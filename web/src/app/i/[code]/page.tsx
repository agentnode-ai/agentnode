"use client";

import { useEffect } from "react";
import { useParams, useRouter } from "next/navigation";

export default function TrackingRedirect() {
  const params = useParams();
  const router = useRouter();
  const code = params.code as string;

  useEffect(() => {
    // Fire tracking event via backend, then redirect
    fetch(`/api/v1/i/${encodeURIComponent(code)}`, { redirect: "manual" })
      .catch(() => {})
      .finally(() => {
        router.replace(`/invite/${encodeURIComponent(code)}`);
      });
  }, [code, router]);

  return (
    <div className="flex min-h-[60vh] items-center justify-center text-muted">
      Redirecting...
    </div>
  );
}
