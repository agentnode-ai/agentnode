"use client";

import QuickStart from "@/components/QuickStart";

interface QuickStartWrapperProps {
  slug: string;
  entrypoint?: string | null;
  examples?: Array<{ title: string; language: string; code: string }> | null;
  envRequirements?: Array<{ name: string; required: boolean; description?: string | null }> | null;
  readmeMd?: string | null;
  installResolution?: string | null;
  installableVersion?: string | null;
  latestVersion?: string | null;
  sdkCode?: string | null;
  postInstallCode?: string | null;
}

export default function QuickStartWrapper(props: QuickStartWrapperProps) {
  return <QuickStart {...props} />;
}
