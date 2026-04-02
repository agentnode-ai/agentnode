import DOMPurify from "isomorphic-dompurify";

/**
 * Sanitize HTML content to prevent XSS.
 * Allows standard blog formatting tags but strips scripts, event handlers, etc.
 */
export function sanitizeHtml(dirty: string): string {
  return DOMPurify.sanitize(dirty, {
    ADD_TAGS: ["iframe"],
    ADD_ATTR: ["target", "rel", "loading", "allow", "allowfullscreen", "frameborder"],
    FORBID_TAGS: ["style"],
    FORBID_ATTR: ["onerror", "onload", "onclick", "onmouseover"],
  });
}
