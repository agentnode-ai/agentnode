import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Import Agent Skills — Convert LangChain, MCP & CrewAI Tools",
  description:
    "Convert your existing AI tools into portable agent skills. Paste LangChain, MCP, CrewAI, or OpenAI code and get a verified ANP package — zero rewrites needed.",
  openGraph: {
    title: "Import Agent Skills — Convert LangChain, MCP & CrewAI Tools",
    description:
      "Convert existing AI tools into portable agent skills. Paste your code and get a verified ANP package — zero rewrites.",
    type: "website",
    url: "https://agentnode.net/import",
    siteName: "AgentNode",
  },
};

export default function ImportLayout({ children }: { children: React.ReactNode }) {
  return children;
}
