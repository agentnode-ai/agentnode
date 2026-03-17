"use client";

import { useState, useEffect, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";

function VerifyEmailContent() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token");

  const [status, setStatus] = useState<"loading" | "success" | "error">("loading");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!token) {
      setStatus("error");
      setError("No verification token provided.");
      return;
    }

    fetch("/api/v1/auth/email/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ token }),
    })
      .then(async (res) => {
        if (res.ok) {
          setStatus("success");
        } else {
          const data = await res.json();
          setError(data.error?.message || "Verification failed. The link may have expired.");
          setStatus("error");
        }
      })
      .catch(() => {
        setError("Network error. Please try again.");
        setStatus("error");
      });
  }, [token]);

  return (
    <div className="mx-auto max-w-md px-4 py-24 text-center">
      {status === "loading" && (
        <>
          <h1 className="mb-4 text-2xl font-bold text-foreground">Verifying your email...</h1>
          <p className="text-sm text-muted">Please wait.</p>
        </>
      )}

      {status === "success" && (
        <>
          <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-success/20">
            <span className="text-2xl text-success">&#10003;</span>
          </div>
          <h1 className="mb-4 text-2xl font-bold text-foreground">Email verified</h1>
          <p className="mb-6 text-sm text-muted">Your email address has been verified successfully.</p>
          <Link href="/dashboard" className="inline-block rounded-md bg-primary px-6 py-2 text-sm font-medium text-white hover:bg-primary/90">
            Go to Dashboard
          </Link>
        </>
      )}

      {status === "error" && (
        <>
          <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-danger/20">
            <span className="text-2xl text-danger">&#10007;</span>
          </div>
          <h1 className="mb-4 text-2xl font-bold text-foreground">Verification failed</h1>
          <p className="mb-6 text-sm text-muted">{error}</p>
          <Link href="/dashboard" className="inline-block rounded-md border border-border px-6 py-2 text-sm text-muted hover:text-foreground">
            Go to Dashboard
          </Link>
        </>
      )}
    </div>
  );
}

export default function VerifyEmailPage() {
  return (
    <Suspense fallback={<div className="py-24 text-center text-muted">Loading...</div>}>
      <VerifyEmailContent />
    </Suspense>
  );
}
