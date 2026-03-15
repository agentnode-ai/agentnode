"use client";

import CodeBlock from "@/components/CodeBlock";

interface CodeBlockWrapperProps {
  code: string;
  language?: string;
}

export default function CodeBlockWrapper({ code, language }: CodeBlockWrapperProps) {
  return <CodeBlock code={code} language={language} />;
}
