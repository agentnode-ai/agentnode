"use client";

import { useState } from "react";

/** Image that hides itself when the src returns an error (e.g. deleted from media library). */
export default function SafeImage(props: React.ImgHTMLAttributes<HTMLImageElement>) {
  const [hidden, setHidden] = useState(false);
  if (hidden) return null;
  return <img {...props} onError={() => setHidden(true)} />;
}
