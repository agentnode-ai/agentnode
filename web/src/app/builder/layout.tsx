import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Agent Skill Generator — Build Skills for Any AI Agent",
  description:
    "Describe what your agent should do — get a fully working ANP package with code, schema and entrypoints. Ready to edit, run and publish on AgentNode.",
  openGraph: {
    title: "AI Agent Skill Builder — Create Agent Skills in Minutes",
    description:
      "Build verified agent skills with AI. Describe what your tool does and get a complete ANP package ready to publish.",
    type: "website",
    url: "https://agentnode.net/builder",
    siteName: "AgentNode",
  },
};

export default function BuilderLayout({ children }: { children: React.ReactNode }) {
  return children;
}
