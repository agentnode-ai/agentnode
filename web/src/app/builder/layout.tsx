import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Agent Skill Generator — Build Skills for Any AI Agent",
  description:
    "Describe what your agent should be able to do — get a complete prompt-only ANP skill: SKILL.md instructions plus manifest. Ready to edit and publish on AgentNode.",
  alternates: {
    canonical: "/builder",
  },
  openGraph: {
    title: "AI Agent Skill Builder — Create Agent Skills in Minutes",
    description:
      "Build agent skills with AI. Describe what your skill does and get a complete prompt-only ANP package ready to publish.",
    type: "website",
    url: "https://agentnode.net/builder",
    siteName: "AgentNode",
  },
  twitter: {
    card: "summary_large_image",
    site: "@AgentNodenet",
  },
};

export default function BuilderLayout({ children }: { children: React.ReactNode }) {
  return children;
}
