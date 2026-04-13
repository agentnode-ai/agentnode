import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Navbar from "@/components/Navbar";
import Footer from "@/components/Footer";
import SkipToMain from "@/components/SkipToMain";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

// P1-SEO1: metadataBase resolves relative OG/image/canonical URLs to the
// production origin. Without this Next emits a warning and relative OG
// URLs do not resolve correctly for external scrapers (X/LinkedIn/Slack
// previews). Overridable via SITE_URL env var for staging/previews.
const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || "https://agentnode.net";

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: {
    template: "%s | AgentNode",
    default: "AgentNode — Verified Agent Skills & Tools for AI Agents",
  },
  description:
    "The verified registry for AI agent skills and tools. Discover, install, and publish agent skills that work across LangChain, CrewAI, MCP, and plain Python.",
  keywords: [
    "agent skills",
    "agent tools",
    "AI agent skills",
    "MCP tools",
    "AI agent tools",
    "agent skill registry",
    "AI agent marketplace",
    "LangChain tools",
    "CrewAI tools",
    "model context protocol",
  ],
  icons: {
    icon: [
      { url: "/favicon.svg", type: "image/svg+xml" },
      { url: "/favicon.ico", sizes: "any" },
    ],
    apple: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased min-h-screen flex flex-col`}
      >
        <SkipToMain />
        <Navbar />
        <main id="main" className="flex-1">{children}</main>
        <Footer />
      </body>
    </html>
  );
}
