/**
 * P1-F: "Skip to main content" link for keyboard users.
 *
 * Rendered as the very first focusable element on every page so that
 * screen-reader and keyboard-only navigators can jump past the global
 * navbar on each page load. The link is visually hidden until it
 * receives keyboard focus, then becomes a high-contrast pill in the
 * top-left corner.
 *
 * The target is the <main id="main"> element rendered by the root
 * layout.
 */
export default function SkipToMain() {
  return (
    <a
      href="#main"
      className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[100] focus:rounded-md focus:bg-primary focus:px-4 focus:py-2 focus:text-sm focus:font-medium focus:text-white focus:outline-none focus:ring-2 focus:ring-white"
    >
      Skip to main content
    </a>
  );
}
