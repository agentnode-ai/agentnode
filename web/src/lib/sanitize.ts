import DOMPurify from "isomorphic-dompurify";

/**
 * Sanitize HTML content to prevent XSS.
 * Allows standard blog formatting tags but strips scripts, event handlers, etc.
 */
export function sanitizeHtml(dirty: string): string {
  const clean = DOMPurify.sanitize(dirty, {
    ADD_TAGS: ["iframe"],
    ADD_ATTR: ["target", "rel", "loading", "allow", "allowfullscreen", "frameborder"],
    FORBID_TAGS: ["style"],
    FORBID_ATTR: ["onerror", "onload", "onclick", "onmouseover"],
    ALLOWED_URI_REGEXP: /^(?:https?:|mailto:|tel:|\/)/i,
  });

  // Restrict iframe src to trusted embed domains only
  const TRUSTED_IFRAME_DOMAINS = [
    "www.youtube.com",
    "youtube.com",
    "player.vimeo.com",
    "www.loom.com",
    "codepen.io",
  ];
  return clean.replace(
    /<iframe\s[^>]*src=["']([^"']*)["'][^>]*>/gi,
    (match, src) => {
      try {
        const url = new URL(src);
        if (TRUSTED_IFRAME_DOMAINS.includes(url.hostname)) {
          return match;
        }
      } catch {
        // invalid URL — strip
      }
      return "";
    },
  );
}
