"use client";

// #37 "Connect a gateway". The website signs a short-lived, single-use link for the signed-in user
// (POST /auth/gateway-link, #8) and shows the ONE command the device runs:
//
//   agentnode remote connect <gateway address> --link <grant>
//
// The browser never talks to the gateway itself. A gateway sends no CORS headers and often runs on
// another machine than this browser, so the device that will use the gateway is the one that
// redeems the link (/v1/link on the gateway, #36) -- through the same transport checks as every
// other connect. The grant is shown and kept nowhere else: no storage, no URL.

import { useEffect, useState } from "react";
import Link from "next/link";
import { fetchWithAuth } from "@/lib/api";

const LINK_ID = /^[0-9a-f]{32}$/;
const ADDRESS = /^https?:\/\/[^\s/]+/;

interface Issued {
  grant: string;
  expiresAt: number;
}

type Problem = { text: string; signIn?: boolean } | null;

function secondsLeft(expiresAt: number): number {
  return Math.max(0, Math.floor(expiresAt - Date.now() / 1000));
}

export default function GatewayLinkPage() {
  const [linkId, setLinkId] = useState("");
  const [address, setAddress] = useState("");
  const [issued, setIssued] = useState<Issued | null>(null);
  const [problem, setProblem] = useState<Problem>(null);
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!issued) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [issued]);

  const left = issued ? secondsLeft(issued.expiresAt) : 0;
  const expired = issued !== null && left === 0;
  void now; // re-render each second while a link is shown

  async function createLink(e?: React.FormEvent) {
    e?.preventDefault();
    setProblem(null);
    setCopied(false);
    const id = linkId.trim();
    const where = address.trim().replace(/\/+$/, "");
    if (!LINK_ID.test(id)) {
      setProblem({ text: "The link id is 32 characters, 0-9 and a-f. Run `agentnode gateway status` on the gateway's machine and copy the line \"Link id (for the website)\"." });
      return;
    }
    if (!ADDRESS.test(where)) {
      setProblem({ text: "Give the gateway's address as your device reaches it, starting with https:// (or http:// on the same machine), e.g. https://sandbox.example:8099." });
      return;
    }
    setBusy(true);
    setIssued(null);
    try {
      const res = await fetchWithAuth("/auth/gateway-link", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ gateway_id: id }),
      });
      if (res.status === 200) {
        const body = await res.json();
        setIssued({ grant: String(body.grant), expiresAt: Number(body.expires_at) });
        setNow(Date.now());
      } else if (res.status === 401) {
        setProblem({ text: "Sign in first: a link connects the gateway to your account.", signIn: true });
      } else if (res.status === 422) {
        setProblem({ text: "The website did not accept that link id. Copy it again from `agentnode gateway status`." });
      } else if (res.status === 429) {
        setProblem({ text: "Too many links in a short time. Wait a minute and try again." });
      } else if (res.status === 503) {
        setProblem({ text: "Connecting a gateway is not available on this website yet." });
      } else {
        setProblem({ text: "The link could not be made. Try again in a moment." });
      }
    } catch {
      setProblem({ text: "The website could not be reached. Check your connection and try again." });
    } finally {
      setBusy(false);
    }
  }

  const command = issued ? `agentnode remote connect ${address.trim().replace(/\/+$/, "")} --link ${issued.grant}` : "";

  async function copy() {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  return (
    <main className="mx-auto max-w-2xl px-4 py-10">
      <h1 className="mb-2 text-2xl font-semibold text-foreground">Connect a gateway</h1>
      <p className="mb-6 text-sm text-muted">
        Connect a device to your sandbox gateway under this account. The website makes a link that
        works once and only for a few minutes; you run one command on the device.
      </p>

      <form onSubmit={createLink} className="mb-6 rounded-lg border border-border bg-card p-6" noValidate>
        <label htmlFor="link-id" className="mb-1 block text-sm font-medium text-foreground">Link id</label>
        <input
          id="link-id"
          value={linkId}
          onChange={(e) => setLinkId(e.target.value)}
          placeholder="32 characters from `agentnode gateway status`"
          className="mb-4 w-full rounded border border-border bg-background px-3 py-2 font-mono text-sm"
          autoComplete="off"
          spellCheck={false}
        />
        <label htmlFor="gateway-address" className="mb-1 block text-sm font-medium text-foreground">Gateway address</label>
        <input
          id="gateway-address"
          value={address}
          onChange={(e) => setAddress(e.target.value)}
          placeholder="https://sandbox.example:8099"
          className="mb-4 w-full rounded border border-border bg-background px-3 py-2 font-mono text-sm"
          autoComplete="off"
          spellCheck={false}
        />
        {!issued && (
          <button
            type="submit"
            disabled={busy}
            className="rounded bg-primary px-4 py-2 text-sm text-white hover:bg-primary/90 disabled:opacity-50"
          >
            Create link
          </button>
        )}
      </form>

      {problem && (
        <div role="alert" className="mb-6 rounded border border-red-500/40 bg-red-500/10 p-4 text-sm text-foreground">
          {problem.text}{" "}
          {problem.signIn && <Link href="/auth/login" className="underline">Sign in</Link>}
        </div>
      )}

      {issued && !expired && (
        <section className="mb-6 rounded-lg border border-border bg-card p-6">
          <h2 className="mb-2 text-lg font-semibold text-foreground">Run this on the device</h2>
          <pre data-testid="link-command" className="mb-3 overflow-x-auto whitespace-pre-wrap break-all rounded bg-background p-3 font-mono text-xs">{command}</pre>
          <div className="mb-3 flex items-center gap-3">
            <button type="button" onClick={copy} className="rounded border border-border px-3 py-1.5 text-sm hover:bg-background">
              {copied ? "Copied" : "Copy"}
            </button>
            <span className="text-sm text-muted">Valid for {left} s</span>
          </div>
          <p className="text-sm text-muted">
            It works once. If it is run a second time, or after it expires, the gateway refuses it
            and nothing is connected; make a new link instead.
          </p>
        </section>
      )}

      {issued && expired && (
        <section className="mb-6 rounded-lg border border-border bg-card p-6">
          <p className="text-sm text-foreground">This link has expired. It was never shown again and cannot be used.</p>
        </section>
      )}

      {issued && (
        <button
          type="button"
          onClick={() => createLink()}
          disabled={busy}
          className="rounded bg-primary px-4 py-2 text-sm text-white hover:bg-primary/90 disabled:opacity-50"
        >
          Make a new link
        </button>
      )}
    </main>
  );
}
