import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Compare Agent Skills — Side-by-Side AI Tool Comparison",
  description:
    "Compare agent skills and AI tools side by side. See trust levels, capabilities, framework compatibility, and verification status at a glance on AgentNode.",
  openGraph: {
    title: "Compare Agent Skills — Side-by-Side Tool Comparison",
    description:
      "Compare agent skills side by side on AgentNode. Trust levels, capabilities, and framework compatibility at a glance.",
    type: "website",
    url: "https://agentnode.net/compare",
    siteName: "AgentNode",
  },
};

export default function CompareLayout({ children }: { children: React.ReactNode }) {
  return children;
}
