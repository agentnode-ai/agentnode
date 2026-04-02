"use client";

import { useState, useEffect } from "react";
import QRCode from "qrcode";

interface QRCodeImageProps {
  uri: string;
  width?: number;
}

export default function QRCodeImage({ uri, width = 200 }: QRCodeImageProps) {
  const [dataUrl, setDataUrl] = useState("");

  useEffect(() => {
    let cancelled = false;
    QRCode.toDataURL(uri, { width, margin: 2 })
      .then((url) => {
        if (!cancelled) setDataUrl(url);
      })
      .catch(() => {
        /* fallback to text */
      });
    return () => {
      cancelled = true;
    };
  }, [uri, width]);

  if (!dataUrl) return null;

  return (
    <div className="mb-4 flex justify-center">
      <img src={dataUrl} alt="2FA QR Code" width={width} height={width} className="rounded-lg" />
    </div>
  );
}
