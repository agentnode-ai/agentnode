import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "AI Agent Skill Builder — Create Agent Skills in Minutes",
  description:
    "Build verified agent skills with AI. Describe what your tool does and get a complete ANP package — manifest, code, schemas — ready to publish on AgentNode.",
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
