import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "AI Agent Capabilities — Browse 80+ Verified Skill Categories",
  description:
    "Explore AgentNode's capability taxonomy with 80+ categories. From PDF extraction to web search, database queries to code generation — find the right agent skill by capability.",
  openGraph: {
    title: "AI Agent Capabilities — 80+ Skill Categories",
    description:
      "Browse 80+ agent skill categories on AgentNode. Find verified tools by what they do, not by package name.",
    type: "website",
    url: "https://agentnode.net/capabilities",
    siteName: "AgentNode",
  },
};

export default function CapabilitiesLayout({ children }: { children: React.ReactNode }) {
  return children;
}
