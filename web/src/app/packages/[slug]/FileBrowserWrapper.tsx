"use client";

import FileBrowser from "@/components/FileBrowser";

interface FileBrowserWrapperProps {
  files: Array<{ path: string; size: number }>;
  slug: string;
  version: string;
}

export default function FileBrowserWrapper(props: FileBrowserWrapperProps) {
  return <FileBrowser {...props} />;
}
