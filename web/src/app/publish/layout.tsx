import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Publish Agent Skills — Share Your AI Tools with Agents Worldwide",
  description:
    "Publish your AI agent skills on AgentNode. Your tools get 4-step verification, trust badges, and cross-framework compatibility — discoverable by agents everywhere.",
  openGraph: {
    title: "Publish Agent Skills on AgentNode",
    description:
      "Publish AI agent skills that get verified, trusted, and installed by agents worldwide. 4-step verification pipeline included.",
    type: "website",
    url: "https://agentnode.net/publish",
    siteName: "AgentNode",
  },
};

export default function PublishLayout({ children }: { children: React.ReactNode }) {
  return children;
}
