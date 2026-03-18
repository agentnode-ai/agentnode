import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Search Agent Skills & Tools — Find Verified AI Capabilities",
  description:
    "Search the AgentNode registry for verified agent skills and tools. Filter by capability, framework, trust level, and runtime — find the right tool for your AI agent.",
  openGraph: {
    title: "Search Agent Skills & Tools on AgentNode",
    description:
      "Find verified agent skills by capability, framework, or trust level. The smart search for AI agent tools.",
    type: "website",
    url: "https://agentnode.net/search",
    siteName: "AgentNode",
  },
};

export default function SearchLayout({ children }: { children: React.ReactNode }) {
  return children;
}
