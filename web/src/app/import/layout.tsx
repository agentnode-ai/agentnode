import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Agent Skill Import — Turn Any Tool Into an AgentNode Package",
  description:
    "Paste code from LangChain, MCP, OpenAI or CrewAI — get a verified, publishable ANP package in seconds. Ready to run on any agent.",
  openGraph: {
    title: "Agent Skill Import — Turn Any Tool Into an AgentNode Package",
    description:
      "Paste code from LangChain, MCP, OpenAI or CrewAI — get a verified, publishable ANP package in seconds. Ready to run on any agent.",
    type: "website",
    url: "https://agentnode.net/import",
    siteName: "AgentNode",
  },
};

export default function ImportLayout({ children }: { children: React.ReactNode }) {
  return children;
}
